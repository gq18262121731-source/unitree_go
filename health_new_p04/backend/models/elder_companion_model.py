from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


CompanionCheckState = Literal["passed", "failed", "pending"]


class CompanionStartCheck(BaseModel):
    key: str
    label: str
    state: CompanionCheckState
    code: str | None = None
    detail: str = ""


class CompanionBindingStatus(BaseModel):
    configured: bool = False
    matched: bool = False
    elder_id: str | None = None
    robot_id: str


class CompanionRobotStatus(BaseModel):
    robot_id: str
    name: str
    model: str
    online: bool = False


class ElderCompanionStatus(BaseModel):
    elder_id: str
    elder_name: str
    binding: CompanionBindingStatus
    robot: CompanionRobotStatus
    gateway_available: bool = False
    state: str = "IDLE"
    reason: str = "status_unavailable"
    runtime_active: bool = False
    resume_required: bool = False
    incident_id: str | None = None
    uwb: dict[str, Any] = Field(default_factory=dict)
    lidar: dict[str, Any] = Field(default_factory=dict)
    risk: dict[str, Any] = Field(default_factory=dict)
    motion: dict[str, Any] = Field(default_factory=dict)
    configuration: dict[str, Any] = Field(default_factory=dict)
    checks: list[CompanionStartCheck] = Field(default_factory=list)
    can_start: bool = False
    can_stop: bool = False
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
