from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.errors import ErrorCode, GatewayError
from app.main import create_app


def test_offline_robot_rejects_move(client):
    client.app.state.adapter.set_online(False)

    response = client.post("/api/robot/move", json={"vx": 0.1, "vy": 0, "wz": 0, "duration": 0.3})

    assert response.status_code == 503
    assert response.json()["code"] == "ROBOT_OFFLINE"


def test_real_mode_without_dds_state_rejects_move_as_dds_not_ready(client):
    client.app.state.adapter.set_online(False)
    real_settings = Settings(mode="real")
    client.app.state.robot_service.settings = real_settings
    client.app.state.task_service.settings = real_settings

    response = client.post("/api/robot/move", json={"vx": 0.1, "vy": 0, "wz": 0, "duration": 0.3})

    assert response.status_code == 503
    assert response.json()["code"] == "DDS_NOT_READY"
    assert client.app.state.adapter.move_count == 0


def test_verified_non_dds_motion_transport_can_refresh_without_state(
    client, monkeypatch
):
    adapter = client.app.state.adapter
    service = client.app.state.robot_service
    adapter.set_online(False)
    monkeypatch.setattr(service.gateway, "motion_transport_ready", lambda: True)

    response = service.refresh_velocity(0.1, 0.0, 0.0, source="test")

    assert response == {"code": 0}
    assert adapter.move_count == 1
    service.safe_stop("test:complete")


@pytest.mark.parametrize(
    "payload",
    [
        {"vx": 0.31, "vy": 0, "wz": 0, "duration": 0.3},
        {"vx": 0, "vy": 0.01, "wz": 0, "duration": 0.3},
        {"vx": 0, "vy": 0, "wz": 0.31, "duration": 0.3},
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


def test_velocity_refresh_does_not_stop_until_safety_requests_it(client):
    adapter = client.app.state.adapter
    service = client.app.state.robot_service
    before = adapter.stop_count

    response = service.refresh_velocity(0.1, 0.0, 0.0, source="test")

    assert response == {"code": 0}
    assert adapter.move_count == 1
    assert adapter.stop_count == before

    service.safe_stop("test:complete")
    assert adapter.stop_count == before + 1


def test_supervised_loop_can_refresh_watchdog_before_wireless_rpc(client, monkeypatch):
    service = client.app.state.robot_service
    owner = "wireless_uwb_follow"
    heartbeats = []
    service.acquire_exclusive_control(owner)
    monkeypatch.setattr(
        service.watchdog,
        "heartbeat",
        lambda: heartbeats.append("healthy"),
    )

    service.refresh_control_heartbeat(owner)

    assert heartbeats == ["healthy"]
    with pytest.raises(GatewayError) as exc_info:
        service.refresh_control_heartbeat("other_writer")
    assert exc_info.value.code is ErrorCode.CONTROL_BUSY
    service.release_exclusive_control(owner)


def test_bounded_slow_move_ack_does_not_trigger_competing_watchdog_stop(
    client, monkeypatch
):
    adapter = client.app.state.adapter
    service = client.app.state.robot_service
    original_move = service.gateway.move
    before_stops = adapter.stop_count
    service.watchdog.timeout_seconds = 0.05

    def slow_ack(vx: float, vy: float, wz: float) -> int:
        time.sleep(0.12)
        return original_move(vx, vy, wz)

    monkeypatch.setattr(service.gateway, "move", slow_ack)

    response = service.refresh_velocity(0.1, 0.0, 0.0, source="test")

    assert response == {"code": 0}
    assert adapter.stop_count == before_stops
    service.safe_stop("test:complete")


def test_exclusive_control_blocks_other_motion_but_never_stop(client):
    adapter = client.app.state.adapter
    service = client.app.state.robot_service
    owner = "phase7_motion_arbiter"
    service.acquire_exclusive_control(owner)

    with pytest.raises(GatewayError) as exc_info:
        service.ensure_ready_for_task_acceptance()
    assert exc_info.value.code is ErrorCode.CONTROL_BUSY

    blocked = client.post(
        "/api/robot/move",
        json={
            "vx": 0.1,
            "vy": 0,
            "wz": 0,
            "duration": 0.05,
            "controlSource": owner,
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "CONTROL_BUSY"

    refreshed = service.refresh_velocity(0.1, 0.0, 0.0, source=owner)
    assert refreshed == {"code": 0}

    before_stop = adapter.stop_count
    stopped = client.post("/api/robot/stop")
    assert stopped.status_code == 200
    assert adapter.stop_count == before_stop + 1

    service.release_exclusive_control(owner)
    assert service.exclusive_control_owner is None


def test_sit_command_uses_gateway(client):
    adapter = client.app.state.adapter

    response = client.post("/api/robot/sit")

    assert response.status_code == 200
    assert adapter.sit_count == 1


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
        assert move_response.json()["code"] == "CONTROL_DISABLED"
        assert stand_response.json()["code"] == "CONTROL_DISABLED"
        assert adapter.move_count == 0
        assert adapter.stand_count == 0
