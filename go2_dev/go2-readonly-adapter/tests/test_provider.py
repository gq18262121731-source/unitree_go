from __future__ import annotations

import ast
from pathlib import Path

import pytest

from go2_readonly_adapter import (
    ProviderConfig,
    SafetyConfigurationError,
    SensorEvent,
    UnitreeReadonlyProvider,
)
from go2_readonly_adapter.sources.replay import JsonlReplaySource
from go2_readonly_adapter.soak import IntervalTracker


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "go2_readonly_adapter"


def test_replay_contract_separates_sensors_from_navigation() -> None:
    replay = JsonlReplaySource(ROOT / "examples" / "offline_replay.jsonl")
    provider = UnitreeReadonlyProvider(
        clock_ns=lambda: 1_700_000_001_000_000_000
    )

    report = provider.collect(replay, 0)

    assert report["provider"] == "unitree_readonly"
    assert report["real_motion_enabled"] is False
    assert report["robot"]["online"] is True
    assert report["sensors"]["lidar"]["available"] is True
    assert report["sensors"]["imu"]["available"] is True
    assert report["sensors"]["imu"]["semantic_valid"] is False
    assert report["sensors"]["odometry"]["available"] is True
    assert report["localization"]["available"] is False
    assert report["navigation"]["available"] is False
    assert report["motion"]["enabled"] is False
    assert report["motion"]["commands_supported"] == []
    assert report["health"]["sensor_online_is_navigation_ready"] is False
    assert report["health"]["status"] == "READONLY_WITH_SEMANTIC_HOLD"


def test_timestamp_rollback_is_reported_without_mutating_time() -> None:
    provider = UnitreeReadonlyProvider(clock_ns=lambda: 2_000_000_000)
    provider.ingest(
        SensorEvent(
            "lidar",
            "/utlidar/cloud",
            received_timestamp_ns=1_000_000_000,
            source_timestamp_ns=900,
        )
    )
    provider.ingest(
        SensorEvent(
            "lidar",
            "/utlidar/cloud",
            received_timestamp_ns=1_100_000_000,
            source_timestamp_ns=800,
        )
    )

    lidar = provider.snapshot()["sensors"]["lidar"]

    assert lidar["timestamp_rollback_count"] == 1
    assert lidar["last_source_timestamp"].endswith("+00:00")


def test_configuration_cannot_enable_motion_or_select_mock() -> None:
    with pytest.raises(SafetyConfigurationError, match="provider=unitree_readonly"):
        UnitreeReadonlyProvider(ProviderConfig(provider="mock"))
    with pytest.raises(SafetyConfigurationError, match="must remain false"):
        UnitreeReadonlyProvider(ProviderConfig(real_motion_enabled=True))


def test_source_tree_has_no_publisher_or_motion_surface() -> None:
    forbidden_names = {
        "create_publisher",
        "publish",
        "write",
        "create_writer",
        "move",
        "cmd_vel",
        "stand_up",
        "stand_down",
        "sportclient",
        "lowcmd",
    }
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.lower() in forbidden_names:
                    offenders.append(f"{path.name}: function {node.name}")
            if isinstance(node, ast.Call):
                target = node.func
                name = None
                if isinstance(target, ast.Attribute):
                    name = target.attr
                elif isinstance(target, ast.Name):
                    name = target.id
                if name and name.lower() in forbidden_names:
                    offenders.append(f"{path.name}: call {name}")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                text = ast.unparse(node).lower()
                if any(
                    token in text
                    for token in (
                        "sport_client",
                        "sportclient",
                        "lowcmd",
                        "datawriter",
                        "channelpublisher",
                        "nav2",
                        "slam_toolbox",
                    )
                ):
                    offenders.append(f"{path.name}: import {text}")

    assert offenders == []


def test_contract_schema_hard_locks_unsafe_capabilities() -> None:
    import json

    schema = json.loads(
        (ROOT / "schema" / "readonly_status.schema.json").read_text(encoding="utf-8")
    )

    assert schema["properties"]["provider"]["const"] == "unitree_readonly"
    assert schema["properties"]["real_motion_enabled"]["const"] is False
    assert (
        schema["properties"]["localization"]["properties"]["available"]["const"]
        is False
    )
    assert (
        schema["properties"]["navigation"]["properties"]["available"]["const"]
        is False
    )
    assert schema["properties"]["motion"]["properties"]["enabled"]["const"] is False


def test_soak_interval_tracker_is_bounded_and_detects_rollback() -> None:
    tracker = IntervalTracker()
    tracker.consume(1_000_000_000)
    tracker.consume(1_004_000_000)
    tracker.consume(1_008_000_000)
    tracker.consume(1_007_000_000)

    report = tracker.report()

    assert report["samples"] == 4
    assert report["rollback_count"] == 1
    assert report["mean_ms"] == pytest.approx(4.0)
    assert report["recent_window_size"] == 2
