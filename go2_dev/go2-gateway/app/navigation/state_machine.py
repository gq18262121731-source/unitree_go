from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.navigation.models import (
    ControlOwner,
    MappingState,
    NavigationErrorCode,
    NavigationExecutionState,
    NavigationState,
    NavigationTaskState,
)


class NavigationTransitionError(ValueError):
    def __init__(
        self,
        code: NavigationErrorCode,
        current: str,
        target: str,
    ) -> None:
        self.code = code
        self.current = current
        self.target = target
        super().__init__(f"{code.value}: {current} -> {target}")


_EXECUTION_TRANSITIONS: dict[NavigationExecutionState, frozenset[NavigationExecutionState]] = {
    NavigationExecutionState.CREATED: frozenset(
        {NavigationExecutionState.SAFETY_CHECKING, NavigationExecutionState.CANCELLED}
    ),
    NavigationExecutionState.SAFETY_CHECKING: frozenset(
        {
            NavigationExecutionState.BLOCKED,
            NavigationExecutionState.QUEUED,
            NavigationExecutionState.RETURNING_HOME,
            NavigationExecutionState.CANCELLED,
        }
    ),
    NavigationExecutionState.BLOCKED: frozenset(
        {NavigationExecutionState.SAFETY_CHECKING, NavigationExecutionState.CANCELLED}
    ),
    NavigationExecutionState.QUEUED: frozenset(
        {NavigationExecutionState.NAVIGATING, NavigationExecutionState.CANCELLED, NavigationExecutionState.FAILED}
    ),
    NavigationExecutionState.NAVIGATING: frozenset(
        {
            NavigationExecutionState.PAUSED_MANUAL,
            NavigationExecutionState.PAUSED_ADMIN,
            NavigationExecutionState.ARRIVED,
            NavigationExecutionState.FAILED,
            NavigationExecutionState.CANCELLED,
        }
    ),
    NavigationExecutionState.PAUSED_MANUAL: frozenset(
        {NavigationExecutionState.SAFETY_CHECKING, NavigationExecutionState.CANCELLED}
    ),
    NavigationExecutionState.PAUSED_ADMIN: frozenset(
        {NavigationExecutionState.SAFETY_CHECKING, NavigationExecutionState.CANCELLED}
    ),
    NavigationExecutionState.ARRIVED: frozenset(
        {
            NavigationExecutionState.VOICE_PROMPTING,
            NavigationExecutionState.WAITING_ADMIN_CONFIRMATION,
            NavigationExecutionState.RETURNING_HOME,
            NavigationExecutionState.COMPLETED,
            NavigationExecutionState.FAILED,
            NavigationExecutionState.CANCELLED,
        }
    ),
    NavigationExecutionState.VOICE_PROMPTING: frozenset(
        {NavigationExecutionState.WAITING_RESPONSE, NavigationExecutionState.FAILED, NavigationExecutionState.CANCELLED}
    ),
    NavigationExecutionState.WAITING_RESPONSE: frozenset(
        {
            NavigationExecutionState.WAITING_ADMIN_CONFIRMATION,
            NavigationExecutionState.FAILED,
            NavigationExecutionState.CANCELLED,
        }
    ),
    NavigationExecutionState.WAITING_ADMIN_CONFIRMATION: frozenset(
        {
            NavigationExecutionState.SAFETY_CHECKING,
            NavigationExecutionState.COMPLETED,
            NavigationExecutionState.CANCELLED,
        }
    ),
    NavigationExecutionState.RETURNING_HOME: frozenset(
        {NavigationExecutionState.COMPLETED, NavigationExecutionState.FAILED, NavigationExecutionState.CANCELLED}
    ),
    NavigationExecutionState.COMPLETED: frozenset(),
    NavigationExecutionState.FAILED: frozenset(),
    NavigationExecutionState.CANCELLED: frozenset(),
}


_NAVIGATION_STATE_BY_EXECUTION = {
    NavigationExecutionState.CREATED: NavigationTaskState.CREATED,
    NavigationExecutionState.SAFETY_CHECKING: NavigationTaskState.SAFETY_CHECKING,
    NavigationExecutionState.BLOCKED: NavigationTaskState.BLOCKED,
    NavigationExecutionState.QUEUED: NavigationTaskState.QUEUED,
    NavigationExecutionState.NAVIGATING: NavigationTaskState.NAVIGATING,
    NavigationExecutionState.PAUSED_MANUAL: NavigationTaskState.PAUSED_MANUAL,
    NavigationExecutionState.PAUSED_ADMIN: NavigationTaskState.PAUSED_ADMIN,
    NavigationExecutionState.ARRIVED: NavigationTaskState.ARRIVED,
    NavigationExecutionState.VOICE_PROMPTING: NavigationTaskState.ARRIVED,
    NavigationExecutionState.WAITING_RESPONSE: NavigationTaskState.ARRIVED,
    NavigationExecutionState.WAITING_ADMIN_CONFIRMATION: NavigationTaskState.ARRIVED,
    NavigationExecutionState.RETURNING_HOME: NavigationTaskState.RETURNING_HOME,
    NavigationExecutionState.COMPLETED: NavigationTaskState.COMPLETED,
    NavigationExecutionState.FAILED: NavigationTaskState.FAILED,
    NavigationExecutionState.CANCELLED: NavigationTaskState.CANCELLED,
}


class NavigationStateMachine:
    def __init__(self, state: NavigationState | None = None) -> None:
        self.state = state or NavigationState()

    def can_transition(self, target: NavigationExecutionState) -> bool:
        return target in _EXECUTION_TRANSITIONS[self.state.execution_state]

    def transition(self, target: NavigationExecutionState) -> NavigationState:
        current = self.state.execution_state
        if not self.can_transition(target):
            raise NavigationTransitionError(
                NavigationErrorCode.INVALID_STATE_TRANSITION,
                current.value,
                target.value,
            )
        self.state = self.state.model_copy(
            update={
                "execution_state": target,
                "navigation_state": _NAVIGATION_STATE_BY_EXECUTION[target],
                "updated_at": datetime.now(timezone.utc),
            }
        )
        return self.state


_MAPPING_TRANSITIONS: dict[MappingState, frozenset[MappingState]] = {
    MappingState.IDLE: frozenset({MappingState.MAPPING}),
    MappingState.MAPPING: frozenset(
        {MappingState.PREVIEW_READY, MappingState.CANCELLED, MappingState.FAILED}
    ),
    MappingState.PREVIEW_READY: frozenset(
        {MappingState.SAVED, MappingState.CANCELLED, MappingState.FAILED}
    ),
    MappingState.SAVED: frozenset(),
    MappingState.CANCELLED: frozenset(),
    MappingState.FAILED: frozenset(),
}


@dataclass
class MappingStateMachine:
    state: MappingState = MappingState.IDLE

    def can_transition(self, target: MappingState) -> bool:
        return target in _MAPPING_TRANSITIONS[self.state]

    def transition(self, target: MappingState) -> MappingState:
        current = self.state
        if not self.can_transition(target):
            raise NavigationTransitionError(
                NavigationErrorCode.INVALID_STATE_TRANSITION,
                current.value,
                target.value,
            )
        self.state = target
        return self.state


_CONTROL_TRANSITIONS: dict[ControlOwner, frozenset[ControlOwner]] = {
    ControlOwner.NONE: frozenset(
        {ControlOwner.MANUAL, ControlOwner.NAVIGATION, ControlOwner.FOLLOW, ControlOwner.EMERGENCY_STOP}
    ),
    ControlOwner.MANUAL: frozenset({ControlOwner.NONE, ControlOwner.EMERGENCY_STOP}),
    ControlOwner.NAVIGATION: frozenset({ControlOwner.NONE, ControlOwner.MANUAL, ControlOwner.EMERGENCY_STOP}),
    ControlOwner.FOLLOW: frozenset({ControlOwner.NONE, ControlOwner.MANUAL, ControlOwner.EMERGENCY_STOP}),
    ControlOwner.EMERGENCY_STOP: frozenset({ControlOwner.NONE}),
}


@dataclass
class ControlOwnershipStateMachine:
    owner: ControlOwner = ControlOwner.NONE

    def can_transition(self, target: ControlOwner) -> bool:
        return target in _CONTROL_TRANSITIONS[self.owner]

    def transition(self, target: ControlOwner) -> ControlOwner:
        current = self.owner
        if not self.can_transition(target):
            code = (
                NavigationErrorCode.EMERGENCY_STOP_ACTIVE
                if current == ControlOwner.EMERGENCY_STOP
                else NavigationErrorCode.INVALID_CONTROL_TRANSITION
            )
            raise NavigationTransitionError(code, current.value, target.value)
        self.owner = target
        return self.owner

    def emergency_stop(self) -> ControlOwner:
        self.owner = ControlOwner.EMERGENCY_STOP
        return self.owner
