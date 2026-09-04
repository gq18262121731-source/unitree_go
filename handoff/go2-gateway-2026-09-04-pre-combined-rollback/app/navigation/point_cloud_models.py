from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.navigation.models import ControlOwner, NavigationTaskState, utc_now


class PointCloudScenario(str, Enum):
    CLASSROOM_DEFAULT = "classroom_default"
    CLASSROOM_SPARSE = "classroom_sparse"
    CLASSROOM_OBSTACLE = "classroom_obstacle"
    EMPTY = "empty"
    STREAM_STALE = "stream_stale"
    STREAM_ERROR = "stream_error"


class PointCloudErrorCode(str, Enum):
    POINT_CLOUD_STREAM_UNAVAILABLE = "POINT_CLOUD_STREAM_UNAVAILABLE"
    POINT_CLOUD_SCENARIO_INVALID = "POINT_CLOUD_SCENARIO_INVALID"
    POINT_CLOUD_FRAME_INVALID = "POINT_CLOUD_FRAME_INVALID"
    POINT_CLOUD_PROVIDER_CONTRACT_VIOLATION = "POINT_CLOUD_PROVIDER_CONTRACT_VIOLATION"
    POINT_CLOUD_CLIENT_TOO_SLOW = "POINT_CLOUD_CLIENT_TOO_SLOW"
    NAVIGATION_STORE_UNAVAILABLE = "NAVIGATION_STORE_UNAVAILABLE"
    INVALID_WEBSOCKET_MESSAGE = "INVALID_WEBSOCKET_MESSAGE"
    HEARTBEAT_TIMEOUT = "HEARTBEAT_TIMEOUT"


class PointCloudPose(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float
    y: float
    z: float = 0.0
    yaw: float

    @field_validator("x", "y", "z", "yaw")
    @classmethod
    def require_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Pose values must be finite")
        return value


class PointCloudStreamInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["point_cloud_stream_info"] = "point_cloud_stream_info"
    provider: Literal["mock"] = "mock"
    real_motion_enabled: Literal[False] = False
    frame_id: Literal["mock_lidar"] = "mock_lidar"
    coordinate_frame: Literal["map"] = "map"
    encoding: Literal["json_xyz_intensity_v1"] = "json_xyz_intensity_v1"
    target_fps: float = Field(gt=0, le=5)
    max_points: int = Field(gt=0, le=5000)
    queue_size: int = Field(gt=0, le=2)
    scenario: PointCloudScenario
    stream_status: Literal["ready", "stale", "error"]
    timestamp: datetime = Field(default_factory=utc_now)


PointCloudPoint = tuple[float, float, float, float]


class PointCloudFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["point_cloud_frame"] = "point_cloud_frame"
    sequence: int = Field(ge=1)
    timestamp: datetime = Field(default_factory=utc_now)
    provider: Literal["mock"] = "mock"
    real_motion_enabled: Literal[False] = False
    frame_id: Literal["mock_lidar"] = "mock_lidar"
    coordinate_frame: Literal["map"] = "map"
    scenario: PointCloudScenario
    point_count: int = Field(ge=0, le=5000)
    points: list[PointCloudPoint] = Field(max_length=5000)
    robot_pose: PointCloudPose
    target_pose: PointCloudPose | None = None
    navigation_state: NavigationTaskState
    control_owner: ControlOwner

    @field_validator("points")
    @classmethod
    def require_finite_points(
        cls, points: list[PointCloudPoint]
    ) -> list[PointCloudPoint]:
        if any(not math.isfinite(value) for point in points for value in point):
            raise ValueError("Point values must be finite")
        return points

    @model_validator(mode="after")
    def validate_point_count(self) -> "PointCloudFrame":
        if self.point_count != len(self.points):
            raise ValueError("point_count must equal the number of points")
        return self


class PointCloudStreamError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["error"] = "error"
    code: PointCloudErrorCode
    message: str
    provider: Literal["mock"] = "mock"
    real_motion_enabled: Literal[False] = False
    timestamp: datetime = Field(default_factory=utc_now)


class PointCloudScenarioResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["mock"] = "mock"
    real_motion_enabled: Literal[False] = False
    scenario: PointCloudScenario
    stream_status: Literal["ready", "stale", "error"]
