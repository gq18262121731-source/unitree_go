from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from app.follow.controller import VelocityCommand


class CompanionState(str, Enum):
    IDLE = "IDLE"
    FOLLOWING = "FOLLOWING"
    PERSON_STOPPED = "PERSON_STOPPED"
    VIEW_ADJUST = "VIEW_ADJUST"
    HOLD = "HOLD"
    TARGET_LOST = "TARGET_LOST"
    SAFE_STOP = "SAFE_STOP"
    OBSTACLE_STOP = "OBSTACLE_STOP"
    FALL_SUSPECTED = "FALL_SUSPECTED"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    VOICE_CHECK = "VOICE_CHECK"
    RECHECK = "RECHECK"
    HELP_REQUESTED = "HELP_REQUESTED"
    ESCALATED_EMERGENCY = "ESCALATED_EMERGENCY"
    MONITORING = "MONITORING"
    RECOVERING = "RECOVERING"
    WAIT_RESUME = "WAIT_RESUME"
    MANUAL_CONTROL = "MANUAL_CONTROL"


class CompanionMotionMode(str, Enum):
    STOP = "STOP"
    FOLLOW = "FOLLOW"
    HOLD = "HOLD"
    VIEW_ADJUST = "VIEW_ADJUST"


@dataclass(frozen=True)
class CompanionSnapshot:
    sequence: int
    state: CompanionState
    previous_state: CompanionState | None
    reason: str
    entered_monotonic: float
    motion_mode: CompanionMotionMode
    target_stationary: bool
    target_available: bool
    resume_required: bool
    active_incident_id: str | None
    help_required: bool | None = None
    response_attempts: int = 0
    emergency_escalated: bool = False
    monitoring_active: bool = False


@dataclass(frozen=True)
class CompanionDirective:
    snapshot: CompanionSnapshot
    command: VelocityCommand | None


class CompanionServiceState(str, Enum):
    IDLE = "IDLE"
    STARTING = "STARTING"
    SAFE_STOP = "SAFE_STOP"
    ERROR = "ERROR"


@dataclass(frozen=True)
class CompanionUwbStatus:
    valid: bool = False
    age_ms: int | None = None
    enabled_from_app: int | None = None
    error_state: int | None = None
    distance_m: float | None = None
    bearing_rad: float | None = None
    orientation_est_rad: float | None = None
    error: str | None = "uwb_not_ready"


@dataclass(frozen=True)
class CompanionLidarStatus:
    valid: bool = False
    state: str = "STOP"
    age_ms: int | None = None
    reason: str = "lidar_not_ready"
    nearest_distance_m: float | None = None


@dataclass(frozen=True)
class CompanionRiskStatus:
    state: str = "NORMAL"
    heartbeat_fresh: bool = False
    age_ms: int | None = None
    incident_id: str | None = None
    manual_takeover: bool = False
    emergency_active: bool = False


@dataclass(frozen=True)
class CompanionMotionStatus:
    vx: float = 0.0
    vy: float = 0.0
    wz: float = 0.0
    authority: str = "IDLE"


@dataclass(frozen=True)
class CompanionStatus:
    state: str
    reason: str
    incident_id: str | None
    resume_required: bool
    runtime_active: bool
    robot_online: bool
    uwb: CompanionUwbStatus = field(default_factory=CompanionUwbStatus)
    lidar: CompanionLidarStatus = field(default_factory=CompanionLidarStatus)
    risk: CompanionRiskStatus = field(default_factory=CompanionRiskStatus)
    motion: CompanionMotionStatus = field(default_factory=CompanionMotionStatus)
    runtime: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
