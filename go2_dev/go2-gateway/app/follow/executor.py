from __future__ import annotations

import json
import logging
import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

from app.config import Settings
from app.core.control_owner import ControlOwner
from app.follow.controller import SafetyState, VelocityCommand
from app.follow.planner import FollowPlan
from app.services.robot_service import RobotService


LOGGER = logging.getLogger(__name__)


class FollowExecutionStatus(str, Enum):
    DISABLED = "disabled"
    SIMULATION_ONLY = "simulation_only"
    EMERGENCY_STOPPED = "emergency_stopped"
    CONTROL_YIELDED = "control_yielded"
    SAFETY_STOPPED = "safety_stopped"
    ZERO_COMMAND_STOPPED = "zero_command_stopped"
    NO_NEW_UWB_DATA = "no_new_uwb_data"
    RATE_LIMITED = "rate_limited"
    SINGLE_COMMAND_LIMIT = "single_command_limit"
    INVALID_COMMAND_STOPPED = "invalid_command_stopped"
    SENT = "sent"
    ERROR_STOPPED = "error_stopped"


@dataclass(frozen=True)
class RealMotionSafetyLimit:
    """Hard first-motion limits, independent of controller tuning."""

    max_vx: float = 0.10
    max_vy: float = 0.0
    max_wz: float = 0.15

    def __post_init__(self) -> None:
        for name in ("max_vx", "max_vy", "max_wz"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and not negative")
        if self.max_vx == 0.0 or self.max_wz == 0.0:
            raise ValueError("max_vx and max_wz must be greater than zero")


@dataclass(frozen=True)
class FollowExecutorConfig:
    execution_enabled: bool = False
    max_frequency_hz: float = 5.0
    command_duration_seconds: float = 0.10
    single_command_only: bool = True
    temporary_joystick_handoff: bool = False
    joystick_handoff_settle_seconds: float = 0.10

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.max_frequency_hz)
            or not 0.0 < self.max_frequency_hz <= 5.0
        ):
            raise ValueError("max_frequency_hz must be in (0, 5]")
        if (
            not math.isfinite(self.command_duration_seconds)
            or self.command_duration_seconds <= 0.0
        ):
            raise ValueError("command_duration_seconds must be greater than zero")
        if (
            not math.isfinite(self.joystick_handoff_settle_seconds)
            or not 0.0 <= self.joystick_handoff_settle_seconds <= 1.0
        ):
            raise ValueError("joystick_handoff_settle_seconds must be in [0, 1]")

    @classmethod
    def from_settings(cls, settings: Settings) -> FollowExecutorConfig:
        return cls(execution_enabled=settings.follow_execution_enabled)


@dataclass(frozen=True)
class FollowExecutionResult:
    timestamp: str
    execution_result: FollowExecutionStatus
    vx: float
    vy: float
    wz: float
    safety_state: SafetyState
    robot_result: dict | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "vx": self.vx,
            "vy": self.vy,
            "wz": self.wz,
            "safety_state": self.safety_state.value,
            "execution_result": self.execution_result.value,
            "robot_result": self.robot_result,
        }


class FollowExecutor:
    """Execute at most one bounded follow motion through ``RobotService``.

    The first real-motion phase deliberately has no continuous loop. A caller
    must provide control ownership and emergency-stop state providers.
    """

    def __init__(
        self,
        robot_service: RobotService,
        *,
        config: FollowExecutorConfig | None = None,
        limits: RealMotionSafetyLimit | None = None,
        control_owner_provider: Callable[[], ControlOwner] = lambda: ControlOwner.NONE,
        emergency_stop_provider: Callable[[], bool] = lambda: False,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleep: Callable[[float], None] = time.sleep,
        logger: logging.Logger = LOGGER,
    ) -> None:
        self.robot_service = robot_service
        self.config = config or FollowExecutorConfig.from_settings(Settings())
        self.limits = limits or RealMotionSafetyLimit()
        self._control_owner_provider = control_owner_provider
        self._emergency_stop_provider = emergency_stop_provider
        self._monotonic_clock = monotonic_clock
        self._wall_clock = wall_clock
        self._sleep = sleep
        self._logger = logger
        self._state_lock = threading.Lock()
        self._last_dispatch_at: float | None = None
        self._last_source_timestamp: str | None = None
        self._motion_command_sent = False

    def execute(
        self,
        command: VelocityCommand,
        *,
        plan: FollowPlan | None = None,
    ) -> FollowExecutionResult:
        """Execute one short motion pulse, or return a non-motion result."""

        if not self.config.execution_enabled:
            return self._complete(
                FollowExecutionStatus.DISABLED,
                command,
                plan=plan,
            )

        if self._emergency_stop_provider():
            self.robot_service.safe_stop("follow_executor:emergency")
            return self._complete(
                FollowExecutionStatus.EMERGENCY_STOPPED,
                command,
                plan=plan,
            )

        control_owner = self._control_owner_provider()
        if control_owner is ControlOwner.EMERGENCY_STOP:
            self.robot_service.safe_stop("follow_executor:emergency_owner")
            return self._complete(
                FollowExecutionStatus.EMERGENCY_STOPPED,
                command,
                plan=plan,
            )
        if control_owner is not ControlOwner.FOLLOW:
            # Yield without issuing StopMove, so a higher-priority manual
            # controller is not interrupted by the follow executor.
            return self._complete(
                FollowExecutionStatus.CONTROL_YIELDED,
                command,
                plan=plan,
            )

        if command.safety_state is not SafetyState.SAFE:
            self.robot_service.safe_stop("follow_executor:safety")
            return self._complete(
                FollowExecutionStatus.SAFETY_STOPPED,
                command,
                plan=plan,
            )

        if command.simulation_mode:
            return self._complete(
                FollowExecutionStatus.SIMULATION_ONLY,
                command,
                plan=plan,
            )

        if not all(math.isfinite(value) for value in (command.vx, command.vy, command.wz)):
            self.robot_service.safe_stop("follow_executor:invalid_command")
            return self._complete(
                FollowExecutionStatus.INVALID_COMMAND_STOPPED,
                command,
                plan=plan,
            )

        now = self._monotonic_clock()
        if not math.isfinite(now):
            raise ValueError("monotonic clock must return a finite value")

        with self._state_lock:
            if (
                plan is not None
                and self._last_source_timestamp is not None
                and plan.timestamp == self._last_source_timestamp
            ):
                return self._complete(
                    FollowExecutionStatus.NO_NEW_UWB_DATA,
                    command,
                    plan=plan,
                )

            if (
                self._last_dispatch_at is not None
                and now - self._last_dispatch_at < 1.0 / self.config.max_frequency_hz
            ):
                return self._complete(
                    FollowExecutionStatus.RATE_LIMITED,
                    command,
                    plan=plan,
                )

            if self.config.single_command_only and self._motion_command_sent:
                return self._complete(
                    FollowExecutionStatus.SINGLE_COMMAND_LIMIT,
                    command,
                    plan=plan,
                )

            applied_vx = _clamp(command.vx, -self.limits.max_vx, self.limits.max_vx)
            applied_vy = (
                0.0
                if self.limits.max_vy == 0.0
                else _clamp(command.vy, -self.limits.max_vy, self.limits.max_vy)
            )
            applied_wz = _clamp(command.wz, -self.limits.max_wz, self.limits.max_wz)

            if _is_zero(applied_vx, applied_vy, applied_wz):
                self.robot_service.safe_stop("follow_executor:zero_command")
                return self._complete(
                    FollowExecutionStatus.ZERO_COMMAND_STOPPED,
                    command,
                    plan=plan,
                )

            # Reserve the one-shot and rate slot before the blocking service
            # call. An exception cannot accidentally permit an immediate retry.
            self._motion_command_sent = True
            self._last_dispatch_at = now
            self._last_source_timestamp = plan.timestamp if plan is not None else None

        handoff_attempted = False
        try:
            try:
                if self.config.temporary_joystick_handoff:
                    handoff_attempted = True
                    self.robot_service.switch_joystick(
                        False,
                        source="follow_executor:handoff",
                    )
                    self._sleep(self.config.joystick_handoff_settle_seconds)
                robot_result = self.robot_service.move(
                    applied_vx,
                    applied_vy,
                    applied_wz,
                    self.config.command_duration_seconds,
                    source="follow_executor",
                )
            finally:
                if handoff_attempted:
                    self._restore_joystick()
        except Exception:
            self.robot_service.safe_stop("follow_executor:error")
            result = self._complete(
                FollowExecutionStatus.ERROR_STOPPED,
                command,
                plan=plan,
                applied=(applied_vx, applied_vy, applied_wz),
            )
            self._logger.exception("follow execution failed result=%s", result.to_dict())
            raise

        return self._complete(
            FollowExecutionStatus.SENT,
            command,
            plan=plan,
            applied=(applied_vx, applied_vy, applied_wz),
            robot_result=robot_result,
        )

    def _restore_joystick(self) -> None:
        for attempt in range(3):
            code = self.robot_service.safe_switch_joystick(
                True,
                source=f"follow_executor:restore:{attempt + 1}",
            )
            if code == 0:
                return
            self._sleep(0.10)
        raise RuntimeError("failed to restore native joystick control")

    def rearm_single_command(self) -> None:
        """Explicitly allow the next isolated motion pulse."""

        with self._state_lock:
            self._motion_command_sent = False

    def _complete(
        self,
        status: FollowExecutionStatus,
        command: VelocityCommand,
        *,
        plan: FollowPlan | None,
        applied: tuple[float, float, float] = (0.0, 0.0, 0.0),
        robot_result: dict | None = None,
    ) -> FollowExecutionResult:
        timestamp = self._wall_clock()
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        result = FollowExecutionResult(
            timestamp=timestamp.astimezone(timezone.utc).isoformat(),
            execution_result=status,
            vx=applied[0],
            vy=applied[1],
            wz=applied[2],
            safety_state=command.safety_state,
            robot_result=robot_result,
        )
        record = {
            "timestamp": result.timestamp,
            "uwb_distance": plan.uwb_distance if plan is not None else None,
            "target_x": plan.target_x if plan is not None else None,
            "target_y": plan.target_y if plan is not None else None,
            "vx": result.vx,
            "wz": result.wz,
            "safety_state": result.safety_state.value,
            "execution_result": result.execution_result.value,
        }
        self._logger.info(
            json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        )
        return result


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def _is_zero(vx: float, vy: float, wz: float) -> bool:
    return math.isclose(vx, 0.0, abs_tol=1e-12) and math.isclose(
        vy, 0.0, abs_tol=1e-12
    ) and math.isclose(wz, 0.0, abs_tol=1e-12)
