from __future__ import annotations

import asyncio

import pytest

from app.adapters.webrtc_motion_backend import WebRTCMotionBackend
from app.core.errors import ErrorCode, GatewayError
from app.webrtc.go2_wireless_runtime import Go2WirelessRuntime


TOPICS = {
    "LF_SPORT_MOD_STATE": "rt/lf/sportmodestate",
    "SPORT_MOD_STATE": "rt/sportmodestate",
    "SPORT_MOD": "rt/api/sport/request",
}
COMMANDS = {"Move": 1008, "StopMove": 1003}


def state_envelope() -> dict:
    return {
        "data": {
            "position": [1.2, -0.4, 0.3],
            "velocity": [0.1, -0.2, 0.0],
            "imu_state": {"rpy": [0.01, -0.02, 1.1]},
            "mode": 3,
            "gait_type": 1,
            "yaw_speed": 0.05,
            "body_height": 0.32,
        }
    }


class FakePubSub:
    def __init__(self, response_code: int = 0) -> None:
        self.response_code = response_code
        self.subscriptions: list[str] = []
        self.requests: list[tuple[str, dict]] = []

    def subscribe(self, topic: str, callback) -> None:
        self.subscriptions.append(topic)
        callback(state_envelope())

    async def publish_request_new(self, topic: str, options: dict) -> dict:
        await asyncio.sleep(0)
        self.requests.append((topic, options))
        return {
            "data": {"header": {"status": {"code": self.response_code}}}
        }


class FakeConnection:
    def __init__(self, response_code: int = 0) -> None:
        self.datachannel = type("DataChannel", (), {})()
        self.datachannel.pub_sub = FakePubSub(response_code)
        self.video = type("Video", (), {})()
        self.video.add_track_callback = lambda _callback: None
        self.video.switchVideoChannel = lambda _enabled: None
        self.connect_count = 0
        self.disconnect_count = 0

    async def connect(self) -> None:
        self.connect_count += 1

    async def disconnect(self) -> None:
        self.disconnect_count += 1


def make_backend(
    connection: FakeConnection, *, enable_sport_state: bool = True
) -> WebRTCMotionBackend:
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        command_timeout_seconds=0.5,
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        enable_video=False,
        enable_sport_state=enable_sport_state,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
    )
    return WebRTCMotionBackend(runtime, "go2-test", close_runtime=True)


def test_persistent_connection_exposes_state_and_move_stop() -> None:
    connection = FakeConnection()
    backend = make_backend(connection)

    backend.initialize()
    backend.initialize()
    assert connection.connect_count == 1
    assert backend.is_initialized() is True
    assert backend.get_status()["online"] is True
    assert backend.get_status()["webrtc"]["videoEnabled"] is False
    state = backend.get_motion_state()
    assert state is not None
    assert state["x"] == pytest.approx(1.2)
    assert state["y"] == pytest.approx(-0.4)
    assert state["yaw"] == pytest.approx(1.1)
    assert state["received_monotonic"] > 0
    assert state["source"] == "WebRTC SportModeState.position+imu_state.rpy"
    assert state["topic"] == "rt/sportmodestate"

    assert backend.move(0.3, -0.2, -0.6) == 0
    assert backend.stop() == 0
    requests = connection.datachannel.pub_sub.requests
    assert requests[0] == (
        "rt/api/sport/request",
        {"api_id": 1008, "parameter": {"x": 0.3, "y": -0.2, "z": -0.6}},
    )
    assert requests[1] == ("rt/api/sport/request", {"api_id": 1003})

    backend.close()
    assert connection.disconnect_count == 1
    assert backend.is_initialized() is False


def test_nonzero_api_ack_fails_closed() -> None:
    connection = FakeConnection(response_code=7)
    backend = make_backend(connection)
    backend.initialize()
    try:
        with pytest.raises(GatewayError) as exc_info:
            backend.move(0.1, 0.0, 0.0)
        assert exc_info.value.code == ErrorCode.SDK_COMMAND_FAILED
        assert "status=7" in str(exc_info.value)
    finally:
        backend.close()


def test_verified_data_channel_is_ready_for_manual_motion_without_sport_state() -> None:
    connection = FakeConnection()
    backend = make_backend(connection, enable_sport_state=False)

    backend.initialize()
    try:
        status = backend.get_status()["webrtc"]
        assert status["sportStateReady"] is False
        assert status["dataChannelReady"] is True
        assert backend.motion_transport_ready() is True
        assert backend.move(0.2, 0.0, 0.0) == 0
    finally:
        backend.close()


def test_backend_rejects_out_of_scope_capabilities() -> None:
    connection = FakeConnection()
    backend = make_backend(connection)
    backend.initialize()
    try:
        with pytest.raises(GatewayError) as exc_info:
            backend.stand_up()
        assert exc_info.value.code == ErrorCode.TASK_NOT_SUPPORTED
        with pytest.raises(GatewayError) as camera_error:
            backend.get_camera_jpeg()
        assert camera_error.value.code == ErrorCode.CAMERA_UNAVAILABLE
    finally:
        backend.close()
