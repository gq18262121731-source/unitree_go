from __future__ import annotations

from app.companion.competition_lifecycle import (
    CompetitionLifecycle,
    LifecycleAction,
    LifecycleReadiness,
)
from app.companion.models import CompanionState


class Clock:
    def __init__(self) -> None:
        self.value = 1.0

    def __call__(self) -> float:
        self.value += 0.1
        return self.value


def ready(**overrides) -> LifecycleReadiness:
    values = {
        "webrtc_connected": True,
        "uwb_fresh": True,
        "uwb_valid": True,
        "motion_writer_available": True,
        "manual_takeover": False,
    }
    values.update(overrides)
    return LifecycleReadiness(**values)


def test_normal_start_stop_and_manual_never_auto_resume() -> None:
    lifecycle = CompetitionLifecycle(monotonic_clock=Clock())
    assert lifecycle.start(ready()).accepted
    assert lifecycle.snapshot().state is CompanionState.FOLLOWING

    manual = lifecycle.acquire_manual()
    assert manual.accepted
    assert manual.snapshot.state is CompanionState.MANUAL_CONTROL
    assert manual.to_dict()["authority"] == "MANUAL"

    released = lifecycle.release_manual()
    assert released.accepted
    assert released.snapshot.state is CompanionState.IDLE
    assert LifecycleAction.START_COMPANION not in released.actions


def test_i_am_ok_does_not_resume_and_requires_visual_risk_clear() -> None:
    lifecycle = CompetitionLifecycle(monotonic_clock=Clock())
    assert lifecycle.start(ready()).accepted
    risk = lifecycle.ingest_fall(incident_id="FALL-001", confirmed=False)
    assert risk.accepted
    assert risk.snapshot.state is CompanionState.VOICE_CHECK
    assert risk.actions[0] is LifecycleAction.STOP_MOVE

    ok = lifecycle.i_am_ok()
    assert ok.accepted
    assert ok.snapshot.state is CompanionState.WAIT_RESUME
    assert ok.snapshot.help_required is False
    assert LifecycleAction.RESUME_COMPANION not in ok.actions
    assert lifecycle.resume(ready()).reason == "risk_active"

    assert lifecycle.clear_risk(incident_id="FALL-001").accepted
    resumed = lifecycle.resume(ready())
    assert resumed.accepted
    assert resumed.snapshot.state is CompanionState.FOLLOWING


def test_two_no_responses_escalate_and_keep_monitoring() -> None:
    lifecycle = CompetitionLifecycle(monotonic_clock=Clock())
    lifecycle.ingest_fall(incident_id="FALL-002", confirmed=True)

    first = lifecycle.no_response()
    assert first.snapshot.state is CompanionState.RECHECK
    assert first.snapshot.response_attempts == 1
    assert first.actions == (
        LifecycleAction.STOP_MOVE,
        LifecycleAction.REASK_FOR_HELP,
    )

    second = lifecycle.no_response()
    assert second.snapshot.state is CompanionState.ESCALATED_EMERGENCY
    assert second.snapshot.response_attempts == 2
    assert second.snapshot.emergency_escalated
    assert second.snapshot.monitoring_active
    assert LifecycleAction.NOTIFY_FAMILY in second.actions
    assert LifecycleAction.NOTIFY_COMMUNITY in second.actions
    assert LifecycleAction.PLAY_ESCALATION in second.actions


def test_reset_clears_demo_context_without_restarting_transports() -> None:
    lifecycle = CompetitionLifecycle(monotonic_clock=Clock())
    lifecycle.ingest_fall(incident_id="FALL-003", confirmed=True)
    lifecycle.no_response()
    lifecycle.no_response()

    reset = lifecycle.reset_demo()
    assert reset.accepted
    assert reset.snapshot.state is CompanionState.IDLE
    assert reset.snapshot.active_incident_id is None
    assert reset.snapshot.help_required is None
    assert reset.snapshot.response_attempts == 0
    assert reset.actions == (
        LifecycleAction.STOP_MOVE,
        LifecycleAction.CLEAR_DEMO_CONTEXT,
    )


def test_emergency_blocks_manual_and_resume_rechecks_health() -> None:
    lifecycle = CompetitionLifecycle(monotonic_clock=Clock())
    lifecycle.ingest_fall(incident_id="FALL-004", confirmed=True)
    assert not lifecycle.acquire_manual().accepted
    lifecycle.i_am_ok()
    lifecycle.clear_risk(incident_id="FALL-004")

    blocked = lifecycle.resume(ready(uwb_fresh=False))
    assert not blocked.accepted
    assert blocked.reason == "uwb_not_fresh"


def test_suspected_then_confirmed_same_incident_is_idempotent_upgrade() -> None:
    lifecycle = CompetitionLifecycle(monotonic_clock=Clock())
    first = lifecycle.ingest_fall(incident_id="FALL-UPGRADE", confirmed=False)
    upgraded = lifecycle.ingest_fall(incident_id="FALL-UPGRADE", confirmed=True)

    assert first.accepted
    assert upgraded.accepted
    assert upgraded.reason == "fall_upgraded_to_confirmed"
    assert upgraded.snapshot.state is CompanionState.VOICE_CHECK
    assert upgraded.snapshot.response_attempts == 0


def test_help_can_override_previous_i_am_ok_without_resuming_motion() -> None:
    lifecycle = CompetitionLifecycle(monotonic_clock=Clock())
    lifecycle.ingest_fall(incident_id="FALL-HELP", confirmed=True)
    lifecycle.i_am_ok()

    help_result = lifecycle.request_help()
    assert help_result.accepted
    assert help_result.snapshot.state is CompanionState.HELP_REQUESTED
    assert help_result.snapshot.help_required is True
    assert LifecycleAction.RESUME_COMPANION not in help_result.actions
