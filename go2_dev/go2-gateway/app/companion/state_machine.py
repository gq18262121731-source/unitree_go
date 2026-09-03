from __future__ import annotations

import math
import time
from typing import Callable

from app.companion.events import CompanionEvent, CompanionEventType
from app.companion.models import (
    CompanionMotionMode,
    CompanionSnapshot,
    CompanionState,
)


_TRANSITIONS: dict[
    CompanionState, dict[CompanionEventType, CompanionState]
] = {
    CompanionState.IDLE: {
        CompanionEventType.START: CompanionState.FOLLOWING,
        CompanionEventType.MANUAL_ACQUIRE: CompanionState.MANUAL_CONTROL,
    },
    CompanionState.FOLLOWING: {
        CompanionEventType.STOP: CompanionState.IDLE,
        CompanionEventType.PERSON_STATIONARY: CompanionState.PERSON_STOPPED,
        CompanionEventType.TARGET_LOST: CompanionState.TARGET_LOST,
        CompanionEventType.OBSTACLE_DETECTED: CompanionState.OBSTACLE_STOP,
        CompanionEventType.FALL_SUSPECTED: CompanionState.FALL_SUSPECTED,
        CompanionEventType.MANUAL_ACQUIRE: CompanionState.MANUAL_CONTROL,
    },
    CompanionState.PERSON_STOPPED: {
        CompanionEventType.STOP: CompanionState.IDLE,
        CompanionEventType.PERSON_MOVING: CompanionState.FOLLOWING,
        CompanionEventType.VIEW_REQUIRED: CompanionState.VIEW_ADJUST,
        CompanionEventType.VIEW_ALIGNED: CompanionState.HOLD,
        CompanionEventType.TARGET_LOST: CompanionState.TARGET_LOST,
        CompanionEventType.OBSTACLE_DETECTED: CompanionState.OBSTACLE_STOP,
        CompanionEventType.FALL_SUSPECTED: CompanionState.FALL_SUSPECTED,
    },
    CompanionState.VIEW_ADJUST: {
        CompanionEventType.STOP: CompanionState.IDLE,
        CompanionEventType.PERSON_MOVING: CompanionState.FOLLOWING,
        CompanionEventType.VIEW_ALIGNED: CompanionState.HOLD,
        CompanionEventType.TARGET_LOST: CompanionState.TARGET_LOST,
        CompanionEventType.OBSTACLE_DETECTED: CompanionState.OBSTACLE_STOP,
        CompanionEventType.FALL_SUSPECTED: CompanionState.FALL_SUSPECTED,
    },
    CompanionState.HOLD: {
        CompanionEventType.STOP: CompanionState.IDLE,
        CompanionEventType.PERSON_MOVING: CompanionState.FOLLOWING,
        CompanionEventType.VIEW_REQUIRED: CompanionState.VIEW_ADJUST,
        CompanionEventType.TARGET_LOST: CompanionState.TARGET_LOST,
        CompanionEventType.OBSTACLE_DETECTED: CompanionState.OBSTACLE_STOP,
        CompanionEventType.FALL_SUSPECTED: CompanionState.FALL_SUSPECTED,
    },
    CompanionState.TARGET_LOST: {
        CompanionEventType.STOP: CompanionState.IDLE,
        CompanionEventType.FAILSAFE_COMMITTED: CompanionState.SAFE_STOP,
        CompanionEventType.TARGET_REACQUIRED: CompanionState.FOLLOWING,
    },
    CompanionState.SAFE_STOP: {
        CompanionEventType.STOP: CompanionState.IDLE,
        CompanionEventType.TARGET_REACQUIRED: CompanionState.FOLLOWING,
    },
    CompanionState.OBSTACLE_STOP: {
        CompanionEventType.STOP: CompanionState.IDLE,
        CompanionEventType.OBSTACLE_CLEARED: CompanionState.FOLLOWING,
        CompanionEventType.TARGET_LOST: CompanionState.TARGET_LOST,
    },
    CompanionState.FALL_SUSPECTED: {
        CompanionEventType.STOP: CompanionState.IDLE,
        CompanionEventType.FALL_DISMISSED: CompanionState.FOLLOWING,
    },
    CompanionState.EMERGENCY_STOP: {
        CompanionEventType.BEGIN_VOICE_CHECK: CompanionState.VOICE_CHECK,
        CompanionEventType.EMERGENCY_ACKNOWLEDGED: CompanionState.MONITORING,
        CompanionEventType.I_AM_OK: CompanionState.WAIT_RESUME,
        CompanionEventType.REQUEST_HELP: CompanionState.HELP_REQUESTED,
        CompanionEventType.CALL_FAMILY: CompanionState.HELP_REQUESTED,
        CompanionEventType.RISK_CLEARED: CompanionState.WAIT_RESUME,
    },
    CompanionState.VOICE_CHECK: {
        CompanionEventType.I_AM_OK: CompanionState.WAIT_RESUME,
        CompanionEventType.REQUEST_HELP: CompanionState.HELP_REQUESTED,
        CompanionEventType.CALL_FAMILY: CompanionState.HELP_REQUESTED,
        CompanionEventType.NO_RESPONSE: CompanionState.RECHECK,
        CompanionEventType.RISK_CLEARED: CompanionState.WAIT_RESUME,
    },
    CompanionState.RECHECK: {
        CompanionEventType.I_AM_OK: CompanionState.WAIT_RESUME,
        CompanionEventType.REQUEST_HELP: CompanionState.HELP_REQUESTED,
        CompanionEventType.CALL_FAMILY: CompanionState.HELP_REQUESTED,
        CompanionEventType.NO_RESPONSE: CompanionState.ESCALATED_EMERGENCY,
        CompanionEventType.RISK_CLEARED: CompanionState.WAIT_RESUME,
    },
    CompanionState.HELP_REQUESTED: {},
    CompanionState.ESCALATED_EMERGENCY: {},
    CompanionState.MONITORING: {
        CompanionEventType.RECOVERY_DETECTED: CompanionState.RECOVERING,
    },
    CompanionState.RECOVERING: {
        CompanionEventType.RECOVERY_STABLE: CompanionState.WAIT_RESUME,
    },
    CompanionState.WAIT_RESUME: {
        CompanionEventType.RESUME: CompanionState.FOLLOWING,
        CompanionEventType.STOP: CompanionState.IDLE,
        CompanionEventType.REQUEST_HELP: CompanionState.HELP_REQUESTED,
        CompanionEventType.CALL_FAMILY: CompanionState.HELP_REQUESTED,
    },
    CompanionState.MANUAL_CONTROL: {
        CompanionEventType.MANUAL_RELEASE: CompanionState.IDLE,
        CompanionEventType.STOP: CompanionState.IDLE,
    },
}

_FALL_CONFIRMABLE_STATES = {
    CompanionState.IDLE,
    CompanionState.FOLLOWING,
    CompanionState.PERSON_STOPPED,
    CompanionState.VIEW_ADJUST,
    CompanionState.HOLD,
    CompanionState.TARGET_LOST,
    CompanionState.SAFE_STOP,
    CompanionState.OBSTACLE_STOP,
    CompanionState.FALL_SUSPECTED,
    CompanionState.MONITORING,
    CompanionState.RECOVERING,
    CompanionState.WAIT_RESUME,
    CompanionState.MANUAL_CONTROL,
}

_RESETTABLE_STATES = set(CompanionState)


class CompanionStateMachine:
    """Deterministic behavior state machine with a latched fall path."""

    def __init__(
        self,
        *,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = monotonic_clock
        now = self._clock()
        if not math.isfinite(now):
            raise ValueError("monotonic clock must return a finite value")
        self._state = CompanionState.IDLE
        self._previous_state: CompanionState | None = None
        self._reason = "initialized"
        self._entered_monotonic = now
        self._sequence = 0

    @property
    def state(self) -> CompanionState:
        return self._state

    def dispatch(self, event: CompanionEvent) -> bool:
        if not math.isfinite(event.monotonic_time):
            raise ValueError("event monotonic_time must be finite")

        if event.event_type is CompanionEventType.RESET_DEMO:
            if self._state not in _RESETTABLE_STATES:
                return False
            next_state = CompanionState.IDLE
        elif event.event_type in {
            CompanionEventType.FALL_SUSPECTED,
            CompanionEventType.FALL_CONFIRMED,
        }:
            if self._state not in _FALL_CONFIRMABLE_STATES:
                return False
            next_state = (
                CompanionState.FALL_SUSPECTED
                if event.event_type is CompanionEventType.FALL_SUSPECTED
                else CompanionState.EMERGENCY_STOP
            )
        else:
            next_state = _TRANSITIONS.get(self._state, {}).get(event.event_type)
            if next_state is None:
                return False

        previous = self._state
        self._state = next_state
        self._previous_state = previous
        self._reason = event.reason
        self._entered_monotonic = event.monotonic_time
        self._sequence += 1
        return True

    def snapshot(
        self,
        *,
        target_stationary: bool,
        target_available: bool,
        active_incident_id: str | None,
        help_required: bool | None = None,
        response_attempts: int = 0,
        emergency_escalated: bool = False,
        monitoring_active: bool = False,
    ) -> CompanionSnapshot:
        return CompanionSnapshot(
            sequence=self._sequence,
            state=self._state,
            previous_state=self._previous_state,
            reason=self._reason,
            entered_monotonic=self._entered_monotonic,
            motion_mode=_motion_mode(self._state),
            target_stationary=target_stationary,
            target_available=target_available,
            resume_required=self._state is CompanionState.WAIT_RESUME,
            active_incident_id=active_incident_id,
            help_required=help_required,
            response_attempts=response_attempts,
            emergency_escalated=emergency_escalated,
            monitoring_active=monitoring_active,
        )


def _motion_mode(state: CompanionState) -> CompanionMotionMode:
    if state is CompanionState.FOLLOWING:
        return CompanionMotionMode.FOLLOW
    if state is CompanionState.VIEW_ADJUST:
        return CompanionMotionMode.VIEW_ADJUST
    if state in {CompanionState.PERSON_STOPPED, CompanionState.HOLD}:
        return CompanionMotionMode.HOLD
    return CompanionMotionMode.STOP
