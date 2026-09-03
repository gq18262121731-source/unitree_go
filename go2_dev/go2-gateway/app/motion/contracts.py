from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Mapping


class ExternalRiskEventType(str, Enum):
    FALL_SUSPECTED = "FALL_SUSPECTED"
    FALL_CONFIRMED = "FALL_CONFIRMED"
    RECOVERY_CONFIRMED = "RECOVERY_CONFIRMED"
    NON_FALL = "NON_FALL"


class RiskState(str, Enum):
    NORMAL = "NORMAL"
    PAUSED_BY_FALL = "PAUSED_BY_FALL"
    MONITORING = "MONITORING"


@dataclass(frozen=True)
class ExternalRiskEvent:
    """Stable black-box contract supplied by the external fall module."""

    event_type: ExternalRiskEventType
    timestamp: datetime
    incident_id: str | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must include an explicit timezone")
        if self.event_type in {
            ExternalRiskEventType.FALL_SUSPECTED,
            ExternalRiskEventType.FALL_CONFIRMED,
        }:
            if self.incident_id is None or not self.incident_id.strip():
                raise ValueError(f"{self.event_type.value} requires a non-empty incident_id")
        if self.event_type is ExternalRiskEventType.FALL_CONFIRMED:
            if self.confidence is None:
                raise ValueError("FALL_CONFIRMED requires confidence")
        if self.event_type is ExternalRiskEventType.RECOVERY_CONFIRMED:
            if self.incident_id is None or not self.incident_id.strip():
                raise ValueError("RECOVERY_CONFIRMED requires a non-empty incident_id")
        if self.incident_id is not None and not self.incident_id.strip():
            raise ValueError("incident_id must be non-empty when provided")
        if self.confidence is not None:
            if not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
                raise ValueError("confidence must be finite and within [0, 1]")

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> ExternalRiskEvent:
        try:
            event_type = ExternalRiskEventType(str(payload["event_type"]))
        except KeyError as exc:
            raise ValueError("event_type is required") from exc
        except ValueError as exc:
            raise ValueError(
                "event_type must be FALL_SUSPECTED, FALL_CONFIRMED, "
                "RECOVERY_CONFIRMED, or NON_FALL"
            ) from exc

        raw_timestamp = payload.get("timestamp")
        if not isinstance(raw_timestamp, str) or not raw_timestamp.strip():
            raise ValueError("timestamp must be a non-empty RFC3339 string")
        try:
            timestamp = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("timestamp must be a valid RFC3339 value") from exc

        raw_incident_id = payload.get("incident_id")
        incident_id = None if raw_incident_id is None else str(raw_incident_id)
        raw_confidence = payload.get("confidence")
        try:
            confidence = None if raw_confidence is None else float(raw_confidence)
        except (TypeError, ValueError) as exc:
            raise ValueError("confidence must be numeric") from exc
        return cls(
            event_type=event_type,
            timestamp=timestamp,
            incident_id=incident_id,
            confidence=confidence,
        )
