from __future__ import annotations

import logging
import time

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_shutdown_calls_stop():
    app = create_app(Settings(mode="mock"))
    with TestClient(app) as client:
        adapter = client.app.state.adapter
        before = adapter.stop_count

    assert adapter.stop_count > before


def test_shutdown_cancels_active_task(caplog):
    caplog.set_level(logging.ERROR, logger="go2_gateway.tasks")
    app = create_app(Settings(mode="mock", task_audit_enabled=False))

    with TestClient(app) as client:
        original_move = client.app.state.robot_service.move

        def slow_move(*args, **kwargs):
            time.sleep(0.3)
            return original_move(*args, **kwargs)

        client.app.state.robot_service.move = slow_move
        response = client.post(
            "/api/events/fall",
            json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 0.95},
        )
        task_id = response.json()["data"]["task_id"]
        assert response.status_code == 200

    task = app.state.task_service.get_task(task_id)
    assert task["status"] == "cancelled"
    assert task["currentStep"] == "cancelled"
    assert task["error"] == "gateway_shutdown"
    assert app.state.task_service.worker_count() == 0
    assert not [record for record in caplog.records if record.name == "go2_gateway.tasks" and "task failed" in record.message]
