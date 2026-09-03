from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _iso(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, timezone.utc).isoformat()


def _values(value: Any) -> list[Any]:
    return list(value or [])


def _imu(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "quaternion": _values(getattr(value, "quaternion", None)),
        "gyroscope": _values(getattr(value, "gyroscope", None)),
        "accelerometer": _values(getattr(value, "accelerometer", None)),
        "rpy": _values(getattr(value, "rpy", None)),
        "temperature": getattr(value, "temperature", None),
    }


@dataclass
class TopicSamples:
    topic: str | None = None
    sample_count: int = 0
    first_received_epoch: float | None = None
    last_received_epoch: float | None = None
    last_payload: dict[str, Any] | None = None

    def record(self, topic: str, payload: dict[str, Any], received_epoch: float) -> None:
        self.topic = topic
        self.sample_count += 1
        if self.first_received_epoch is None:
            self.first_received_epoch = received_epoch
        self.last_received_epoch = received_epoch
        self.last_payload = payload

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
            "first_received_at": (
                _iso(self.first_received_epoch) if self.first_received_epoch is not None else None
            ),
            "last_received_at": (
                _iso(self.last_received_epoch) if self.last_received_epoch is not None else None
            ),
            "value": self.last_payload,
        }


@dataclass
class StateReader:
    low_state: TopicSamples = field(default_factory=TopicSamples)
    sport_mode_state: TopicSamples = field(default_factory=TopicSamples)

    def consume_low_state(self, topic: str, sample: Any, received_epoch: float) -> None:
        bms = getattr(sample, "bms_state", None)
        payload = {
            "battery": {
                "percentage": getattr(bms, "soc", None),
                "voltage": getattr(sample, "power_v", None),
                "current": getattr(sample, "power_a", None),
            },
            "imu": _imu(getattr(sample, "imu_state", None)),
            "tick": getattr(sample, "tick", None),
        }
        self.low_state.record(topic, payload, received_epoch)

    def consume_sport_mode_state(self, topic: str, sample: Any, received_epoch: float) -> None:
        stamp = getattr(sample, "stamp", None)
        payload = {
            "mode": getattr(sample, "mode", None),
            "position": _values(getattr(sample, "position", None)),
            "imu": _imu(getattr(sample, "imu_state", None)),
            "source_timestamp": {
                "sec": getattr(stamp, "sec", None),
                "nanosec": getattr(stamp, "nanosec", None),
            },
        }
        self.sport_mode_state.record(topic, payload, received_epoch)

    def report(self) -> dict[str, Any]:
        return {
            "low_state": self.low_state.report(),
            "sport_mode_state": self.sport_mode_state.report(),
        }
