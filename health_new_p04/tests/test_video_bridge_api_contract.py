from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import backend.api.video_bridge_api as video_bridge_api
from backend.models.alarm_model import AlarmLayer, AlarmPriority, AlarmRecord, AlarmType


class _FakeVideoBridgeService:
    async def receive_fall_event_async(self, payload, *, source_ip, push_token):
        alarm = AlarmRecord(
            device_mac="AA:BB:CC:DD:EE:11",
            alarm_type=AlarmType.FALL_DETECTED,
            alarm_level=AlarmPriority.CRITICAL,
            alarm_layer=AlarmLayer.INTELLIGENT,
            message="fall",
            metadata={"elder_id": "elder-001"},
        )
        return {
            "promoted": True,
            "deduplicated": False,
            "alarm": alarm,
            "qwen_review_saved": True,
            "event": {
                "incident_id": payload.incident_id,
                "event_type": payload.event_type,
                "companion_event_type": "FALL_CONFIRMED",
                "metadata": {
                    "multimodal_review": {
                        "status": "completed",
                        "judgement": "confirmed_fall",
                        "confidence": 0.95,
                        "reason": "关键帧显示持续倒地。",
                        "recommended_action": "请立即人工确认。",
                    }
                },
            },
        }


class _ProductionTokenVideoBridgeService(_FakeVideoBridgeService):
    async def receive_fall_event_async(self, payload, *, source_ip, push_token):
        if push_token != "matching-production-token":
            raise PermissionError("VIDEO_BRIDGE_PUSH_FORBIDDEN")
        return await super().receive_fall_event_async(
            payload,
            source_ip=source_ip,
            push_token=push_token,
        )


def test_fall_event_http_contract_accepts_degraded_keyframes(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(video_bridge_api.router, prefix="/api/v1")
    monkeypatch.setattr(video_bridge_api, "get_video_bridge_service", lambda: _FakeVideoBridgeService())
    client = TestClient(app)

    response = client.post(
        "/api/v1/video-bridge/fall-events",
        headers={"X-Vision-Service-Token": "fixture-token"},
        json={
            "schema_version": "companion_fall_event.v1",
            "event_id": "pfv2-camera_01-http",
            "incident_id": "pfv2-camera_01-http",
            "event_type": "FALL_CONFIRMED",
            "timestamp": "2026-08-24T15:30:00+08:00",
            "camera_id": "camera_01",
            "elder_id": "elder-001",
            "confidence": 0.95,
            "source": "vision_service",
            "keyframes": {
                "status": "degraded",
                "requested_offsets_ms": [-3000, -1000, 0, 1000, 3000, 5000],
                "resolved_offsets_ms": [-1000, 0, 1000, 3000, 5000],
                "missing_offsets_ms": [-3000]
            },
            "metadata": {
                "qwen_review": {
                    "analysis_status": "completed",
                    "review_result": "likely_fall",
                    "confidence": 0.95,
                    "summary": "关键帧显示持续倒地。",
                    "community_advice": "请立即人工确认。"
                }
            }
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["incident_id"] == "pfv2-camera_01-http"
    assert body["event_type"] == "FALL_CONFIRMED"
    assert body["qwen_review_saved"] is True
    assert body["multimodal_review"]["judgement"] == "confirmed_fall"
    assert body["alarm_type"] == "fall_detected"


@pytest.mark.parametrize("token", [None, "wrong-production-token"])
def test_fall_event_http_contract_rejects_missing_or_wrong_production_token(monkeypatch, token) -> None:
    app = FastAPI()
    app.include_router(video_bridge_api.router, prefix="/api/v1")
    monkeypatch.setattr(
        video_bridge_api,
        "get_video_bridge_service",
        lambda: _ProductionTokenVideoBridgeService(),
    )
    client = TestClient(app)
    headers = {"X-Vision-Service-Token": token} if token is not None else {}

    response = client.post(
        "/api/v1/video-bridge/fall-events",
        headers=headers,
        json={
            "camera_id": "camera_01",
            "incident_id": "pfv2-camera_01-token-rejected",
            "event_type": "FALL_SUSPECTED",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "VIDEO_BRIDGE_PUSH_FORBIDDEN"


def test_fall_event_http_contract_accepts_matching_production_token(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(video_bridge_api.router, prefix="/api/v1")
    monkeypatch.setattr(
        video_bridge_api,
        "get_video_bridge_service",
        lambda: _ProductionTokenVideoBridgeService(),
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/video-bridge/fall-events",
        headers={"X-Vision-Service-Token": "matching-production-token"},
        json={
            "camera_id": "camera_01",
            "incident_id": "pfv2-camera_01-token-accepted",
            "event_type": "FALL_SUSPECTED",
        },
    )

    assert response.status_code == 200
