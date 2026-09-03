from __future__ import annotations

from app.config import Settings
from app.services.feedback_service import HealthNewFeedbackService


def test_health_new_feedback_posts_task_update(monkeypatch):
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

        def post(self, url, json, headers):
            calls.append({"url": url, "json": json, "headers": headers, "timeout": self.timeout})
            return FakeResponse()

    monkeypatch.setattr("app.services.feedback_service.httpx.Client", FakeClient)
    service = HealthNewFeedbackService(
        Settings(
            mode="mock",
            health_new_callback_url="http://health.local/api/robot/callback",
            health_new_callback_token="token-123",
            health_new_callback_timeout_seconds=1.5,
        )
    )

    service._post_task_update(
        {
            "task_id": "fall_001",
            "robotId": "go2-edu-001",
            "task": "confirm_fall",
            "status": "arrived",
            "revision": 3,
            "currentStep": "arrived",
            "steps": [
                {"name": "receive_event", "status": "done"},
                {"name": "moving", "status": "done"},
                {"name": "arrived", "status": "done"},
                {"name": "robot_camera", "status": "pending"},
            ],
            "camera": "ready",
            "voice": "waiting",
            "source": {
                "elderId": "001",
                "location": "bedroom",
                "confidence": 0.94,
                "sourceEventId": "camera-fall-001",
                "cameraId": "fixed-camera-01",
                "externalTaskId": "health-task-callback-001",
            },
            "result": {"confirm": "elder_present"},
            "error": None,
            "updatedAt": "2026-07-20T10:00:00+08:00",
        }
    )

    assert len(calls) == 1
    assert calls[0]["url"] == "http://health.local/api/robot/callback"
    assert calls[0]["headers"] == {"Authorization": "Bearer token-123"}
    assert calls[0]["timeout"] == 1.5
    payload = calls[0]["json"]
    assert payload["callback_id"].startswith("cb_")
    assert payload["sequence"] == 1
    assert payload["task_id"] == "fall_001"
    assert payload["robot_id"] == "go2-edu-001"
    assert payload["elder_id"] == "001"
    assert payload["location"] == "bedroom"
    assert payload["confidence"] == 0.94
    assert payload["source_event_id"] == "camera-fall-001"
    assert payload["camera_id"] == "fixed-camera-01"
    assert payload["external_task_id"] == "health-task-callback-001"
    assert payload["location_resolution"] is None
    assert payload["task"] == "confirm_fall"
    assert payload["status"] == "arrived"
    assert payload["status_v2"] == "RUNNING"
    assert payload["legacy_status"] == "arrived"
    assert payload["revision"] == 3
    assert payload["queue"] == {}
    assert payload["step"] == "arrived"
    assert payload["legacy_step"] == "arrived"
    assert payload["steps"] == [
        {"name": "receive_event", "status": "done"},
        {"name": "moving", "status": "done"},
        {"name": "arrived", "status": "done"},
        {"name": "robot_camera", "status": "pending"},
    ]
    assert payload["progress"] == {
        "completed_steps": 3,
        "total_steps": 4,
        "current_index": 3,
        "percent": 75,
    }
    assert payload["camera"] == "ready"
    assert payload["voice"] == "waiting"
    assert payload["result"] == {"confirm": "elder_present"}
    assert payload["error"] is None
    assert payload["finished"] is False
    assert payload["updated_at"] == "2026-07-20T10:00:00+08:00"
    status = service.status()
    assert status["configured"] is True
    assert status["sent"] == 1
    assert status["failed"] == 0
    assert status["last_success_at"] is not None
    assert status["last_error"] is None


def test_health_new_feedback_retries_failed_post(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, should_fail):
            self.should_fail = should_fail

        def raise_for_status(self):
            if self.should_fail:
                raise RuntimeError("temporary failure")

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def post(self, url, json, headers):
            calls.append({"url": url, "json": json, "headers": headers, "timeout": self.timeout})
            return FakeResponse(should_fail=len(calls) == 1)

    monkeypatch.setattr("app.services.feedback_service.httpx.Client", FakeClient)
    service = HealthNewFeedbackService(
        Settings(
            mode="mock",
            health_new_callback_url="http://health.local/api/robot/callback",
            health_new_callback_retries=1,
            health_new_callback_retry_delay_seconds=0,
        )
    )

    service._post_task_update(
        {
            "task_id": "fall_002",
            "task": "confirm_fall",
            "status": "finished",
            "camera": "ready",
            "voice": "waiting",
            "source": {"sourceEventId": "camera-fall-002"},
            "result": {"confirm": "elder_present"},
            "error": None,
            "updatedAt": "2026-07-20T10:01:00+08:00",
        }
    )

    assert len(calls) == 2
    assert calls[0]["json"] == calls[1]["json"]
    assert calls[1]["json"]["finished"] is True
    status = service.status()
    assert status["sent"] == 1
    assert status["failed"] == 0


def test_health_new_feedback_uses_task_callback_url(monkeypatch):
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

        def post(self, url, json, headers):
            calls.append({"url": url, "json": json, "headers": headers})
            return FakeResponse()

    monkeypatch.setattr("app.services.feedback_service.httpx.Client", FakeClient)
    service = HealthNewFeedbackService(Settings(mode="mock"))

    service._post_task_update(
        {
            "task_id": "fall_003",
            "task": "confirm_fall",
            "status": "arrived",
            "camera": "ready",
            "voice": "waiting",
            "source": {"callbackUrl": "http://health.local/task-specific-callback"},
            "result": {},
            "error": None,
            "updatedAt": "2026-07-20T10:02:00+08:00",
        }
    )

    assert calls[0]["url"] == "http://health.local/task-specific-callback"


def test_health_new_feedback_publish_sends_updates_in_fifo_order():
    sent = []
    service = HealthNewFeedbackService(
        Settings(mode="mock", health_new_callback_url="http://health.local/api/robot/callback")
    )

    def fake_post(task, callback_url=None):
        sent.append((task["revision"], callback_url))

    service._post_task_update = fake_post

    for revision in range(4):
        service.publish_task_update(
            {
                "task_id": "fall_ordered",
                "task": "confirm_fall",
                "status": "running",
                "revision": revision,
                "source": {},
            }
        )
    service.close()

    assert sent == [
        (0, "http://health.local/api/robot/callback"),
        (1, "http://health.local/api/robot/callback"),
        (2, "http://health.local/api/robot/callback"),
        (3, "http://health.local/api/robot/callback"),
    ]


def test_health_new_feedback_can_replay_to_explicit_callback_url():
    sent = []
    service = HealthNewFeedbackService(Settings(mode="mock"))

    def fake_post(task, callback_url=None):
        sent.append((task["task_id"], task["revision"], callback_url))

    service._post_task_update = fake_post

    queued = service.publish_task_update_to(
        {
            "task_id": "fall_replay",
            "task": "confirm_fall",
            "status": "finished",
            "revision": 7,
            "source": {},
        },
        "http://health.local/replay",
    )
    service.close()

    assert queued is True
    assert sent == [("fall_replay", 7, "http://health.local/replay")]


def test_health_new_feedback_close_drops_late_updates():
    sent = []
    service = HealthNewFeedbackService(
        Settings(mode="mock", health_new_callback_url="http://health.local/api/robot/callback")
    )

    def fake_post(task, callback_url=None):
        sent.append(task["revision"])

    service._post_task_update = fake_post
    service.close()
    service.publish_task_update(
        {
            "task_id": "fall_closed",
            "task": "confirm_fall",
            "status": "running",
            "revision": 1,
            "source": {},
        }
    )

    assert sent == []
    status = service.status()
    assert status["closed"] is True
    assert status["dropped"] == 1


def test_health_new_feedback_status_reports_failed_delivery(monkeypatch):
    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def post(self, url, json, headers):
            raise RuntimeError("health callback down")

    monkeypatch.setattr("app.services.feedback_service.httpx.Client", FakeClient)
    service = HealthNewFeedbackService(
        Settings(
            mode="mock",
            health_new_callback_url="http://health.local/api/robot/callback",
            health_new_callback_retries=0,
        )
    )

    service._post_task_update(
        {
            "task_id": "fall_failed_delivery",
            "task": "confirm_fall",
            "status": "finished",
            "source": {},
        }
    )

    status = service.status()
    assert status["failed"] == 1
    assert status["sent"] == 0
    assert status["last_failure_at"] is not None
    assert status["last_error"] == "health callback down"
