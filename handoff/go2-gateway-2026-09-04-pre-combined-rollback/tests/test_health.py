from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _wait_for_task(client, task_id: str, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200
        task = response.json()["data"]
        if task["status"] in {"finished", "failed", "cancelled"}:
            return task
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not finish")


def _wait_for_active_health(client, timeout: float = 1.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()["data"]
        if data["activeTask"]["active"]:
            return data
        time.sleep(0.02)
    raise AssertionError("health endpoint did not report an active task")


def test_health_endpoint(client):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["service"] == "go2-gateway"
    assert body["data"]["mode"] == "mock"
    assert body["data"]["initialized"] is True
    assert body["data"]["ready"] is True
    assert body["data"]["robotOnline"] is True
    assert body["data"]["controlEnabled"] is True
    assert body["data"]["robotStateStale"] is False
    assert body["data"]["robotBusy"] is False
    assert body["data"]["activeTask"] == {"active": False, "task_id": None, "task": None, "status": "idle"}
    assert body["data"]["feedback"]["pending"] == 0
    assert body["data"]["feedback"]["sent"] == 0
    assert body["data"]["feedback"]["failed"] == 0


def test_health_endpoint_reports_active_task(client):
    original_move = client.app.state.robot_service.move

    def slow_move(*args, **kwargs):
        time.sleep(0.2)
        return original_move(*args, **kwargs)

    client.app.state.robot_service.move = slow_move
    try:
        response = client.post(
            "/api/events/fall",
            json={
                "event": "fall_detected",
                "elder_id": "001",
                "location": "bedroom",
                "confidence": 0.95,
                "source_event_id": "health-active-fall-001",
                "camera_id": "fixed-camera-01",
            },
        )
        assert response.status_code == 200
        task_id = response.json()["data"]["task_id"]

        data = _wait_for_active_health(client)

        assert data["ready"] is False
        assert data["activeTask"]["active"] is True
        assert data["activeTask"]["task_id"] == task_id
        assert data["activeTask"]["task"] == "confirm_fall"
        assert data["activeTask"]["elder_id"] == "001"
        assert data["activeTask"]["location"] == "bedroom"
        assert data["activeTask"]["confidence"] == 0.95
        assert data["activeTask"]["source_event_id"] == "health-active-fall-001"
        assert data["activeTask"]["camera_id"] == "fixed-camera-01"
        assert data["feedback"]["pending"] >= 0

        _wait_for_task(client, task_id)
    finally:
        client.app.state.robot_service.move = original_move


def test_health_endpoint_reports_control_disabled():
    app = create_app(Settings(mode="mock", control_enabled=False))
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ready"] is False
    assert data["controlEnabled"] is False
    assert data["activeTask"]["active"] is False


def test_preflight_endpoint_reports_dispatch_ready_without_sampling_camera(client):
    response = client.get("/api/preflight")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["service"] == "go2-gateway"
    assert data["mode"] == "mock"
    assert data["ready"] is True
    assert data["dispatch_ready"] is True
    assert data["dispatch_immediate_ready"] is True
    assert data["dispatch_accepting"] is True
    assert data["next_action"] == "dispatch"
    assert data["connection"]["initialized"] is True
    assert data["readiness"]["ready"] is True
    assert data["readiness"]["accepting_tasks"] is True
    assert data["camera"]["sampled"] is False
    assert data["voice"]["ready"] is True
    assert data["voice"]["delivery_mode"] == "mock"
    assert data["checks"]["sdk_initialized"]["ok"] is True
    assert data["checks"]["robot_online"]["ok"] is True
    assert data["checks"]["control_enabled"]["ok"] is True
    assert data["checks"]["dispatch_accepting"]["ok"] is True
    assert data["checks"]["dispatch_idle"]["ok"] is True
    assert data["checks"]["voice_ready"]["ok"] is True
    assert data["checks"]["voice_ready"]["delivery_mode"] == "mock"
    assert data["capabilities"]["status"]["preflight_url"] == "/api/preflight"


def test_preflight_endpoint_reports_readonly_dispatch_guard():
    app = create_app(Settings(mode="mock", control_enabled=False))
    with TestClient(app) as client:
        response = client.get("/api/preflight")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ready"] is False
    assert data["dispatch_ready"] is False
    assert data["dispatch_immediate_ready"] is False
    assert data["dispatch_accepting"] is False
    assert data["next_action"] == "CONTROL_DISABLED"
    assert data["readiness"]["error"] == "CONTROL_DISABLED"
    assert data["readiness"]["acceptance_error"] == "CONTROL_DISABLED"
    assert data["checks"]["control_enabled"]["ok"] is False


def test_preflight_endpoint_reports_missing_http_voice_bridge_without_blocking_robot_readiness():
    app = create_app(Settings(mode="mock", voice_mode="http", voice_prompt_url=""))
    with TestClient(app) as client:
        response = client.get("/api/preflight")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ready"] is True
    assert data["dispatch_ready"] is True
    assert data["dispatch_accepting"] is True
    assert data["voice"]["voice"] == "not_configured"
    assert data["voice"]["ready"] is False
    assert data["voice"]["delivery_mode"] == "http"
    assert data["voice"]["prompt_url_configured"] is False
    assert data["checks"]["voice_ready"]["ok"] is False
    assert data["checks"]["voice_ready"]["mode"] == "http"


def test_preflight_reports_task_acceptance_while_task_is_active(client):
    original_move = client.app.state.robot_service.move
    move_started = threading.Event()
    release_move = threading.Event()

    def slow_move(*args, **kwargs):
        move_started.set()
        assert release_move.wait(timeout=2.0)
        return original_move(*args, **kwargs)

    client.app.state.robot_service.move = slow_move

    response = client.post(
        "/api/events/fall",
        json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 0.95},
    )
    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    assert move_started.wait(timeout=2.0)

    preflight = client.get("/api/preflight").json()["data"]

    assert preflight["ready"] is False
    assert preflight["dispatch_ready"] is False
    assert preflight["dispatch_immediate_ready"] is False
    assert preflight["dispatch_accepting"] is True
    assert preflight["next_action"] == "queue"
    assert preflight["readiness"]["ready"] is False
    assert preflight["readiness"]["accepting_tasks"] is True
    assert preflight["readiness"]["error"] == "CONTROL_BUSY"
    assert preflight["readiness"]["acceptance_error"] is None
    assert preflight["checks"]["dispatch_accepting"]["ok"] is True
    assert preflight["checks"]["dispatch_idle"]["ok"] is False

    release_move.set()
    _wait_for_task(client, task_id)
