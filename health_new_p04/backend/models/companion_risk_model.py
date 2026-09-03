from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CompanionRiskEventType(str, Enum):
    NON_FALL = "NON_FALL"
    FALL_SUSPECTED = "FALL_SUSPECTED"
    FALL_CONFIRMED = "FALL_CONFIRMED"
    FALL_DISMISSED = "FALL_DISMISSED"
    RECOVERY_SUSPECTED = "RECOVERY_SUSPECTED"
    RECOVERY_CONFIRMED = "RECOVERY_CONFIRMED"


class CompanionRiskState(str, Enum):
    FOLLOWING = "FOLLOWING"
    PAUSED_BY_FALL = "PAUSED_BY_FALL"
    MONITORING = "MONITORING"
    WAIT_RESUME = "WAIT_RESUME"


class CompanionRiskEvent(BaseModel):
    """Deterministic event consumed by the Companion safety authority."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "companion_risk_event.v1"
    event_type: CompanionRiskEventType
    incident_id: str = Field(..., min_length=1, max_length=160)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source: str = Field(default="vision_service", min_length=1, max_length=120)
    camera_id: str | None = Field(default=None, max_length=80)
    elder_id: str | None = Field(default=None, max_length=160)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("incident_id", "source")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        return value.strip()


class CompanionRiskTransition(BaseModel):
    accepted: bool = True
    deduplicated: bool = False
    ignored: bool = False
    incident_id: str
    event_type: CompanionRiskEventType
    previous_state: CompanionRiskState
    state: CompanionRiskState
    stop_required: bool = False
    motion_action: str = "NONE"
    motion_executor: str = "disabled"
    reason: str


class CompanionResumeRequest(BaseModel):
    incident_id: str = Field(..., min_length=1, max_length=160)

    @field_validator("incident_id")
    @classmethod
    def strip_incident_id(cls, value: str) -> str:
        return value.strip()


class CompanionRiskStatus(BaseModel):
    state: CompanionRiskState
    active_incident_id: str | None = None
    confirmed_incident_locked: bool = False
    motion_executor: str = "disabled"
    motion_allowed: bool = True

