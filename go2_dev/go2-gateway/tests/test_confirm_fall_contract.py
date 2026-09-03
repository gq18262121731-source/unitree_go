from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _wait_for_task(client: TestClient, task_id: str, terminal: bool = True, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/robot/tasks/{task_id}")
        assert response.status_code == 200
        last = response.json()["data"]
        if not terminal and last.get("current_step") == "WAITING_RESPONSE":
            return last
        if terminal and last["status"] in {"finished", "failed", "cancelled", "BLOCKED_ROBOT_OFFLINE"}:
            return last
        time.sleep(0.02)
    return last


def _create_app(**overrides):
    mode = overrides.pop("mode", "mock")
    elder_response_timeout_seconds = overrides.pop("elder_response_timeout_seconds", 0.2)
    settings = Settings(
        mode=mode,
        state_stale_seconds=2.0,
        task_audit_enabled=False,
        elder_response_timeout_seconds=elder_response_timeout_seconds,
        task_evidence_dir=overrides.pop("task_evidence_dir", "data/test_task_evidence"),
        **overrides,
    )
    return create_app(settings)


def test_confirm_fall_safe_manual_response(tmp_path):
    app = _create_app(mock_confirm_fall_outcome="", elder_response_timeout_seconds=2.0, task_evidence_dir=str(tmp_path))
    with TestClient(app) as client:
        created = client.post(
            "/api/robot/events/fall",
            json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 0.95},
        ).json()["data"]
        waiting = _wait_for_task(client, created["task_id"], terminal=False)

        assert waiting["status_v2"] == "RUNNING"
        assert waiting["current_step"] == "WAITING_RESPONSE"

        response = client.post(
            f"/api/robot/tasks/{created['task_id']}/elder-response",
            json={"response_type": "SAFE", "transcript": "I am okay"},
        )
        final_task = _wait_for_task(client, created["task_id"])
        evidence = client.get(f"/api/robot/tasks/{created['task_id']}/evidence/arrival.jpg")

        assert response.status_code == 200
        assert final_task["status"] == "finished"
        assert final_task["status_v2"] == "COMPLETED"
        assert final_task["result"]["outcome"] == "SAFE"
        assert final_task["result"]["observation"]["camera_available"] is True
        assert final_task["result"]["observation"]["snapshot_url"].startswith("/api/robot/tasks/")
        assert evidence.status_code == 200
        assert evidence.content.startswith(b"\xff\xd8")


def test_confirm_fall_need_help_and_duplicate_response_is_idempotent(tmp_path):
    app = _create_app(mock_confirm_fall_outcome="", elder_response_timeout_seconds=2.0, task_evidence_dir=str(tmp_path))
    with TestClient(app) as client:
        created = client.post(
            "/api/events/fall",
            json={"event": "fall_detected", "elder_id": "001", "location": "bathroom", "confidence": 0.95},
        ).json()["data"]
        _wait_for_task(client, created["task_id"], terminal=False)

        first = client.post(
            f"/api/robot/tasks/{created['task_id']}/elder-response",
            json={"response_type": "NEED_HELP", "transcript": "need help"},
        )
        second = client.post(
            f"/api/robot/tasks/{created['task_id']}/elder-response",
            json={"response_type": "NEED_HELP", "transcript": "need help"},
        )
        final_task = _wait_for_task(client, created["task_id"])

        assert first.status_code == 200
        assert second.status_code in {200, 409}
        assert final_task["result"]["outcome"] == "NEED_HELP"
        assert final_task["result"]["needHelp"] is True


def test_confirm_fall_no_response_timeout(tmp_path):
    app = _create_app(mock_confirm_fall_outcome="NO_RESPONSE", task_evidence_dir=str(tmp_path))
    with TestClient(app) as client:
        created = client.post(
            "/api/events/fall",
            json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 0.95},
        ).json()["data"]
        final_task = _wait_for_task(client, created["task_id"])

        assert final_task["status"] == "finished"
        assert final_task["result"]["outcome"] == "NO_RESPONSE"
        assert final_task["result"]["voiceResult"] == "awaiting_response"


def test_callback_delivery_sequence_and_query(tmp_path):
    app = _create_app(task_evidence_dir=str(tmp_path), health_new_callback_url="http://127.0.0.1:9/callback")
    with TestClient(app) as client:
        created = client.post(
            "/api/events/fall",
            json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 0.95},
        ).json()["data"]
        _wait_for_task(client, created["task_id"])
        deliveries = client.get(f"/api/robot/tasks/{created['task_id']}/callback-deliveries").json()["data"]["deliveries"]

        assert deliveries
        assert [item["sequence"] for item in deliveries] == sorted(item["sequence"] for item in deliveries)
        assert all(item["callback_id"].startswith("cb_") for item in deliveries)
        assert any(item["status"] in {"queued", "sending", "failed"} for item in deliveries)


def test_real_unknown_location_is_blocked_without_motion(tmp_path):
    app = _create_app(task_evidence_dir=str(tmp_path))
    with TestClient(app) as client:
        client.app.state.adapter.set_online(True)
        real_settings = Settings(mode="real", state_stale_seconds=2.0, task_audit_enabled=False, task_evidence_dir=str(tmp_path))
        client.app.state.robot_service.settings = real_settings
        client.app.state.task_service.settings = real_settings
        response = client.post(
            "/api/events/fall",
            json={"event": "fall_detected", "elder_id": "001", "location": "unknown_room", "confidence": 0.95},
        )
        task = response.json()["data"]

        assert response.status_code == 200
        assert task["status"] == "BLOCKED_ROBOT_OFFLINE"
        assert task["status_v2"] == "BLOCKED"
        assert task["result"]["errorCode"] == "LOCATION_PLAN_NOT_VALIDATED"
        assert client.app.state.adapter.move_count == 0
