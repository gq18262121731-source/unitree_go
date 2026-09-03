from __future__ import annotations

import json
import math

import pytest

from app.telemetry.uwb_dashboard import (
    CompanionStatusSource,
    MockTelemetrySource,
    TelemetryHistory,
    TelemetrySample,
    distance_history_figure,
    relative_position_figure,
    speed_history_figure,
)
from tools.go2_uwb_telemetry import build_parser


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_cli_defaults_to_the_gateway_companion_status_port() -> None:
    args = build_parser().parse_args([])

    assert args.status_url is None
    assert args.wireless is False


def _status_payload() -> dict:
    return {
        "data": {
            "state": "FOLLOWING",
            "runtime_active": True,
            "robot_online": True,
            "uwb": {
                "valid": True,
                "age_ms": 42,
                "distance_m": 1.82,
                "bearing_rad": 0.20,
                "orientation_est_rad": -0.35,
            },
            "lidar": {"valid": True, "state": "CLEAR", "age_ms": 28},
            "motion": {
                "vx": 0.21,
                "vy": 0.0,
                "wz": -0.13,
                "authority": "FOLLOW",
            },
            "runtime": {
                "worker_alive": True,
                "failure": None,
                "input": {
                    "uwb_topic": "rt/uwbstate",
                    "lidar_topic": "rt/utlidar/cloud_base",
                    "uwb_samples": 100,
                    "lidar_samples": 300,
                    "dds_publishers": 0,
                },
                "control": {"execution_status": "SENT"},
            },
            "configuration": {
                "target_distance_m": 1.75,
                "target_bearing_rad": 0.32175,
                "control_frequency_hz": 5.0,
            },
        }
    }


def test_status_source_is_get_only_and_uses_runtime_final_motion(monkeypatch) -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _Response(_status_payload())

    monkeypatch.setattr(
        "app.telemetry.uwb_dashboard.urllib.request.urlopen", fake_urlopen
    )
    source = CompanionStatusSource(
        "http://127.0.0.1:8000/api/v1/robot/companion/status",
        target_distance_m=0.875,
        target_bearing_rad=0.0,
    )

    sample = source.read()

    assert requests[0][0].get_method() == "GET"
    assert requests[0][1] == pytest.approx(0.50)
    assert sample.distance_m == pytest.approx(1.82)
    assert sample.target_distance_m == pytest.approx(1.75)
    assert sample.target_bearing_rad == pytest.approx(0.32175)
    assert sample.vx == pytest.approx(0.21)
    assert sample.wz == pytest.approx(-0.13)
    assert sample.uwb_state == "正常"
    assert sample.lidar_state == "正常"
    assert sample.control_state == "正常"


def test_status_source_waits_without_raising_when_gateway_is_unavailable(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise ConnectionRefusedError("offline")

    monkeypatch.setattr(
        "app.telemetry.uwb_dashboard.urllib.request.urlopen", fail
    )
    sample = CompanionStatusSource(
        "http://127.0.0.1:8000/api/v1/robot/companion/status",
        target_distance_m=1.75,
        target_bearing_rad=0.2,
    ).read()

    assert sample.distance_m is None
    assert sample.vx == 0.0
    assert sample.wz == 0.0
    assert sample.uwb_state == "等待数据"
    assert "读取错误" in sample.debug


def test_status_source_accepts_wireless_runtime_contract(monkeypatch) -> None:
    payload = _status_payload()
    data = payload["data"]
    data["uwb"].pop("bearing_rad")
    data["uwb"]["bearing_deg"] = 12.0
    data["lidar"] = {"valid": False, "state": "UNAVAILABLE"}
    data["configuration"]["target_distance_m"] = 0.875
    data["configuration"]["target_bearing_rad"] = 0.32175
    data["configuration"]["transport"] = "webrtc"
    data["runtime"]["input"]["transport"] = "webrtc"

    monkeypatch.setattr(
        "app.telemetry.uwb_dashboard.urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response(payload),
    )
    sample = CompanionStatusSource(
        "http://127.0.0.1:8093/api/v1/robot/companion/status",
        target_distance_m=1.75,
        target_bearing_rad=0.0,
    ).read()

    assert sample.bearing_rad == pytest.approx(math.radians(12.0))
    assert sample.target_distance_m == pytest.approx(0.875)
    assert sample.lidar_state == "不可用"
    assert sample.control_state == "正常"


def test_mock_source_stays_in_requested_distance_envelope() -> None:
    source = MockTelemetrySource()
    source._started -= 120.0

    samples = [source.read() for _ in range(5)]

    assert all(1.5 <= sample.distance_m <= 2.4 for sample in samples if sample.distance_m is not None)
    assert all(sample.simulated for sample in samples)


def test_history_is_bounded_and_ignores_waiting_samples() -> None:
    history = TelemetryHistory(max_points=3)
    for index in range(5):
        history.append(_sample(float(index)))
    history.append(
        TelemetrySample(
            captured_at=10.0,
            distance_m=None,
            bearing_rad=None,
            target_distance_m=1.75,
            target_bearing_rad=0.0,
            vx=0.0,
            wz=0.0,
            uwb_state="等待数据",
            lidar_state="等待数据",
            control_state="等待数据",
        )
    )

    assert [sample.captured_at for sample in history.snapshot()] == [2.0, 3.0, 4.0]


def test_figures_contain_only_required_engineering_series() -> None:
    sample = _sample(1.0)
    position = relative_position_figure(sample)
    distance = distance_history_figure((sample,), 1.75)
    speed = speed_history_figure((sample,))

    assert {trace.name for trace in distance.data} == {"当前距离", "目标距离"}
    assert {trace.name for trace in speed.data} == {"前进速度（vx）", "转向速度（wz）"}
    expected_x = sample.target_distance_m * math.cos(sample.bearing_rad)
    expected_y = sample.target_distance_m * math.sin(sample.bearing_rad)
    assert position.data[0].x[0] == pytest.approx(expected_x)
    assert position.data[0].y[0] == pytest.approx(expected_y)


def _sample(captured_at: float) -> TelemetrySample:
    return TelemetrySample(
        captured_at=captured_at,
        distance_m=1.82,
        bearing_rad=0.20,
        target_distance_m=1.75,
        target_bearing_rad=0.32175,
        vx=0.21,
        wz=-0.13,
        uwb_state="正常",
        lidar_state="正常",
        control_state="正常",
    )
