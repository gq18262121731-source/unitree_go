from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.navigation.models import utc_now


class NavigationEventType(str, Enum):
    NAVIGATION_SNAPSHOT = "navigation_snapshot"
    NAVIGATION_STATE_CHANGED = "navigation_state_changed"
    MAPPING_STATE_CHANGED = "mapping_state_changed"
    TASK_CREATED = "task_created"
    TASK_BLOCKED = "task_blocked"
    TASK_STARTED = "task_started"
    TASK_PAUSED = "task_paused"
    TASK_RESUMED = "task_resumed"
    TASK_CANCELLED = "task_cancelled"
    TASK_ARRIVED = "task_arrived"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    MANUAL_CONTROL_ACQUIRED = "manual_control_acquired"
    MANUAL_CONTROL_RELEASED = "manual_control_released"
    SAFETY_INTERLOCK_CHECKED = "safety_interlock_checked"
    MAP_PREVIEW_READY = "map_preview_ready"
    MAP_SAVED = "map_saved"
    RETURN_HOME_STARTED = "return_home_started"
    RETURN_HOME_COMPLETED = "return_home_completed"
    RETURN_HOME_FAILED = "return_home_failed"
    MOCK_SCENARIO_CHANGED = "mock_scenario_changed"


class NavigationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: NavigationEventType
    sequence: int = Field(ge=1)
    timestamp: datetime = Field(default_factory=utc_now)
    provider: Literal["mock"] = "mock"
    real_motion_enabled: Literal[False] = False
    data: dict[str, Any] = Field(default_factory=dict)


class NavigationWebSocketError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["error"] = "error"
    code: str
    message: str
    provider: Literal["mock"] = "mock"
    real_motion_enabled: Literal[False] = False
    timestamp: datetime = Field(default_factory=utc_now)


def websocket_control_message(message_type: Literal["ping", "pong"]) -> dict[str, Any]:
    return {
        "type": message_type,
        "timestamp": utc_now().isoformat(),
        "provider": "mock",
        "real_motion_enabled": False,
    }
