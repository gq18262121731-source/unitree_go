from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictTelemetryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReadonlyObservation(StrictTelemetryModel):
    available: bool
    fresh: bool
    topic: str | None
    frame: str | None
    sample_count: int = Field(ge=0)
    frequency_hz: float | None = Field(default=None, ge=0)
    last_source_timestamp: datetime | None
    last_received_at: datetime | None
    sample_age_ms: float | None = Field(default=None, ge=0)
    timestamp_rollback_count: int = Field(ge=0)
    value: dict[str, Any] = Field(default_factory=dict)


class ReadonlySensorObservation(ReadonlyObservation):
    semantic_valid: bool | None


class ReadonlyImuObservation(ReadonlySensorObservation):
    semantic_note: str


class ReadonlyRobot(StrictTelemetryModel):
    online: bool
    model: str
    hardware: str
    firmware: str
    telemetry: ReadonlyObservation


class ReadonlyTransport(StrictTelemetryModel):
    source: Literal["replay", "ros2", "dds"] | None
    healthy: bool
    errors: list[str] = Field(default_factory=list)


class ReadonlySensors(StrictTelemetryModel):
    lidar: ReadonlySensorObservation
    imu: ReadonlyImuObservation
    odometry: ReadonlySensorObservation
    lidar_state: ReadonlyObservation


class ReadonlyCapabilities(StrictTelemetryModel):
    lidar: bool
    imu: bool
    odometry: bool
    localization: Literal[False]
    navigation: Literal[False]
    motion: Literal[False]


class ReadonlyLocalization(StrictTelemetryModel):
    available: Literal[False]
    source: None
    reason: Literal["INTERNAL_LOCALIZATION_NOT_VALIDATED"]


class ReadonlyNavigation(StrictTelemetryModel):
    available: Literal[False]
    reason: Literal["PHASE_5_5_HOLD"]


class ReadonlyMotion(StrictTelemetryModel):
    enabled: Literal[False]
    commands_supported: list[Any] = Field(max_length=0)
    reason: Literal["PHASE_6_1_READONLY_BOUNDARY"]


ReadonlyHealthStatus = Literal[
    "OFFLINE",
    "DEGRADED_TRANSPORT",
    "READONLY_WITH_SEMANTIC_HOLD",
    "READONLY_READY",
    "READONLY_PARTIAL",
]


class ReadonlyHealth(StrictTelemetryModel):
    status: ReadonlyHealthStatus
    sensor_online_is_navigation_ready: Literal[False]


class UnitreeReadonlyStatus(StrictTelemetryModel):
    """Exact health_new representation of the frozen Phase 6.1 v1 contract."""

    schema_version: Literal["1.0"]
    provider: Literal["unitree_readonly"]
    real_motion_enabled: Literal[False]
    generated_at: datetime
    robot: ReadonlyRobot
    transport: ReadonlyTransport
    sensors: ReadonlySensors
    capabilities: ReadonlyCapabilities
    localization: ReadonlyLocalization
    navigation: ReadonlyNavigation
    motion: ReadonlyMotion
    health: ReadonlyHealth


class RobotReadonlyTelemetryIntegration(StrictTelemetryModel):
    schema_version: Literal["1.0"] = "1.0"
    provider: Literal["mock", "unitree_readonly"]
    real_motion_enabled: Literal[False] = False
    integration_mode: Literal["mock", "unitree_readonly"]
    source_status: Literal["mock_frozen", "ready", "unavailable", "invalid"]
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    readonly_status: UnitreeReadonlyStatus | None = None
    error_code: str | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def validate_integration_boundary(self) -> "RobotReadonlyTelemetryIntegration":
        if self.provider != self.integration_mode:
            raise ValueError("provider must match integration_mode")
        if self.integration_mode == "mock":
            if self.source_status != "mock_frozen" or self.readonly_status is not None:
                raise ValueError("Mock mode cannot expose a Unitree readonly snapshot")
        elif self.source_status == "ready":
            if self.readonly_status is None:
                raise ValueError("Ready Unitree mode requires readonly_status")
        elif self.readonly_status is not None:
            raise ValueError("Unavailable or invalid Unitree mode cannot expose stale status")
        return self
