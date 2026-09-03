from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .events import SensorEvent
from .sources.base import ReadonlySource


class SafetyConfigurationError(ValueError):
    pass


def _iso(timestamp_ns: int | None) -> str | None:
    if timestamp_ns is None:
        return None
    return datetime.fromtimestamp(timestamp_ns / 1_000_000_000, timezone.utc).isoformat()


@dataclass(frozen=True)
class ProviderConfig:
    provider: str = "unitree_readonly"
    real_motion_enabled: bool = False
    model: str = "Go2 X EDU"
    hardware: str = "V2.0"
    firmware: str = "V1.1.15"
    stale_after_seconds: float = 2.0
    imu_semantic_valid: bool = False
    imu_semantic_note: str = (
        "/utlidar/imu raw-specific-force semantics failed Phase 5.4.8/5.4.9 audit"
    )

    def validate(self) -> None:
        if self.provider != "unitree_readonly":
            raise SafetyConfigurationError(
                "Phase 6.1 requires provider=unitree_readonly"
            )
        if self.real_motion_enabled:
            raise SafetyConfigurationError(
                "Phase 6.1 is observation-only; real_motion_enabled must remain false"
            )
        if self.stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")


@dataclass
class _TopicTracker:
    sample_count: int = 0
    first_received_ns: int | None = None
    last_received_ns: int | None = None
    last_source_ns: int | None = None
    timestamp_rollback_count: int = 0
    topic_counts: Counter[str] = field(default_factory=Counter)
    frame_counts: Counter[str] = field(default_factory=Counter)
    last_payload: dict[str, Any] = field(default_factory=dict)

    def consume(self, event: SensorEvent) -> None:
        if (
            self.last_source_ns is not None
            and event.source_timestamp_ns is not None
            and event.source_timestamp_ns < self.last_source_ns
        ):
            self.timestamp_rollback_count += 1
        self.sample_count += 1
        if self.first_received_ns is None:
            self.first_received_ns = event.received_timestamp_ns
        self.last_received_ns = event.received_timestamp_ns
        if event.source_timestamp_ns is not None:
            self.last_source_ns = event.source_timestamp_ns
        self.topic_counts[event.topic] += 1
        if event.frame_id:
            self.frame_counts[event.frame_id] += 1
        self.last_payload = dict(event.payload)

    def snapshot(self, now_ns: int, stale_after_ns: int) -> dict[str, Any]:
        age_ms = None
        if self.last_received_ns is not None:
            age_ms = max(now_ns - self.last_received_ns, 0) / 1_000_000
        fresh = age_ms is not None and age_ms <= stale_after_ns / 1_000_000
        frequency_hz = None
        if (
            self.sample_count > 1
            and self.first_received_ns is not None
            and self.last_received_ns is not None
            and self.last_received_ns > self.first_received_ns
        ):
            frequency_hz = (self.sample_count - 1) / (
                (self.last_received_ns - self.first_received_ns) / 1_000_000_000
            )
        topic = self.topic_counts.most_common(1)[0][0] if self.topic_counts else None
        frame = self.frame_counts.most_common(1)[0][0] if self.frame_counts else None
        return {
            "available": self.sample_count > 0,
            "fresh": fresh,
            "topic": topic,
            "frame": frame,
            "sample_count": self.sample_count,
            "frequency_hz": round(frequency_hz, 3) if frequency_hz is not None else None,
            "last_source_timestamp": _iso(self.last_source_ns),
            "last_received_at": _iso(self.last_received_ns),
            "sample_age_ms": round(age_ms, 3) if age_ms is not None else None,
            "timestamp_rollback_count": self.timestamp_rollback_count,
            "value": self.last_payload,
        }


class UnitreeReadonlyProvider:
    """Normalize Go2 observations without exposing command, map, or navigation APIs."""

    provider_name = "unitree_readonly"
    real_motion_enabled = False

    def __init__(
        self,
        config: ProviderConfig | None = None,
        *,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self.config = config or ProviderConfig()
        self.config.validate()
        self._clock_ns = clock_ns
        self._transport_source: str | None = None
        self._transport_errors: list[str] = []
        self._trackers = {
            kind: _TopicTracker()
            for kind in ("robot", "lidar", "imu", "odometry", "lidar_state")
        }

    def ingest(self, event: SensorEvent) -> None:
        self._trackers[event.kind].consume(event)

    def set_transport_source(self, source_name: str) -> None:
        """Record the active observation transport without enabling any command path."""
        self._transport_source = source_name

    def collect(self, source: ReadonlySource, duration_seconds: float = 10.0) -> dict[str, Any]:
        self.set_transport_source(source.source_name)
        try:
            for event in source.events(duration_seconds):
                self.ingest(event)
        except Exception as exc:
            self._transport_errors.append(f"{type(exc).__name__}: {exc}")
        return self.snapshot()

    def snapshot(self, *, now_ns: int | None = None) -> dict[str, Any]:
        current_ns = self._clock_ns() if now_ns is None else now_ns
        stale_after_ns = int(self.config.stale_after_seconds * 1_000_000_000)
        observations = {
            name: tracker.snapshot(current_ns, stale_after_ns)
            for name, tracker in self._trackers.items()
        }
        fresh_any = any(item["fresh"] for item in observations.values())

        lidar = observations["lidar"]
        imu = observations["imu"]
        odometry = observations["odometry"]
        lidar["semantic_valid"] = True if lidar["available"] else None
        imu["semantic_valid"] = (
            self.config.imu_semantic_valid if imu["available"] else None
        )
        imu["semantic_note"] = self.config.imu_semantic_note
        odometry["semantic_valid"] = True if odometry["available"] else None

        if not fresh_any:
            health_status = "OFFLINE"
        elif self._transport_errors:
            health_status = "DEGRADED_TRANSPORT"
        elif imu["available"] and not imu["semantic_valid"]:
            health_status = "READONLY_WITH_SEMANTIC_HOLD"
        elif all(item["fresh"] for item in (lidar, imu, odometry)):
            health_status = "READONLY_READY"
        else:
            health_status = "READONLY_PARTIAL"

        return {
            "schema_version": "1.0",
            "provider": self.provider_name,
            "real_motion_enabled": False,
            "generated_at": _iso(current_ns),
            "robot": {
                "online": fresh_any,
                "model": self.config.model,
                "hardware": self.config.hardware,
                "firmware": self.config.firmware,
                "telemetry": observations["robot"],
            },
            "transport": {
                "source": self._transport_source,
                "healthy": fresh_any and not self._transport_errors,
                "errors": list(self._transport_errors),
            },
            "sensors": {
                "lidar": lidar,
                "imu": imu,
                "odometry": odometry,
                "lidar_state": observations["lidar_state"],
            },
            "capabilities": {
                "lidar": lidar["fresh"],
                "imu": imu["fresh"],
                "odometry": odometry["fresh"],
                "localization": False,
                "navigation": False,
                "motion": False,
            },
            "localization": {
                "available": False,
                "source": None,
                "reason": "INTERNAL_LOCALIZATION_NOT_VALIDATED",
            },
            "navigation": {
                "available": False,
                "reason": "PHASE_5_5_HOLD",
            },
            "motion": {
                "enabled": False,
                "commands_supported": [],
                "reason": "PHASE_6_1_READONLY_BOUNDARY",
            },
            "health": {
                "status": health_status,
                "sensor_online_is_navigation_ready": False,
            },
        }
