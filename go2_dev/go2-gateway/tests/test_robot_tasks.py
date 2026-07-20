from __future__ import annotations

import time


def _wait_for_task(client, task_id: str, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/robot/tasks/{task_id}")
        assert response.status_code == 200
        last = response.json()["data"]
        if last["status"] in {"finished", "failed"}:
            return last
        time.sleep(0.02)
    assert last is not None
    return last


def test_fall_event_creates_confirmation_task(client):
    response = client.post(
        "/api/robot/events/fall",
        json={
            "event": "fall_detected",
            "elder_id": "001",
            "location": "bedroom",
            "confidence": 0.94,
            "source_event_id": "cam-event-1",
        },
    )

    assert response.status_code == 200
    task = response.json()["data"]
    finished = _wait_for_task(client, task["taskId"])

    assert finished["task"] == "confirm_fall"
    assert finished["priority"] == "high"
    assert finished["status"] == "finished"
    assert finished["source"]["elderId"] == "001"
    assert finished["currentStep"] == "finished"
    assert finished["step"] == ["receive_event", "moving", "arrived", "robot_camera", "voice_check", "finished"]
    assert finished["result"]["robotCamera"]["streamUrl"] == "/api/robot/camera/snapshot"
    assert finished["result"]["confirm"] == "elder_present"
    assert finished["result"]["voiceResult"] == "awaiting_response"


def test_fall_event_accepts_camel_case_payload(client):
    response = client.post(
        "/api/robot/events/fall",
        json={
            "event": "fall_detected",
            "elderId": "elder-002",
            "location": "living_room",
            "confidence": 0.9,
            "sourceEventId": "camera-service-2",
            "cameraId": "fixed-cam-1",
        },
    )

    assert response.status_code == 200
    task_id = response.json()["data"]["taskId"]
    finished = _wait_for_task(client, task_id)

    assert finished["status"] == "finished"
    assert finished["source"]["elderId"] == "elder-002"
    assert finished["source"]["cameraId"] == "fixed-cam-1"


def test_target_move_task_uses_same_task_status_contract(client):
    response = client.post("/api/robot/tasks/target-move", json={"location": "bathroom"})

    assert response.status_code == 200
    task_id = response.json()["data"]["taskId"]
    finished = _wait_for_task(client, task_id)

    assert finished["task"] == "move_to_target"
    assert finished["status"] == "finished"
    assert finished["step"] == ["receive_task", "moving", "arrived", "finished"]


def test_task_conflict_is_reported(client):
    original_move = client.app.state.robot_service.move

    def slow_move(*args, **kwargs):
        time.sleep(0.2)
        return original_move(*args, **kwargs)

    client.app.state.robot_service.move = slow_move

    first = client.post(
        "/api/robot/events/fall",
        json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 0.94},
    )
    second = client.post("/api/robot/tasks/target-move", json={"location": "bathroom"})

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["code"] == "CONTROL_BUSY"


def test_unknown_task_returns_404(client):
    response = client.get("/api/robot/tasks/task_missing")

    assert response.status_code == 404
    assert response.json()["code"] == "TASK_NOT_FOUND"
