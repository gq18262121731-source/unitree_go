from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictLocalizationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LocalizationStatus(str, Enum):
    UNAVAILABLE = "UNAVAILABLE"
    OBSERVING = "OBSERVING"
    READY = "READY"


class LocalizationSource(str, Enum):
    GO2_USLAM = "go2_uslam"
    POINT_LIO = "point_lio"
    ROS2_LOCALIZATION = "ros2_localization"


class Vector3(StrictLocalizationModel):
    x: float
    y: float
    z: float


class Quaternion(StrictLocalizationModel):
    x: float
    y: float
    z: float
    w: float


class LocalizationPose(StrictLocalizationModel):
    position: Vector3
    orientation: Quaternion


class LocalizationCandidate(StrictLocalizationModel):
    """A future source sample presented to the admission gate.

    Constructing a candidate does not make localization available. The
    controller must validate a consecutive observation window before exposing
    any pose as READY.
    """

    source: LocalizationSource
    pose: LocalizationPose
    frame: str
    map_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    timestamp: datetime
    timestamp_rollback_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def require_source_timestamp(self) -> "LocalizationCandidate":
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware and source-derived")
        return self


class LocalizationState(StrictLocalizationModel):
    provider: Literal["localization"] = "localization"
    state: LocalizationStatus
    available: bool
    source: LocalizationSource | None
    pose: LocalizationPose | None
    frame: str | None
    map_id: str | None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    timestamp: datetime | None
    reason: str

    @model_validator(mode="after")
    def enforce_fail_closed_contract(self) -> "LocalizationState":
        if self.available != (self.state is LocalizationStatus.READY):
            raise ValueError("available may be true only when state is READY")

        ready_fields = (
            self.source,
            self.pose,
            self.frame,
            self.map_id,
            self.confidence,
            self.timestamp,
        )
        if self.state is LocalizationStatus.READY and any(
            value is None for value in ready_fields
        ):
            raise ValueError("READY requires a complete validated localization sample")

        if self.state is LocalizationStatus.UNAVAILABLE and any(
            value is not None for value in ready_fields
        ):
            raise ValueError("UNAVAILABLE must not expose localization data")

        if self.state is LocalizationStatus.OBSERVING:
            if self.source is None:
                raise ValueError("OBSERVING requires an identified candidate source")
            if self.pose is not None:
                raise ValueError("OBSERVING must not expose an unvalidated pose")
        return self


class LocalizationHealth(StrictLocalizationModel):
    healthy: bool
    state: LocalizationStatus
    reason: str
