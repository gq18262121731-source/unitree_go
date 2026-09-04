from __future__ import annotations

import math
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping

from app.config import Settings
from app.follow.controller import SafetyState, VelocityCommand
from app.motion.contracts import ExternalRiskEvent, ExternalRiskEventType, RiskState
from app.motion.lidar_safety import LidarSafetyDecision, LidarSafetyLevel


class MotionAuthority(str, Enum):
    EMERGENCY = "EMERGENCY"
    MANUAL = "MANUAL"
    LIDAR_STOP = "LIDAR_STOP"
    FOLLOW = "FOLLOW"
    IDLE = "IDLE"


@dataclass(frozen=True)
class MotionArbiterConfig:
    uwb_timeout_seconds: float = 1.0
    external_risk_timeout_seconds: float = 2.0
    require_external_risk_feed: bool = True

    def __post_init__(self) -> None:
        for name in ("uwb_timeout_seconds", "external_risk_timeout_seconds"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and greater than zero")

    @classmethod
    def from_settings(cls, settings: Settings) -> MotionArbiterConfig:
        return cls(
            require_external_risk_feed=settings.phase7_require_external_risk_feed
        )


@dataclass(frozen=True)
class ArbiterDecision:
    sequence: int
    authority: MotionAuthority
    stop_required: bool
    reason: str
    vx: float
    vy: float
    wz: float
    risk_state: RiskState
    active_incident_id: str | None
    lidar_level: LidarSafetyLevel | None


class MotionArbiter:
    """Single fail-closed decision point for Phase 7 autonomous motion."""

    def __init__(
        self,
        config: MotionArbiterConfig | None = None,
        *,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config or MotionArbiterConfig.from_settings(Settings())
        self._monotonic_clock = monotonic_clock
        self._sequence = 0
        self._emergency_active = False
        self._emergency_reason = "external_emergency"
        self._manual_takeover = False
        self._risk_state = RiskState.NORMAL
        self._active_incident_id: str | None = None
        self._active_event_type: ExternalRiskEventType | None = None
        self._seen_incidents: set[str] = set()
        self._last_risk_feed_at: float | None = None
        self._last_non_fall_timestamp = None
        self._last_recovery_timestamp = None
        self._recovery_confirmed = False

    @property
    def risk_state(self) -> RiskState:
        return self._risk_state

    @property
    def active_incident_id(self) -> str | None:
        return self._active_incident_id

    def set_emergency(self, active: bool, *, reason: str = "external_emergency") -> None:
        self._emergency_active = bool(active)
        self._emergency_reason = reason or "external_emergency"

    def set_manual_takeover(self, active: bool) -> None:
        self._manual_takeover = bool(active)

    def status(self, *, now_monotonic: float | None = None) -> dict[str, object]:
        """Expose the risk/control gates without advancing arbitration."""

        now = self._monotonic_clock() if now_monotonic is None else now_monotonic
        if not math.isfinite(now):
            raise ValueError("now_monotonic must be finite")
        age = (
            None
            if self._last_risk_feed_at is None
            else now - self._last_risk_feed_at
        )
        heartbeat_fresh = (
            not self.config.require_external_risk_feed
            or (
                age is not None
                and math.isfinite(age)
                and 0.0 <= age < self.config.external_risk_timeout_seconds
            )
        )
        return {
            "state": self._risk_state.value,
            "incident_id": self._active_incident_id,
            "event_type": (
                None if self._active_event_type is None else self._active_event_type.value
            ),
            "heartbeat_fresh": heartbeat_fresh,
            "age_seconds": age,
            "manual_takeover": self._manual_takeover,
            "emergency_active": self._emergency_active,
            "emergency_reason": self._emergency_reason,
            "require_external_risk_feed": self.config.require_external_risk_feed,
        }

    def ingest_risk_event(
        self,
        event: ExternalRiskEvent | Mapping[str, object],
        *,
        received_monotonic: float | None = None,
    ) -> bool:
        parsed = event if isinstance(event, ExternalRiskEvent) else ExternalRiskEvent.from_payload(event)
        received_at = self._monotonic_clock() if received_monotonic is None else received_monotonic
        if not math.isfinite(received_at):
            raise ValueError("received_monotonic must be finite")
        if parsed.event_type is ExternalRiskEventType.NON_FALL:
            if (
                self._last_non_fall_timestamp is not None
                and parsed.timestamp <= self._last_non_fall_timestamp
            ):
                return False
            if self._last_risk_feed_at is not None and received_at < self._last_risk_feed_at:
                raise ValueError("risk feed receive time must be monotonic")
            self._last_non_fall_timestamp = parsed.timestamp
            self._last_risk_feed_at = received_at
            return True

        if parsed.event_type is ExternalRiskEventType.RECOVERY_CONFIRMED:
            if self._active_incident_id is None:
                raise ValueError("RECOVERY_CONFIRMED requires an active fall incident")
            if parsed.incident_id != self._active_incident_id:
                raise ValueError("recovery incident_id does not match the active fall")
            if self._risk_state is not RiskState.MONITORING:
                raise ValueError("fall must enter MONITORING before recovery")
            if (
                self._last_recovery_timestamp is not None
                and parsed.timestamp <= self._last_recovery_timestamp
            ):
                return False
            if self._last_risk_feed_at is not None and received_at < self._last_risk_feed_at:
                raise ValueError("risk feed receive time must be monotonic")
            self._last_recovery_timestamp = parsed.timestamp
            self._last_risk_feed_at = received_at
            self._recovery_confirmed = True
            return True

        incident_id = parsed.incident_id
        if incident_id is None:
            raise RuntimeError("validated fall event lacks incident_id")
        if incident_id == self._active_incident_id:
            if (
                self._active_event_type is ExternalRiskEventType.FALL_SUSPECTED
                and parsed.event_type is ExternalRiskEventType.FALL_CONFIRMED
            ):
                self._active_event_type = ExternalRiskEventType.FALL_CONFIRMED
                self._last_risk_feed_at = received_at
                return True
            return False
        if incident_id in self._seen_incidents:
            return False
        if self._last_risk_feed_at is not None and received_at < self._last_risk_feed_at:
            raise ValueError("risk feed receive time must be monotonic")
        self._last_risk_feed_at = received_at
        self._seen_incidents.add(incident_id)
        self._active_incident_id = incident_id
        self._active_event_type = parsed.event_type
        self._risk_state = RiskState.PAUSED_BY_FALL
        self._recovery_confirmed = False
        return True

    def acknowledge_fall(self, incident_id: str) -> None:
        if incident_id != self._active_incident_id:
            raise ValueError("incident_id is not the active fall incident")
        if self._risk_state is not RiskState.PAUSED_BY_FALL:
            raise ValueError("fall incident is not awaiting acknowledgement")
        self._risk_state = RiskState.MONITORING

    def clear_fall(self, incident_id: str) -> None:
        if incident_id != self._active_incident_id:
            raise ValueError("incident_id is not the active fall incident")
        if self._risk_state is not RiskState.MONITORING:
            raise ValueError("fall must enter MONITORING before it can be cleared")
        if not self._recovery_confirmed:
            raise ValueError(
                "a matching RECOVERY_CONFIRMED event is required before clearing"
            )
        self._risk_state = RiskState.NORMAL
        self._active_incident_id = None
        self._active_event_type = None
        self._recovery_confirmed = False

    def decide(
        self,
        *,
        follow_command: VelocityCommand | None,
        uwb_age_seconds: float | None,
        lidar: LidarSafetyDecision | None,
        now_monotonic: float | None = None,
    ) -> ArbiterDecision:
        now = self._monotonic_clock() if now_monotonic is None else now_monotonic
        if not math.isfinite(now):
            raise ValueError("now_monotonic must be finite")

        if self._emergency_active:
            return self._stop(MotionAuthority.EMERGENCY, self._emergency_reason, lidar)
        if self._active_incident_id is not None:
            return self._stop(MotionAuthority.EMERGENCY, "fall_incident_active", lidar)
        if self.config.require_external_risk_feed:
            if self._last_risk_feed_at is None:
                return self._stop(MotionAuthority.EMERGENCY, "risk_feed_not_ready", lidar)
            risk_age = now - self._last_risk_feed_at
            if risk_age < 0.0 or risk_age >= self.config.external_risk_timeout_seconds:
                return self._stop(MotionAuthority.EMERGENCY, "risk_feed_stale", lidar)

        if self._manual_takeover:
            return self._stop(MotionAuthority.MANUAL, "manual_takeover", lidar)

        if lidar is None:
            return self._stop(MotionAuthority.LIDAR_STOP, "lidar_not_ready", None)
        if lidar.stop_required or lidar.level is LidarSafetyLevel.STOP:
            return self._stop(MotionAuthority.LIDAR_STOP, lidar.reason, lidar)

        if uwb_age_seconds is None or not math.isfinite(uwb_age_seconds) or uwb_age_seconds < 0.0:
            return self._stop(MotionAuthority.IDLE, "uwb_not_ready", lidar)
        if uwb_age_seconds >= self.config.uwb_timeout_seconds:
            return self._stop(MotionAuthority.IDLE, "uwb_stale", lidar)
        if follow_command is None:
            return self._stop(MotionAuthority.IDLE, "follow_command_missing", lidar)
        if follow_command.simulation_mode:
            return self._stop(MotionAuthority.IDLE, "simulation_command_rejected", lidar)
        if follow_command.safety_state is not SafetyState.SAFE:
            return self._stop(MotionAuthority.IDLE, "follow_safety_stop", lidar)
        values = (follow_command.vx, follow_command.vy, follow_command.wz)
        if not all(math.isfinite(value) for value in values):
            return self._stop(MotionAuthority.IDLE, "invalid_follow_command", lidar)

        scale = lidar.speed_scale if lidar.level is LidarSafetyLevel.SLOW else 1.0
        return self._decision(
            MotionAuthority.FOLLOW,
            False,
            "follow_slowed_by_lidar" if scale < 1.0 else "follow_clear",
            follow_command.vx * scale,
            follow_command.vy * scale,
            follow_command.wz * scale,
            lidar,
        )

    def decide_manual(self, *, vx: float, vy: float, wz: float) -> ArbiterDecision:
        """Authorize a keyboard command without entering the follow pipeline.

        Manual commands still pass through the shared arbiter so an emergency
        or fall incident always wins.  UWB and LiDAR are intentionally not
        prerequisites for operator control; their risk events preempt through
        the emergency/incident gates above this branch.
        """

        if self._emergency_active:
            return self._stop(
                MotionAuthority.EMERGENCY, self._emergency_reason, None
            )
        if self._active_incident_id is not None:
            return self._stop(
                MotionAuthority.EMERGENCY, "fall_incident_active", None
            )
        if not self._manual_takeover:
            return self._stop(
                MotionAuthority.IDLE, "manual_takeover_not_active", None
            )
        values = (vx, vy, wz)
        if not all(math.isfinite(value) for value in values):
            return self._stop(MotionAuthority.MANUAL, "invalid_manual_command", None)
        return self._decision(
            MotionAuthority.MANUAL,
            False,
            "manual_command",
            vx,
            vy,
            wz,
            None,
        )

    def _stop(
        self,
        authority: MotionAuthority,
        reason: str,
        lidar: LidarSafetyDecision | None,
    ) -> ArbiterDecision:
        return self._decision(authority, True, reason, 0.0, 0.0, 0.0, lidar)

    def _decision(
        self,
        authority: MotionAuthority,
        stop_required: bool,
        reason: str,
        vx: float,
        vy: float,
        wz: float,
        lidar: LidarSafetyDecision | None,
    ) -> ArbiterDecision:
        self._sequence += 1
        return ArbiterDecision(
            sequence=self._sequence,
            authority=authority,
            stop_required=stop_required,
            reason=reason,
            vx=vx,
            vy=vy,
            wz=wz,
            risk_state=self._risk_state,
            active_incident_id=self._active_incident_id,
            lidar_level=lidar.level if lidar is not None else None,
        )
