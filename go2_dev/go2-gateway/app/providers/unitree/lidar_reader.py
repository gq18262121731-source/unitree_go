from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def _iso(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, timezone.utc).isoformat()


def _source_epoch(sample: Any) -> float | None:
    header = getattr(sample, "header", None)
    stamp = getattr(header, "stamp", None)
    seconds = getattr(stamp, "sec", None)
    nanoseconds = getattr(stamp, "nanosec", None)
    if seconds is None or nanoseconds is None:
        return None
    return float(seconds) + float(nanoseconds) / 1_000_000_000.0


@dataclass
class LidarReader:
    topic: str | None = None
    sample_count: int = 0
    first_received_epoch: float | None = None
    last_received_epoch: float | None = None
    last_source_epoch: float | None = None
    last_frame: str | None = None
    last_points: int | None = None
    last_latency_ms: float | None = None

    def consume(self, topic: str, sample: Any, received_epoch: float) -> None:
        height = int(getattr(sample, "height", 0) or 0)
        width = int(getattr(sample, "width", 0) or 0)
        points = height * width
        if points == 0:
            point_step = int(getattr(sample, "point_step", 0) or 0)
            data = getattr(sample, "data", None)
            if point_step > 0 and data is not None:
                points = len(data) // point_step

        source_epoch = _source_epoch(sample)
        latency_ms = None
        if source_epoch is not None:
            latency_ms = round((received_epoch - source_epoch) * 1000.0, 3)

        self.topic = topic
        self.sample_count += 1
        if self.first_received_epoch is None:
            self.first_received_epoch = received_epoch
        self.last_received_epoch = received_epoch
        self.last_source_epoch = source_epoch
        self.last_frame = getattr(getattr(sample, "header", None), "frame_id", None)
        self.last_points = points
        self.last_latency_ms = latency_ms

    def report(self) -> dict[str, Any]:
        frequency = None
        if (
            self.sample_count > 1
            and self.first_received_epoch is not None
            and self.last_received_epoch is not None
        ):
            elapsed = self.last_received_epoch - self.first_received_epoch
            if elapsed > 0:
                frequency = round((self.sample_count - 1) / elapsed, 3)
        return {
            "topic": self.topic,
            "sample_count": self.sample_count,
            "frequency_hz": frequency,
            "points": self.last_points,
            "frame": self.last_frame,
            "source_timestamp": (
                _iso(self.last_source_epoch) if self.last_source_epoch is not None else None
            ),
            "received_at": (
                _iso(self.last_received_epoch) if self.last_received_epoch is not None else None
            ),
            "latency_ms": self.last_latency_ms,
        }
