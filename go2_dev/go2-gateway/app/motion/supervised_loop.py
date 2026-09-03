from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Iterable, Mapping, Sequence

from app.companion.models import (
    CompanionMotionMode,
    CompanionSnapshot,
    CompanionState,
)
from app.core.control_owner import ControlOwner
from app.follow.controller import FollowController, VelocityCommand
from app.follow.planner import FollowPlan, FollowTargetPlanner
from app.follow.uwb_input import UwbInputValidator, UwbObservation
from app.motion.arbiter import ArbiterDecision, MotionArbiter
from app.motion.contracts import ExternalRiskEvent, ExternalRiskEventType
from app.motion.lidar_safety import LidarSafetyDecision, LidarSafetyGuard
from app.motion.real_follow_executor import (
    RealFollowExecutionResult,
    RealFollowExecutor,
)

if TYPE_CHECKING:
    from app.companion.supervisor import CompanionSupervisor


@dataclass(frozen=True)
class RawUwbSample:
    distance_est: float
    orientation_est: float
    enabled_from_app: int
    error_state: int
    sample_monotonic: float


@dataclass(frozen=True)
class SupervisedCycleResult:
    cycle: int
    now_monotonic: float
    uwb_age_seconds: float | None
    uwb_error: str | None
    lidar_error: str | None
    follow_plan: FollowPlan | None
    candidate: VelocityCommand | None
    companion: CompanionSnapshot | None
    lidar: LidarSafetyDecision | None
    decision: ArbiterDecision
    execution: RealFollowExecutionResult


class SupervisedMotionLoop:
    """One fail-closed Phase 7.2-C UWB/LiDAR/risk control cycle.

    DDS callbacks only ingest read-only observations. ``step`` re-evaluates
    every source, arbitrates once, and delegates at most one short command to
    ``RealFollowExecutor``. Any stop clears the executor's resume grant.
    """

    def __init__(
        self,
        *,
        uwb_validator: UwbInputValidator,
        planner: FollowTargetPlanner,
        controller: FollowController,
        lidar_guard: LidarSafetyGuard,
        arbiter: MotionArbiter,
        executor: RealFollowExecutor,
        companion_supervisor: CompanionSupervisor | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.uwb_validator = uwb_validator
        self.planner = planner
        self.controller = controller
        self.lidar_guard = lidar_guard
        self.arbiter = arbiter
        self.executor = executor
        self.companion_supervisor = companion_supervisor
        self._monotonic_clock = monotonic_clock
        self._lock = threading.RLock()
        self._cycle = 0
        self._observation: UwbObservation | None = None
        self._raw_orientation_est: float | None = None
        self._plan: FollowPlan | None = None
        self._uwb_error: str | None = "uwb_not_ready"
        self._lidar_error: str | None = "lidar_not_ready"

    def ingest_uwb(self, sample: RawUwbSample) -> UwbObservation:
        with self._lock:
            try:
                observation = self.uwb_validator.normalize(
                    distance_est=sample.distance_est,
                    orientation_est=sample.orientation_est,
                    sample_monotonic=sample.sample_monotonic,
                    enabled_from_app=sample.enabled_from_app,
                    error_state=sample.error_state,
                )
                plan = self.planner.process_measurement(
                    observation.distance_metres,
                    observation.bearing_radians,
                    sample_monotonic=observation.sample_monotonic,
                )
            except Exception as exc:
                self._uwb_error = f"{type(exc).__name__}: {exc}"
                self.controller.reset_dynamic_state("invalid_uwb_input")
                raise
            self._observation = observation
            self._raw_orientation_est = float(sample.orientation_est)
            self._plan = plan
            self._uwb_error = None
            if self.companion_supervisor is not None:
                self.companion_supervisor.ingest_uwb(observation, plan)
            return observation

    def report_uwb_error(self, error: Exception | str) -> None:
        with self._lock:
            self._uwb_error = (
                error if isinstance(error, str) else f"{type(error).__name__}: {error}"
            )
            self.controller.reset_dynamic_state("uwb_input_error")

    def ingest_lidar(
        self,
        points: Iterable[Sequence[float]],
        *,
        frame_id: str,
        sample_monotonic: float,
    ) -> LidarSafetyDecision:
        with self._lock:
            try:
                decision = self.lidar_guard.update(
                    points,
                    frame_id=frame_id,
                    sample_monotonic=sample_monotonic,
                )
            except Exception as exc:
                self._lidar_error = f"{type(exc).__name__}: {exc}"
                raise
            self._lidar_error = None
            return decision

    def report_lidar_error(self, error: Exception | str) -> None:
        with self._lock:
            self._lidar_error = (
                error if isinstance(error, str) else f"{type(error).__name__}: {error}"
            )

    def ingest_risk_event(
        self,
        event: ExternalRiskEvent | Mapping[str, object],
        *,
        received_monotonic: float | None = None,
    ) -> bool:
        with self._lock:
            parsed = (
                event
                if isinstance(event, ExternalRiskEvent)
                else ExternalRiskEvent.from_payload(event)
            )
            accepted = self.arbiter.ingest_risk_event(
                parsed, received_monotonic=received_monotonic
            )
            if accepted and self.companion_supervisor is not None:
                self.companion_supervisor.ingest_risk_event(
                    parsed, received_monotonic=received_monotonic
                )
                if parsed.event_type is ExternalRiskEventType.RECOVERY_CONFIRMED:
                    incident_id = parsed.incident_id
                    if incident_id is None:
                        raise RuntimeError(
                            "validated RECOVERY_CONFIRMED lacks incident_id"
                        )
                    # Recovery confirmation clears the risk incident but only
                    # advances behavior to WAIT_RESUME. The supervisor still
                    # rejects motion until an explicit human resume.
                    self.arbiter.clear_fall(incident_id)
                    if not self.companion_supervisor.mark_recovery_stable(
                        now_monotonic=received_monotonic
                    ):
                        raise RuntimeError("companion recovery transition failed")
            return accepted

    def set_manual_takeover(self, active: bool) -> None:
        with self._lock:
            self.arbiter.set_manual_takeover(active)

    def set_emergency(self, active: bool, *, reason: str = "external_emergency") -> None:
        with self._lock:
            self.arbiter.set_emergency(active, reason=reason)

    def arm_for_supervised_test(self) -> None:
        self.executor.arm_for_supervised_test()

    def start_companion(self) -> None:
        with self._lock:
            if self.companion_supervisor is None:
                raise RuntimeError("companion supervisor is not configured")
            if not self.companion_supervisor.start():
                raise RuntimeError(
                    f"cannot start companion from {self.companion_supervisor.state.value}"
                )

    def authorize_resume(self) -> None:
        with self._lock:
            supervisor = self.companion_supervisor
            if supervisor is not None and supervisor.state not in {
                CompanionState.FOLLOWING,
                CompanionState.WAIT_RESUME,
            }:
                raise RuntimeError(
                    f"resume is not allowed from {supervisor.state.value}"
                )
            self.controller.reset_dynamic_state("explicit_human_resume")
            self.executor.authorize_resume()
            if (
                supervisor is not None
                and supervisor.state is CompanionState.WAIT_RESUME
                and not supervisor.resume()
            ):
                raise RuntimeError("companion resume transition failed")

    def input_status(self, *, now_monotonic: float | None = None) -> dict[str, object]:
        """Return a side-effect-free lifecycle preflight/status snapshot."""

        with self._lock:
            now = self._monotonic_clock() if now_monotonic is None else now_monotonic
            if not math.isfinite(now):
                raise ValueError("now_monotonic must be finite")
            observation = self._observation
            uwb_age = None if observation is None else now - observation.sample_monotonic
            uwb_valid = (
                self._uwb_error is None
                and observation is not None
                and uwb_age is not None
                and math.isfinite(uwb_age)
                and 0.0 <= uwb_age < self.arbiter.config.uwb_timeout_seconds
            )
            lidar = self.lidar_guard.snapshot(now_monotonic=now)
            risk = self.arbiter.status(now_monotonic=now)
            return {
                "uwb": {
                    "valid": uwb_valid,
                    "age_seconds": uwb_age,
                    "error": self._uwb_error,
                    "distance_metres": (
                        None if observation is None else observation.distance_metres
                    ),
                    "bearing_radians": (
                        None if observation is None else observation.bearing_radians
                    ),
                    "orientation_est_radians": self._raw_orientation_est,
                    "enabled_from_app": (
                        None if observation is None else observation.enabled_from_app
                    ),
                    "error_state": (
                        None if observation is None else observation.error_state
                    ),
                },
                "lidar": {
                    "valid": (
                        lidar.sample_age_seconds is not None
                        and lidar.reason not in {
                            "lidar_not_ready",
                            "lidar_stale",
                            "non_monotonic_lidar_time",
                            "untrusted_lidar_frame",
                            "malformed_cloud",
                            "insufficient_cloud_points",
                            "clearance_confirmation_pending",
                        }
                    ),
                    "level": lidar.level.value,
                    "reason": lidar.reason,
                    "age_seconds": lidar.sample_age_seconds,
                    "nearest_distance": lidar.nearest_distance,
                },
                "risk": risk,
            }

    def report_fall_suspected(self) -> None:
        with self._lock:
            if self.companion_supervisor is None:
                raise RuntimeError("companion supervisor is not configured")
            if not self.companion_supervisor.report_fall_suspected():
                raise RuntimeError(
                    "fall suspicion is not valid from "
                    f"{self.companion_supervisor.state.value}"
                )

    def acknowledge_fall(self, incident_id: str) -> None:
        with self._lock:
            if self.companion_supervisor is None:
                raise RuntimeError("companion supervisor is not configured")
            if self.companion_supervisor.state is not CompanionState.EMERGENCY_STOP:
                raise RuntimeError("companion is not in EMERGENCY_STOP")
            self.arbiter.acknowledge_fall(incident_id)
            if not self.companion_supervisor.acknowledge_emergency():
                raise RuntimeError("companion emergency acknowledgement failed")

    def mark_recovery_stable(self, incident_id: str) -> None:
        with self._lock:
            if self.companion_supervisor is None:
                raise RuntimeError("companion supervisor is not configured")
            if self.companion_supervisor.state is not CompanionState.RECOVERING:
                raise RuntimeError("companion is not in RECOVERING")
            self.arbiter.clear_fall(incident_id)
            if not self.companion_supervisor.mark_recovery_stable():
                raise RuntimeError("companion recovery transition failed")

    def step(self, *, now_monotonic: float | None = None) -> SupervisedCycleResult:
        with self._lock:
            # Read the clock only after acquiring the same lock used by DDS
            # callbacks. A callback can otherwise stamp a newer sample between
            # an early clock read and this critical section, producing a false
            # negative age and an unnecessary fail-closed stop.
            now = self._monotonic_clock() if now_monotonic is None else now_monotonic
            if not math.isfinite(now):
                raise ValueError("now_monotonic must be finite")
            self._cycle += 1
            uwb_age: float | None = None
            plan: FollowPlan | None = None
            candidate: VelocityCommand | None = None
            if self._uwb_error is None and self._observation is not None:
                uwb_age = now - self._observation.sample_monotonic
                if uwb_age >= 0.0:
                    plan = self.planner.check_target_liveness(now_monotonic=now)

            lidar: LidarSafetyDecision | None = None
            if self._lidar_error is None:
                lidar = self.lidar_guard.evaluate(now_monotonic=now)

            companion: CompanionSnapshot | None = None
            supervisor_snapshot: CompanionSnapshot | None = None
            if self.companion_supervisor is not None:
                self.companion_supervisor.evaluate_sources(
                    uwb_age_seconds=uwb_age,
                    lidar=lidar,
                    now_monotonic=now,
                )
                supervisor_snapshot = self.companion_supervisor.snapshot()
                if supervisor_snapshot.motion_mode is CompanionMotionMode.STOP:
                    self.controller.reset_dynamic_state(
                        f"companion_{supervisor_snapshot.state.value.lower()}"
                    )

            should_calculate = plan is not None and (
                supervisor_snapshot is None
                or supervisor_snapshot.motion_mode is not CompanionMotionMode.STOP
            )
            if should_calculate:
                assert plan is not None
                assert self._observation is not None
                candidate = self.controller.calculate_velocity(
                    plan,
                    control_owner=ControlOwner.FOLLOW,
                    measurement_age_seconds=uwb_age,
                    sample_monotonic=self._observation.sample_monotonic,
                )

            if self.companion_supervisor is not None:
                directive = self.companion_supervisor.govern(candidate)
                companion = directive.snapshot
                candidate = directive.command

            decision = self.arbiter.decide(
                follow_command=candidate,
                uwb_age_seconds=uwb_age,
                lidar=lidar,
                now_monotonic=now,
            )
            execution = self.executor.execute(decision)
            return SupervisedCycleResult(
                cycle=self._cycle,
                now_monotonic=now,
                uwb_age_seconds=uwb_age,
                uwb_error=self._uwb_error,
                lidar_error=self._lidar_error,
                follow_plan=plan,
                candidate=candidate,
                companion=companion,
                lidar=lidar,
                decision=decision,
                execution=execution,
            )

    def shutdown(self) -> None:
        self.executor.disarm()
