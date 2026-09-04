from __future__ import annotations

import math
import logging
import time
from dataclasses import asdict, dataclass, replace
from enum import Enum
from pathlib import Path
from threading import Condition, Event, Thread
from typing import Any, Callable, Protocol

import yaml

from app.companion.config import FollowProfile
from app.core.control_owner import ControlOwner
from app.follow.controller import (
    FollowController,
    SafetyGuard,
    SafetyGuardConfig,
    SafetyState,
)
from app.follow.planner import FollowTargetPlanner
from app.follow.uwb_input import (
    UwbBearingSource,
    UwbBearingUnit,
    UwbInputConfig,
    UwbInputValidator,
    UwbSampleOrderingError,
)


LOGGER = logging.getLogger(__name__)


class WirelessUwbRuntime(Protocol):
    def status(self) -> dict[str, Any]: ...
    def get_uwb_snapshot(self) -> dict[str, Any]: ...


class WirelessFollowRobotService(Protocol):
    def acquire_exclusive_control(self, owner: str) -> None: ...
    def release_exclusive_control(self, owner: str) -> None: ...
    def refresh_velocity(
        self, vx: float, vy: float, wz: float, source: str = "api"
    ) -> dict: ...
    def safe_stop(self, source: str = "api") -> int: ...


@dataclass(frozen=True)
class WirelessUwbFollowConfig:
    duration_seconds: float = 180.0
    control_rate_hz: float = 5.0
    uwb_stale_timeout_seconds: float = 0.75
    max_vx_mps: float = 0.504
    max_wz_radps: float = 1.10
    normal_max_wz_radps: float = 0.90
    alignment_enter_error_deg: float = 45.0
    alignment_exit_error_deg: float = 30.0
    alignment_turn_speed_radps: float = 1.10
    full_speed_distance_m: float = 2.0
    distance_speed_curve_exponent: float = 1.4
    turn_slowdown_start_error_deg: float = 30.0
    turn_slowdown_min_scale: float = 0.65
    require_video_fresh: bool = True
    require_low_state_fresh: bool = True
    require_uwb_switch: bool = True
    allow_missing_error_state: bool = False
    auto_recover_uwb_stale: bool = False
    recover_max_age_seconds: float = 0.50
    recover_consecutive_samples: int = 3
    recover_min_duration_seconds: float = 0.50
    uwb_fault_escalation_seconds: float = 5.0

    def validate(self) -> None:
        if not 1.0 <= self.duration_seconds <= 180.0:
            raise ValueError("duration_seconds must be within [1, 180]")
        if not 1.0 <= self.control_rate_hz <= 5.0:
            raise ValueError("control_rate_hz must be within [1, 5]")
        if not 0.25 <= self.uwb_stale_timeout_seconds <= 1.0:
            raise ValueError("uwb_stale_timeout_seconds must be within [0.25, 1.0]")
        if not 0.0 < self.max_vx_mps <= 0.504:
            raise ValueError("max_vx_mps must be within (0, 0.504]")
        if not 0.0 < self.max_wz_radps <= 1.10:
            raise ValueError("max_wz_radps must be within (0, 1.10]")
        if not 0.0 < self.normal_max_wz_radps <= self.max_wz_radps:
            raise ValueError(
                "normal_max_wz_radps must be positive and no greater "
                "than max_wz_radps"
            )
        if not 0.0 < self.alignment_exit_error_deg < self.alignment_enter_error_deg < 180.0:
            raise ValueError(
                "alignment angles must satisfy 0 < exit < enter < 180 degrees"
            )
        if not 0.0 < self.alignment_turn_speed_radps <= self.max_wz_radps:
            raise ValueError(
                "alignment_turn_speed_radps must be positive and no greater "
                "than max_wz_radps"
            )
        if not math.isfinite(self.full_speed_distance_m) or self.full_speed_distance_m <= 0.0:
            raise ValueError("full_speed_distance_m must be finite and positive")
        if not 0.5 <= self.distance_speed_curve_exponent <= 3.0:
            raise ValueError(
                "distance_speed_curve_exponent must be within [0.5, 3.0]"
            )
        if not (
            0.0
            < self.turn_slowdown_start_error_deg
            < self.alignment_enter_error_deg
        ):
            raise ValueError(
                "turn_slowdown_start_error_deg must be positive and below "
                "alignment_enter_error_deg"
            )
        if not 0.0 < self.turn_slowdown_min_scale <= 1.0:
            raise ValueError("turn_slowdown_min_scale must be within (0, 1]")
        for name in (
            "require_video_fresh",
            "require_low_state_fresh",
            "require_uwb_switch",
            "allow_missing_error_state",
            "auto_recover_uwb_stale",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")
        if not 0.10 <= self.recover_max_age_seconds < self.uwb_stale_timeout_seconds:
            raise ValueError(
                "recover_max_age_seconds must be below stale timeout and at least 0.10"
            )
        if not 2 <= self.recover_consecutive_samples <= 10:
            raise ValueError("recover_consecutive_samples must be within [2, 10]")
        if not 0.25 <= self.recover_min_duration_seconds <= 2.0:
            raise ValueError(
                "recover_min_duration_seconds must be within [0.25, 2.0]"
            )
        if not 3.0 <= self.uwb_fault_escalation_seconds <= 5.0:
            raise ValueError(
                "uwb_fault_escalation_seconds must be within [3.0, 5.0]"
            )


def load_wireless_uwb_follow_config(path: str | Path) -> WirelessUwbFollowConfig:
    source = Path(path)
    payload = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    section = payload.get("wireless_uwb_follow")
    if not isinstance(section, dict):
        raise ValueError("config must contain wireless_uwb_follow mapping")
    known = set(WirelessUwbFollowConfig.__dataclass_fields__)
    unknown = set(section) - known
    if unknown:
        raise ValueError(f"unknown wireless UWB follow settings: {sorted(unknown)}")
    config = WirelessUwbFollowConfig(**section)
    config.validate()
    return config


@dataclass(frozen=True)
class WirelessUwbFollowResult:
    completed: bool
    reason: str
    duration_seconds: float
    cycles: int
    commands_sent: int
    stop_count: int
    uwb_samples_consumed: int
    missing_error_state_samples: int
    maximum_uwb_age_seconds: float
    uwb_dropout_count: int
    auto_recovery_count: int
    last_dropout_duration_seconds: float | None
    maximum_dropout_duration_seconds: float
    duplicate_samples_dropped: int = 0
    out_of_order_samples_dropped: int = 0
    sport_state_dropout_count: int = 0
    sport_state_auto_recovery_count: int = 0
    uwb_stale_escalation_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class WirelessFollowState(str, Enum):
    FOLLOWING = "FOLLOWING"
    FOLLOW_DEGRADED_VIDEO = "FOLLOW_DEGRADED_VIDEO"
    UWB_WAITING = "UWB_WAITING"
    SPORT_WAITING = "SPORT_WAITING"
    RECOVERING = "RECOVERING"
    STOPPED = "STOPPED"


class WirelessFollowMotionState(str, Enum):
    """Motion sub-state inside a still-active Companion session."""

    FOLLOW_TRACKING = "FOLLOW_TRACKING"
    FOLLOW_HOLD_TOO_CLOSE = "FOLLOW_HOLD_TOO_CLOSE"


class WirelessFollowStopState(str, Enum):
    """Remote StopMove acknowledgement state for the latest-only dispatcher."""

    NONE = "NONE"
    STOP_PENDING = "STOP_PENDING"
    STOP_CONFIRMED = "STOP_CONFIRMED"
    STOP_UNCONFIRMED = "STOP_UNCONFIRMED"


class _LatestVelocityDispatcher:
    """Serialize motion RPCs while retaining only the newest pending Move.

    A temporary StopMove is an ordered barrier: it clears an older pending
    Move, waits behind any Move already in flight, and runs before a newer
    recovery Move. This keeps the Companion control worker non-blocking while
    preserving the safety ordering required by a too-close HOLD.
    """

    def __init__(
        self,
        robot_service: WirelessFollowRobotService,
        *,
        source: str,
    ) -> None:
        self._robot_service = robot_service
        self._source = source
        self._condition = Condition()
        self._pending: tuple[float, float, float] | None = None
        self._stop_pending = False
        self._stop_in_flight = False
        self._stop_state = WirelessFollowStopState.NONE
        self._closed = False
        self._in_flight = False
        self._failure: Exception | None = None
        self._submitted = 0
        self._dispatched = 0
        self._replaced = 0
        self._stop_submitted = 0
        self._stop_dispatched = 0
        self._thread = Thread(
            target=self._run,
            name="go2-wireless-follow-latest-command",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def submit(self, vx: float, vy: float, wz: float) -> bool:
        with self._condition:
            self._raise_if_failed_locked()
            if self._closed:
                raise RuntimeError("latest velocity dispatcher is closed")
            # Never queue a Move behind an unacknowledged StopMove. The control
            # loop will retry its latest velocity on a later cycle after the
            # barrier has been confirmed.
            if self._stop_pending or self._stop_in_flight:
                return False
            if self._pending is not None:
                self._replaced += 1
            self._pending = (float(vx), float(vy), float(wz))
            self._stop_state = WirelessFollowStopState.NONE
            self._submitted += 1
            self._condition.notify()
            return True

    def submit_stop(self) -> bool:
        """Schedule one StopMove barrier without closing the dispatcher."""

        with self._condition:
            self._raise_if_failed_locked()
            if self._closed:
                raise RuntimeError("latest velocity dispatcher is closed")
            # No pre-HOLD Move may execute after this barrier is requested.
            self._pending = None
            if self._stop_pending or self._stop_in_flight:
                return False
            if self._stop_state is WirelessFollowStopState.STOP_CONFIRMED:
                return False
            self._stop_pending = True
            self._stop_state = WirelessFollowStopState.STOP_PENDING
            self._stop_submitted += 1
            self._condition.notify()
            return True

    def discard_pending(self) -> None:
        with self._condition:
            self._pending = None

    def raise_if_failed(self) -> None:
        with self._condition:
            self._raise_if_failed_locked()

    def snapshot(self) -> dict[str, int | bool | str]:
        with self._condition:
            return {
                "in_flight": self._in_flight,
                "pending": self._pending is not None,
                "stop_in_flight": self._stop_in_flight,
                "stop_pending": self._stop_pending,
                "stop_state": self._stop_state.value,
                "submitted": self._submitted,
                "dispatched": self._dispatched,
                "replaced": self._replaced,
                "stop_submitted": self._stop_submitted,
                "stop_dispatched": self._stop_dispatched,
            }

    def close(self) -> None:
        with self._condition:
            self._pending = None
            self._stop_pending = False
            self._closed = True
            self._condition.notify_all()
        self._thread.join(timeout=5.0)
        if self._thread.is_alive():
            LOGGER.error("LATEST_COMMAND_DISPATCHER_STOP_TIMEOUT")

    def _run(self) -> None:
        while True:
            with self._condition:
                while (
                    self._pending is None
                    and not self._stop_pending
                    and not self._closed
                ):
                    self._condition.wait()
                if self._closed:
                    return
                is_stop = self._stop_pending
                if is_stop:
                    self._stop_pending = False
                    self._stop_in_flight = True
                    self._stop_dispatched += 1
                    command = None
                else:
                    command = self._pending
                    self._pending = None
                    self._dispatched += 1
                self._in_flight = True
            try:
                if is_stop:
                    code = self._robot_service.safe_stop(
                        f"{self._source}:stop_barrier"
                    )
                    if code != 0:
                        raise RuntimeError(
                            f"StopMove barrier was not acknowledged, code={code}"
                        )
                else:
                    assert command is not None
                    self._robot_service.refresh_velocity(
                        command[0], command[1], command[2], source=self._source
                    )
            except Exception as exc:  # propagated to the session thread
                with self._condition:
                    if is_stop:
                        self._stop_state = (
                            WirelessFollowStopState.STOP_UNCONFIRMED
                        )
                    self._failure = exc
                    self._pending = None
                    self._condition.notify_all()
                return
            finally:
                with self._condition:
                    self._in_flight = False
                    if is_stop:
                        self._stop_in_flight = False
                        if self._failure is None:
                            self._stop_state = (
                                WirelessFollowStopState.STOP_CONFIRMED
                            )
                    self._condition.notify_all()

    def _raise_if_failed_locked(self) -> None:
        if self._failure is not None:
            raise self._failure


class WirelessUwbFollowSession:
    """UWB-only WebRTC follow session with fail-stop and UWB-only recovery."""

    CONTROL_OWNER = "wireless_uwb_follow"

    def __init__(
        self,
        runtime: WirelessUwbRuntime,
        robot_service: WirelessFollowRobotService,
        profile: FollowProfile,
        config: WirelessUwbFollowConfig,
        *,
        bearing_sign: int,
        bearing_zero_offset_rad: float,
        cancel_event: Event,
        monotonic_clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        config.validate()
        self.runtime = runtime
        self.robot_service = robot_service
        self.profile = profile
        self.config = config
        self.cancel_event = cancel_event
        self._clock = monotonic_clock
        self._sleep = sleep
        self._progress_callback = progress_callback
        if config.full_speed_distance_m <= profile.follow_start_distance:
            raise ValueError(
                "full_speed_distance_m must exceed follow_start_distance"
            )
        if config.full_speed_distance_m > profile.max_distance:
            raise ValueError(
                "full_speed_distance_m must not exceed the safe max distance"
            )
        self._controller_config = replace(
            profile.controller_config(simulation_mode=False),
            max_vx=min(profile.vx_max, config.max_vx_mps),
            max_wz=min(profile.wz_max, config.normal_max_wz_radps),
        )
        self._safety_guard_config = SafetyGuardConfig(
            min_distance=profile.min_distance,
            uwb_timeout_seconds=config.uwb_stale_timeout_seconds,
        )
        self._uwb_input_config = UwbInputConfig(
            bearing_source=UwbBearingSource.ORIENTATION_EST,
            bearing_unit=UwbBearingUnit.RADIANS,
            bearing_sign=bearing_sign,
            bearing_zero_offset_rad=bearing_zero_offset_rad,
            calibration_confirmed=True,
        )
        self._alignment_active = False
        self._reset_control_chain()

    def run(self, *, run_until_stopped: bool = False) -> WirelessUwbFollowResult:
        started = self._clock()
        deadline = None if run_until_stopped else started + self.config.duration_seconds
        period = 1.0 / self.config.control_rate_hz
        cycles = commands_sent = stops = samples = missing_errors = 0
        duplicate_drops = out_of_order_drops = 0
        maximum_age = 0.0
        last_sample_count = -1
        was_moving = False
        state = WirelessFollowState.FOLLOWING
        motion_state = WirelessFollowMotionState.FOLLOW_TRACKING
        resume_motion_log_pending = False
        reason = "operator_stop" if run_until_stopped else "duration_complete"
        completed = False
        dropout_count = auto_recoveries = 0
        sport_dropout_count = sport_auto_recoveries = 0
        uwb_stale_escalations = 0
        dropout_started: float | None = None
        last_dropout_duration: float | None = None
        maximum_dropout_duration = 0.0
        recovery_count = 0
        recovery_first_received: float | None = None
        recovery_last_sample_count = -1
        recovery_kind: str | None = None
        uwb_escalated = False
        dispatcher: _LatestVelocityDispatcher | None = None
        self._preflight()
        self.robot_service.acquire_exclusive_control(self.CONTROL_OWNER)
        try:
            dispatcher = _LatestVelocityDispatcher(
                self.robot_service,
                source=self.CONTROL_OWNER,
            )
            dispatcher.start()
            while deadline is None or self._clock() < deadline:
                cycle_started = self._clock()
                cycles += 1
                dispatcher.raise_if_failed()
                if self.cancel_event.is_set():
                    reason = "operator_stop"
                    break
                refresh_control_heartbeat = getattr(
                    self.robot_service, "refresh_control_heartbeat", None
                )
                if was_moving and callable(refresh_control_heartbeat):
                    # The control worker is alive before transport/status work.
                    # The watchdog still expires if this loop or an RPC stalls
                    # beyond its configured wireless latency budget.
                    refresh_control_heartbeat(self.CONTROL_OWNER)
                status = self.runtime.status()
                snapshot = self.runtime.get_uwb_snapshot()
                received_value = snapshot.get("received_monotonic")
                if received_value is not None:
                    observed_age = cycle_started - float(received_value)
                    if math.isfinite(observed_age) and observed_age >= 0.0:
                        maximum_age = max(maximum_age, observed_age)
                failure = self._health_failure(status, snapshot, cycle_started)
                if (
                    state is WirelessFollowState.FOLLOWING
                    and failure == "video_stale"
                ):
                    state = WirelessFollowState.FOLLOW_DEGRADED_VIDEO
                    self._reset_control_chain()
                    motion_state = WirelessFollowMotionState.FOLLOW_TRACKING
                    resume_motion_log_pending = False
                    dispatcher.discard_pending()
                    if dispatcher.submit_stop():
                        stops += 1
                    was_moving = False
                    self._emit_status(
                        {
                            "event": "VIDEO_STALE_STOP",
                            "state": state.value,
                            "elapsed_s": cycle_started - started,
                            "motion": "STOPPED",
                            "stop_state": dispatcher.snapshot()["stop_state"],
                            "worker_alive": True,
                            "authority": "COMPANION",
                            "control_owner": self.CONTROL_OWNER,
                        }
                    )

                if state is WirelessFollowState.FOLLOW_DEGRADED_VIDEO:
                    if failure == "video_stale":
                        self._sleep_to_rate(cycle_started, period)
                        continue
                    if failure in {"uwb_stale", "uwb_not_ready"}:
                        state = WirelessFollowState.UWB_WAITING
                        recovery_kind = "uwb"
                        dropout_count += 1
                        dropout_started = (
                            float(received_value)
                            if received_value is not None
                            else cycle_started
                        )
                        recovery_count = 0
                        recovery_first_received = None
                        recovery_last_sample_count = -1
                        uwb_escalated = False
                        self._reset_control_chain()
                        self._emit_status(
                            {
                                "event": "VIDEO_RECOVERED_UWB_WAITING",
                                "state": state.value,
                                "motion": "STOPPED",
                                "worker_alive": True,
                                "authority": "COMPANION",
                                "control_owner": self.CONTROL_OWNER,
                            }
                        )
                    elif failure == "sport_state_stale":
                        state = WirelessFollowState.SPORT_WAITING
                        recovery_kind = "sport"
                        sport_dropout_count += 1
                        dropout_started = cycle_started
                        recovery_count = 0
                        recovery_first_received = None
                        recovery_last_sample_count = -1
                        self._reset_control_chain()
                        self._emit_status(
                            {
                                "event": "VIDEO_RECOVERED_SPORT_WAITING",
                                "state": state.value,
                                "motion": "STOPPED",
                                "worker_alive": True,
                                "authority": "COMPANION",
                                "control_owner": self.CONTROL_OWNER,
                            }
                        )
                    elif failure is not None:
                        reason = failure
                        break
                    else:
                        dispatcher.raise_if_failed()
                        if (
                            dispatcher.snapshot()["stop_state"]
                            != WirelessFollowStopState.STOP_CONFIRMED.value
                        ):
                            self._sleep_to_rate(cycle_started, period)
                            continue
                        state = WirelessFollowState.FOLLOWING
                        self._reset_control_chain()
                        last_sample_count = -1
                        was_moving = False
                        motion_state = WirelessFollowMotionState.FOLLOW_TRACKING
                        resume_motion_log_pending = False
                        self._emit_status(
                            {
                                "event": "VIDEO_RECOVERED",
                                "state": state.value,
                                "replanning": True,
                                "motion": "STOPPED_UNTIL_NEW_PLAN",
                                "worker_alive": True,
                                "authority": "COMPANION",
                                "control_owner": self.CONTROL_OWNER,
                            }
                        )
                if (
                    state is WirelessFollowState.FOLLOWING
                    and failure in {"uwb_stale", "uwb_not_ready"}
                ):
                    if not self.config.auto_recover_uwb_stale:
                        reason = failure
                        break
                    state = WirelessFollowState.UWB_WAITING
                    recovery_kind = "uwb"
                    dropout_count += 1
                    dropout_started = (
                        float(received_value)
                        if received_value is not None
                        else cycle_started
                    )
                    recovery_count = 0
                    recovery_first_received = None
                    recovery_last_sample_count = -1
                    uwb_escalated = False
                    self._reset_control_chain()
                    motion_state = WirelessFollowMotionState.FOLLOW_TRACKING
                    resume_motion_log_pending = False
                    dispatcher.discard_pending()
                    if dispatcher.submit_stop():
                        stops += 1
                    was_moving = False
                    self._emit_status(
                        {
                            "event": "UWB_STALE_STOP",
                            "state": state.value,
                            "elapsed_s": cycle_started - started,
                            "uwb_age_ms": (
                                None
                                if received_value is None
                                else (cycle_started - float(received_value)) * 1000.0
                            ),
                            "motion": "STOPPED",
                            "auto_recovery": "WAITING",
                            "stop_state": dispatcher.snapshot()["stop_state"],
                        }
                    )
                elif (
                    state is WirelessFollowState.FOLLOWING
                    and failure == "sport_state_stale"
                ):
                    state = WirelessFollowState.SPORT_WAITING
                    recovery_kind = "sport"
                    sport_dropout_count += 1
                    dropout_started = cycle_started
                    recovery_count = 0
                    recovery_first_received = None
                    recovery_last_sample_count = -1
                    self._reset_control_chain()
                    motion_state = WirelessFollowMotionState.FOLLOW_TRACKING
                    resume_motion_log_pending = False
                    dispatcher.discard_pending()
                    if dispatcher.submit_stop():
                        stops += 1
                    was_moving = False
                    self._emit_status(
                        {
                            "event": "SPORT_STATE_STALE_STOP",
                            "state": state.value,
                            "elapsed_s": cycle_started - started,
                            "motion": "STOPPED",
                            "auto_recovery": "WAITING",
                            "stop_state": dispatcher.snapshot()["stop_state"],
                            "worker_alive": True,
                            "authority": "COMPANION",
                            "control_owner": self.CONTROL_OWNER,
                        }
                    )
                elif state is WirelessFollowState.FOLLOWING and failure is not None:
                    reason = failure
                    break

                if state is not WirelessFollowState.FOLLOWING:
                    # Recoverable signal loss never destroys the Companion
                    # worker. Motion stays behind one acknowledged StopMove
                    # barrier until every required signal is healthy again.
                    if failure == "video_stale":
                        self._sleep_to_rate(cycle_started, period)
                        continue
                    if failure not in {
                        None,
                        "uwb_stale",
                        "uwb_not_ready",
                        "sport_state_stale",
                    }:
                        reason = failure
                        break

                    if (
                        recovery_kind == "uwb"
                        and dropout_started is not None
                        and not uwb_escalated
                        and cycle_started - dropout_started
                        >= self.config.uwb_fault_escalation_seconds
                    ):
                        uwb_escalated = True
                        uwb_stale_escalations += 1
                        self._emit_status(
                            {
                                "event": "UWB_STALE_ESCALATED",
                                "state": WirelessFollowState.UWB_WAITING.value,
                                "dropout_duration_s": (
                                    cycle_started - dropout_started
                                ),
                                "motion": "STOPPED",
                                "session_action": "KEEP_WAITING",
                                "worker_alive": True,
                                "authority": "COMPANION",
                                "control_owner": self.CONTROL_OWNER,
                            }
                        )

                    received = snapshot.get("received_monotonic")
                    if recovery_kind == "sport":
                        counts = status.get("stateSampleCounts") or {}
                        sample_count = sum(int(value) for value in counts.values())
                        sample_received = cycle_started
                    else:
                        sample_count = int(snapshot.get("sample_count") or 0)
                        sample_received = (
                            None if received is None else float(received)
                        )
                    fresh_for_recovery = bool(
                        failure is None
                        and sample_received is not None
                        and (
                            recovery_kind == "sport"
                            or 0.0
                            <= cycle_started - float(sample_received)
                            < self.config.recover_max_age_seconds
                        )
                    )
                    if fresh_for_recovery and sample_count != recovery_last_sample_count:
                        received_float = float(sample_received)
                        if recovery_count == 0:
                            recovery_first_received = cycle_started
                        recovery_count += 1
                        recovery_last_sample_count = sample_count
                        state = WirelessFollowState.RECOVERING
                        recovery_label = (
                            "SPORT_STATE" if recovery_kind == "sport" else "UWB"
                        )
                        self._emit_status(
                            {
                                "event": f"{recovery_label}_RECOVERY_PROGRESS",
                                "state": state.value,
                                "sample": recovery_count,
                                "required": self.config.recover_consecutive_samples,
                                "signal_age_ms": (
                                    cycle_started - received_float
                                ) * 1000.0,
                                "motion": "STOPPED",
                                "stop_state": dispatcher.snapshot()["stop_state"],
                            }
                        )
                    elif not fresh_for_recovery:
                        recovery_count = 0
                        recovery_first_received = None
                        state = (
                            WirelessFollowState.SPORT_WAITING
                            if recovery_kind == "sport"
                            else WirelessFollowState.UWB_WAITING
                        )

                    recovery_duration = (
                        0.0
                        if recovery_first_received is None
                        else cycle_started - recovery_first_received
                    )
                    recovered = bool(
                        fresh_for_recovery
                        and recovery_count >= self.config.recover_consecutive_samples
                        and recovery_duration
                        >= self.config.recover_min_duration_seconds
                        and dispatcher.snapshot()["stop_state"]
                        == WirelessFollowStopState.STOP_CONFIRMED.value
                    )
                    if not recovered:
                        self._sleep_to_rate(cycle_started, period)
                        continue

                    recovered_at = cycle_started
                    last_dropout_duration = (
                        0.0
                        if dropout_started is None
                        else recovered_at - dropout_started
                    )
                    maximum_dropout_duration = max(
                        maximum_dropout_duration, last_dropout_duration
                    )
                    if recovery_kind == "sport":
                        sport_auto_recoveries += 1
                        recovery_event = "SPORT_STATE_RECOVERED"
                    else:
                        auto_recoveries += 1
                        recovery_event = "UWB_RECOVERED"
                    state = WirelessFollowState.FOLLOWING
                    self._reset_control_chain()
                    last_sample_count = -1
                    was_moving = False
                    motion_state = WirelessFollowMotionState.FOLLOW_TRACKING
                    resume_motion_log_pending = False
                    self._emit_status(
                        {
                            "event": recovery_event,
                            "state": state.value,
                            "dropout_duration_s": last_dropout_duration,
                            "replanning": True,
                            "motion": "STOPPED_UNTIL_NEW_PLAN",
                        }
                    )
                    recovery_kind = None

                if failure is not None:
                    # Only the explicitly handled UWB stale transition may
                    # reach this point. Recovery above must establish a fresh
                    # sample before motion is reconsidered.
                    self._sleep_to_rate(cycle_started, period)
                    continue
                received = float(snapshot["received_monotonic"])
                age = cycle_started - received
                sample_count = int(snapshot["sample_count"])
                fields = dict(snapshot["fields"] or {})
                new_measurement = False
                if sample_count != last_sample_count:
                    error_state = fields.get("error_state")
                    if error_state is None:
                        missing_errors += 1
                        error_state = 0
                    try:
                        observation = self.validator.normalize(
                            distance_est=float(fields["distance_est"]),
                            orientation_est=float(fields["orientation_est"]),
                            sample_monotonic=received,
                            enabled_from_app=int(fields["enabled_from_app"]),
                            error_state=int(error_state),
                        )
                    except UwbSampleOrderingError as exc:
                        # WebRTC may deliver a duplicate or slightly reordered
                        # UWB frame. Consume its sample counter, discard only
                        # this frame, and let the existing freshness timeout
                        # stop/recover the session if good samples do not resume.
                        last_sample_count = sample_count
                        if exc.kind == "duplicate":
                            duplicate_drops += 1
                        else:
                            out_of_order_drops += 1
                        LOGGER.warning(
                            "UWB_SAMPLE_DROPPED kind=%s sample_count=%s "
                            "received=%.9f previous=%.9f",
                            exc.kind,
                            sample_count,
                            exc.sample_monotonic,
                            exc.previous_monotonic,
                        )
                        self._emit_status(
                            {
                                "event": "UWB_SAMPLE_DROPPED",
                                "state": state.value,
                                "kind": exc.kind,
                                "sample_count": sample_count,
                                "motion": "UNCHANGED",
                            }
                        )
                        self._sleep_to_rate(cycle_started, period)
                        continue
                    self.planner.process_measurement(
                        observation.distance_metres,
                        observation.bearing_radians,
                        sample_monotonic=observation.sample_monotonic,
                    )
                    last_sample_count = sample_count
                    samples += 1
                    new_measurement = True
                plan = self.planner.check_target_liveness(now_monotonic=cycle_started)
                command = self.controller.calculate_velocity(
                    plan,
                    control_owner=ControlOwner.FOLLOW,
                    measurement_age_seconds=age,
                    sample_monotonic=received,
                )
                bearing_error = math.atan2(
                    math.sin(plan.uwb_yaw - self.profile.target_bearing_radians),
                    math.cos(plan.uwb_yaw - self.profile.target_bearing_radians),
                )
                error_degrees = abs(math.degrees(bearing_error))
                controller_allows_motion = command.safety_state in {
                    SafetyState.SAFE,
                    SafetyState.LIMITED_ABNORMAL_YAW,
                }
                hold_was_active = (
                    motion_state
                    is WirelessFollowMotionState.FOLLOW_HOLD_TOO_CLOSE
                )
                hold_entered = False
                if hold_was_active:
                    if (
                        plan.uwb_distance is not None
                        and plan.uwb_distance
                        >= self.profile.follow_start_distance - 1e-9
                        and controller_allows_motion
                    ):
                        motion_state = WirelessFollowMotionState.FOLLOW_TRACKING
                        resume_motion_log_pending = True
                        LOGGER.info(
                            "COMPANION_HOLD_EXIT reason=distance_recovered "
                            "distance=%.3f",
                            plan.uwb_distance,
                        )
                        self._emit_status(
                            {
                                "event": "COMPANION_HOLD_EXIT",
                                "reason": "distance_recovered",
                                "state": state.value,
                                "motion_state": motion_state.value,
                                "distance_m": plan.uwb_distance,
                                "worker_alive": True,
                                "authority": "COMPANION",
                                "control_owner": self.CONTROL_OWNER,
                            }
                        )
                elif (
                    plan.uwb_distance is not None
                    and plan.uwb_distance
                    <= self.profile.follow_stop_distance + 1e-9
                ):
                    motion_state = (
                        WirelessFollowMotionState.FOLLOW_HOLD_TOO_CLOSE
                    )
                    hold_entered = True
                    resume_motion_log_pending = False
                    self._alignment_active = False
                    if dispatcher.submit_stop():
                        stops += 1
                    LOGGER.info(
                        "COMPANION_HOLD_ENTER reason=too_close distance=%.3f",
                        plan.uwb_distance,
                    )
                    self._emit_status(
                        {
                            "event": "COMPANION_HOLD_ENTER",
                            "reason": "too_close",
                            "state": state.value,
                            "motion_state": motion_state.value,
                            "distance_m": plan.uwb_distance,
                            "worker_alive": True,
                            "authority": "COMPANION",
                            "control_owner": self.CONTROL_OWNER,
                        }
                    )
                alignment_was_active = self._alignment_active
                if (
                    motion_state
                    is WirelessFollowMotionState.FOLLOW_HOLD_TOO_CLOSE
                ):
                    self._alignment_active = False
                    vx = 0.0
                    wz = 0.0
                elif not controller_allows_motion:
                    self._alignment_active = False
                    vx = 0.0
                    wz = 0.0
                else:
                    if self._alignment_active:
                        if (
                            error_degrees
                            <= self.config.alignment_exit_error_deg + 1e-9
                        ):
                            self._alignment_active = False
                    elif (
                        error_degrees
                        > self.config.alignment_enter_error_deg + 1e-9
                    ):
                        self._alignment_active = True
                    if self._alignment_active:
                        # Alignment is an explicit state: never combine it
                        # with forward motion. A fresh UWB sample must enter
                        # the exit band before normal follow is reconsidered.
                        vx = 0.0
                        wz = math.copysign(
                            self.config.alignment_turn_speed_radps,
                            bearing_error,
                        )
                    else:
                        vx = self._shape_forward_speed(
                            command.vx,
                            distance_m=plan.uwb_distance,
                            bearing_error_deg=error_degrees,
                        )
                        wz = command.wz
                # One final hard bound applies to every behavior mode,
                # including ALIGNMENT. No specialized mode may bypass the
                # configured wireless motion envelope.
                vx = max(0.0, min(vx, self.config.max_vx_mps))
                wz = max(
                    -self.config.max_wz_radps,
                    min(wz, self.config.max_wz_radps),
                )
                if self.cancel_event.is_set():
                    reason = "operator_stop"
                    break
                moving = abs(vx) > 1e-6 or abs(wz) > 1e-6
                if moving:
                    # Do not enqueue repeat calculations from the same UWB
                    # timestamp. A newer sample replaces the one pending slot
                    # while the preceding Move RPC is awaiting its ACK.
                    if (
                        new_measurement
                        or not was_moving
                        or alignment_was_active != self._alignment_active
                    ):
                        dispatcher.submit(vx, 0.0, wz)
                        if resume_motion_log_pending:
                            resume_motion_log_pending = False
                            LOGGER.info(
                                "COMPANION_MOTION_RESUMED vx=%.3f wz=%.3f",
                                vx,
                                wz,
                            )
                            self._emit_status(
                                {
                                    "event": "COMPANION_MOTION_RESUMED",
                                    "reason": "distance_recovered",
                                    "state": state.value,
                                    "motion_state": motion_state.value,
                                    "distance_m": plan.uwb_distance,
                                    "vx": vx,
                                    "wz": wz,
                                    "worker_alive": True,
                                    "authority": "COMPANION",
                                    "control_owner": self.CONTROL_OWNER,
                                }
                            )
                elif was_moving and not hold_entered:
                    dispatcher.discard_pending()
                    if dispatcher.submit_stop():
                        stops += 1
                was_moving = moving
                if self._progress_callback is not None:
                    self._progress_callback(
                        {
                            "cycle": cycles,
                            "elapsed_s": cycle_started - started,
                            "remaining_s": (
                                None
                                if deadline is None
                                else max(0.0, deadline - cycle_started)
                            ),
                            "state": state.value,
                            "motion_state": motion_state.value,
                            "worker_alive": True,
                            "authority": "COMPANION",
                            "control_owner": self.CONTROL_OWNER,
                            "distance_m": plan.uwb_distance,
                            "bearing_deg": (
                                None
                                if plan.uwb_yaw is None
                                else math.degrees(plan.uwb_yaw)
                            ),
                            "uwb_age_ms": age * 1000.0,
                            "vx": vx,
                            "wz": wz,
                            "safety_state": command.safety_state.value,
                            "alignment_mode": self._alignment_active,
                            "bearing_error_deg": math.degrees(bearing_error),
                            "command_dispatch": dispatcher.snapshot(),
                        }
                    )
                self._sleep_to_rate(cycle_started, period)
            else:
                completed = True
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
        finally:
            final_stop_required = True
            if dispatcher is not None:
                dispatcher.discard_pending()
                dispatcher.close()
                dispatch_snapshot = dispatcher.snapshot()
                commands_sent = int(dispatch_snapshot["dispatched"])
                final_stop_required = dispatch_snapshot["stop_state"] not in {
                    WirelessFollowStopState.STOP_CONFIRMED.value,
                    WirelessFollowStopState.STOP_UNCONFIRMED.value,
                }
            if final_stop_required:
                self.robot_service.safe_stop(f"{self.CONTROL_OWNER}:{reason}")
                stops += 1
            self.robot_service.release_exclusive_control(self.CONTROL_OWNER)
        return WirelessUwbFollowResult(
            completed=completed,
            reason=reason,
            duration_seconds=self._clock() - started,
            cycles=cycles,
            commands_sent=commands_sent,
            stop_count=stops,
            uwb_samples_consumed=samples,
            missing_error_state_samples=missing_errors,
            maximum_uwb_age_seconds=maximum_age,
            uwb_dropout_count=dropout_count,
            auto_recovery_count=auto_recoveries,
            last_dropout_duration_seconds=last_dropout_duration,
            maximum_dropout_duration_seconds=maximum_dropout_duration,
            duplicate_samples_dropped=duplicate_drops,
            out_of_order_samples_dropped=out_of_order_drops,
            sport_state_dropout_count=sport_dropout_count,
            sport_state_auto_recovery_count=sport_auto_recoveries,
            uwb_stale_escalation_count=uwb_stale_escalations,
        )

    def preflight(self) -> None:
        """Validate the live WebRTC/UWB inputs without acquiring motion control."""

        self._preflight()

    def _reset_control_chain(self) -> None:
        """Discard all pre-stop controller history before a new plan."""

        self._alignment_active = False
        self.controller = FollowController(
            self._controller_config,
            safety_guard=SafetyGuard(self._safety_guard_config),
        )
        self.planner = FollowTargetPlanner(
            self.profile.follow_offset(),
            lost_timeout_seconds=self.config.uwb_stale_timeout_seconds,
            monotonic_clock=self._clock,
        )
        self.validator = UwbInputValidator(self._uwb_input_config)

    def _shape_forward_speed(
        self,
        candidate_vx: float,
        *,
        distance_m: float | None,
        bearing_error_deg: float,
    ) -> float:
        """Apply the V2.1 distance curve and medium-turn slowdown."""

        if (
            candidate_vx <= 0.0
            or distance_m is None
            or not math.isfinite(distance_m)
        ):
            return 0.0
        start_distance = self.profile.follow_start_distance
        progress = max(
            0.0,
            min(
                1.0,
                (distance_m - start_distance)
                / (self.config.full_speed_distance_m - start_distance),
            ),
        )
        curve_vx = self.profile.walk_min + (
            (self.config.max_vx_mps - self.profile.walk_min)
            * (progress ** self.config.distance_speed_curve_exponent)
        )
        shaped_vx = max(candidate_vx, curve_vx)
        if bearing_error_deg > self.config.turn_slowdown_start_error_deg:
            turn_progress = min(
                1.0,
                (
                    bearing_error_deg
                    - self.config.turn_slowdown_start_error_deg
                )
                / (
                    self.config.alignment_enter_error_deg
                    - self.config.turn_slowdown_start_error_deg
                ),
            )
            turn_scale = 1.0 - (
                turn_progress * (1.0 - self.config.turn_slowdown_min_scale)
            )
            shaped_vx *= turn_scale
        return max(0.0, min(shaped_vx, self.config.max_vx_mps))

    def _sleep_to_rate(self, cycle_started: float, period: float) -> None:
        delay = period - (self._clock() - cycle_started)
        if delay > 0.0:
            self._sleep(delay)

    def _emit_status(self, record: dict[str, object]) -> None:
        if self._progress_callback is not None:
            self._progress_callback(record)

    def _preflight(self) -> None:
        now = self._clock()
        status = self.runtime.status()
        snapshot = self.runtime.get_uwb_snapshot()
        failure = self._health_failure(status, snapshot, now)
        if failure is not None:
            raise RuntimeError(f"wireless follow preflight failed: {failure}")

    def _health_failure(
        self,
        status: dict[str, Any],
        snapshot: dict[str, Any],
        now: float,
    ) -> str | None:
        if not status.get("connected") or status.get("connectionCount") != 1:
            return "webrtc_connection_not_single"
        if not status.get("sportStateReady"):
            return "sport_state_stale"
        if self.config.require_video_fresh and not status.get("videoReady"):
            return "video_stale"
        if self.config.require_low_state_fresh and not status.get("lowState", {}).get(
            "fresh"
        ):
            return "low_state_stale"
        if self.config.require_uwb_switch and status.get("multipleState", {}).get(
            "uwbSwitch"
        ) is not True:
            return "uwb_switch_not_enabled"
        fields = snapshot.get("fields")
        received = snapshot.get("received_monotonic")
        if not isinstance(fields, dict) or received is None:
            return "uwb_not_ready"
        age = now - float(received)
        if not math.isfinite(age) or age < 0.0 or age >= self.config.uwb_stale_timeout_seconds:
            return "uwb_stale"
        try:
            distance = float(fields["distance_est"])
            orientation = float(fields["orientation_est"])
            enabled = int(fields["enabled_from_app"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return "uwb_schema_invalid"
        if not math.isfinite(distance) or distance < 0.0 or not math.isfinite(orientation):
            return "uwb_measurement_invalid"
        if enabled != 1:
            return "uwb_not_enabled"
        error_state = fields.get("error_state")
        if error_state is None:
            source_keys = {str(key) for key in snapshot.get("source_keys") or ()}
            if (
                not self.config.allow_missing_error_state
                or "error_state" in source_keys
                or "errorState" in source_keys
            ):
                return "uwb_error_state_missing"
        else:
            try:
                if int(error_state) != 0:
                    return "uwb_error_state_nonzero"
            except (TypeError, ValueError, OverflowError):
                return "uwb_error_state_invalid"
        return None
