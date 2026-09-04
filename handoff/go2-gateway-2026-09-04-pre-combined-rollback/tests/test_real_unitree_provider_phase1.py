from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.providers.unitree.dds_reader import DiscoveredTopic
from app.providers.unitree.network_diagnostics import NetworkProbe
from app.providers.unitree.real_provider import (
    RealGo2Provider,
    RealProviderConfig,
    SafetyConfigurationError,
)


ROOT = Path(__file__).resolve().parents[1]
UNITREE_PROVIDER_DIR = ROOT / "app" / "providers" / "unitree"


def _network(reachable: bool = True) -> NetworkProbe:
    return NetworkProbe(
        robot_ip="192.168.123.161",
        network_interface="eth0",
        source_ip="192.168.123.222",
        reachable=reachable,
        packets_sent=4,
        packets_received=4 if reachable else 0,
        packet_loss_percent=0.0 if reachable else 100.0,
        average_latency_ms=1.25 if reachable else None,
        route="192.168.123.161 dev eth0 src 192.168.123.222",
        error=None,
    )


def _imu():
    return SimpleNamespace(
        quaternion=[1.0, 0.0, 0.0, 0.0],
        gyroscope=[0.1, 0.2, 0.3],
        accelerometer=[0.0, 0.0, 9.8],
        rpy=[0.01, 0.02, 0.03],
        temperature=42,
    )


class FakeDdsReader:
    def __init__(self, network_interface: str, robot_ip: str, domain_id: int) -> None:
        self.initialized = False
        self.closed = False
        self.samples: dict[str, list[object]] = {}

    def initialize(self) -> None:
        self.initialized = True

    def discover_topics(self, duration_seconds: float) -> list[DiscoveredTopic]:
        return [
            DiscoveredTopic(
                "rt/lowstate", "unitree_go.msg.dds_.LowState_", "publication"
            ),
            DiscoveredTopic(
                "rt/sportmodestate",
                "unitree_go.msg.dds_.SportModeState_",
                "publication",
            ),
            DiscoveredTopic(
                "rt/utlidar/cloud",
                "sensor_msgs.msg.dds_.PointCloud2_",
                "publication",
            ),
            DiscoveredTopic("rt/utlidar/switch", "std_msgs.msg.dds_.String_", "subscription"),
        ]

    def create_reader(self, topic_name: str, message_type):
        if topic_name == "rt/lowstate":
            self.samples[topic_name] = [
                SimpleNamespace(
                    bms_state=SimpleNamespace(soc=91),
                    power_v=29.1,
                    power_a=1.2,
                    imu_state=_imu(),
                    tick=123,
                )
            ]
        elif topic_name == "rt/sportmodestate":
            self.samples[topic_name] = [
                SimpleNamespace(
                    mode=1,
                    position=[1.0, 2.0, 0.1],
                    imu_state=_imu(),
                    stamp=SimpleNamespace(sec=1_700_000_000, nanosec=0),
                )
            ]
        else:
            self.samples[topic_name] = [
                SimpleNamespace(
                    height=1,
                    width=321,
                    point_step=16,
                    data=bytes(321 * 16),
                    header=SimpleNamespace(
                        frame_id="utlidar_lidar",
                        stamp=SimpleNamespace(sec=1_700_000_000, nanosec=0),
                    ),
                )
            ]
        return topic_name

    def take(self, reader, limit: int = 128):
        return self.samples.pop(reader, [])

    def close(self) -> None:
        self.closed = True


def test_phase1_provider_collects_only_discovered_readonly_state_and_lidar():
    clock_values = iter([1_700_000_000.1, 1_700_000_000.2, 1_700_000_000.3])
    provider = RealGo2Provider(
        RealProviderConfig("192.168.123.161", "eth0"),
        dds_reader_factory=FakeDdsReader,
        network_probe=lambda ip, interface, count: _network(),
        clock=lambda: next(clock_values),
    )

    report = provider.collect_report(sample_seconds=0, discovery_seconds=0)

    assert report["provider"] == "unitree_real"
    assert report["real_motion_enabled"] is False
    assert report["status"] == "PASSED"
    assert report["passed"] is True
    assert report["dds"]["initialized"] is True
    assert report["state"]["low_state"]["value"]["battery"]["percentage"] == 91
    assert report["state"]["sport_mode_state"]["value"]["mode"] == 1
    assert report["lidar"]["topic"] == "rt/utlidar/cloud"
    assert report["lidar"]["points"] == 321
    assert report["lidar"]["frame"] == "utlidar_lidar"
    assert report["lidar"]["latency_ms"] == pytest.approx(300.0)


def test_phase1_provider_stops_before_dds_when_network_is_unreachable():
    constructed = False

    def dds_factory(*args):
        nonlocal constructed
        constructed = True
        return FakeDdsReader(*args)

    provider = RealGo2Provider(
        RealProviderConfig("192.168.123.161", "eth0"),
        dds_reader_factory=dds_factory,
        network_probe=lambda ip, interface, count: _network(False),
    )

    report = provider.collect_report(sample_seconds=0, discovery_seconds=0)

    assert constructed is False
    assert report["dds"]["initialized"] is False
    assert report["dds"]["error"] == "NETWORK_UNREACHABLE"
    assert report["status"] == "BLOCKED_NETWORK"
    assert report["passed"] is False


def test_phase1_provider_rejects_mock_selection_and_motion_enablement():
    with pytest.raises(SafetyConfigurationError, match="ROBOT_PROVIDER=unitree_real"):
        RealGo2Provider(RealProviderConfig("192.168.123.161", "eth0", provider="mock"))

    with pytest.raises(SafetyConfigurationError, match="must remain false"):
        RealGo2Provider(
            RealProviderConfig(
                "192.168.123.161",
                "eth0",
                provider="unitree_real",
                real_motion_enabled=True,
            )
        )


def test_phase1_unitree_provider_has_no_control_or_dds_write_surface():
    forbidden_import_parts = {
        "sport_client",
        "ChannelPublisher",
        "DataWriter",
        "nav2",
        "slam",
    }
    forbidden_methods = {
        "move",
        "stop",
        "stand_up",
        "stand_down",
        "sit",
        "cmd_vel",
        "velocity",
        "write",
        "create_writer",
    }
    offenders: list[str] = []
    for path in UNITREE_PROVIDER_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.lower() in forbidden_methods:
                    offenders.append(f"{path.name}: method {node.name}")
            if isinstance(node, ast.ImportFrom):
                text = f"{node.module} {' '.join(alias.name for alias in node.names)}"
                if any(part.lower() in text.lower() for part in forbidden_import_parts):
                    offenders.append(f"{path.name}: import {text}")
            if isinstance(node, ast.Import):
                text = " ".join(alias.name for alias in node.names)
                if any(part.lower() in text.lower() for part in forbidden_import_parts):
                    offenders.append(f"{path.name}: import {text}")

    assert offenders == []
