from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _wait_for_task(client, task_id: str, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200
        last = response.json()["data"]
        if last["status"] in {"finished", "failed"}:
            return last
        time.sleep(0.02)
    assert last is not None
    return last


def test_status_endpoint_returns_robot_state(client):
    response = client.get("/api/robot/status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["robotId"] == "go2-edu-001"
    assert data["online"] is True
    assert data["motion"]["velocityX"] == 0.0
    assert data["battery"]["voltage"] == 31.2


def test_compact_status_endpoint_matches_health_new_contract(client):
    response = client.get("/api/status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["robot_id"] == "go2-edu-001"
    assert data["online"] is True
    assert data["ip"] == "192.168.123.161"
    assert data["battery"] == 78
    assert data["battery_detail"] == {
        "percentage": 78,
        "voltage": 31.2,
        "current": 1.4,
        "raw": {"mock": True},
    }
    assert data["mode"] == "idle"
    assert data["action"] == "mock-locomotion"
    assert data["action_updated_at"] is None
    assert data["busy"] is False
    assert data["control_enabled"] is True
    assert data["state_stale"] is False
    assert data["last_seen"] is not None
    assert data["task"] is None
    assert data["revision"] is None
    assert data["step"] is None
    assert data["steps"] is None
    assert data["progress"] is None
    assert data["finished"] is None
    assert data["elder_id"] is None
    assert data["location"] is None
    assert data["location_resolution"] is None
    assert data["confidence"] is None
    assert data["source_event_id"] is None
    assert data["camera_id"] is None
    assert data["external_task_id"] is None
    assert data["voice"] is None
    assert data["error"] is None
    assert data["last_error"] is None


def test_compact_status_endpoint_reports_active_task(client):
    original_move = client.app.state.robot_service.move

    def slow_move(*args, **kwargs):
        time.sleep(0.2)
        return original_move(*args, **kwargs)

    client.app.state.robot_service.move = slow_move

    task_response = client.post(
        "/api/events/fall",
        json={
            "event": "fall_detected",
            "elder_id": "001",
            "location": "bedroom",
            "confidence": 0.95,
            "source_event_id": "compact-status-fall-001",
            "camera_id": "compact-status-camera-01",
            "external_task_id": "health-task-compact-001",
        },
    )
    status_response = client.get("/api/status")

    assert task_response.status_code == 200
    assert status_response.status_code == 200
    data = status_response.json()["data"]
    assert data["mode"] == "task"
    assert data["task"] == "confirm_fall"
    assert data["task_id"] == task_response.json()["data"]["task_id"]
    assert data["status"] in {"waiting", "running", "moving", "arrived", "checking"}
    assert isinstance(data["revision"], int)
    assert data["busy"] in {False, True}
    assert data["control_enabled"] is True
    assert data["state_stale"] is False
    assert data["finished"] is False
    assert [step["name"] for step in data["steps"]] == [
        "receive_event",
        "moving",
        "arrived",
        "robot_camera",
        "voice_check",
        "finished",
    ]
    assert data["progress"]["total_steps"] == 6
    assert 0 <= data["progress"]["percent"] <= 100
    assert data["elder_id"] == "001"
    assert data["location"] == "bedroom"
    assert data["confidence"] == 0.95
    assert data["source_event_id"] == "compact-status-fall-001"
    assert data["camera_id"] == "compact-status-camera-01"
    assert data["external_task_id"] == "health-task-compact-001"
    assert data["location_resolution"]["location"] == "bedroom"
    assert data["location_resolution"]["known"] is True
    assert "voice" in data
    _wait_for_task(client, data["task_id"])


def test_capabilities_endpoint_describes_supported_robot_contract(client):
    response = client.get("/api/capabilities")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["robot_id"] == "go2-edu-001"
    assert data["gateway"]["sdk_wrapped"] is True
    assert data["gateway"]["methods"] == ["connect", "get_status", "stand", "sit", "stop", "move", "get_camera"]
    assert data["control"]["move"] is True
    assert data["camera"]["stream_url"] == "/api/camera/stream"
    assert data["voice"]["prompt"] is True
    assert data["voice"]["ready"] is True
    assert data["voice"]["status_url"] == "/api/voice/status"
    assert data["voice"]["speech_recognition"] is False
    assert data["tasks"]["confirm_fall"] == "implemented"
    assert data["tasks"]["follow"] == "reserved"
    assert data["tasks"]["confirm_fall_statuses"] == [
        "waiting",
        "running",
        "moving",
        "arrived",
        "checking",
        "finished",
        "failed",
        "cancelled",
    ]
    assert data["tasks"]["terminal_statuses"] == ["finished", "failed", "cancelled"]
    assert data["tasks"]["locations_url"] == "/api/locations"
    assert data["tasks"]["location_resolve_url"] == "/api/locations/resolve?location={location}"
    assert data["tasks"]["urls"]["summary"] == "/api/tasks/summary"
    assert data["tasks"]["urls"]["latest"] == "/api/tasks/latest"
    assert data["tasks"]["urls"]["external_lookup"] == "/api/tasks/external/{external_task_id}"
    assert data["tasks"]["urls"]["external_status"] == "/api/tasks/external/{external_task_id}/status"
    assert data["tasks"]["urls"]["external_result"] == "/api/tasks/external/{external_task_id}/result"
    assert data["tasks"]["urls"]["external_timeline"] == "/api/tasks/external/{external_task_id}/timeline"
    assert data["tasks"]["urls"]["external_feedback_replay"] == "/api/tasks/external/{external_task_id}/feedback/replay"
    assert data["tasks"]["urls"]["external_voice_result"] == "/api/tasks/external/{external_task_id}/voice-result"
    assert data["tasks"]["urls"]["external_cancel"] == "/api/tasks/external/{external_task_id}/cancel"
    assert data["tasks"]["urls"]["confirm_fall"] == "/api/tasks/confirm-fall"
    assert data["tasks"]["urls"]["target_move"] == "/api/tasks/target-move"
    assert data["tasks"]["urls"]["status"] == "/api/tasks/{task_id}/status"
    assert data["tasks"]["urls"]["result"] == "/api/tasks/{task_id}/result"
    assert data["tasks"]["urls"]["feedback_replay"] == "/api/tasks/{task_id}/feedback/replay"
    assert data["status"]["preflight_url"] == "/api/preflight"
    assert data["tasks"]["source_fields"] == [
        "elder_id",
        "location",
        "location_resolution",
        "confidence",
        "source_event_id",
        "camera_id",
        "external_task_id",
    ]
    assert data["events"]["fall"]["submit_url"] == "/api/events/fall"
    assert data["events"]["fall"]["lookup_url"] == "/api/events/fall/{source_event_id}"
    assert data["events"]["fall"]["idempotency_aliases"] == [
        "source_event_id",
        "sourceEventId",
        "event_id",
        "eventId",
        "camera_event_id",
        "cameraEventId",
    ]
    assert data["events"]["fall"]["callback_url_replay_attach"] is True
    assert data["events"]["fall"]["external_task_id_idempotency"] is True
    assert data["status"]["health_url"] == "/health"
    assert data["status"]["compact_url"] == "/api/status"
    assert data["status"]["task_context_fields"] == data["tasks"]["source_fields"]
    assert data["feedback"]["status_url"] == "/api/feedback/status"
    assert data["feedback"]["replay_url"] == "/api/tasks/{task_id}/feedback/replay"
    assert data["feedback"]["callback_source_fields"] == data["tasks"]["source_fields"]
    assert data["navigation"]["slam"] is False


def test_robot_capabilities_endpoint_uses_same_contract(client):
    response = client.get("/api/robot/capabilities")

    assert response.status_code == 200
    assert response.json()["data"]["tasks"]["move_to_target"] == "implemented"


def test_feedback_status_endpoint_exposes_health_new_delivery_state(client):
    response = client.get("/api/feedback/status")
    compat_response = client.get("/api/robot/feedback/status")

    assert response.status_code == 200
    assert compat_response.status_code == 200
    data = response.json()["data"]
    compat_data = compat_response.json()["data"]
    assert data["configured"] is False
    assert data["default_callback_url"] is None
    assert data["worker_alive"] is False
    assert data["closed"] is False
    assert data["pending"] == 0
    assert data["sent"] == 0
    assert data["failed"] == 0
    assert data["dropped"] == 0
    assert compat_data == data


def test_locations_endpoint_lists_default_fixed_motion_plans(client):
    response = client.get("/api/locations")

    assert response.status_code == 200
    data = response.json()["data"]
    locations = {item["location"]: item for item in data["locations"]}
    assert locations["bedroom"]["source"] == "default"
    assert "卧室" in locations["bedroom"]["aliases"]
    assert locations["bedroom"]["plan"][0] == {"vx": 0.1, "vy": 0.0, "wz": 0.0, "duration": 0.2}
    assert locations["bathroom"]["source"] == "default"
    assert {"alias": "卫生间", "location": "bathroom"} in data["aliases"]
    assert data["fallback"]["enabled"] is True


def test_location_resolve_endpoint_maps_chinese_room_alias(client):
    response = client.get("/api/locations/resolve", params={"location": "卧室"})
    compat_response = client.get("/api/robot/locations/resolve", params={"location": "卫生间"})

    assert response.status_code == 200
    assert compat_response.status_code == 200
    data = response.json()["data"]
    compat_data = compat_response.json()["data"]
    assert data["input"] == "卧室"
    assert data["location"] == "bedroom"
    assert data["known"] is True
    assert data["fallback_used"] is False
    assert data["source"] == "default"
    assert data["plan"][0] == {"vx": 0.1, "vy": 0.0, "wz": 0.0, "duration": 0.2}
    assert compat_data["location"] == "bathroom"
    assert compat_data["known"] is True


def test_locations_endpoint_lists_configured_fixed_motion_plans():
    app = create_app(
        Settings(
            mode="mock",
            task_audit_enabled=False,
            location_motion_plans_json='{"bedroom":[[0.05,0,0,0.1]],"demo room":[[0,0,0.1,0.2]]}',
        )
    )

    with TestClient(app) as client:
        response = client.get("/api/robot/locations")

    assert response.status_code == 200
    locations = {item["location"]: item for item in response.json()["data"]["locations"]}
    assert locations["bedroom"]["source"] == "configured"
    assert locations["bedroom"]["plan"][0] == {"vx": 0.05, "vy": 0.0, "wz": 0.0, "duration": 0.1}
    assert locations["demo_room"]["source"] == "configured"


def test_offline_status(client):
    client.app.state.adapter.set_online(False)

    response = client.get("/api/robot/status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["online"] is False
    assert data["stateStale"] is True


def test_dds_diagnostics_endpoint_distinguishes_mock_state_available(client):
    response = client.get("/api/robot/diagnostics/dds")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ddsInitialized"] is True
    assert data["ddsStateAvailable"] is True
    assert data["robotOnline"] is True
    assert data["motionReady"] is True
    assert data["dds"]["sportState"]["received"] is True
    assert data["dds"]["lowState"]["received"] is True


def test_dds_diagnostics_endpoint_reports_no_state_samples(client):
    client.app.state.adapter.set_online(False)
    real_settings = Settings(mode="real")
    client.app.state.robot_service.settings = real_settings
    client.app.state.status_service.settings = real_settings
    client.app.state.network_diagnostics_service.settings = real_settings

    response = client.get("/api/robot/diagnostics/dds")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ddsInitialized"] is True
    assert data["ddsStateAvailable"] is False
    assert data["robotOnline"] is False
    assert data["motionReady"] is False
    assert data["errorCode"] in {"UNITREE_DDS_NO_STATE_SAMPLES", "UNITREE_INTERFACE_NOT_FOUND"}
    assert data["dds"]["sportState"]["timeoutCode"] == "SPORT_STATE_TIMEOUT"
    assert data["dds"]["lowState"]["timeoutCode"] == "LOW_STATE_TIMEOUT"


def test_connection_status_endpoint(client):
    response = client.get("/api/connection")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["robot_id"] == "go2-edu-001"
    assert data["online"] is True
    assert data["ip"] == "192.168.123.161"
    assert data["network_interface"] == "enp3s0"
    assert data["mode"] == "mock"
    assert data["initialized"] is True
    assert data["sdk_version"] == "mock"
    assert data["error"] is None
    assert data["last_error"] is None


def test_reconnect_endpoint_reinitializes_gateway(client):
    client.app.state.gateway.close()

    before = client.get("/api/connection").json()["data"]
    response = client.post("/api/connection/reconnect")

    assert before["initialized"] is False
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["initialized"] is True
    assert data["online"] is True
    assert data["sdk_version"] == "mock"


def test_reconnect_endpoint_rejects_while_task_is_active(client):
    original_move = client.app.state.robot_service.move

    def slow_move(*args, **kwargs):
        time.sleep(0.2)
        return original_move(*args, **kwargs)

    client.app.state.robot_service.move = slow_move

    task_response = client.post(
        "/api/events/fall",
        json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 0.95},
    )
    reconnect_response = client.post("/api/robot/connection/reconnect")

    assert task_response.status_code == 200
    assert reconnect_response.status_code == 409
    assert reconnect_response.json()["code"] == "CONTROL_BUSY"
    _wait_for_task(client, task_response.json()["data"]["task_id"])


def test_reconnect_failure_is_exposed_in_connection_and_readiness(client):
    def fail_connect():
        raise RuntimeError("mock reconnect failure")

    client.app.state.gateway.connect = fail_connect

    response = client.post("/api/connection/reconnect")
    connection = client.get("/api/connection").json()["data"]
    readiness = client.get("/api/readiness").json()["data"]

    assert response.status_code == 503
    assert response.json()["code"] == "SDK_NOT_INITIALIZED"
    assert connection["last_error"] == "mock reconnect failure"
    assert readiness["last_error"] == "mock reconnect failure"


def test_readiness_endpoint_accepts_dispatch_when_idle(client):
    response = client.get("/api/readiness")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ready"] is True
    assert data["online"] is True
    assert data["initialized"] is True
    assert data["control_enabled"] is True
    assert data["state_stale"] is False
    assert data["busy"] is False
    assert data["active_task"] is None
    assert data["error"] is None


def test_readiness_endpoint_rejects_dispatch_when_offline(client):
    client.app.state.adapter.set_online(False)

    response = client.get("/api/readiness")

    assert response.status_code == 503
    assert response.json()["code"] == "ROBOT_OFFLINE"
    assert response.json()["data"]["ready"] is False
    assert response.json()["data"]["online"] is False
    assert response.json()["data"]["error"] == "ROBOT_OFFLINE"


def test_readiness_endpoint_rejects_dispatch_when_task_active(client):
    original_move = client.app.state.robot_service.move

    def slow_move(*args, **kwargs):
        time.sleep(0.2)
        return original_move(*args, **kwargs)

    client.app.state.robot_service.move = slow_move

    task_response = client.post(
        "/api/events/fall",
        json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 0.95},
    )
    readiness_response = client.get("/api/readiness")

    assert task_response.status_code == 200
    assert readiness_response.status_code == 409
    assert readiness_response.json()["code"] == "CONTROL_BUSY"
    assert readiness_response.json()["data"]["ready"] is False
    assert readiness_response.json()["data"]["active_task"]["task"] == "confirm_fall"
    _wait_for_task(client, task_response.json()["data"]["task_id"])


def test_voice_status_endpoint_returns_prompt_contract(client):
    response = client.get("/api/voice/status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["voice"] == "ready"
    assert data["ready"] is True
    assert data["mode"] == "mock"
    assert data["delivery_mode"] == "mock"
    assert data["fall_prompt"] == "您好，请问您现在是否需要帮助？"
    assert data["prompt_url"] is None
    assert data["last_prompt"] is None
    assert data["supports_speech_recognition"] is False
    assert data["next_action"] == "dispatch"


def test_voice_status_endpoint_reports_missing_http_bridge_configuration():
    app = create_app(Settings(mode="mock", voice_mode="http", voice_prompt_url=""))
    with TestClient(app) as client:
        response = client.get("/api/voice/status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["voice"] == "not_configured"
    assert data["ready"] is False
    assert data["mode"] == "http"
    assert data["delivery_mode"] == "http"
    assert data["prompt_url_configured"] is False
    assert data["prompt_url"] is None
    assert data["next_action"] == "configure GO2_VOICE_PROMPT_URL or set GO2_VOICE_MODE=mock"


def test_status_reports_control_disabled():
    app = create_app(Settings(mode="mock", control_enabled=False))
    with TestClient(app) as client:
        response = client.get("/api/robot/status")

    assert response.status_code == 200
    assert response.json()["data"]["control"]["enabled"] is False


def test_readiness_endpoint_rejects_dispatch_when_control_disabled():
    app = create_app(Settings(mode="mock", control_enabled=False))
    with TestClient(app) as client:
        response = client.get("/api/readiness")

    assert response.status_code == 403
    assert response.json()["code"] == "CONTROL_DISABLED"
    data = response.json()["data"]
    assert data["ready"] is False
    assert data["control_enabled"] is False
    assert data["error"] == "CONTROL_DISABLED"
