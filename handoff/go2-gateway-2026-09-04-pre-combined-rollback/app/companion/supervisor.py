from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

from app.companion.config import CompanionConfig, FollowProfile
from app.companion.events import CompanionEvent, CompanionEventType
from app.companion.models import (
    CompanionDirective,
    CompanionMotionMode,
    CompanionSnapshot,
    CompanionState,
)
from app.companion.state_machine import CompanionStateMachine
from app.follow.controller import VelocityCommand
from app.follow.planner import FollowPlan
from app.follow.uwb_input import UwbObservation
from app.motion.contracts import ExternalRiskEvent, ExternalRiskEventType
from app.motion.lidar_safety import LidarSafetyDecision, LidarSafetyLevel


@dataclass(frozen=True)
class _TargetSample:
    distance: float
    bearing: float
    monotonic_time: float


class CompanionSupervisor:
    """Behavior supervisor above planning and below the motion arbiter.

    It never dispatches robot motion. It can pass, constrain, zero, or reject a
    controller candidate; LiDAR, risk arbitration, executor arming, and the
    executor's human-resume latch remain independent downstream gates.
    """

    def __init__(
        self,
        config: CompanionConfig | None = None,
        profile: FollowProfile | None = None,
        *,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config or CompanionConfig()
        self.profile = profile or FollowProfile()
        self._clock = monotonic_clock
        self.state_machine = CompanionStateMachine(monotonic_clock=monotonic_clock)
        self._target_samples: deque[_TargetSample] = deque()
        self._last_observation: UwbObservation | None = None
        self._last_plan: FollowPlan | None = None
        self._target_stationary = False
        self._active_incident_id: str | None = None

    @property
    def state(self) -> CompanionState:
        return self.state_machine.state

    def start(self, *, now_monotonic: float | None = None) -> bool:
        return self._dispatch(CompanionEventType.START, "companion_started", now_monotonic)

    def stop(self, *, now_monotonic: float | None = None) -> bool:
        return self._dispatch(CompanionEventType.STOP, "companion_stopped", now_monotonic)

    def ingest_uwb(self, observation: UwbObservation, plan: FollowPlan) -> None:
        self._last_observation = observation
        self._last_plan = plan
        sample = _TargetSample(
            distance=observation.distance_metres,
            bearing=observation.bearing_radians,
            monotonic_time=observation.sample_monotonic,
        )
        self._target_samples.append(sample)
        cutoff = sample.monotonic_time - self.profile.person_stop_hold_seconds
        while self._target_samples and self._target_samples[0].monotonic_time < cutoff:
            self._target_samples.popleft()

        was_stationary = self._target_stationary
        stationary_detected = self._is_stationary()
        moving = self._is_clearly_moving(
            include_bearing=self.state is not CompanionState.VIEW_ADJUST
        )
        self._target_stationary = (
            False if moving else stationary_detected or was_stationary
        )

        if self.state in {CompanionState.TARGET_LOST, CompanionState.SAFE_STOP}:
            self._dispatch(
                CompanionEventType.TARGET_REACQUIRED,
                "fresh_uwb_target_reacquired",
                sample.monotonic_time,
            )

        if self.state is CompanionState.FOLLOWING and self._target_stationary:
            self._dispatch(
                CompanionEventType.PERSON_STATIONARY,
                "uwb_target_stationary",
                sample.monotonic_time,
            )
            return

        if self.state in {
            CompanionState.PERSON_STOPPED,
            CompanionState.VIEW_ADJUST,
            CompanionState.HOLD,
        } and moving:
            self._dispatch(
                CompanionEventType.PERSON_MOVING,
                "uwb_target_motion_resumed",
                sample.monotonic_time,
            )
            return

        if self.state is CompanionState.PERSON_STOPPED:
            view = self.config.view_adjust
            event_type = (
                CompanionEventType.VIEW_REQUIRED
                if view.enabled
                and abs(self._view_bearing_error()) > view.deadband_radians
                else CompanionEventType.VIEW_ALIGNED
            )
            self._dispatch(event_type, "stationary_target_view_check", sample.monotonic_time)
        elif self.state is CompanionState.VIEW_ADJUST and (
            not self.config.view_adjust.enabled
            or abs(self._view_bearing_error())
            <= self.config.view_adjust.deadband_radians
        ):
            self._dispatch(
                CompanionEventType.VIEW_ALIGNED,
                "observation_view_aligned",
                sample.monotonic_time,
            )
        elif self.state is CompanionState.HOLD and (
            self.config.view_adjust.enabled
            and abs(self._view_bearing_error())
            > self.config.view_adjust.deadband_radians
        ):
            self._dispatch(
                CompanionEventType.VIEW_REQUIRED,
                "observation_view_drifted",
                sample.monotonic_time,
            )

    def ingest_risk_event(
        self,
        event: ExternalRiskEvent,
        *,
        received_monotonic: float | None = None,
    ) -> None:
        now = self._now(received_monotonic)
        if event.event_type is ExternalRiskEventType.FALL_SUSPECTED:
            self._active_incident_id = event.incident_id
            self._dispatch(
                CompanionEventType.FALL_SUSPECTED,
                "external_fall_suspected",
                now,
            )
            return

        if event.event_type is ExternalRiskEventType.FALL_CONFIRMED:
            self._active_incident_id = event.incident_id
            self._dispatch(
                CompanionEventType.FALL_CONFIRMED,
                "external_fall_confirmed",
                now,
            )
            return

        if event.event_type is ExternalRiskEventType.RECOVERY_CONFIRMED:
            if self.state is CompanionState.MONITORING:
                self._dispatch(
                    CompanionEventType.RECOVERY_DETECTED,
                    "external_recovery_confirmed",
                    now,
                )
            return

        if self.state is CompanionState.FALL_SUSPECTED:
            self._dispatch(
                CompanionEventType.FALL_DISMISSED,
                "external_non_fall",
                now,
            )

    def report_fall_suspected(self, *, now_monotonic: float | None = None) -> bool:
        return self._dispatch(
            CompanionEventType.FALL_SUSPECTED,
            "fall_suspected",
            now_monotonic,
        )

    def acknowledge_emergency(self, *, now_monotonic: float | None = None) -> bool:
        return self._dispatch(
            CompanionEventType.EMERGENCY_ACKNOWLEDGED,
            "fall_emergency_acknowledged",
            now_monotonic,
        )

    def mark_recovery_stable(self, *, now_monotonic: float | None = None) -> bool:
        return self._dispatch(
            CompanionEventType.RECOVERY_STABLE,
            "recovery_stable_waiting_for_resume",
            now_monotonic,
        )

    def resume(self, *, now_monotonic: float | None = None) -> bool:
        changed = self._dispatch(
            CompanionEventType.RESUME,
            "explicit_human_resume",
            now_monotonic,
        )
        if changed:
            self._active_incident_id = None
            self._target_samples.clear()
            self._target_stationary = False
        return changed

    def evaluate_sources(
        self,
        *,
        uwb_age_seconds: float | None,
        lidar: LidarSafetyDecision | None,
        now_monotonic: float,
    ) -> None:
        if self.state is CompanionState.TARGET_LOST:
            self._dispatch(
                CompanionEventType.FAILSAFE_COMMITTED,
                "target_loss_safe_stop_committed",
                now_monotonic,
            )
        elif (
            self.state not in _FALL_PATH_STATES
            and (
                uwb_age_seconds is None
                or not math.isfinite(uwb_age_seconds)
                or uwb_age_seconds < 0.0
                or uwb_age_seconds >= self.profile.uwb_timeout_seconds
            )
        ):
            self._dispatch(
                CompanionEventType.TARGET_LOST,
                "uwb_target_lost",
                now_monotonic,
            )

        if self.state in _FALL_PATH_STATES:
            return
        obstacle = (
            lidar is None
            or lidar.stop_required
            or lidar.level is LidarSafetyLevel.STOP
        )
        if obstacle and self.state not in {
            CompanionState.TARGET_LOST,
            CompanionState.SAFE_STOP,
            CompanionState.OBSTACLE_STOP,
            CompanionState.IDLE,
        }:
            self._dispatch(
                CompanionEventType.OBSTACLE_DETECTED,
                "lidar_stop",
                now_monotonic,
            )
        elif not obstacle and self.state is CompanionState.OBSTACLE_STOP:
            self._dispatch(
                CompanionEventType.OBSTACLE_CLEARED,
                "lidar_clear",
                now_monotonic,
            )

    def govern(self, candidate: VelocityCommand | None) -> CompanionDirective:
        snapshot = self.snapshot()
        if candidate is None or snapshot.motion_mode is CompanionMotionMode.STOP:
            return CompanionDirective(snapshot=snapshot, command=None)
        if snapshot.motion_mode is CompanionMotionMode.FOLLOW:
            return CompanionDirective(snapshot=snapshot, command=candidate)
        if snapshot.motion_mode is CompanionMotionMode.HOLD:
            return CompanionDirective(snapshot=snapshot, command=_zero_command(candidate))

        # VIEW_ADJUST has its own observation target. It must not reuse the
        # normal right-rear follow bearing because those two desired angles
        # are intentionally allowed to differ in the field profile.
        view = self.config.view_adjust
        error = self._view_bearing_error()
        raw_wz = 0.0 if abs(error) <= view.deadband_radians else view.gain * error
        wz = max(
            -view.max_wz,
            min(raw_wz, view.max_wz),
        )
        command = VelocityCommand(
            vx=0.0,
            vy=0.0,
            wz=wz,
            safety_state=candidate.safety_state,
            simulation_mode=candidate.simulation_mode,
        )
        return CompanionDirective(snapshot=snapshot, command=command)

    def snapshot(self) -> CompanionSnapshot:
        return self.state_machine.snapshot(
            target_stationary=self._target_stationary,
            target_available=self._last_observation is not None,
            active_incident_id=self._active_incident_id,
        )

    def _is_stationary(self) -> bool:
        if len(self._target_samples) < 2:
            return False
        samples = tuple(self._target_samples)
        if (
            samples[-1].monotonic_time - samples[0].monotonic_time
            < self.profile.person_stop_hold_seconds
        ):
            return False
        distance_span = max(item.distance for item in samples) - min(
            item.distance for item in samples
        )
        bearing_span = _circular_span(item.bearing for item in samples)
        return (
            distance_span <= self.config.stationary_distance_delta
            and bearing_span <= self.config.stationary_bearing_delta_radians
        )

    def _is_clearly_moving(self, *, include_bearing: bool = True) -> bool:
        if len(self._target_samples) < 2:
            return False
        first = self._target_samples[0]
        last = self._target_samples[-1]
        return (
            abs(last.distance - first.distance) >= self.config.moving_distance_delta
            or (
                include_bearing
                and abs(_wrap_angle(last.bearing - first.bearing))
                >= self.config.moving_bearing_delta_radians
            )
        )

    def _view_bearing_error(self) -> float:
        if self._last_observation is None:
            return 0.0
        return _wrap_angle(
            self._last_observation.bearing_radians
            - self.config.view_adjust.target_bearing_radians
        )

    def _dispatch(
        self,
        event_type: CompanionEventType,
        reason: str,
        now_monotonic: float | None,
    ) -> bool:
        now = self._now(now_monotonic)
        return self.state_machine.dispatch(
            CompanionEvent(event_type=event_type, monotonic_time=now, reason=reason)
        )

    def _now(self, value: float | None) -> float:
        now = self._clock() if value is None else value
        if not math.isfinite(now):
            raise ValueError("monotonic time must be finite")
        return now


_FALL_PATH_STATES = {
    CompanionState.FALL_SUSPECTED,
    CompanionState.EMERGENCY_STOP,
    CompanionState.MONITORING,
    CompanionState.RECOVERING,
    CompanionState.WAIT_RESUME,
}


def _zero_command(candidate: VelocityCommand) -> VelocityCommand:
    return VelocityCommand(
        vx=0.0,
        vy=0.0,
        wz=0.0,
        safety_state=candidate.safety_state,
        simulation_mode=candidate.simulation_mode,
    )


def _wrap_angle(value: float) -> float:
    return math.atan2(math.sin(value), math.cos(value))


def _circular_span(values) -> float:
    items = tuple(values)
    if not items:
        return 0.0
    reference = items[0]
    unwrapped = [reference + _wrap_angle(value - reference) for value in items]
    return max(unwrapped) - min(unwrapped)
