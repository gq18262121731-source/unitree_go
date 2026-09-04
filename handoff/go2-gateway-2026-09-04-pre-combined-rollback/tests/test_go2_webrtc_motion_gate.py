from __future__ import annotations

import argparse
import asyncio
import math
import sys
import types

import pytest

from tools.go2_webrtc_motion_gate import (
    PulseConfig,
    StateCollector,
    api_status_code,
    forward_progress,
    pose_from_state,
    run_gate,
)


def test_pulse_config_accepts_bounded_default() -> None:
    PulseConfig().validate()


@pytest.mark.parametrize(
    "speed,duration",
    [(0.19, 0.4), (0.24, 0.4), (0.23, 0.19), (0.23, 0.51), (math.nan, 0.4)],
)
def test_pulse_config_rejects_values_outside_gate(speed: float, duration: float) -> None:
    with pytest.raises(ValueError):
        PulseConfig(speed, duration).validate()


def test_api_status_code_requires_zero_ack_shape() -> None:
    response = {"data": {"header": {"status": {"code": 0}}}}
    assert api_status_code(response) == 0
    assert api_status_code({}) is None


def test_pose_and_forward_progress_use_start_yaw_frame() -> None:
    start_state = {
        "position": [1.0, 2.0, 0.0],
        "imu_state": {"rpy": [0.0, 0.0, math.pi / 2]},
    }
    end_state = {
        "position": [1.0, 2.1, 0.0],
        "imu_state": {"rpy": [0.0, 0.0, math.pi / 2]},
    }
    start = pose_from_state(start_state)
    end = pose_from_state(end_state)
    assert forward_progress(start, end) == pytest.approx(0.1)


def test_state_collector_records_valid_sport_state() -> None:
    async def scenario() -> None:
        collector = StateCollector()
        collector.callback("rt/sportmodestate")(
            {"data": {"position": [0, 0, 0], "imu_state": {"rpy": [0, 0, 0]}}}
        )
        await asyncio.wait_for(collector.first_sample.wait(), timeout=0.1)
        assert collector.counts == {"rt/sportmodestate": 1}
        assert collector.is_fresh()

    asyncio.run(scenario())


class FakePubSub:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict]] = []

    def subscribe(self, topic: str, callback) -> None:
        callback(
            {
                "data": {
                    "position": [0.0, 0.0, 0.0],
                    "imu_state": {"rpy": [0.0, 0.0, 0.0]},
                }
            }
        )

    async def publish_request_new(self, topic: str, options: dict) -> dict:
        self.requests.append((topic, options))
        return {"data": {"header": {"status": {"code": 0}}}}


class FakeConnection:
    instances: list["FakeConnection"] = []

    def __init__(self, *_args, **_kwargs) -> None:
        self.datachannel = types.SimpleNamespace(pub_sub=FakePubSub())
        self.disconnected = False
        self.__class__.instances.append(self)

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        self.disconnected = True


def install_fake_webrtc(monkeypatch) -> None:
    FakeConnection.instances.clear()
    module = types.SimpleNamespace(
        RTC_TOPIC={
            "LF_SPORT_MOD_STATE": "rt/lf/sportmodestate",
            "SPORT_MOD_STATE": "rt/sportmodestate",
            "SPORT_MOD": "rt/api/sport/request",
        },
        SPORT_CMD={"StopMove": 1003, "Move": 1008},
        UnitreeWebRTCConnection=FakeConnection,
        WebRTCConnectionMethod=types.SimpleNamespace(LocalSTA="LocalSTA"),
    )
    monkeypatch.setitem(sys.modules, "unitree_webrtc_connect", module)


def gate_args(stage: str) -> argparse.Namespace:
    return argparse.Namespace(
        robot_ip="192.168.8.252",
        stage=stage,
        speed=0.20,
        duration=0.20,
        connect_timeout=1.0,
        state_timeout=1.0,
    )


def test_readonly_gate_never_sends_motion(monkeypatch) -> None:
    install_fake_webrtc(monkeypatch)

    result = asyncio.run(run_gate(gate_args("readonly")))

    assert result["completed"] is True
    assert result["motionCommandsSent"] == 0
    assert FakeConnection.instances[0].datachannel.pub_sub.requests == []
    assert FakeConnection.instances[0].disconnected is True


def test_stop_gate_sends_stop_zero_stop_and_final_cleanup(monkeypatch) -> None:
    install_fake_webrtc(monkeypatch)

    result = asyncio.run(run_gate(gate_args("stop")))
    api_ids = [
        options["api_id"]
        for _topic, options in FakeConnection.instances[0].datachannel.pub_sub.requests
    ]

    assert result["completed"] is True
    assert api_ids == [1003, 1008, 1003, 1003]
    assert result["finalStop"]["acknowledged"] is True


def test_forward_gate_is_bounded_and_finishes_with_stop(monkeypatch) -> None:
    install_fake_webrtc(monkeypatch)

    result = asyncio.run(run_gate(gate_args("forward-pulse")))
    requests = FakeConnection.instances[0].datachannel.pub_sub.requests
    api_ids = [options["api_id"] for _topic, options in requests]

    assert result["completed"] is True
    assert result["pulse"]["maximumCommandedDistanceM"] == pytest.approx(0.04)
    assert api_ids == [1003, 1008, 1003, 1008, 1003]
    assert requests[-2][1]["parameter"] == {"x": 0.20, "y": 0.0, "z": 0.0}
    assert requests[-1][1]["api_id"] == 1003
