from __future__ import annotations

import math
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from app.config import Settings
from app.follow.executor import RealMotionSafetyLimit
from app.motion.arbiter import ArbiterDecision, MotionAuthority
from app.services.robot_service import RobotService


class RealFollowExecutionStatus(str, Enum):
    DISABLED = "DISABLED"
    NOT_ARMED = "NOT_ARMED"
    RESUME_REQUIRED = "RESUME_REQUIRED"
    STOPPED = "STOPPED"
    SENT = "SENT"
    DUPLICATE_DECISION = "DUPLICATE_DECISION"
    RATE_LIMITED = "RATE_LIMITED"


@dataclass(frozen=True)
class RealFollowExecutorConfig:
    execution_enabled: bool = False
    command_duration_seconds: float = 0.10
    max_frequency_hz: float = 5.0
    continuous_velocity_refresh: bool = False

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.command_duration_seconds)
            or not 0.05 <= self.command_duration_seconds <= 0.10
        ):
            raise ValueError("command_duration_seconds must be within [0.05, 0.10]")
        if (
            not math.isfinite(self.max_frequency_hz)
            or not 0.0 < self.max_frequency_hz <= 5.0
        ):
            raise ValueError("max_frequency_hz must be within (0, 5]")

    @classmethod
    def from_settings(cls, settings: Settings) -> RealFollowExecutorConfig:
        return cls(execution_enabled=settings.phase7_motion_execution_enabled)


@dataclass(frozen=True)
class RealFollowExecutionResult:
    status: RealFollowExecutionStatus
    decision_sequence: int
    authority: MotionAuthority
    reason: str
    vx: float = 0.0
    vy: float = 0.0
    wz: float = 0.0


class RealFollowExecutor:
    """The Phase 7 autonomous path from arbiter decision to RobotService.

    Motion is disabled and unarmed by default. Any stop/preemption latches a
    resume hold; a human operator must call ``authorize_resume`` after all
    inputs return safe. Raw FollowController commands are intentionally not
    accepted by this interface.
    """

    def __init__(
        self,
        robot_service: RobotService,
        *,
        config: RealFollowExecutorConfig | None = None,
        limits: RealMotionSafetyLimit | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.robot_service = robot_service
        self.config = config or RealFollowExecutorConfig.from_settings(Settings())
        self.limits = limits or RealMotionSafetyLimit()
        self._monotonic_clock = monotonic_clock
        self._armed = False
        self._resume_authorized = False
        self._last_decision_sequence = 0
        self._last_stop_key: tuple[MotionAuthority, str] | None = None
        self._last_dispatch_at: float | None = None

    @property
    def armed(self) -> bool:
        return self._armed

    @property
    def resume_authorized(self) -> bool:
        return self._resume_authorized

    def arm_for_supervised_test(self) -> None:
        if not self.config.execution_enabled:
            raise RuntimeError("real follow execution gate is disabled")
        self._armed = True
        self._resume_authorized = False

    def authorize_resume(self) -> None:
        if not self._armed:
            raise RuntimeError("executor must be armed before resume is authorized")
        self._resume_authorized = True

    def disarm(self) -> None:
        self._armed = False
        self._resume_authorized = False
        self.robot_service.safe_stop("phase7:disarm")

    def execute(self, decision: ArbiterDecision) -> RealFollowExecutionResult:
        if decision.sequence <= self._last_decision_sequence:
            return self._result(RealFollowExecutionStatus.DUPLICATE_DECISION, decision)
        self._last_decision_sequence = decision.sequence

        if not self.config.execution_enabled:
            return self._result(RealFollowExecutionStatus.DISABLED, decision)
        if not self._armed:
            return self._result(RealFollowExecutionStatus.NOT_ARMED, decision)

        if decision.stop_required or decision.authority is not MotionAuthority.FOLLOW:
            self._resume_authorized = False
            stop_key = (decision.authority, decision.reason)
            if stop_key != self._last_stop_key:
                code = self.robot_service.safe_stop(
                    f"phase7:{decision.authority.value.lower()}:{decision.reason}"
                )
                if code == 0:
                    self._last_stop_key = stop_key
            return self._result(RealFollowExecutionStatus.STOPPED, decision)

        if not self._resume_authorized:
            return self._result(RealFollowExecutionStatus.RESUME_REQUIRED, decision)

        now = self._monotonic_clock()
        if not math.isfinite(now):
            raise ValueError("monotonic clock must return a finite value")
        if (
            self._last_dispatch_at is not None
            and now - self._last_dispatch_at < 1.0 / self.config.max_frequency_hz
        ):
            return self._result(RealFollowExecutionStatus.RATE_LIMITED, decision)

        vx = _clamp(decision.vx, -self.limits.max_vx, self.limits.max_vx)
        vy = (
            0.0
            if self.limits.max_vy == 0.0
            else _clamp(decision.vy, -self.limits.max_vy, self.limits.max_vy)
        )
        wz = _clamp(decision.wz, -self.limits.max_wz, self.limits.max_wz)
        if not all(math.isfinite(value) for value in (vx, vy, wz)):
            self._resume_authorized = False
            self.robot_service.safe_stop("phase7:invalid_arbitrated_command")
            return self._result(RealFollowExecutionStatus.STOPPED, decision)

        self._last_dispatch_at = now
        try:
            if self.config.continuous_velocity_refresh:
                self.robot_service.refresh_velocity(
                    vx,
                    vy,
                    wz,
                    source="phase7_motion_arbiter",
                )
            else:
                self.robot_service.move(
                    vx,
                    vy,
                    wz,
                    self.config.command_duration_seconds,
                    source="phase7_motion_arbiter",
                )
        except Exception:
            self._resume_authorized = False
            self.robot_service.safe_stop("phase7:move_error")
            raise
        self._last_stop_key = None
        return self._result(RealFollowExecutionStatus.SENT, decision, vx, vy, wz)

    @staticmethod
    def _result(
        status: RealFollowExecutionStatus,
        decision: ArbiterDecision,
        vx: float = 0.0,
        vy: float = 0.0,
        wz: float = 0.0,
    ) -> RealFollowExecutionResult:
        return RealFollowExecutionResult(
            status=status,
            decision_sequence=decision.sequence,
            authority=decision.authority,
            reason=decision.reason,
            vx=vx,
            vy=vy,
            wz=wz,
        )


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))
