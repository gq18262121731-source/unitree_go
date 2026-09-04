from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Callable

from app.companion.events import CompanionEvent, CompanionEventType
from app.companion.models import CompanionSnapshot, CompanionState
from app.companion.state_machine import CompanionStateMachine


class LifecycleAction(str, Enum):
    STOP_MOVE = "STOP_MOVE"
    START_COMPANION = "START_COMPANION"
    RESUME_COMPANION = "RESUME_COMPANION"
    ASK_FOR_HELP = "ASK_FOR_HELP"
    REASK_FOR_HELP = "REASK_FOR_HELP"
    NOTIFY_FAMILY = "NOTIFY_FAMILY"
    NOTIFY_COMMUNITY = "NOTIFY_COMMUNITY"
    PLAY_ESCALATION = "PLAY_ESCALATION"
    KEEP_MONITORING = "KEEP_MONITORING"
    CLEAR_DEMO_CONTEXT = "CLEAR_DEMO_CONTEXT"


@dataclass(frozen=True)
class LifecycleReadiness:
    webrtc_connected: bool = True
    uwb_fresh: bool = True
    uwb_valid: bool = True
    motion_writer_available: bool = True
    manual_takeover: bool = False

    def failure(self) -> str | None:
        if not self.webrtc_connected:
            return "webrtc_not_connected"
        if not self.uwb_fresh:
            return "uwb_not_fresh"
        if not self.uwb_valid:
            return "uwb_not_valid"
        if not self.motion_writer_available:
            return "motion_writer_unavailable"
        if self.manual_takeover:
            return "manual_takeover_active"
        return None


@dataclass(frozen=True)
class LifecycleResult:
    accepted: bool
    reason: str
    snapshot: CompanionSnapshot
    actions: tuple[LifecycleAction, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "state": self.snapshot.state.value,
            "authority": _authority(self.snapshot.state),
            "actions": [action.value for action in self.actions],
            "snapshot": asdict(self.snapshot),
        }


class CompetitionLifecycle:
    """One high-level lifecycle for voice, risk, manual and demo reset.

    The class is deliberately side-effect free. Callers execute the returned
    actions through their existing single RobotService/Go2 WebRTC writer.
    """

    def __init__(
        self,
        *,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = monotonic_clock
        self._machine = CompanionStateMachine(monotonic_clock=monotonic_clock)
        self._lock = threading.RLock()
        self._incident_id: str | None = None
        self._risk_active = False
        self._fall_confirmed = False
        self._help_required: bool | None = None
        self._response_attempts = 0
        self._emergency_escalated = False

    @property
    def state(self) -> CompanionState:
        with self._lock:
            return self._machine.state

    @property
    def risk_active(self) -> bool:
        with self._lock:
            return self._risk_active

    def snapshot(self) -> CompanionSnapshot:
        with self._lock:
            return self._snapshot()

    def start(self, readiness: LifecycleReadiness) -> LifecycleResult:
        with self._lock:
            failure = readiness.failure()
            if failure:
                return self._result(False, failure)
            if self._risk_active:
                return self._result(False, "risk_active")
            return self._dispatch(
                CompanionEventType.START,
                "explicit_start",
                (LifecycleAction.START_COMPANION,),
            )

    def stop(self, *, reason: str = "explicit_stop") -> LifecycleResult:
        with self._lock:
            emergency = _is_emergency_state(self._machine.state)
            changed = False if emergency else self._emit(CompanionEventType.STOP, reason)
            return self._result(
                changed or emergency,
                "emergency_remains_latched" if emergency else reason,
                (LifecycleAction.STOP_MOVE,),
            )

    def acquire_manual(self) -> LifecycleResult:
        with self._lock:
            if self._risk_active or _is_emergency_state(self._machine.state):
                return self._result(False, "emergency_blocks_manual")
            return self._dispatch(
                CompanionEventType.MANUAL_ACQUIRE,
                "manual_control_acquired",
                (LifecycleAction.STOP_MOVE,),
            )

    def release_manual(self) -> LifecycleResult:
        with self._lock:
            return self._dispatch(
                CompanionEventType.MANUAL_RELEASE,
                "manual_control_released_explicit_resume_required",
                (LifecycleAction.STOP_MOVE,),
            )

    def ingest_fall(self, *, incident_id: str, confirmed: bool) -> LifecycleResult:
        normalized = str(incident_id or "").strip()
        if not normalized:
            raise ValueError("incident_id is required")
        with self._lock:
            if self._incident_id is not None and self._incident_id != normalized:
                return self._result(False, "different_incident_already_active")
            if self._incident_id == normalized and self._risk_active:
                upgraded = confirmed and not self._fall_confirmed
                self._fall_confirmed = self._fall_confirmed or confirmed
                return self._result(
                    True,
                    "fall_upgraded_to_confirmed" if upgraded else "fall_event_idempotent",
                    (LifecycleAction.STOP_MOVE,),
                )
            self._incident_id = normalized
            self._risk_active = True
            self._fall_confirmed = bool(confirmed)
            self._help_required = None
            self._response_attempts = 0
            event_type = (
                CompanionEventType.FALL_CONFIRMED
                if confirmed
                else CompanionEventType.FALL_SUSPECTED
            )
            if not self._emit(event_type, "fall_confirmed" if confirmed else "fall_suspected"):
                return self._result(False, "fall_transition_rejected")
            if not confirmed:
                # A suspected fall is already enough to stop. Voice checking is
                # allowed immediately while vision continues confirming it.
                self._emit(CompanionEventType.FALL_CONFIRMED, "suspected_fall_safety_stop")
            self._emit(CompanionEventType.BEGIN_VOICE_CHECK, "begin_voice_check")
            return self._result(
                True,
                "fall_latched_voice_check",
                (LifecycleAction.STOP_MOVE, LifecycleAction.ASK_FOR_HELP),
            )

    def clear_risk(self, *, incident_id: str) -> LifecycleResult:
        with self._lock:
            if incident_id != self._incident_id:
                return self._result(False, "incident_id_mismatch")
            self._risk_active = False
            self._fall_confirmed = False
            if self._machine.state in {
                CompanionState.EMERGENCY_STOP,
                CompanionState.VOICE_CHECK,
                CompanionState.RECHECK,
            }:
                self._emit(CompanionEventType.RISK_CLEARED, "risk_cleared_wait_resume")
            return self._result(
                True,
                "risk_cleared_explicit_resume_required",
                (LifecycleAction.STOP_MOVE, LifecycleAction.KEEP_MONITORING),
            )

    def i_am_ok(self) -> LifecycleResult:
        with self._lock:
            self._help_required = False
            if self._machine.state is CompanionState.WAIT_RESUME:
                return self._result(
                    True,
                    "already_waiting_for_explicit_resume",
                    (LifecycleAction.STOP_MOVE, LifecycleAction.KEEP_MONITORING),
                )
            return self._dispatch(
                CompanionEventType.I_AM_OK,
                "elder_says_no_help_needed",
                (LifecycleAction.STOP_MOVE, LifecycleAction.KEEP_MONITORING),
            )

    def request_help(self, *, call_family: bool = False) -> LifecycleResult:
        with self._lock:
            self._help_required = True
            event = (
                CompanionEventType.CALL_FAMILY
                if call_family
                else CompanionEventType.REQUEST_HELP
            )
            actions = [LifecycleAction.STOP_MOVE, LifecycleAction.NOTIFY_FAMILY]
            if not call_family:
                actions.append(LifecycleAction.NOTIFY_COMMUNITY)
            actions.append(LifecycleAction.KEEP_MONITORING)
            return self._dispatch(event, "help_requested", tuple(actions))

    def no_response(self) -> LifecycleResult:
        with self._lock:
            if self._machine.state not in {
                CompanionState.VOICE_CHECK,
                CompanionState.RECHECK,
            }:
                return self._result(False, "no_active_voice_check")
            self._response_attempts += 1
            if not self._emit(CompanionEventType.NO_RESPONSE, "no_valid_response"):
                return self._result(False, "no_response_transition_rejected")
            if self._machine.state is CompanionState.RECHECK:
                return self._result(
                    True,
                    "first_no_response_recheck",
                    (LifecycleAction.STOP_MOVE, LifecycleAction.REASK_FOR_HELP),
                )
            self._help_required = True
            self._emergency_escalated = True
            return self._result(
                True,
                "second_no_response_escalated",
                (
                    LifecycleAction.STOP_MOVE,
                    LifecycleAction.NOTIFY_FAMILY,
                    LifecycleAction.NOTIFY_COMMUNITY,
                    LifecycleAction.PLAY_ESCALATION,
                    LifecycleAction.KEEP_MONITORING,
                ),
            )

    def resume(self, readiness: LifecycleReadiness) -> LifecycleResult:
        with self._lock:
            if self._machine.state is not CompanionState.WAIT_RESUME:
                return self._result(False, "resume_requires_wait_resume")
            if self._risk_active:
                return self._result(False, "risk_active")
            failure = readiness.failure()
            if failure:
                return self._result(False, failure)
            return self._dispatch(
                CompanionEventType.RESUME,
                "explicit_safe_resume",
                (LifecycleAction.RESUME_COMPANION,),
            )

    def reset_demo(self) -> LifecycleResult:
        with self._lock:
            self._incident_id = None
            self._risk_active = False
            self._fall_confirmed = False
            self._help_required = None
            self._response_attempts = 0
            self._emergency_escalated = False
            self._emit(CompanionEventType.RESET_DEMO, "demo_reset")
            return self._result(
                True,
                "ready",
                (LifecycleAction.STOP_MOVE, LifecycleAction.CLEAR_DEMO_CONTEXT),
            )

    def _dispatch(
        self,
        event_type: CompanionEventType,
        reason: str,
        actions: tuple[LifecycleAction, ...],
    ) -> LifecycleResult:
        changed = self._emit(event_type, reason)
        return self._result(changed, reason if changed else "state_conflict", actions if changed else ())

    def _emit(self, event_type: CompanionEventType, reason: str) -> bool:
        return self._machine.dispatch(
            CompanionEvent(event_type, self._clock(), reason)
        )

    def _snapshot(self) -> CompanionSnapshot:
        return self._machine.snapshot(
            target_stationary=False,
            target_available=True,
            active_incident_id=self._incident_id,
            help_required=self._help_required,
            response_attempts=self._response_attempts,
            emergency_escalated=self._emergency_escalated,
            monitoring_active=_is_emergency_state(self._machine.state),
        )

    def _result(
        self,
        accepted: bool,
        reason: str,
        actions: tuple[LifecycleAction, ...] = (),
    ) -> LifecycleResult:
        return LifecycleResult(accepted, reason, self._snapshot(), actions)


def _is_emergency_state(state: CompanionState) -> bool:
    return state in {
        CompanionState.FALL_SUSPECTED,
        CompanionState.EMERGENCY_STOP,
        CompanionState.VOICE_CHECK,
        CompanionState.RECHECK,
        CompanionState.HELP_REQUESTED,
        CompanionState.ESCALATED_EMERGENCY,
        CompanionState.MONITORING,
        CompanionState.RECOVERING,
        CompanionState.WAIT_RESUME,
    }


def _authority(state: CompanionState) -> str:
    if _is_emergency_state(state):
        return "EMERGENCY"
    if state is CompanionState.MANUAL_CONTROL:
        return "MANUAL"
    if state is CompanionState.FOLLOWING:
        return "COMPANION"
    return "IDLE"
