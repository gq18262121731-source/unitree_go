from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


ALLOWED_KINDS = frozenset(
    {
        "robot",
        "lidar",
        "imu",
        "odometry",
        "lidar_state",
    }
)


@dataclass(frozen=True)
class SensorEvent:
    """Transport-neutral observation received from a read-only source."""

    kind: str
    topic: str
    received_timestamp_ns: int
    source_timestamp_ns: int | None = None
    frame_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in ALLOWED_KINDS:
            raise ValueError(f"unsupported read-only event kind: {self.kind}")
        if not self.topic:
            raise ValueError("topic must not be empty")
        if self.received_timestamp_ns < 0:
            raise ValueError("received_timestamp_ns must be non-negative")
        if self.source_timestamp_ns is not None and self.source_timestamp_ns < 0:
            raise ValueError("source_timestamp_ns must be non-negative")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SensorEvent":
        return cls(
            kind=str(value["kind"]),
            topic=str(value["topic"]),
            received_timestamp_ns=int(value["received_timestamp_ns"]),
            source_timestamp_ns=(
                int(value["source_timestamp_ns"])
                if value.get("source_timestamp_ns") is not None
                else None
            ),
            frame_id=(
                str(value["frame_id"]) if value.get("frame_id") is not None else None
            ),
            payload=dict(value.get("payload") or {}),
        )

