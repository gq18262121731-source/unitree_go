from __future__ import annotations

import math
from collections.abc import Collection
from datetime import datetime, timezone
from typing import Protocol

from backend.localization.models import (
    LocalizationCandidate,
    LocalizationHealth,
    LocalizationPose,
    LocalizationSource,
    LocalizationState,
    LocalizationStatus,
)


class LocalizationProvider(Protocol):
    """Read-only localization contract; it contains no lifecycle or commands."""

    def get_status(self) -> LocalizationState:
        ...

    def get_pose(self) -> LocalizationPose | None:
        ...

    def health_check(self) -> LocalizationHealth:
        ...


def _unavailable(reason: str) -> LocalizationState:
    return LocalizationState(
        state=LocalizationStatus.UNAVAILABLE,
        available=False,
        source=None,
        pose=None,
        frame=None,
        map_id=None,
        confidence=None,
        timestamp=None,
        reason=reason,
    )


class UnavailableLocalizationProvider:
    """The only active Phase 6.5 provider: no source, no pose, fail closed."""

    def __init__(self, reason: str = "NO_LOCALIZATION_SOURCE") -> None:
        self._status = _unavailable(reason)

    def get_status(self) -> LocalizationState:
        return self._status

    def get_pose(self) -> LocalizationPose | None:
        return None

    def health_check(self) -> LocalizationHealth:
        return LocalizationHealth(
            healthy=False,
            state=self._status.state,
            reason=self._status.reason,
        )


class LocalizationAdmissionController:
    """Pure admission state machine for a future read-only source.

    This controller consumes already-received candidates. It does not subscribe
    to ROS2/DDS, publish TF, start SLAM, load maps, or issue robot commands.
    """

    def __init__(
        self,
        *,
        validated_sources: Collection[LocalizationSource] = (),
        minimum_ready_samples: int = 3,
        max_sample_age_ms: float = 1_000.0,
        max_future_skew_ms: float = 50.0,
        quaternion_norm_tolerance: float = 1e-3,
    ) -> None:
        if minimum_ready_samples < 2:
            raise ValueError("minimum_ready_samples must preserve an OBSERVING stage")
        if max_sample_age_ms <= 0 or max_future_skew_ms < 0:
            raise ValueError("time thresholds must be non-negative")
        if quaternion_norm_tolerance <= 0:
            raise ValueError("quaternion_norm_tolerance must be positive")

        self.minimum_ready_samples = minimum_ready_samples
        self.max_sample_age_ms = max_sample_age_ms
        self.max_future_skew_ms = max_future_skew_ms
        self.quaternion_norm_tolerance = quaternion_norm_tolerance
        self._validated_sources = frozenset(validated_sources)
        self._valid_sample_count = 0
        self._source_key: tuple[str, str, str] | None = None
        self._last_timestamp: datetime | None = None
        self._status = _unavailable("NO_LOCALIZATION_SOURCE")

    def get_status(self) -> LocalizationState:
        return self._status

    def get_pose(self) -> LocalizationPose | None:
        if self._status.state is not LocalizationStatus.READY:
            return None
        return self._status.pose

    def health_check(self) -> LocalizationHealth:
        return LocalizationHealth(
            healthy=self._status.state is LocalizationStatus.READY,
            state=self._status.state,
            reason=self._status.reason,
        )

    def observe(
        self,
        candidate: LocalizationCandidate,
        *,
        now: datetime | None = None,
    ) -> LocalizationState:
        checked_at = now or datetime.now(timezone.utc)
        if checked_at.tzinfo is None or checked_at.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

        failure = self._validate(candidate, checked_at)
        if failure is not None:
            return self._reject(failure)

        key = (candidate.source.value, candidate.frame.strip(), candidate.map_id.strip())
        if self._source_key is not None and key != self._source_key:
            if key[0] != self._source_key[0]:
                return self._reject("SOURCE_CHANGED")
            if key[1] != self._source_key[1]:
                return self._reject("FRAME_CHANGED")
            return self._reject("MAP_ID_CHANGED")

        self._source_key = key
        self._last_timestamp = candidate.timestamp
        self._valid_sample_count += 1

        if self._valid_sample_count < self.minimum_ready_samples:
            self._status = LocalizationState(
                state=LocalizationStatus.OBSERVING,
                available=False,
                source=candidate.source,
                pose=None,
                frame=candidate.frame.strip(),
                map_id=candidate.map_id.strip(),
                confidence=candidate.confidence,
                timestamp=candidate.timestamp,
                reason="OBSERVATION_WINDOW_INCOMPLETE",
            )
            return self._status

        self._status = LocalizationState(
            state=LocalizationStatus.READY,
            available=True,
            source=candidate.source,
            pose=candidate.pose,
            frame=candidate.frame.strip(),
            map_id=candidate.map_id.strip(),
            confidence=candidate.confidence,
            timestamp=candidate.timestamp,
            reason="LOCALIZATION_GATES_PASSED",
        )
        return self._status

    def _validate(
        self,
        candidate: LocalizationCandidate,
        checked_at: datetime,
    ) -> str | None:
        if candidate.source not in self._validated_sources:
            return "SOURCE_NOT_VALIDATED"
        if not candidate.frame.strip():
            return "FRAME_MISSING"
        if not candidate.map_id.strip():
            return "MAP_ID_MISSING"
        if candidate.timestamp_rollback_count != 0:
            return "TIMESTAMP_ROLLBACK"
        if (
            self._last_timestamp is not None
            and candidate.timestamp <= self._last_timestamp
        ):
            return "TIMESTAMP_ROLLBACK"

        sample_age_ms = (checked_at - candidate.timestamp).total_seconds() * 1_000.0
        if sample_age_ms > self.max_sample_age_ms:
            return "TIMESTAMP_STALE"
        if sample_age_ms < -self.max_future_skew_ms:
            return "TIMESTAMP_IN_FUTURE"

        values = (
            candidate.pose.position.x,
            candidate.pose.position.y,
            candidate.pose.position.z,
            candidate.pose.orientation.x,
            candidate.pose.orientation.y,
            candidate.pose.orientation.z,
            candidate.pose.orientation.w,
            candidate.confidence,
        )
        if not all(math.isfinite(value) for value in values):
            return "POSE_INVALID"

        orientation = candidate.pose.orientation
        norm = math.sqrt(
            orientation.x**2
            + orientation.y**2
            + orientation.z**2
            + orientation.w**2
        )
        if abs(norm - 1.0) > self.quaternion_norm_tolerance:
            return "ORIENTATION_NOT_NORMALIZED"
        return None

    def _reject(self, reason: str) -> LocalizationState:
        self._valid_sample_count = 0
        self._source_key = None
        self._last_timestamp = None
        self._status = _unavailable(reason)
        return self._status
