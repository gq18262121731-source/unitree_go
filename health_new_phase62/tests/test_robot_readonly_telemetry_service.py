from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.dependencies import get_robot_readonly_telemetry_service
from backend.main import app
from backend.services.robot_readonly_telemetry_service import RobotReadonlyTelemetryService


def _observation(
    *,
    topic: str | None,
    available: bool = True,
    semantic_valid: bool | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "available": available,
        "fresh": available,
        "topic": topic,
        "frame": "base_link" if available else None,
        "sample_count": 10 if available else 0,
        "frequency_hz": 15.4 if available else None,
        "last_source_timestamp": "2026-07-31T05:00:00Z" if available else None,
        "last_received_at": "2026-07-31T05:00:00Z" if available else None,
        "sample_age_ms": 12.0 if available else None,
        "timestamp_rollback_count": 0,
        "value": {},
    }
    if semantic_valid is not None:
        payload["semantic_valid"] = semantic_valid
    return payload


def _readonly_payload() -> dict[str, object]:
    telemetry = _observation(topic="rt/lowstate")
    telemetry["value"] = {"battery_percentage": 78}
    lidar = _observation(topic="/utlidar/cloud", semantic_valid=True)
    imu = _observation(topic="/utlidar/imu", semantic_valid=False)
    imu["semantic_note"] = "Phase 5.4.8/5.4.9 semantic hold"
    odometry = _observation(topic="/odom", semantic_valid=True)
    lidar_state = _observation(topic="/utlidar/lidar_state")
    return {
        "schema_version": "1.0",
        "provider": "unitree_readonly",
        "real_motion_enabled": False,
        "generated_at": "2026-07-31T05:00:00Z",
        "robot": {
            "online": True,
            "model": "Go2 X EDU",
            "hardware": "V2.0",
            "firmware": "V1.1.15",
            "telemetry": telemetry,
        },
        "transport": {"source": "ros2", "healthy": True, "errors": []},
        "sensors": {
            "lidar": lidar,
            "imu": imu,
            "odometry": odometry,
            "lidar_state": lidar_state,
        },
        "capabilities": {
            "lidar": True,
            "imu": True,
            "odometry": True,
            "localization": False,
            "navigation": False,
            "motion": False,
        },
        "localization": {
            "available": False,
            "source": None,
            "reason": "INTERNAL_LOCALIZATION_NOT_VALIDATED",
        },
        "navigation": {"available": False, "reason": "PHASE_5_5_HOLD"},
        "motion": {
            "enabled": False,
            "commands_supported": [],
            "reason": "PHASE_6_1_READONLY_BOUNDARY",
        },
        "health": {
            "status": "READONLY_WITH_SEMANTIC_HOLD",
            "sensor_online_is_navigation_ready": False,
        },
    }


def test_mock_mode_does_not_read_external_source(tmp_path: Path) -> None:
    service = RobotReadonlyTelemetryService(
        integration_mode="mock",
        snapshot_path=str(tmp_path / "missing.json"),
    )

    result = service.snapshot()

    assert result.provider == "mock"
    assert result.real_motion_enabled is False
    assert result.source_status == "mock_frozen"
    assert result.readonly_status is None


def test_unitree_mode_reads_and_validates_phase61_snapshot(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "readonly.json"
    snapshot_path.write_text(json.dumps(_readonly_payload()), encoding="utf-8")
    service = RobotReadonlyTelemetryService(
        integration_mode="unitree_readonly",
        snapshot_path=str(snapshot_path),
    )

    result = service.snapshot()

    assert result.provider == "unitree_readonly"
    assert result.real_motion_enabled is False
    assert result.source_status == "ready"
    assert result.readonly_status is not None
    assert result.readonly_status.robot.telemetry.value["battery_percentage"] == 78
    assert result.readonly_status.sensors.imu.available is True
    assert result.readonly_status.sensors.imu.semantic_valid is False
    assert result.readonly_status.motion.enabled is False
    assert result.readonly_status.navigation.available is False


def test_invalid_contract_fails_closed(tmp_path: Path) -> None:
    payload = _readonly_payload()
    payload["real_motion_enabled"] = True
    snapshot_path = tmp_path / "unsafe.json"
    snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
    service = RobotReadonlyTelemetryService(
        integration_mode="unitree_readonly",
        snapshot_path=str(snapshot_path),
    )

    result = service.snapshot()

    assert result.provider == "unitree_readonly"
    assert result.real_motion_enabled is False
    assert result.source_status == "invalid"
    assert result.readonly_status is None
    assert result.error_code == "READONLY_CONTRACT_INVALID"


def test_telemetry_api_is_separate_from_frozen_robot_status() -> None:
    service = RobotReadonlyTelemetryService(integration_mode="mock")
    app.dependency_overrides[get_robot_readonly_telemetry_service] = lambda: service
    try:
        response = TestClient(app).get("/api/v1/robot/telemetry")
    finally:
        app.dependency_overrides.pop(get_robot_readonly_telemetry_service, None)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["provider"] == "mock"
    assert body["data"]["real_motion_enabled"] is False
    assert body["data"]["readonly_status"] is None
