from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock

from backend.models.companion_risk_model import (
    CompanionRiskEvent,
    CompanionRiskEventType,
    CompanionRiskState,
    CompanionRiskStatus,
    CompanionRiskTransition,
)


class CompanionRiskConflict(RuntimeError):
    pass


@dataclass
class MockCompanionMotionExecutor:
    """Records safety decisions without sending a real robot command."""

    mode: str = "mock"
    actions: list[dict[str, str]] = field(default_factory=list)

    def stop(self, incident_id: str) -> None:
        self.actions.append({"action": "STOP_MOVE", "incident_id": incident_id})

    def resume(self, incident_id: str) -> None:
        self.actions.append({"action": "RESUME_COMPANION", "incident_id": incident_id})


@dataclass
class DisabledCompanionMotionExecutor:
    mode: str = "disabled"

    def stop(self, incident_id: str) -> None:
        return None

    def resume(self, incident_id: str) -> None:
        return None


class CompanionRiskService:
    """In-memory V1 safety state machine; it never issues Move commands."""

    _LOCKED_STATES = {
        CompanionRiskState.PAUSED_BY_FALL,
        CompanionRiskState.MONITORING,
        CompanionRiskState.WAIT_RESUME,
    }

    def __init__(self, executor: MockCompanionMotionExecutor | DisabledCompanionMotionExecutor | None = None) -> None:
        self._executor = executor or DisabledCompanionMotionExecutor()
        self._state = CompanionRiskState.FOLLOWING
        self._active_incident_id: str | None = None
        self._confirmed_incident_locked = False
        self._handled: dict[tuple[str, CompanionRiskEventType], CompanionRiskTransition] = {}
        self._lock = RLock()

    @property
    def executor(self) -> MockCompanionMotionExecutor | DisabledCompanionMotionExecutor:
        return self._executor

    def status(self) -> CompanionRiskStatus:
        with self._lock:
            return CompanionRiskStatus(
                state=self._state,
                active_incident_id=self._active_incident_id,
                confirmed_incident_locked=self._confirmed_incident_locked,
                motion_executor=self._executor.mode,
                motion_allowed=self._state not in self._LOCKED_STATES,
            )

    def motion_conflict_code(self) -> str | None:
        return "COMPANION_RISK_LOCK_ACTIVE" if not self.status().motion_allowed else None

    def handle_event(self, event: CompanionRiskEvent) -> CompanionRiskTransition:
        key = (event.incident_id, event.event_type)
        with self._lock:
            previous = self._handled.get(key)
            if previous is not None:
                return previous.model_copy(update={"deduplicated": True})

            self._validate_incident(event)
            previous_state = self._state
            stop_required = False
            motion_action = "NONE"
            ignored = False
            reason = "risk_event_recorded"

            if event.event_type == CompanionRiskEventType.FALL_SUSPECTED:
                self._activate(event.incident_id)
                stop_required = previous_state == CompanionRiskState.FOLLOWING
                if stop_required:
                    self._executor.stop(event.incident_id)
                    motion_action = "STOP_MOVE"
                self._state = CompanionRiskState.PAUSED_BY_FALL
                reason = "binary_candidate_safety_preemption"
            elif event.event_type == CompanionRiskEventType.FALL_CONFIRMED:
                self._activate(event.incident_id)
                stop_required = previous_state == CompanionRiskState.FOLLOWING
                if stop_required:
                    self._executor.stop(event.incident_id)
                    motion_action = "STOP_MOVE"
                self._confirmed_incident_locked = True
                self._state = CompanionRiskState.MONITORING
                reason = "fall_confirmed_incident_locked"
            elif event.event_type == CompanionRiskEventType.FALL_DISMISSED:
                self._activate(event.incident_id)
                if self._confirmed_incident_locked:
                    ignored = True
                    self._state = CompanionRiskState.MONITORING
                    reason = "confirmed_incident_cannot_be_dismissed"
                else:
                    self._state = CompanionRiskState.WAIT_RESUME
                    reason = "dismissed_requires_manual_resume"
            elif event.event_type == CompanionRiskEventType.RECOVERY_SUSPECTED:
                reason = "recovery_not_confirmed"
            elif event.event_type == CompanionRiskEventType.RECOVERY_CONFIRMED:
                self._state = CompanionRiskState.WAIT_RESUME
                reason = "recovery_confirmed_requires_manual_resume"
            elif event.event_type == CompanionRiskEventType.NON_FALL:
                if self._state in self._LOCKED_STATES:
                    ignored = True
                    reason = "non_fall_cannot_auto_resume"

            transition = CompanionRiskTransition(
                ignored=ignored,
                incident_id=event.incident_id,
                event_type=event.event_type,
                previous_state=previous_state,
                state=self._state,
                stop_required=stop_required,
                motion_action=motion_action,
                motion_executor=self._executor.mode,
                reason=reason,
            )
            self._handled[key] = transition
            return transition

    def resume(self, incident_id: str) -> CompanionRiskTransition:
        normalized = incident_id.strip()
        with self._lock:
            if self._active_incident_id != normalized:
                raise CompanionRiskConflict("RECOVERY_INCIDENT_MISMATCH")
            if self._state != CompanionRiskState.WAIT_RESUME:
                raise CompanionRiskConflict("COMPANION_NOT_WAITING_FOR_RESUME")
            previous_state = self._state
            self._executor.resume(normalized)
            self._state = CompanionRiskState.FOLLOWING
            self._active_incident_id = None
            self._confirmed_incident_locked = False
            return CompanionRiskTransition(
                incident_id=normalized,
                event_type=CompanionRiskEventType.RECOVERY_CONFIRMED,
                previous_state=previous_state,
                state=self._state,
                motion_action="RESUME_COMPANION",
                motion_executor=self._executor.mode,
                reason="manual_resume_accepted",
            )

    def _activate(self, incident_id: str) -> None:
        if self._active_incident_id is None:
            self._active_incident_id = incident_id
        elif self._active_incident_id != incident_id and self._state in self._LOCKED_STATES:
            raise CompanionRiskConflict("ACTIVE_INCIDENT_CONFLICT")

    def _validate_incident(self, event: CompanionRiskEvent) -> None:
        if event.event_type in {
            CompanionRiskEventType.FALL_DISMISSED,
            CompanionRiskEventType.RECOVERY_SUSPECTED,
            CompanionRiskEventType.RECOVERY_CONFIRMED,
        }:
            if self._active_incident_id != event.incident_id:
                raise CompanionRiskConflict("RECOVERY_INCIDENT_MISMATCH")

