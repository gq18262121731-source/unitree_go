from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_offline_robot_rejects_move(client):
    client.app.state.adapter.set_online(False)

    response = client.post("/api/robot/move", json={"vx": 0.1, "vy": 0, "wz": 0, "duration": 0.3})

    assert response.status_code == 503
    assert response.json()["code"] == "ROBOT_OFFLINE"


@pytest.mark.parametrize(
    "payload",
    [
        {"vx": 0.21, "vy": 0, "wz": 0, "duration": 0.3},
        {"vx": 0, "vy": 0.16, "wz": 0, "duration": 0.3},
        {"vx": 0, "vy": 0, "wz": 0.36, "duration": 0.3},
        {"vx": 0, "vy": 0, "wz": 0, "duration": 1.01},
    ],
)
def test_motion_limits_are_enforced(client, payload):
    response = client.post("/api/robot/move", json=payload)

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_MOTION_PARAMETER"


def test_move_success_auto_stops(client):
    adapter = client.app.state.adapter

    response = client.post("/api/robot/move", json={"vx": 0.1, "vy": 0, "wz": 0, "duration": 0.05})

    assert response.status_code == 200
    assert adapter.move_count == 1
    assert adapter.stop_count >= 1


def test_move_failure_still_stops(client):
    adapter = client.app.state.adapter
    adapter.fail_next_move = True
    before = adapter.stop_count

    response = client.post("/api/robot/move", json={"vx": 0.1, "vy": 0, "wz": 0, "duration": 0.05})

    assert response.status_code == 503
    assert response.json()["code"] == "SDK_COMMAND_FAILED"
    assert adapter.stop_count > before


def test_control_disabled_rejects_motion_commands():
    app = create_app(Settings(mode="mock", control_enabled=False))
    with TestClient(app) as client:
        adapter = client.app.state.adapter

        move_response = client.post("/api/robot/move", json={"vx": 0.1, "vy": 0, "wz": 0, "duration": 0.05})
        stand_response = client.post("/api/robot/stand")

        assert move_response.status_code == 403
        assert stand_response.status_code == 403
        assert adapter.move_count == 0
        assert adapter.stand_count == 0
