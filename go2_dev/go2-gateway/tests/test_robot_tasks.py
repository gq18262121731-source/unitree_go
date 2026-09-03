from __future__ import annotations

import json
import threading
import time

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _wait_for_task(client, task_id: str, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/robot/tasks/{task_id}")
        assert response.status_code == 200
        last = response.json()["data"]
        if last["status"] in {"finished", "failed", "cancelled"}:
            return last
        time.sleep(0.02)
    assert last is not None
    return last


def _wait_for_task_status(client, task_id: str, statuses: set[str], timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/robot/tasks/{task_id}")
        assert response.status_code == 200
        last = response.json()["data"]
        if last["status"] in statuses:
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
    assert finished["task_id"] == finished["taskId"]
    assert finished["camera"] == "ready"
    assert finished["voice"] == "waiting"
    assert finished["source"]["elderId"] == "001"
    assert finished["currentStep"] == "finished"
    assert finished["step"] == ["receive_event", "moving", "arrived", "robot_camera", "voice_check", "finished"]
    assert finished["result"]["robotCamera"]["streamUrl"] == "/api/camera/stream"
    assert finished["result"]["robotCamera"]["snapshotUrl"] == "/api/camera/snapshot"
    assert finished["result"]["confirm"] == "elder_present"
    assert finished["result"]["voicePrompt"] == "您好，请问您现在是否需要帮助？"
    assert finished["result"]["voiceResult"] == "awaiting_response"


def test_health_new_fall_event_endpoint_creates_task(client):
    response = client.post(
        "/api/events/fall",
        json={
            "event": "fall_detected",
            "elder_id": "001",
            "location": "bedroom",
            "confidence": 0.95,
            "source_event_id": "camera-status-summary-001",
            "camera_id": "fixed-camera-status-01",
            "external_task_id": "health-task-status-001",
        },
    )

    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    finished = _wait_for_task(client, task_id)

    assert finished["task"] == "confirm_fall"
    assert finished["status"] == "finished"
    assert finished["camera"] == "ready"
    assert finished["voice"] == "waiting"


def test_fall_event_validation_error_uses_gateway_error_contract(client):
    response = client.post(
        "/api/events/fall",
        json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 1.5},
        headers={"X-Request-ID": "request-validation-001"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["code"] == "INVALID_REQUEST"
    assert body["message"] == "Request validation failed."
    assert body["requestId"] == "request-validation-001"
    assert response.headers["X-Request-ID"] == "request-validation-001"
    assert body["data"]["errors"][0]["type"] in {"less_than_equal", "value_error.number.not_le"}


def test_fall_event_missing_required_field_uses_gateway_error_contract(client):
    response = client.post(
        "/api/events/fall",
        json={"event": "fall_detected", "location": "bedroom", "confidence": 0.95},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["code"] == "INVALID_REQUEST"
    assert body["data"]["errors"][0]["loc"][-1] in {"elderId", "elder_id"}


def test_fall_event_blank_required_fields_are_rejected_without_task(client):
    response = client.post(
        "/api/events/fall",
        json={"event": "fall_detected", "elder_id": "   ", "location": "", "confidence": 0.95},
    )
    tasks = client.get("/api/tasks")

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "INVALID_REQUEST"
    assert tasks.status_code == 200
    assert tasks.json()["data"] == []


def test_fall_event_strips_required_string_fields(client):
    response = client.post(
        "/api/events/fall",
        json={"event": "fall_detected", "elder_id": "  001  ", "location": "  bedroom  ", "confidence": 0.95},
    )

    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    finished = _wait_for_task(client, task_id)
    assert finished["source"]["elderId"] == "001"
    assert finished["location"] == "bedroom"


def test_fall_event_source_event_id_is_idempotent(client):
    payload = {
        "event": "fall_detected",
        "elder_id": "001",
        "location": "bedroom",
        "confidence": 0.95,
        "source_event_id": "camera-fall-001",
    }

    first = client.post("/api/events/fall", json=payload)
    second = client.post("/api/events/fall", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["data"]["task_id"] == first.json()["data"]["task_id"]


def test_idempotent_fall_event_replay_can_attach_callback_url(client):
    payload = {
        "event": "fall_detected",
        "elder_id": "001",
        "location": "bedroom",
        "confidence": 0.95,
        "source_event_id": "camera-fall-callback-replay",
    }

    first = client.post("/api/events/fall", json=payload)
    assert first.status_code == 200
    task_id = first.json()["data"]["task_id"]
    _wait_for_task(client, task_id)

    updates = []

    class Recorder:
        def publish_task_update(self, task):
            updates.append(task)

    client.app.state.task_service.feedback_service = Recorder()
    replay = client.post(
        "/api/events/fall",
        json={**payload, "callback_url": "http://health.local/api/robot/callback"},
    )

    assert replay.status_code == 200
    replay_task = replay.json()["data"]
    assert replay_task["task_id"] == task_id
    assert replay_task["source"]["callbackUrl"] == "http://health.local/api/robot/callback"
    assert replay_task["status"] == "finished"
    assert updates[-1]["task_id"] == task_id
    assert updates[-1]["source"]["callbackUrl"] == "http://health.local/api/robot/callback"


def test_fall_event_external_task_id_is_idempotent_and_queryable(client):
    payload = {
        "event": "fall_detected",
        "elder_id": "001",
        "location": "bedroom",
        "confidence": 0.95,
        "external_task_id": "health-task-idempotent-001",
    }

    first = client.post("/api/events/fall", json=payload)
    assert first.status_code == 200
    task_id = first.json()["data"]["task_id"]
    _wait_for_task(client, task_id)
    move_count = client.app.state.adapter.move_count
    second = client.post("/api/events/fall", json=payload)
    lookup = client.get("/api/tasks/external/health-task-idempotent-001")
    compat_lookup = client.get("/api/robot/tasks/external/health-task-idempotent-001")
    missing_lookup = client.get("/api/tasks/external/health-task-missing")

    assert second.status_code == 200
    assert second.json()["data"]["task_id"] == task_id
    assert client.app.state.adapter.move_count == move_count
    assert lookup.status_code == 200
    data = lookup.json()["data"]
    assert data["received"] is True
    assert data["task_id"] == task_id
    assert data["task"]["external_task_id"] == "health-task-idempotent-001"
    assert data["task"]["status"] == "finished"
    assert compat_lookup.json()["data"]["task_id"] == task_id
    assert missing_lookup.json()["data"] == {
        "external_task_id": "health-task-missing",
        "received": False,
        "task_id": None,
        "task": None,
    }


def test_fall_event_replay_can_attach_external_task_id(client):
    payload = {
        "event": "fall_detected",
        "elder_id": "001",
        "location": "bedroom",
        "confidence": 0.95,
        "source_event_id": "camera-fall-external-replay",
    }

    first = client.post("/api/events/fall", json=payload)
    assert first.status_code == 200
    task_id = first.json()["data"]["task_id"]
    _wait_for_task(client, task_id)
    replay = client.post("/api/events/fall", json={**payload, "external_task_id": "health-task-replay-attach-001"})
    lookup = client.get("/api/tasks/external/health-task-replay-attach-001")

    assert replay.status_code == 200
    replay_task = replay.json()["data"]
    assert replay_task["task_id"] == task_id
    assert replay_task["source"]["externalTaskId"] == "health-task-replay-attach-001"
    assert lookup.json()["data"]["task_id"] == task_id
    assert lookup.json()["data"]["task"]["external_task_id"] == "health-task-replay-attach-001"


def test_fall_event_source_event_id_can_be_queried(client):
    missing = client.get("/api/events/fall/camera-fall-missing")
    assert missing.status_code == 200
    assert missing.json()["data"] == {
        "source_event_id": "camera-fall-missing",
        "received": False,
        "task_id": None,
        "task": None,
    }

    payload = {
        "event": "fall_detected",
        "elder_id": "001",
        "location": "bedroom",
        "confidence": 0.95,
        "source_event_id": "camera-fall-query-001",
    }

    created = client.post("/api/events/fall", json=payload)
    lookup = client.get("/api/events/fall/camera-fall-query-001")
    compat_lookup = client.get("/api/robot/events/fall/camera-fall-query-001")

    assert created.status_code == 200
    task_id = created.json()["data"]["task_id"]
    assert lookup.status_code == 200
    assert compat_lookup.status_code == 200
    data = lookup.json()["data"]
    assert data["source_event_id"] == "camera-fall-query-001"
    assert data["received"] is True
    assert data["task_id"] == task_id
    assert data["task"]["task_id"] == task_id
    assert data["task"]["task"] == "confirm_fall"
    assert data["task"]["source"]["sourceEventId"] == "camera-fall-query-001"
    assert compat_lookup.json()["data"] == data


def test_different_fall_event_queues_while_task_active(client):
    original_move = client.app.state.robot_service.move
    move_started = threading.Event()
    release_move = threading.Event()

    def slow_move(*args, **kwargs):
        move_started.set()
        assert release_move.wait(timeout=2.0)
        return original_move(*args, **kwargs)

    client.app.state.robot_service.move = slow_move

    first = client.post(
        "/api/events/fall",
        json={
            "event": "fall_detected",
            "elder_id": "001",
            "location": "bedroom",
            "confidence": 0.95,
            "source_event_id": "camera-fall-001",
        },
    )
    assert move_started.wait(timeout=2.0)
    second = client.post(
        "/api/events/fall",
        json={
            "event": "fall_detected",
            "elder_id": "001",
            "location": "bedroom",
            "confidence": 0.95,
            "source_event_id": "camera-fall-002",
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    first_task_id = first.json()["data"]["task_id"]
    second_task_id = second.json()["data"]["task_id"]
    assert second_task_id != first_task_id
    assert client.get(f"/api/tasks/{second_task_id}/status").json()["data"]["status"] == "waiting"
    release_move.set()
    assert _wait_for_task(client, first_task_id)["status"] == "finished"
    assert _wait_for_task(client, second_task_id)["status"] == "finished"


def test_task_updates_are_published_to_feedback_service(client):
    updates = []

    class Recorder:
        def publish_task_update(self, task):
            updates.append(task)

    client.app.state.task_service.feedback_service = Recorder()

    response = client.post(
        "/api/events/fall",
        json={
            "event": "fall_detected",
            "elder_id": "001",
            "location": "bedroom",
            "confidence": 0.95,
            "source_event_id": "camera-status-summary-001",
            "camera_id": "fixed-camera-status-01",
            "external_task_id": "health-task-status-001",
        },
    )

    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    _wait_for_task(client, task_id)

    statuses = {update["status"] for update in updates}
    assert "waiting" in statuses
    assert "running" in statuses
    assert "moving" in statuses
    assert "arrived" in statuses
    assert "checking" in statuses
    assert "finished" in statuses
    assert updates[-1]["task_id"] == task_id
    assert updates[-1]["camera"] == "ready"
    assert updates[-1]["voice"] == "waiting"


def test_task_feedback_replay_endpoint_queues_current_task_snapshot(client):
    sent = []
    feedback_service = client.app.state.task_service.feedback_service

    def fake_post(task, callback_url=None):
        sent.append((task["task_id"], task["revision"], task["status"], callback_url))

    feedback_service._post_task_update = fake_post

    response = client.post(
        "/api/tasks/confirm-fall",
        json={
            "task": "confirm_fall",
            "elder_id": "001",
            "location": "bedroom",
            "confidence": 0.95,
            "source_event_id": "camera-feedback-replay-001",
            "task_id": "health-task-feedback-replay-001",
        },
    )

    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    finished = _wait_for_task(client, task_id)
    before_replay_count = len(sent)

    replay = client.post(
        f"/api/tasks/{task_id}/feedback/replay",
        json={"callback_url": "http://health.local/replay"},
    )

    assert replay.status_code == 200
    data = replay.json()["data"]
    assert data["task_id"] == task_id
    assert data["queued"] is True
    assert data["callback_configured"] is True
    assert data["callback_url"] == "http://health.local/replay"
    assert data["revision"] == finished["revision"]

    expected = (task_id, finished["revision"], "finished", "http://health.local/replay")
    deadline = time.monotonic() + 1.0
    while expected not in sent[before_replay_count:] and time.monotonic() < deadline:
        time.sleep(0.01)

    assert expected in sent[before_replay_count:]


def test_task_feedback_replay_endpoint_reports_missing_callback(client):
    response = client.post(
        "/api/events/fall",
        json={
            "event": "fall_detected",
            "elder_id": "001",
            "location": "bedroom",
            "confidence": 0.95,
            "source_event_id": "camera-feedback-replay-no-url",
        },
    )

    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    _wait_for_task(client, task_id)

    replay = client.post(f"/api/robot/tasks/{task_id}/feedback/replay", json={})

    assert replay.status_code == 200
    data = replay.json()["data"]
    assert data["task_id"] == task_id
    assert data["queued"] is False
    assert data["callback_configured"] is False
    assert data["callback_url"] is None


def test_task_feedback_replay_can_use_external_task_id(client):
    sent = []
    feedback_service = client.app.state.task_service.feedback_service

    def fake_post(task, callback_url=None):
        sent.append((task["task_id"], task["revision"], task["status"], callback_url))

    feedback_service._post_task_update = fake_post

    response = client.post(
        "/api/tasks/confirm-fall",
        json={
            "task": "confirm_fall",
            "elder_id": "001",
            "location": "bedroom",
            "confidence": 0.95,
            "source_event_id": "camera-external-feedback-replay-001",
            "task_id": "health-task-external-feedback-replay-001",
        },
    )

    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    finished = _wait_for_task(client, task_id)
    before_replay_count = len(sent)

    replay = client.post(
        "/api/tasks/external/health-task-external-feedback-replay-001/feedback/replay",
        json={"callback_url": "http://health.local/external-replay"},
    )

    assert replay.status_code == 200
    data = replay.json()["data"]
    assert data["external_task_id"] == "health-task-external-feedback-replay-001"
    assert data["task_id"] == task_id
    assert data["queued"] is True
    assert data["revision"] == finished["revision"]

    expected = (task_id, finished["revision"], "finished", "http://health.local/external-replay")
    deadline = time.monotonic() + 1.0
    while expected not in sent[before_replay_count:] and time.monotonic() < deadline:
        time.sleep(0.01)

    assert expected in sent[before_replay_count:]


def test_external_task_id_can_query_status_result_and_timeline_directly(client):
    response = client.post(
        "/api/tasks/confirm-fall",
        json={
            "task": "confirm_fall",
            "elder_id": "001",
            "location": "bedroom",
            "confidence": 0.95,
            "source_event_id": "camera-external-direct-query-001",
            "camera_id": "fixed-camera-external-direct-query-01",
            "task_id": "health-task-external-direct-query-001",
        },
    )

    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    finished = _wait_for_task(client, task_id)

    status_response = client.get("/api/tasks/external/health-task-external-direct-query-001/status")
    result_response = client.get("/api/tasks/external/health-task-external-direct-query-001/result")
    timeline_response = client.get("/api/robot/tasks/external/health-task-external-direct-query-001/timeline")

    assert status_response.status_code == 200
    assert result_response.status_code == 200
    assert timeline_response.status_code == 200

    status = status_response.json()["data"]
    result = result_response.json()["data"]
    timeline = timeline_response.json()["data"]

    assert status["task_id"] == task_id
    assert status["status"] == "finished"
    assert status["finished"] is True
    assert status["external_task_id"] == "health-task-external-direct-query-001"
    assert result["task_id"] == task_id
    assert result["revision"] == finished["revision"]
    assert result["finished"] is True
    assert result["external_task_id"] == "health-task-external-direct-query-001"
    assert result["source_event_id"] == "camera-external-direct-query-001"
    assert timeline["task_id"] == task_id
    assert timeline["status"] == "finished"
    assert timeline["external_task_id"] == "health-task-external-direct-query-001"
    assert timeline["progress"]["percent"] == 100


def test_external_task_direct_query_returns_not_found_for_unknown_task(client):
    status = client.get("/api/tasks/external/health-task-missing/status")
    result = client.get("/api/robot/tasks/external/health-task-missing/result")
    timeline = client.get("/api/tasks/external/health-task-missing/timeline")

    assert status.status_code == 404
    assert result.status_code == 404
    assert timeline.status_code == 404
    assert status.json()["code"] == "TASK_NOT_FOUND"
    assert result.json()["code"] == "TASK_NOT_FOUND"
    assert timeline.json()["code"] == "TASK_NOT_FOUND"


def test_external_task_feedback_replay_returns_not_found_for_unknown_task(client):
    response = client.post(
        "/api/robot/tasks/external/health-task-missing/feedback/replay",
        json={"callback_url": "http://health.local/external-replay"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "TASK_NOT_FOUND"


def test_task_updates_include_monotonic_revision_for_callbacks(client):
    updates = []

    class Recorder:
        def publish_task_update(self, task):
            updates.append(task)

    client.app.state.task_service.feedback_service = Recorder()

    response = client.post(
        "/api/events/fall",
        json={
            "event": "fall_detected",
            "elder_id": "001",
            "location": "bedroom",
            "confidence": 0.95,
            "source_event_id": "camera-timeline-001",
            "camera_id": "fixed-camera-timeline-01",
        },
    )

    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    _wait_for_task(client, task_id)
    result = client.get(f"/api/tasks/{task_id}/result").json()["data"]
    status = client.get(f"/api/tasks/{task_id}/status").json()["data"]
    compat_status = client.get(f"/api/robot/tasks/{task_id}/status").json()["data"]
    timeline = client.get(f"/api/tasks/{task_id}/timeline").json()["data"]

    revisions = [update["revision"] for update in updates]
    assert revisions == sorted(revisions)
    assert len(set(revisions)) == len(revisions)
    assert revisions[0] == 0
    assert result["revision"] == revisions[-1]
    assert status["revision"] == revisions[-1]
    assert compat_status == status
    assert timeline["revision"] == revisions[-1]


def test_task_audit_log_records_lifecycle(tmp_path):
    audit_path = tmp_path / "task-events.jsonl"
    app = create_app(Settings(mode="mock", state_stale_seconds=2.0, task_audit_log_path=str(audit_path)))

    with TestClient(app) as client:
        response = client.post(
            "/api/events/fall",
            json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 0.95},
        )

        assert response.status_code == 200
        task_id = response.json()["data"]["task_id"]
        _wait_for_task(client, task_id)

    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    audit_events = [entry["auditEvent"] for entry in entries]
    assert "created" in audit_events
    assert "finished" in audit_events
    assert entries[-1]["task"]["task_id"] == task_id
    assert entries[-1]["task"]["status"] == "finished"


def test_task_audit_log_records_cancelled_task(tmp_path):
    audit_path = tmp_path / "task-events.jsonl"
    app = create_app(Settings(mode="mock", state_stale_seconds=2.0, task_audit_log_path=str(audit_path)))

    with TestClient(app) as client:
        original_move = client.app.state.robot_service.move

        def slow_move(*args, **kwargs):
            time.sleep(0.2)
            return original_move(*args, **kwargs)

        client.app.state.robot_service.move = slow_move
        response = client.post(
            "/api/events/fall",
            json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 0.95},
        )

        assert response.status_code == 200
        task_id = response.json()["data"]["task_id"]
        cancel_response = client.post(f"/api/robot/tasks/{task_id}/cancel", json={"reason": "audit_cancel"})
        _wait_for_task(client, task_id)

        assert cancel_response.status_code == 200

    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert any(entry["auditEvent"] == "cancelled" for entry in entries)
    assert entries[-1]["task"]["status"] == "cancelled"
    assert entries[-1]["task"]["error"] == "audit_cancel"


def test_task_audit_log_endpoint_returns_recent_entries(tmp_path):
    audit_path = tmp_path / "task-events.jsonl"
    app = create_app(Settings(mode="mock", state_stale_seconds=2.0, task_audit_log_path=str(audit_path)))

    with TestClient(app) as client:
        response = client.post(
            "/api/events/fall",
            json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 0.95},
        )

        assert response.status_code == 200
        task_id = response.json()["data"]["task_id"]
        _wait_for_task(client, task_id)
        audit_response = client.get("/api/tasks/audit-log?limit=2")
        audit_compat_response = client.get("/api/robot/tasks/audit-log?limit=2")

    assert audit_response.status_code == 200
    data = audit_response.json()["data"]
    assert data["enabled"] is True
    assert data["path"] == str(audit_path)
    assert len(data["entries"]) == 2
    assert data["entries"][-1]["auditEvent"] == "finished"
    assert data["entries"][-1]["task"]["task_id"] == task_id
    assert audit_compat_response.status_code == 200
    assert audit_compat_response.json()["data"]["entries"][-1]["task"]["task_id"] == task_id


def test_task_audit_log_endpoint_filters_by_task_and_external_id(tmp_path):
    audit_path = tmp_path / "task-events.jsonl"
    app = create_app(Settings(mode="mock", state_stale_seconds=2.0, task_audit_log_path=str(audit_path)))
    external_task_id = "health-fall-audit-001"

    with TestClient(app) as client:
        response = client.post(
            "/api/events/fall",
            json={
                "event": "fall_detected",
                "elder_id": "001",
                "location": "bedroom",
                "confidence": 0.95,
                "external_task_id": external_task_id,
            },
        )

        assert response.status_code == 200
        task_id = response.json()["data"]["task_id"]
        _wait_for_task(client, task_id)
        task_audit_response = client.get(f"/api/tasks/{task_id}/audit-log")
        task_audit_compat_response = client.get(f"/api/robot/tasks/{task_id}/audit-log")
        external_audit_response = client.get(f"/api/tasks/external/{external_task_id}/audit-log")
        external_audit_compat_response = client.get(f"/api/robot/tasks/external/{external_task_id}/audit-log?limit=1")

    assert task_audit_response.status_code == 200
    task_audit = task_audit_response.json()["data"]
    audit_events = [entry["auditEvent"] for entry in task_audit["entries"]]
    assert task_audit["task_id"] == task_id
    assert "created" in audit_events
    assert "finished" in audit_events
    assert all(entry["task"]["task_id"] == task_id for entry in task_audit["entries"])
    assert task_audit["entries"][-1]["task"]["result"]["confirm"] == "elder_present"

    assert task_audit_compat_response.status_code == 200
    assert task_audit_compat_response.json()["data"]["entries"][-1]["task"]["task_id"] == task_id

    assert external_audit_response.status_code == 200
    external_audit = external_audit_response.json()["data"]
    assert external_audit["external_task_id"] == external_task_id
    assert external_audit["task_id"] == task_id
    assert external_audit["entries"][-1]["task"]["source"]["externalTaskId"] == external_task_id

    assert external_audit_compat_response.status_code == 200
    external_limited = external_audit_compat_response.json()["data"]
    assert len(external_limited["entries"]) == 1
    assert external_limited["entries"][0]["auditEvent"] == "finished"


def test_terminal_task_restores_from_audit_log_after_restart(tmp_path):
    audit_path = tmp_path / "task-events.jsonl"
    settings = Settings(mode="mock", state_stale_seconds=2.0, task_audit_log_path=str(audit_path))
    source_event_id = "camera-restart-001"
    external_task_id = "health-restart-001"

    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/events/fall",
            json={
                "event": "fall_detected",
                "elder_id": "001",
                "location": "bedroom",
                "confidence": 0.95,
                "source_event_id": source_event_id,
                "external_task_id": external_task_id,
            },
        )

        assert response.status_code == 200
        task_id = response.json()["data"]["task_id"]
        finished = _wait_for_task(client, task_id)
        assert finished["status"] == "finished"

    restarted_app = create_app(settings)
    with TestClient(restarted_app) as client:
        external_status = client.get(f"/api/tasks/external/{external_task_id}")
        source_status = client.get(f"/api/events/fall/{source_event_id}")
        result = client.get(f"/api/tasks/external/{external_task_id}/result")
        timeline = client.get(f"/api/tasks/{task_id}/timeline")
        replay = client.post(f"/api/tasks/external/{external_task_id}/feedback/replay", json={})
        duplicate = client.post(
            "/api/events/fall",
            json={
                "event": "fall_detected",
                "elder_id": "001",
                "location": "bedroom",
                "confidence": 0.95,
                "source_event_id": source_event_id,
                "external_task_id": external_task_id,
            },
        )
        tasks = client.get("/api/tasks")

    assert external_status.status_code == 200
    external_data = external_status.json()["data"]
    assert external_data["received"] is True
    assert external_data["task_id"] == task_id
    assert external_data["task"]["status"] == "finished"

    assert source_status.status_code == 200
    source_data = source_status.json()["data"]
    assert source_data["received"] is True
    assert source_data["task_id"] == task_id

    assert result.status_code == 200
    result_data = result.json()["data"]
    assert result_data["task_id"] == task_id
    assert result_data["external_task_id"] == external_task_id
    assert result_data["finished"] is True
    assert result_data["confirm"] == "elder_present"

    assert timeline.status_code == 200
    assert timeline.json()["data"]["task_id"] == task_id

    assert replay.status_code == 200
    assert replay.json()["data"]["task_id"] == task_id

    assert duplicate.status_code == 200
    assert duplicate.json()["data"]["task_id"] == task_id
    assert tasks.status_code == 200
    assert len(tasks.json()["data"]) == 1


def test_task_query_compat_endpoint(client):
    response = client.post("/api/robot/tasks/target-move", json={"location": "bedroom"})

    assert response.status_code == 200
    task_id = response.json()["data"]["taskId"]
    finished = _wait_for_task(client, task_id)
    alias_response = client.get(f"/api/tasks/{task_id}")

    assert alias_response.status_code == 200
    assert alias_response.json()["data"]["taskId"] == finished["taskId"]


def test_task_status_summary_endpoint(client):
    response = client.post(
        "/api/events/fall",
        json={
            "event": "fall_detected",
            "elder_id": "001",
            "location": "bedroom",
            "confidence": 0.95,
            "source_event_id": "camera-status-summary-001",
            "camera_id": "fixed-camera-status-01",
            "external_task_id": "health-task-status-001",
        },
    )

    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    _wait_for_task(client, task_id)
    status_response = client.get(f"/api/tasks/{task_id}/status")

    assert status_response.status_code == 200
    status = status_response.json()["data"]
    assert status["task_id"] == task_id
    assert status["status"] == "finished"
    assert status["elder_id"] == "001"
    assert status["location"] == "bedroom"
    assert status["confidence"] == 0.95
    assert status["source_event_id"] == "camera-status-summary-001"
    assert status["camera_id"] == "fixed-camera-status-01"
    assert status["external_task_id"] == "health-task-status-001"
    assert status["camera"] == "ready"
    assert status["voice"] == "waiting"
    assert status["progress"] == {
        "completed_steps": 6,
        "total_steps": 6,
        "current_index": 6,
        "percent": 100,
    }
    assert status["source"]["event"] == "fall_detected"
    assert status["source"]["externalTaskId"] == "health-task-status-001"
    assert status["result"]["confirm"] == "elder_present"


def test_task_result_endpoint_returns_health_new_result_contract(client):
    response = client.post(
        "/api/events/fall",
        json={
            "event": "fall_detected",
            "elder_id": "001",
            "location": "bedroom",
            "confidence": 0.95,
            "source_event_id": "camera-result-contract-001",
            "camera_id": "fixed-camera-result-01",
            "external_task_id": "health-task-result-001",
        },
    )

    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    _wait_for_task(client, task_id)
    result_response = client.get(f"/api/tasks/{task_id}/result")

    assert result_response.status_code == 200
    result = result_response.json()["data"]
    assert result["task_id"] == task_id
    assert result["task"] == "confirm_fall"
    assert result["status"] == "finished"
    assert result["finished"] is True
    assert result["elder_id"] == "001"
    assert result["location"] == "bedroom"
    assert result["confidence"] == 0.95
    assert result["source_event_id"] == "camera-result-contract-001"
    assert result["camera_id"] == "fixed-camera-result-01"
    assert result["external_task_id"] == "health-task-result-001"
    assert result["location_resolution"]["input"] == "bedroom"
    assert result["location_resolution"]["location"] == "bedroom"
    assert result["location_resolution"]["known"] is True
    assert result["location_resolution"]["fallbackUsed"] is False
    assert result["progress"]["percent"] == 100
    assert result["progress"]["completed_steps"] == result["progress"]["total_steps"]
    assert result["camera"] == "ready"
    assert result["voice"] == "waiting"
    assert result["confirm"] == "elder_present"
    assert result["robot_camera"]["streamUrl"] == "/api/camera/stream"
    assert result["voice_result"] == "awaiting_response"
    assert result["source"]["event"] == "fall_detected"
    assert result["source"]["locationResolution"]["location"] == "bedroom"


def test_task_summary_list_returns_health_new_fields_without_changing_raw_list(client):
    response = client.post(
        "/api/events/fall",
        json={
            "event": "fall_detected",
            "elder_id": "001",
            "location": "bedroom",
            "confidence": 0.95,
            "source_event_id": "camera-summary-list-001",
            "camera_id": "fixed-camera-summary-list-01",
            "external_task_id": "health-task-summary-list-001",
        },
    )

    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    _wait_for_task(client, task_id)
    summary_response = client.get("/api/tasks/summary?limit=1")
    compat_response = client.get("/api/robot/tasks/summary?limit=1")
    raw_response = client.get("/api/tasks?limit=1")

    assert summary_response.status_code == 200
    summaries = summary_response.json()["data"]
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary["task_id"] == task_id
    assert summary["task"] == "confirm_fall"
    assert summary["status"] == "finished"
    assert summary["finished"] is True
    assert summary["elder_id"] == "001"
    assert summary["location"] == "bedroom"
    assert summary["source_event_id"] == "camera-summary-list-001"
    assert summary["camera_id"] == "fixed-camera-summary-list-01"
    assert summary["external_task_id"] == "health-task-summary-list-001"
    assert summary["progress"]["percent"] == 100
    assert summary["result"]["confirm"] == "elder_present"
    assert compat_response.json()["data"][0]["task_id"] == task_id

    raw_task = raw_response.json()["data"][0]
    assert raw_task["taskId"] == task_id
    assert raw_task["source"]["externalTaskId"] == "health-task-summary-list-001"


def test_latest_task_endpoint_returns_empty_contract_before_any_task(client):
    response = client.get("/api/tasks/latest")
    compat_response = client.get("/api/robot/tasks/latest")

    assert response.status_code == 200
    assert compat_response.status_code == 200
    assert response.json()["data"] == {"exists": False, "task_id": None, "task": None, "status": "none"}
    assert compat_response.json()["data"] == response.json()["data"]


def test_latest_task_endpoint_returns_most_recent_summary(client):
    response = client.post(
        "/api/events/fall",
        json={
            "event": "fall_detected",
            "elder_id": "001",
            "location": "bedroom",
            "confidence": 0.95,
            "source_event_id": "camera-latest-task-001",
            "camera_id": "fixed-camera-latest-01",
            "external_task_id": "health-task-latest-001",
        },
    )

    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    _wait_for_task(client, task_id)

    latest_response = client.get("/api/tasks/latest")

    assert latest_response.status_code == 200
    latest = latest_response.json()["data"]
    assert latest["exists"] is True
    assert latest["task_id"] == task_id
    assert latest["task"] == "confirm_fall"
    assert latest["status"] == "finished"
    assert latest["finished"] is True
    assert latest["source_event_id"] == "camera-latest-task-001"
    assert latest["camera_id"] == "fixed-camera-latest-01"
    assert latest["external_task_id"] == "health-task-latest-001"
    assert latest["progress"]["percent"] == 100


def test_latest_task_endpoint_uses_latest_update_not_latest_creation(client):
    first = client.post(
        "/api/events/fall",
        json={
            "event": "fall_detected",
            "elder_id": "001",
            "location": "bedroom",
            "confidence": 0.95,
            "external_task_id": "health-task-latest-update-001",
        },
    )
    assert first.status_code == 200
    fall_task_id = first.json()["data"]["task_id"]
    _wait_for_task(client, fall_task_id)

    second = client.post("/api/tasks/target-move", json={"location": "living_room"})
    assert second.status_code == 200
    move_task_id = second.json()["data"]["task_id"]
    _wait_for_task(client, move_task_id)
    assert client.get("/api/tasks/latest").json()["data"]["task_id"] == move_task_id

    voice = client.post(
        "/api/tasks/external/health-task-latest-update-001/voice-result",
        json={"voice_result": "need_help", "need_help": True},
    )
    latest = client.get("/api/tasks/latest").json()["data"]

    assert voice.status_code == 200
    assert latest["task_id"] == fall_task_id
    assert latest["task"] == "confirm_fall"
    assert latest["voice"] == "completed"
    assert latest["result"]["voiceResult"] == "need_help"


def test_confirm_fall_result_reports_voice_bridge_delivery(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def post(self, url, json):
            calls.append({"url": url, "json": json, "timeout": self.timeout})
            return FakeResponse()

    monkeypatch.setattr("app.services.voice_service.httpx.Client", FakeClient)
    app = create_app(
        Settings(
            mode="mock",
            state_stale_seconds=2.0,
            task_audit_enabled=False,
            voice_mode="http",
            voice_prompt_url="http://audio.local/api/speak",
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/events/fall",
            json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 0.95},
        )

        assert response.status_code == 200
        task_id = response.json()["data"]["task_id"]
        finished = _wait_for_task(client, task_id)
        result_response = client.get(f"/api/tasks/{task_id}/result")

    result = result_response.json()["data"]
    assert finished["voice"] == "waiting"
    assert finished["result"]["voiceDelivery"] == "sent"
    assert result["voice"] == "waiting"
    assert result["voice_delivery"] == "sent"
    assert result["voice_prompt_url"] == "http://audio.local/api/speak"
    assert result["voice_error"] is None
    assert calls[0]["json"]["task_id"] == task_id
    assert calls[0]["json"]["elder_id"] == "001"


def test_confirm_fall_result_reports_voice_bridge_failure(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            raise RuntimeError("speaker offline")

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def post(self, url, json):
            return FakeResponse()

    monkeypatch.setattr("app.services.voice_service.httpx.Client", FakeClient)
    app = create_app(
        Settings(
            mode="mock",
            state_stale_seconds=2.0,
            task_audit_enabled=False,
            voice_mode="http",
            voice_prompt_url="http://audio.local/api/speak",
            voice_prompt_retries=0,
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/events/fall",
            json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 0.95},
        )

        assert response.status_code == 200
        task_id = response.json()["data"]["task_id"]
        finished = _wait_for_task(client, task_id)
        result_response = client.get(f"/api/tasks/{task_id}/result")

    result = result_response.json()["data"]
    assert finished["status"] == "finished"
    assert finished["voice"] == "failed"
    assert finished["result"]["voiceDelivery"] == "failed"
    assert result["voice"] == "failed"
    assert result["voice_delivery"] == "failed"
    assert result["voice_error"] == "speaker offline"


def test_confirm_fall_result_reports_robot_camera_failure(client):
    client.app.state.adapter.bad_camera = True

    response = client.post(
        "/api/events/fall",
        json={
            "event": "fall_detected",
            "elder_id": "001",
            "location": "bedroom",
            "confidence": 0.95,
            "source_event_id": "camera-timeline-001",
            "camera_id": "fixed-camera-timeline-01",
        },
    )

    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    finished = _wait_for_task(client, task_id)
    result_response = client.get(f"/api/tasks/{task_id}/result")

    assert finished["status"] == "finished"
    assert finished["camera"] == "failed"
    assert finished["result"]["robotCamera"]["snapshot"] == "failed"
    assert finished["result"]["robotCamera"]["cameraAvailable"] is False
    assert finished["result"]["observation"]["camera_available"] is False
    assert finished["result"]["cameraError"]

    result = result_response.json()["data"]
    assert result["status"] == "finished"
    assert result["camera"] == "failed"
    assert result["error_code"] is None
    assert result["failure_step"] is None
    assert result["robot_camera"]["snapshot"] == "failed"
    assert result["robot_camera"]["cameraAvailable"] is False
    assert result["observation"]["camera_available"] is False


def test_task_result_robot_path_returns_404_for_missing_task(client):
    response = client.get("/api/robot/tasks/task_missing/result")

    assert response.status_code == 404
    assert response.json()["code"] == "TASK_NOT_FOUND"


def test_task_timeline_endpoint_returns_events_and_steps(client):
    response = client.post(
        "/api/events/fall",
        json={
            "event": "fall_detected",
            "elder_id": "001",
            "location": "bedroom",
            "confidence": 0.95,
            "source_event_id": "camera-timeline-001",
            "camera_id": "fixed-camera-timeline-01",
        },
    )

    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    _wait_for_task(client, task_id)
    timeline_response = client.get(f"/api/tasks/{task_id}/timeline")

    assert timeline_response.status_code == 200
    timeline = timeline_response.json()["data"]
    assert timeline["task_id"] == task_id
    assert timeline["status"] == "finished"
    assert timeline["finished"] is True
    assert timeline["elder_id"] == "001"
    assert timeline["location"] == "bedroom"
    assert timeline["confidence"] == 0.95
    assert timeline["source_event_id"] == "camera-timeline-001"
    assert timeline["camera_id"] == "fixed-camera-timeline-01"
    assert timeline["current_step"] == "finished"
    assert timeline["progress"]["percent"] == 100
    assert [step["name"] for step in timeline["steps"]] == [
        "receive_event",
        "moving",
        "arrived",
        "robot_camera",
        "voice_check",
        "finished",
    ]
    assert [event["step"] for event in timeline["events"]][-1] == "finished"


def test_task_timeline_robot_path_returns_404_for_missing_task(client):
    response = client.get("/api/robot/tasks/task_missing/timeline")

    assert response.status_code == 404
    assert response.json()["code"] == "TASK_NOT_FOUND"


def test_active_task_endpoint_reports_idle(client):
    response = client.get("/api/tasks/active")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["active"] is False
    assert data["task_id"] is None
    assert data["status"] == "idle"


def test_active_task_endpoint_reports_running_task(client):
    original_move = client.app.state.robot_service.move

    def slow_move(*args, **kwargs):
        time.sleep(0.2)
        return original_move(*args, **kwargs)

    client.app.state.robot_service.move = slow_move

    response = client.post(
        "/api/events/fall",
        json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 0.95},
    )
    active_response = client.get("/api/robot/tasks/active")

    assert response.status_code == 200
    assert active_response.status_code == 200
    active = active_response.json()["data"]
    assert active["active"] is True
    assert active["task"] == "confirm_fall"
    assert active["task_id"] == response.json()["data"]["task_id"]
    _wait_for_task(client, active["task_id"])


def test_fall_events_queue_when_robot_task_is_running(client):
    original_move = client.app.state.robot_service.move
    move_started = threading.Event()
    release_move = threading.Event()

    def slow_move(*args, **kwargs):
        move_started.set()
        assert release_move.wait(timeout=2.0)
        return original_move(*args, **kwargs)

    client.app.state.robot_service.move = slow_move

    first = client.post(
        "/api/events/fall",
        json={
            "event": "fall_detected",
            "elder_id": "001",
            "location": "bedroom",
            "confidence": 0.95,
            "source_event_id": "camera-queue-001",
        },
    )
    assert first.status_code == 200
    first_task_id = first.json()["data"]["task_id"]
    assert move_started.wait(timeout=2.0)

    second = client.post(
        "/api/events/fall",
        json={
            "event": "fall_detected",
            "elder_id": "002",
            "location": "bathroom",
            "confidence": 0.91,
            "source_event_id": "camera-queue-002",
        },
    )

    assert second.status_code == 200
    second_task_id = second.json()["data"]["task_id"]
    assert second_task_id != first_task_id
    queued = client.get(f"/api/tasks/{second_task_id}/status").json()["data"]
    assert queued["status"] == "waiting"
    assert queued["progress"]["percent"] == 0
    assert queued["queue_position"] == 2
    assert queued["queue_size"] == 2
    assert queued["queue_head"] is False
    assert queued["blocked_by_task_id"] == first_task_id
    assert queued["queue"] == {
        "position": 2,
        "size": 2,
        "head": False,
        "blockedByTaskId": first_task_id,
    }
    compact = client.get("/api/status").json()["data"]
    assert compact["queue_position"] == 1
    assert compact["queue_size"] == 2
    assert compact["queue_head"] is True
    assert compact["blocked_by_task_id"] is None
    queue_response = client.get("/api/tasks/queue")
    queue_compat_response = client.get("/api/robot/tasks/queue")
    assert queue_response.status_code == 200
    queue = queue_response.json()["data"]
    assert queue["size"] == 2
    assert queue["active"]["task_id"] == first_task_id
    assert queue["active"]["queue_position"] == 1
    assert [task["task_id"] for task in queue["waiting"]] == [second_task_id]
    assert [task["task_id"] for task in queue["tasks"]] == [first_task_id, second_task_id]
    assert queue_compat_response.status_code == 200
    assert queue_compat_response.json()["data"]["tasks"][1]["task_id"] == second_task_id

    release_move.set()
    first_finished = _wait_for_task(client, first_task_id, timeout=3.0)
    second_finished = _wait_for_task(client, second_task_id, timeout=3.0)

    assert first_finished["status"] == "finished"
    assert second_finished["status"] == "finished"
    assert second_finished["source"]["sourceEventId"] == "camera-queue-002"
    assert second_finished["queuePosition"] is None
    assert second_finished["queueSize"] == 0
    assert second_finished["blockedByTaskId"] is None
    empty_queue = client.get("/api/tasks/queue").json()["data"]
    assert empty_queue == {"size": 0, "active": None, "waiting": [], "tasks": []}
    assert client.app.state.adapter.move_count == 3


def test_voice_result_can_be_recorded_after_fall_confirmation(client):
    response = client.post(
        "/api/events/fall",
        json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 0.95},
    )

    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    _wait_for_task(client, task_id)
    voice_response = client.post(
        f"/api/tasks/{task_id}/voice-result",
        json={"voice_result": "需要帮助", "need_help": True},
    )

    assert voice_response.status_code == 200
    task = voice_response.json()["data"]
    assert task["voice"] == "completed"
    assert task["result"]["voiceResult"] == "需要帮助"
    assert task["result"]["needHelp"] is True

    status_response = client.get(f"/api/tasks/{task_id}/status")
    status = status_response.json()["data"]
    assert status["voice"] == "completed"
    assert status["result"]["voiceResult"] == "需要帮助"


def test_voice_result_accepts_camel_case_payload_and_robot_path(client):
    response = client.post(
        "/api/events/fall",
        json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 0.95},
    )

    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    _wait_for_task(client, task_id)
    voice_response = client.post(
        f"/api/robot/tasks/{task_id}/voice-result",
        json={"voiceResult": "暂时不需要", "needHelp": False},
    )

    assert voice_response.status_code == 200
    task = voice_response.json()["data"]
    assert task["voice"] == "completed"
    assert task["result"]["voiceResult"] == "暂时不需要"
    assert task["result"]["needHelp"] is False


def test_voice_result_can_be_recorded_by_external_task_id(client):
    response = client.post(
        "/api/tasks/confirm-fall",
        json={
            "task": "confirm_fall",
            "elder_id": "001",
            "location": "bedroom",
            "confidence": 0.95,
            "source_event_id": "camera-external-voice-result-001",
            "task_id": "health-task-external-voice-result-001",
        },
    )

    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    _wait_for_task(client, task_id)

    voice_response = client.post(
        "/api/tasks/external/health-task-external-voice-result-001/voice-result",
        json={"voice_result": "need_help", "need_help": True},
    )
    compat_response = client.post(
        "/api/robot/tasks/external/health-task-external-voice-result-001/voice-result",
        json={"voiceResult": "still_need_help", "needHelp": True},
    )

    assert voice_response.status_code == 200
    assert compat_response.status_code == 200
    task = compat_response.json()["data"]
    assert task["task_id"] == task_id
    assert task["voice"] == "completed"
    assert task["result"]["voiceResult"] == "still_need_help"
    assert task["result"]["needHelp"] is True


def test_external_voice_result_unknown_task_returns_404(client):
    response = client.post(
        "/api/tasks/external/health-task-missing/voice-result",
        json={"voice_result": "need_help"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "TASK_NOT_FOUND"


def test_early_voice_result_is_not_overwritten_by_voice_check(client):
    original_move = client.app.state.robot_service.move

    def slow_move(*args, **kwargs):
        time.sleep(0.2)
        return original_move(*args, **kwargs)

    client.app.state.robot_service.move = slow_move

    response = client.post(
        "/api/events/fall",
        json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 0.95},
    )

    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    voice_response = client.post(
        f"/api/tasks/{task_id}/voice-result",
        json={"voice_result": "need_help_now", "need_help": True},
    )
    finished = _wait_for_task(client, task_id)

    assert voice_response.status_code == 200
    assert finished["voice"] == "completed"
    assert finished["result"]["voiceResult"] == "need_help_now"
    assert finished["result"]["needHelp"] is True


def test_running_task_can_be_cancelled(client):
    original_move = client.app.state.robot_service.move

    def slow_move(*args, **kwargs):
        time.sleep(0.2)
        return original_move(*args, **kwargs)

    client.app.state.robot_service.move = slow_move

    response = client.post(
        "/api/events/fall",
        json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 0.95},
    )

    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    cancel_response = client.post(f"/api/tasks/{task_id}/cancel", json={"reason": "demo_operator_cancel"})
    cancelled = _wait_for_task(client, task_id)

    assert cancel_response.status_code == 200
    assert cancel_response.json()["data"]["status"] == "cancelled"
    assert cancelled["status"] == "cancelled"
    assert cancelled["error"] == "demo_operator_cancel"
    assert client.app.state.adapter.stop_count >= 1


def test_running_task_can_be_cancelled_by_external_task_id(client):
    original_move = client.app.state.robot_service.move

    def slow_move(*args, **kwargs):
        time.sleep(0.2)
        return original_move(*args, **kwargs)

    client.app.state.robot_service.move = slow_move

    response = client.post(
        "/api/tasks/confirm-fall",
        json={
            "task": "confirm_fall",
            "elder_id": "001",
            "location": "bedroom",
            "confidence": 0.95,
            "source_event_id": "camera-external-cancel-001",
            "task_id": "health-task-external-cancel-001",
        },
    )

    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    cancel_response = client.post(
        "/api/tasks/external/health-task-external-cancel-001/cancel",
        json={"reason": "health_new_cancel"},
    )
    compat_response = client.post(
        "/api/robot/tasks/external/health-task-external-cancel-001/cancel",
        json={"reason": "health_new_cancel_again"},
    )
    cancelled = _wait_for_task(client, task_id)

    assert cancel_response.status_code == 200
    assert compat_response.status_code == 200
    assert cancel_response.json()["data"]["status"] == "cancelled"
    assert compat_response.json()["data"]["status"] == "cancelled"
    assert cancelled["status"] == "cancelled"
    assert cancelled["error"] == "health_new_cancel"
    assert client.app.state.adapter.stop_count >= 1


def test_cancel_unknown_task_returns_404(client):
    response = client.post("/api/tasks/task_missing/cancel", json={"reason": "missing"})

    assert response.status_code == 404
    assert response.json()["code"] == "TASK_NOT_FOUND"


def test_cancel_external_unknown_task_returns_404(client):
    response = client.post("/api/tasks/external/health-task-missing/cancel", json={"reason": "missing"})

    assert response.status_code == 404
    assert response.json()["code"] == "TASK_NOT_FOUND"


def test_voice_result_unknown_task_returns_404(client):
    response = client.post("/api/tasks/task_missing/voice-result", json={"voice_result": "需要帮助"})

    assert response.status_code == 404
    assert response.json()["code"] == "TASK_NOT_FOUND"


def test_voice_result_rejects_cancelled_task(client):
    original_move = client.app.state.robot_service.move

    def slow_move(*args, **kwargs):
        time.sleep(0.2)
        return original_move(*args, **kwargs)

    client.app.state.robot_service.move = slow_move

    response = client.post(
        "/api/events/fall",
        json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 0.95},
    )

    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    cancel_response = client.post(f"/api/tasks/{task_id}/cancel", json={"reason": "operator_cancelled_voice"})
    cancelled = _wait_for_task(client, task_id)
    voice_response = client.post(
        f"/api/tasks/{task_id}/voice-result",
        json={"voice_result": "need_help", "need_help": True},
    )

    assert cancel_response.status_code == 200
    assert cancelled["status"] == "cancelled"
    assert voice_response.status_code == 409
    assert voice_response.json()["code"] == "TASK_STATE_CONFLICT"


def test_voice_result_rejects_failed_task(client):
    def failed_move(*args, **kwargs):
        raise RuntimeError("motion controller unavailable")

    client.app.state.robot_service.move = failed_move

    response = client.post(
        "/api/events/fall",
        json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 0.95},
    )

    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    failed = _wait_for_task(client, task_id)
    voice_response = client.post(
        f"/api/tasks/{task_id}/voice-result",
        json={"voice_result": "need_help", "need_help": True},
    )

    assert failed["status"] == "failed"
    assert voice_response.status_code == 409
    assert voice_response.json()["code"] == "TASK_STATE_CONFLICT"


def test_follow_and_patrol_are_reserved_not_implemented(client):
    follow = client.post("/api/tasks/follow", json={"target": "elder001"})
    patrol = client.post("/api/tasks/patrol", json={"route": "night"})

    assert follow.status_code == 501
    assert follow.json()["code"] == "TASK_NOT_SUPPORTED"
    assert patrol.status_code == 501
    assert patrol.json()["code"] == "TASK_NOT_SUPPORTED"


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
            "callbackUrl": "http://health.local/api/robot/callback",
        },
    )

    assert response.status_code == 200
    task_id = response.json()["data"]["taskId"]
    finished = _wait_for_task(client, task_id)

    assert finished["status"] == "finished"
    assert finished["source"]["elderId"] == "elder-002"
    assert finished["source"]["cameraId"] == "fixed-cam-1"
    assert finished["source"]["callbackUrl"] == "http://health.local/api/robot/callback"
    status = client.get(f"/api/tasks/{task_id}/status").json()["data"]
    result = client.get(f"/api/tasks/{task_id}/result").json()["data"]
    assert status["elder_id"] == "elder-002"
    assert status["location"] == "living_room"
    assert status["source_event_id"] == "camera-service-2"
    assert status["camera_id"] == "fixed-cam-1"
    assert result["elder_id"] == "elder-002"
    assert result["source_event_id"] == "camera-service-2"


def test_fall_event_accepts_camera_event_id_alias_for_idempotency(client):
    payload = {
        "event": "fall_detected",
        "elder_id": "elder-003",
        "location": "bedroom",
        "confidence": 0.91,
        "event_id": "camera-service-event-alias-001",
    }

    first = client.post("/api/events/fall", json=payload)
    assert first.status_code == 200
    task_id = first.json()["data"]["task_id"]
    finished = _wait_for_task(client, task_id)
    move_count = client.app.state.adapter.move_count
    second = client.post("/api/events/fall", json={**payload, "eventId": "camera-service-event-alias-001"})
    lookup = client.get("/api/events/fall/camera-service-event-alias-001")
    status = client.get(f"/api/tasks/{task_id}/status").json()["data"]

    assert finished["source"]["sourceEventId"] == "camera-service-event-alias-001"
    assert status["source_event_id"] == "camera-service-event-alias-001"
    assert second.status_code == 200
    assert second.json()["data"]["task_id"] == task_id
    assert client.app.state.adapter.move_count == move_count
    assert lookup.json()["data"]["received"] is True
    assert lookup.json()["data"]["task_id"] == task_id


def test_confirm_fall_task_endpoint_accepts_health_new_robot_task_payload(client):
    response = client.post(
        "/api/tasks/confirm-fall",
        json={
            "task": "confirm_fall",
            "elder_id": "001",
            "location": "卧室",
            "confidence": 0.96,
            "source_event_id": "camera-confirm-task-001",
            "camera_id": "fixed-camera-confirm-01",
            "taskId": "health-confirm-task-001",
        },
    )

    assert response.status_code == 200
    task_id = response.json()["data"]["task_id"]
    finished = _wait_for_task(client, task_id)
    result = client.get(f"/api/tasks/{task_id}/result").json()["data"]
    lookup = client.get("/api/tasks/external/health-confirm-task-001").json()["data"]

    assert finished["task"] == "confirm_fall"
    assert finished["status"] == "finished"
    assert finished["source"]["externalTaskId"] == "health-confirm-task-001"
    assert finished["location"] == "卧室"
    assert result["location_resolution"]["location"] == "bedroom"
    assert result["location_resolution"]["input"] == "卧室"
    assert result["source_event_id"] == "camera-confirm-task-001"
    assert result["camera_id"] == "fixed-camera-confirm-01"
    assert lookup["task_id"] == task_id
    assert client.app.state.adapter.moves == [(0.1, 0.0, 0.0)]


def test_confirm_fall_task_endpoint_is_idempotent_by_external_task_id(client):
    payload = {
        "task": "confirm_fall",
        "elderId": "001",
        "location": "bedroom",
        "taskId": "health-confirm-idempotent-001",
    }

    first = client.post("/api/tasks/confirm-fall", json=payload)
    second = client.post("/api/robot/tasks/confirm-fall", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["data"]["task_id"] == first.json()["data"]["task_id"]
    _wait_for_task(client, first.json()["data"]["task_id"])
    assert client.app.state.adapter.move_count == 1


def test_confirm_fall_task_endpoint_rejects_dispatch_when_control_disabled():
    app = create_app(Settings(mode="mock", control_enabled=False, task_audit_enabled=False))
    with TestClient(app) as client:
        response = client.post(
            "/api/tasks/confirm-fall",
            json={"task": "confirm_fall", "elder_id": "001", "location": "bedroom", "confidence": 0.95},
        )
        tasks = client.get("/api/tasks").json()["data"]

        assert response.status_code == 403
        assert response.json()["code"] == "CONTROL_DISABLED"
        assert tasks == []
        assert client.app.state.adapter.move_count == 0


def test_fall_event_rejects_dispatch_when_robot_offline(client):
    client.app.state.adapter.set_online(False)

    response = client.post(
        "/api/events/fall",
        json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 0.95},
    )
    tasks = client.get("/api/tasks").json()["data"]

    assert response.status_code == 503
    assert response.json()["code"] == "ROBOT_OFFLINE"
    assert tasks == []
    assert client.app.state.adapter.move_count == 0


def test_real_fall_event_is_saved_as_blocked_when_dds_state_is_missing(client):
    client.app.state.adapter.set_online(False)
    real_settings = Settings(mode="real", task_audit_enabled=False)
    client.app.state.robot_service.settings = real_settings
    client.app.state.task_service.settings = real_settings

    response = client.post(
        "/api/events/fall",
        json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 0.95},
    )
    tasks = client.get("/api/tasks").json()["data"]

    assert response.status_code == 200
    task = response.json()["data"]
    assert task["status"] == "BLOCKED_ROBOT_OFFLINE"
    assert task["currentStep"] == "waiting"
    assert task["result"]["errorCode"] == "DDS_NOT_READY"
    assert len(tasks) == 1
    assert tasks[0]["status"] == "BLOCKED_ROBOT_OFFLINE"
    assert client.get("/api/tasks/queue").json()["data"] == {"size": 0, "active": None, "waiting": [], "tasks": []}
    assert client.app.state.adapter.move_count == 0


def test_fall_event_rejects_dispatch_when_gateway_uninitialized(client):
    client.app.state.gateway.close()

    response = client.post(
        "/api/events/fall",
        json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 0.95},
    )
    tasks = client.get("/api/tasks").json()["data"]

    assert response.status_code == 503
    assert response.json()["code"] == "SDK_NOT_INITIALIZED"
    assert tasks == []


def test_fall_event_rejects_dispatch_when_control_disabled():
    app = create_app(Settings(mode="mock", control_enabled=False, task_audit_enabled=False))
    with TestClient(app) as client:
        response = client.post(
            "/api/events/fall",
            json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 0.95},
        )
        tasks = client.get("/api/tasks").json()["data"]

        assert response.status_code == 403
        assert response.json()["code"] == "CONTROL_DISABLED"
        assert tasks == []
        assert client.app.state.adapter.move_count == 0


def test_target_move_task_uses_same_task_status_contract(client):
    response = client.post("/api/robot/tasks/target-move", json={"location": "bathroom"})

    assert response.status_code == 200
    task_id = response.json()["data"]["taskId"]
    finished = _wait_for_task(client, task_id)

    assert finished["task"] == "move_to_target"
    assert finished["status"] == "finished"
    assert finished["step"] == ["receive_task", "moving", "arrived", "finished"]


def test_target_move_task_accepts_plain_api_path(client):
    response = client.post("/api/tasks/target-move", json={"location": "living_room"})

    assert response.status_code == 200
    task_id = response.json()["data"]["taskId"]
    finished = _wait_for_task(client, task_id)

    assert finished["task"] == "move_to_target"
    assert finished["status"] == "finished"
    assert finished["location"] == "living_room"


def test_voice_result_rejects_non_fall_task(client):
    response = client.post("/api/robot/tasks/target-move", json={"location": "bathroom"})

    assert response.status_code == 200
    task_id = response.json()["data"]["taskId"]
    _wait_for_task(client, task_id)
    voice_response = client.post(
        f"/api/tasks/{task_id}/voice-result",
        json={"voice_result": "need_help", "need_help": True},
    )
    task_response = client.get(f"/api/tasks/{task_id}")

    assert voice_response.status_code == 409
    assert voice_response.json()["code"] == "TASK_STATE_CONFLICT"
    task = task_response.json()["data"]
    assert task["task"] == "move_to_target"
    assert task["voice"] == "idle"
    assert "voiceResult" not in task["result"]


def test_target_move_rejects_dispatch_when_robot_offline(client):
    client.app.state.adapter.set_online(False)

    response = client.post("/api/robot/tasks/target-move", json={"location": "bathroom"})
    tasks = client.get("/api/tasks").json()["data"]

    assert response.status_code == 503
    assert response.json()["code"] == "ROBOT_OFFLINE"
    assert tasks == []
    assert client.app.state.adapter.move_count == 0


def test_target_move_rejects_dispatch_when_control_disabled():
    app = create_app(Settings(mode="mock", control_enabled=False, task_audit_enabled=False))
    with TestClient(app) as client:
        response = client.post("/api/tasks/target-move", json={"location": "bathroom"})
        tasks = client.get("/api/tasks").json()["data"]

        assert response.status_code == 403
        assert response.json()["code"] == "CONTROL_DISABLED"
        assert tasks == []
        assert client.app.state.adapter.move_count == 0


def test_target_move_task_uses_configured_motion_plan():
    app = create_app(
        Settings(
            mode="mock",
            state_stale_seconds=2.0,
            task_audit_enabled=False,
            location_motion_plans_json='{"demo_room":[[0.05,0,0,0.05],[0,0,0.1,0.05]]}',
        )
    )

    with TestClient(app) as client:
        response = client.post("/api/robot/tasks/target-move", json={"location": "demo_room"})

        assert response.status_code == 200
        _wait_for_task(client, response.json()["data"]["taskId"])
        assert client.app.state.adapter.moves == [(0.05, 0.0, 0.0), (0.0, 0.0, 0.1)]


def test_target_move_task_accepts_chinese_location_alias(client):
    response = client.post("/api/tasks/target-move", json={"location": "卧室"})

    assert response.status_code == 200
    task_id = response.json()["data"]["taskId"]
    finished = _wait_for_task(client, task_id)

    assert finished["status"] == "finished"
    assert finished["location"] == "卧室"
    assert finished["result"]["locationResolution"]["input"] == "卧室"
    assert finished["result"]["locationResolution"]["location"] == "bedroom"
    assert finished["result"]["locationResolution"]["fallbackUsed"] is False
    assert client.app.state.adapter.moves == [(0.1, 0.0, 0.0)]


def test_configured_motion_plan_accepts_chinese_location_key():
    app = create_app(
        Settings(
            mode="mock",
            state_stale_seconds=2.0,
            task_audit_enabled=False,
            location_motion_plans_json='{"卧室":[[0.06,0,0,0.05]]}',
        )
    )

    with TestClient(app) as client:
        resolve_response = client.get("/api/locations/resolve", params={"location": "bedroom"})
        task_response = client.post("/api/tasks/target-move", json={"location": "卧室"})

        assert resolve_response.status_code == 200
        assert resolve_response.json()["data"]["source"] == "configured"
        assert task_response.status_code == 200
        _wait_for_task(client, task_response.json()["data"]["taskId"])
        assert client.app.state.adapter.moves == [(0.06, 0.0, 0.0)]


def test_target_move_queues_while_fall_task_active(client):
    original_move = client.app.state.robot_service.move
    move_started = threading.Event()
    release_move = threading.Event()

    def slow_move(*args, **kwargs):
        move_started.set()
        assert release_move.wait(timeout=2.0)
        return original_move(*args, **kwargs)

    client.app.state.robot_service.move = slow_move
    try:
        first = client.post(
            "/api/robot/events/fall",
            json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 0.94},
        )
        assert first.status_code == 200
        first_task_id = first.json()["data"]["task_id"]
        assert move_started.wait(timeout=2.0)

        second = client.post("/api/robot/tasks/target-move", json={"location": "bathroom"})

        assert second.status_code == 200
        second_task_id = second.json()["data"]["task_id"]
        assert client.get(f"/api/tasks/{second_task_id}/status").json()["data"]["status"] == "waiting"
        release_move.set()
        assert _wait_for_task(client, first_task_id)["status"] == "finished"
        assert _wait_for_task(client, second_task_id)["status"] == "finished"
    finally:
        release_move.set()
        client.app.state.robot_service.move = original_move


def test_unknown_task_returns_404(client):
    response = client.get("/api/robot/tasks/task_missing")

    assert response.status_code == 404
    assert response.json()["code"] == "TASK_NOT_FOUND"
