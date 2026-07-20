from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_status_endpoint_returns_robot_state(client):
    response = client.get("/api/robot/status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["robotId"] == "go2-edu-001"
    assert data["online"] is True
    assert data["motion"]["velocityX"] == 0.0
    assert data["battery"]["voltage"] == 31.2


def test_offline_status(client):
    client.app.state.adapter.set_online(False)

    response = client.get("/api/robot/status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["online"] is False
    assert data["stateStale"] is True


def test_status_reports_control_disabled():
    app = create_app(Settings(mode="mock", control_enabled=False))
    with TestClient(app) as client:
        response = client.get("/api/robot/status")

    assert response.status_code == 200
    assert response.json()["data"]["control"]["enabled"] is False
