from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.config import get_settings
from backend.models.alarm_model import AlarmLayer, AlarmPriority, AlarmRecord, AlarmType
from backend.models.companion_risk_model import CompanionRiskEvent, CompanionRiskEventType, CompanionRiskState
from backend.models.video_bridge_model import VideoBridgeFallEventRequest
from backend.services.companion_risk_service import (
    CompanionRiskConflict,
    CompanionRiskService,
    MockCompanionMotionExecutor,
)
from backend.services.fall_alarm_contract import select_fall_alarm_type
from backend.services.video_bridge_service import VideoBridgeService
from backend.services.vision_fall_event_adapter import VisionFallEventAdapter


def _risk_event(event_type: CompanionRiskEventType, incident_id: str = "pfv2-camera_01-0001") -> CompanionRiskEvent:
    return CompanionRiskEvent(
        event_type=event_type,
        incident_id=incident_id,
        timestamp=datetime.now(timezone.utc),
        confidence=0.95,
        camera_id="camera_01",
        elder_id="elder-001",
    )


@pytest.mark.parametrize(
    ("review_result", "judgement", "risk_type"),
    [
        ("likely_fall", "confirmed_fall", CompanionRiskEventType.FALL_CONFIRMED),
        ("likely_false_positive", "likely_false_positive", CompanionRiskEventType.FALL_DISMISSED),
        ("uncertain", "uncertain", CompanionRiskEventType.FALL_SUSPECTED),
    ],
)
def test_qwen_review_primary_contract_preserves_machine_enums(review_result, judgement, risk_type) -> None:
    adapter = VisionFallEventAdapter()
    event = adapter.normalize_event(
        {
            "incident_id": "pfv2-camera_01-0001",
            "event_type": "fall_confirmed",
            "camera_id": "camera_01",
            "metadata": {
                "qwen_review": {
                    "analysis_status": "completed",
                    "review_result": review_result,
                    "confidence": 0.95,
                    "summary": "关键帧显示人员持续处于低位姿态。",
                    "community_advice": "请立即人工查看实时画面。",
                    "movement_level": "active",
                    "model_role": "qwen_secondary_reviewer",
                    "model_name": "qwen3.5:4b",
                    "latency_ms": 13508.313,
                }
            },
        }
    )

    assert event["metadata"]["qwen_review"]["review_result"] == review_result
    assert event["metadata"]["multimodal_review"] == {
        "status": "completed",
        "judgement": judgement,
        "confidence": 0.95,
        "reason": "关键帧显示人员持续处于低位姿态。",
        "recommended_action": "请立即人工查看实时画面。",
        "provider": None,
        "model_name": "qwen3.5:4b",
        "latency_ms": 13508.313,
    }
    assert adapter.resolve_event_type(event) == risk_type


@pytest.mark.parametrize(
    "legacy_event",
    [
        {"qwen_advisory": {"review_result": "likely_fall", "confidence": 0.9}},
        {"metadata": {"event": {"qwen_advisory": {"review_result": "likely_fall", "confidence": 0.9}}}},
    ],
)
def test_legacy_qwen_advisory_is_converted_to_qwen_review(legacy_event) -> None:
    event = {"incident_id": "legacy-001", "event_type": "fall_confirmed", **legacy_event}
    normalized = VisionFallEventAdapter().normalize_event(event)
    assert normalized["metadata"]["qwen_review"]["review_result"] == "likely_fall"


@pytest.mark.parametrize(
    ("injury", "expected"),
    [
        (None, AlarmType.FALL_DETECTED),
        ({}, AlarmType.FALL_DETECTED),
        ({"level": "I3", "suspected": False}, AlarmType.FALL_DETECTED),
        ({"suspected": True, "source": "manual_confirmation"}, AlarmType.FALL_INJURY_RISK),
    ],
)
def test_injury_requires_explicit_suspected_true(injury, expected) -> None:
    assert select_fall_alarm_type({"severity": "L3", "injury": injury}) == expected


def test_binary_candidate_stops_immediately_and_false_positive_needs_manual_resume() -> None:
    executor = MockCompanionMotionExecutor()
    service = CompanionRiskService(executor)

    suspected = service.handle_event(_risk_event(CompanionRiskEventType.FALL_SUSPECTED))
    assert suspected.state == CompanionRiskState.PAUSED_BY_FALL
    assert suspected.stop_required is True
    assert executor.actions == [{"action": "STOP_MOVE", "incident_id": "pfv2-camera_01-0001"}]

    dismissed = service.handle_event(_risk_event(CompanionRiskEventType.FALL_DISMISSED))
    assert dismissed.state == CompanionRiskState.WAIT_RESUME
    assert service.status().motion_allowed is False

    resumed = service.resume("pfv2-camera_01-0001")
    assert resumed.state == CompanionRiskState.FOLLOWING
    assert executor.actions[-1]["action"] == "RESUME_COMPANION"


def test_confirmed_incident_is_locked_until_recovery_and_manual_resume() -> None:
    service = CompanionRiskService(MockCompanionMotionExecutor())
    confirmed = service.handle_event(_risk_event(CompanionRiskEventType.FALL_CONFIRMED))
    assert confirmed.state == CompanionRiskState.MONITORING

    dismissed = service.handle_event(_risk_event(CompanionRiskEventType.FALL_DISMISSED))
    assert dismissed.ignored is True
    assert dismissed.state == CompanionRiskState.MONITORING

    recovery = service.handle_event(_risk_event(CompanionRiskEventType.RECOVERY_CONFIRMED))
    assert recovery.state == CompanionRiskState.WAIT_RESUME
    assert service.resume("pfv2-camera_01-0001").state == CompanionRiskState.FOLLOWING


def test_risk_event_is_idempotent_and_recovery_incident_mismatch_is_rejected() -> None:
    executor = MockCompanionMotionExecutor()
    service = CompanionRiskService(executor)
    event = _risk_event(CompanionRiskEventType.FALL_SUSPECTED)
    service.handle_event(event)
    duplicate = service.handle_event(event)
    assert duplicate.deduplicated is True
    assert len(executor.actions) == 1

    with pytest.raises(CompanionRiskConflict, match="RECOVERY_INCIDENT_MISMATCH"):
        service.handle_event(_risk_event(CompanionRiskEventType.RECOVERY_CONFIRMED, "other-incident"))


def test_non_fall_does_not_automatically_resume_after_preemption() -> None:
    service = CompanionRiskService(MockCompanionMotionExecutor())
    service.handle_event(_risk_event(CompanionRiskEventType.FALL_SUSPECTED))
    non_fall = service.handle_event(_risk_event(CompanionRiskEventType.NON_FALL))
    assert non_fall.ignored is True
    assert non_fall.state == CompanionRiskState.PAUSED_BY_FALL


def test_ports_are_frozen_for_health_new_and_local_vision() -> None:
    settings = get_settings()
    assert settings.port == 8000
    assert settings.vision_service_base_url == "http://127.0.0.1:8011"
    assert settings.vision_service_local_base_url == "http://127.0.0.1:8011"


def test_production_bridge_requires_matching_nonempty_token(tmp_path: Path) -> None:
    settings = get_settings().model_copy(
        update={
            "data_dir": tmp_path,
            "vision_service_base_url": "http://127.0.0.1:8011",
            "vision_bridge_production_mode": True,
        }
    )
    service = VideoBridgeService(settings=settings, runtime_config_path=tmp_path / "runtime.json")
    assert service.is_authorized_push(source_ip="127.0.0.1", push_token=None) is False
    service.update_runtime_config({"push_token": "shared-test-token"})
    assert service.is_authorized_push(source_ip="127.0.0.1", push_token="wrong") is False
    assert service.is_authorized_push(source_ip="10.0.0.8", push_token="shared-test-token") is True


def test_development_bridge_allows_configured_vision_host_without_token(tmp_path: Path) -> None:
    settings = get_settings().model_copy(
        update={
            "data_dir": tmp_path,
            "vision_service_base_url": "http://127.0.0.1:8011",
            "vision_bridge_production_mode": False,
        }
    )
    service = VideoBridgeService(settings=settings, runtime_config_path=tmp_path / "runtime.json")
    assert service.is_authorized_push(source_ip="127.0.0.1", push_token=None) is True


def test_qwen_review_is_persisted_and_duplicate_transition_is_idempotent(tmp_path: Path) -> None:
    settings = get_settings().model_copy(
        update={
            "data_dir": tmp_path,
            "vision_service_base_url": "http://127.0.0.1:8011",
            "vision_bridge_production_mode": False,
        }
    )
    created: list[dict[str, object]] = []

    async def create_alarm(event: dict[str, object]) -> AlarmRecord:
        created.append(event)
        return AlarmRecord(
            device_mac="AA:BB:CC:DD:EE:11",
            alarm_type=AlarmType.FALL_DETECTED,
            alarm_level=AlarmPriority.CRITICAL,
            alarm_layer=AlarmLayer.INTELLIGENT,
            message="fall",
            metadata={"incident_id": event["incident_id"]},
        )

    service = VideoBridgeService(
        settings=settings,
        runtime_config_path=tmp_path / "runtime.json",
        alarm_ingest_callback=create_alarm,
    )
    payload = VideoBridgeFallEventRequest(
        camera_id="camera_01",
        incident_id="pfv2-camera_01-persist",
        event_type="fall_confirmed",
        fall_prob=0.93,
        metadata={
            "qwen_review": {
                "analysis_status": "completed",
                "review_result": "likely_fall",
                "confidence": 0.95,
                "summary": "关键帧显示持续倒地。",
                "community_advice": "请立即人工确认。",
            }
        },
    )

    first = asyncio.run(service.receive_fall_event_async(payload, source_ip="127.0.0.1"))
    duplicate = asyncio.run(service.receive_fall_event_async(payload, source_ip="127.0.0.1"))

    assert first["promoted"] is True
    assert first["qwen_review_saved"] is True
    assert duplicate["deduplicated"] is True
    assert len(created) == 1
    saved = json.loads(
        (tmp_path / "fall_events" / "qwen_review" / "pfv2-camera_01-persist.json").read_text(encoding="utf-8")
    )
    assert saved["review_result"] == "likely_fall"


def test_same_incident_can_advance_from_suspected_to_confirmed(tmp_path: Path) -> None:
    settings = get_settings().model_copy(
        update={
            "data_dir": tmp_path,
            "vision_service_base_url": "http://127.0.0.1:8011",
            "vision_bridge_production_mode": False,
        }
    )
    created: list[dict[str, object]] = []

    async def create_alarm(event: dict[str, object]) -> AlarmRecord:
        created.append(event)
        return AlarmRecord(
            device_mac="AA:BB:CC:DD:EE:11",
            alarm_type=AlarmType.FALL_DETECTED,
            alarm_level=AlarmPriority.CRITICAL,
            alarm_layer=AlarmLayer.INTELLIGENT,
            message="fall",
            metadata={"incident_id": event["incident_id"]},
        )

    service = VideoBridgeService(
        settings=settings,
        runtime_config_path=tmp_path / "runtime.json",
        alarm_ingest_callback=create_alarm,
    )
    suspected = VideoBridgeFallEventRequest(
        camera_id="camera_01",
        incident_id="pfv2-camera_01-transition",
        event_type="fall_suspected",
        fall_prob=0.77,
    )
    confirmed = suspected.model_copy(
        update={
            "event_type": "fall_confirmed",
            "fall_prob": 0.96,
        }
    )

    first = asyncio.run(service.receive_fall_event_async(suspected, source_ip="127.0.0.1"))
    second = asyncio.run(service.receive_fall_event_async(confirmed, source_ip="127.0.0.1"))

    assert first["deduplicated"] is False
    assert second["deduplicated"] is False
    assert [event["event_type"] for event in created] == ["fall_suspected", "fall_confirmed"]
