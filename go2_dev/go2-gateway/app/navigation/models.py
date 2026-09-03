from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.control_owner import ControlOwner


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CapabilityAvailability(str, Enum):
    MOCK = "mock"
    UNAVAILABLE = "unavailable"
    NOT_VERIFIED = "not_verified"
    BLOCKED = "blocked"
    READY = "ready"


class NavigationExecutionState(str, Enum):
    CREATED = "created"
    SAFETY_CHECKING = "safety_checking"
    BLOCKED = "blocked"
    QUEUED = "queued"
    NAVIGATING = "navigating"
    PAUSED_MANUAL = "paused_manual"
    PAUSED_ADMIN = "paused_admin"
    ARRIVED = "arrived"
    VOICE_PROMPTING = "voice_prompting"
    WAITING_RESPONSE = "waiting_response"
    WAITING_ADMIN_CONFIRMATION = "waiting_admin_confirmation"
    RETURNING_HOME = "returning_home"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MappingState(str, Enum):
    IDLE = "idle"
    MAPPING = "mapping"
    PREVIEW_READY = "preview_ready"
    SAVED = "saved"
    CANCELLED = "cancelled"
    FAILED = "failed"


class NavigationTaskState(str, Enum):
    IDLE = "idle"
    CREATED = "created"
    SAFETY_CHECKING = "safety_checking"
    BLOCKED = "blocked"
    QUEUED = "queued"
    NAVIGATING = "navigating"
    PAUSED_MANUAL = "paused_manual"
    PAUSED_ADMIN = "paused_admin"
    ARRIVED = "arrived"
    RETURNING_HOME = "returning_home"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NavigationErrorCode(str, Enum):
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    INVALID_CONTROL_TRANSITION = "INVALID_CONTROL_TRANSITION"
    MOCK_PROVIDER_REQUIRED = "MOCK_PROVIDER_REQUIRED"
    ROBOT_OFFLINE = "ROBOT_OFFLINE"
    DDS_NOT_READY = "DDS_NOT_READY"
    LIDAR_NOT_READY = "LIDAR_NOT_READY"
    EMERGENCY_STOP_ACTIVE = "EMERGENCY_STOP_ACTIVE"
    LOCALIZATION_INVALID = "LOCALIZATION_INVALID"
    MAP_NOT_LOADED = "MAP_NOT_LOADED"
    MAP_POINTS_INVALID = "MAP_POINTS_INVALID"
    PATH_NOT_PLANNABLE = "PATH_NOT_PLANNABLE"
    ROBOT_NOT_STATIONARY = "ROBOT_NOT_STATIONARY"
    CONTROL_NOT_AVAILABLE = "CONTROL_NOT_AVAILABLE"
    MANUAL_CONTROL_ACTIVE = "MANUAL_CONTROL_ACTIVE"
    NAVIGATION_NOT_READY = "NAVIGATION_NOT_READY"
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    TASK_STATE_CONFLICT = "TASK_STATE_CONFLICT"
    TASK_ALREADY_ACTIVE = "TASK_ALREADY_ACTIVE"
    MAPPING_ALREADY_ACTIVE = "MAPPING_ALREADY_ACTIVE"
    MAPPING_NOT_ACTIVE = "MAPPING_NOT_ACTIVE"
    MAP_PREVIEW_NOT_READY = "MAP_PREVIEW_NOT_READY"
    MAP_REPLACEMENT_CONFIRMATION_REQUIRED = "MAP_REPLACEMENT_CONFIRMATION_REQUIRED"
    SAFETY_INTERLOCK_FAILED = "SAFETY_INTERLOCK_FAILED"
    REAL_MOTION_DISABLED = "REAL_MOTION_DISABLED"
    MOCK_SCENARIO_INVALID = "MOCK_SCENARIO_INVALID"


class MockNavigationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["mock"] = "mock"
    real_motion_enabled: Literal[False] = False


class NavigationCapability(MockNavigationModel):
    mapping: CapabilityAvailability = CapabilityAvailability.MOCK
    map_preview: CapabilityAvailability = CapabilityAvailability.MOCK
    map_save: CapabilityAvailability = CapabilityAvailability.MOCK
    maps: CapabilityAvailability = CapabilityAvailability.MOCK
    point_navigation: CapabilityAvailability = CapabilityAvailability.MOCK
    localization: CapabilityAvailability = CapabilityAvailability.MOCK
    path_planning: CapabilityAvailability = CapabilityAvailability.MOCK
    patrol: CapabilityAvailability = CapabilityAvailability.MOCK
    emergency_dispatch: CapabilityAvailability = CapabilityAvailability.MOCK
    return_home: CapabilityAvailability = CapabilityAvailability.MOCK
    point_cloud: CapabilityAvailability = CapabilityAvailability.MOCK
    audio_input: CapabilityAvailability = CapabilityAvailability.MOCK
    audio_output: CapabilityAvailability = CapabilityAvailability.MOCK
    manual_takeover: CapabilityAvailability = CapabilityAvailability.MOCK
    ros2: CapabilityAvailability = CapabilityAvailability.UNAVAILABLE
    nav2: CapabilityAvailability = CapabilityAvailability.UNAVAILABLE
    slam_toolbox: CapabilityAvailability = CapabilityAvailability.UNAVAILABLE
    real_lidar_point_cloud: CapabilityAvailability = CapabilityAvailability.NOT_VERIFIED


class SafetyChecks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    robot_online: bool
    emergency_stop_clear: bool
    localization_valid: bool
    map_loaded: bool
    path_plannable: bool
    robot_stationary: bool
    control_available: bool


class SafetyInterlockResult(MockNavigationModel):
    passed: bool
    checks: SafetyChecks
    blocked_by: list[str] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=utc_now)


class MockPose(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float
    y: float
    yaw: float
    source: Literal["mock"] = "mock"


class MockNavigationPoint(MockPose):
    point_id: str = Field(min_length=1, max_length=160)


class MockMapPreview(MockNavigationModel):
    session_id: str
    width: int = 20
    height: int = 20
    resolution: float = 0.1
    occupied_cells: list[list[int]] = Field(default_factory=list)
    source: Literal["mock"] = "mock"


class MockMap(MockNavigationModel):
    map_id: str
    session_id: str
    name: str
    status: Literal["active"] = "active"
    revision: int = Field(default=1, ge=1)
    preview: MockMapPreview


class MockMappingSession(MockNavigationModel):
    session_id: str
    session_name: str
    mapping_state: MappingState
    guidance: str
    preview: MockMapPreview | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class MockPatrolRoute(MockNavigationModel):
    route_id: str
    map_id: str
    points: list[MockNavigationPoint]
    return_home_point_id: str


class MockNavigationTask(MockNavigationModel):
    task_id: str
    external_task_id: str | None = None
    incident_id: str | None = None
    task_type: Literal["point_navigation", "patrol", "emergency", "return_home"]
    status: Literal["QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "BLOCKED"]
    execution_state: NavigationExecutionState
    navigation_state: NavigationTaskState
    control_owner: ControlOwner = ControlOwner.NONE
    map_id: str | None = None
    target_point_id: str | None = None
    home_point_id: str | None = None
    target_pose: MockPose | None = None
    patrol_route: MockPatrolRoute | None = None
    current_point_index: int | None = None
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    error_code: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class NavigationState(MockNavigationModel):
    mapping_state: MappingState = MappingState.IDLE
    active_map_id: str | None = None
    localization_valid: bool = False
    map_loaded: bool = False
    path_plannable: bool = True
    robot_online: bool = True
    emergency_stop_clear: bool = True
    robot_stationary: bool = True
    control_available: bool = True
    control_owner: ControlOwner = ControlOwner.NONE
    emergency_stop_active: bool = False
    execution_state: NavigationExecutionState = NavigationExecutionState.CREATED
    navigation_state: NavigationTaskState = NavigationTaskState.IDLE
    active_task_id: str | None = None
    active_map: MockMap | None = None
    current_pose: MockPose = Field(default_factory=lambda: MockPose(x=0.0, y=0.0, yaw=0.0))
    target_pose: MockPose | None = None
    active_task: MockNavigationTask | None = None
    patrol_route: MockPatrolRoute | None = None
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    last_error: str | None = None
    safety_interlock: SafetyInterlockResult | None = None
    mock_scenario: str = "robot_ready"
    updated_at: datetime = Field(default_factory=utc_now)
