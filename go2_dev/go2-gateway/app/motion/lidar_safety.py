from __future__ import annotations

import math
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable, Sequence


class LidarSafetyLevel(str, Enum):
    CLEAR = "CLEAR"
    SLOW = "SLOW"
    STOP = "STOP"


@dataclass(frozen=True)
class LidarSafetyConfig:
    """Conservative forward ROI expressed in the robot base frame."""

    stop_distance: float = 0.65
    slow_distance: float = 1.20
    roi_min_x: float = 0.10
    roi_max_x: float = 2.00
    roi_half_width: float = 0.45
    roi_min_z: float = -0.35
    roi_max_z: float = 0.65
    minimum_cloud_points: int = 10
    minimum_obstacle_points: int = 3
    stale_timeout_seconds: float = 0.35
    slow_speed_scale: float = 0.35
    clear_samples_required: int = 3
    accepted_frames: tuple[str, ...] = ("base_link", "cloud_base")

    def __post_init__(self) -> None:
        for name in (
            "stop_distance",
            "slow_distance",
            "roi_min_x",
            "roi_max_x",
            "roi_half_width",
            "stale_timeout_seconds",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and greater than zero")
        if self.stop_distance >= self.slow_distance:
            raise ValueError("stop_distance must be less than slow_distance")
        if self.roi_min_x >= self.roi_max_x:
            raise ValueError("roi_min_x must be less than roi_max_x")
        if not math.isfinite(self.roi_min_z) or not math.isfinite(self.roi_max_z):
            raise ValueError("ROI z bounds must be finite")
        if self.roi_min_z >= self.roi_max_z:
            raise ValueError("roi_min_z must be less than roi_max_z")
        if self.minimum_cloud_points < 1 or self.minimum_obstacle_points < 1:
            raise ValueError("point-count thresholds must be positive")
        if self.clear_samples_required < 1:
            raise ValueError("clear_samples_required must be positive")
        if not math.isfinite(self.slow_speed_scale) or not 0.0 < self.slow_speed_scale < 1.0:
            raise ValueError("slow_speed_scale must be in (0, 1)")
        if not self.accepted_frames:
            raise ValueError("accepted_frames must not be empty")


@dataclass(frozen=True)
class LidarSafetyDecision:
    level: LidarSafetyLevel
    stop_required: bool
    speed_scale: float
    reason: str
    nearest_distance: float | None
    roi_point_count: int
    sample_age_seconds: float | None
    frame_id: str | None


class LidarSafetyGuard:
    """Turn base-frame PointCloud2 points into CLEAR/SLOW/STOP.

    This class does not import ROS and never publishes a topic. A ROS adapter
    may decode PointCloud2 into ``(x, y, z)`` triples before calling it.
    Unknown frames, stale clouds, and malformed clouds fail closed.
    """

    def __init__(
        self,
        config: LidarSafetyConfig | None = None,
        *,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config or LidarSafetyConfig()
        self._monotonic_clock = monotonic_clock
        self._latest: LidarSafetyDecision | None = None
        self._sample_at: float | None = None
        self._clear_count = 0
        self._ready = False
        self._slow_latched = False

    def update(
        self,
        points: Iterable[Sequence[float]],
        *,
        frame_id: str,
        sample_monotonic: float | None = None,
    ) -> LidarSafetyDecision:
        sample_at = self._monotonic_clock() if sample_monotonic is None else sample_monotonic
        if not math.isfinite(sample_at):
            raise ValueError("sample_monotonic must be finite")
        if self._sample_at is not None and sample_at < self._sample_at:
            return self._force_stop("non_monotonic_lidar_time", frame_id, sample_at)
        self._sample_at = sample_at

        if frame_id not in self.config.accepted_frames:
            return self._force_stop("untrusted_lidar_frame", frame_id, sample_at)

        cloud: list[tuple[float, float, float]] = []
        try:
            for point in points:
                if len(point) < 3:
                    return self._force_stop("malformed_cloud", frame_id, sample_at)
                xyz = (float(point[0]), float(point[1]), float(point[2]))
                if all(math.isfinite(value) for value in xyz):
                    cloud.append(xyz)
        except (TypeError, ValueError, IndexError):
            return self._force_stop("malformed_cloud", frame_id, sample_at)

        if len(cloud) < self.config.minimum_cloud_points:
            return self._force_stop("insufficient_cloud_points", frame_id, sample_at)

        roi_distances = [
            math.hypot(x, y)
            for x, y, z in cloud
            if self.config.roi_min_x <= x <= self.config.roi_max_x
            and abs(y) <= self.config.roi_half_width
            and self.config.roi_min_z <= z <= self.config.roi_max_z
        ]
        nearest = min(roi_distances) if roi_distances else None
        obstacle_points = len(roi_distances)
        stop_zone_points = sum(
            distance <= self.config.stop_distance for distance in roi_distances
        )
        slow_zone_points = sum(
            distance <= self.config.slow_distance for distance in roi_distances
        )

        if (
            nearest is not None
            and stop_zone_points >= self.config.minimum_obstacle_points
            and nearest <= self.config.stop_distance
        ):
            return self._force_stop(
                "obstacle_in_stop_zone",
                frame_id,
                sample_at,
                nearest,
                obstacle_points,
            )

        if not self._ready:
            if nearest is None or nearest > self.config.slow_distance:
                self._clear_count += 1
            else:
                self._clear_count = 0
            if self._clear_count < self.config.clear_samples_required:
                return self._decision(
                    LidarSafetyLevel.STOP,
                    "clearance_confirmation_pending",
                    nearest,
                    obstacle_points,
                    frame_id,
                    0.0,
                )
            self._ready = True
            self._clear_count = 0

        if (
            nearest is not None
            and slow_zone_points >= self.config.minimum_obstacle_points
            and nearest <= self.config.slow_distance
        ):
            self._slow_latched = True
            self._clear_count = 0
            return self._decision(
                LidarSafetyLevel.SLOW,
                "obstacle_in_slow_zone",
                nearest,
                obstacle_points,
                frame_id,
                self.config.slow_speed_scale,
            )

        if self._slow_latched:
            self._clear_count += 1
            if self._clear_count < self.config.clear_samples_required:
                return self._decision(
                    LidarSafetyLevel.SLOW,
                    "slow_clearance_confirmation_pending",
                    nearest,
                    obstacle_points,
                    frame_id,
                    self.config.slow_speed_scale,
                )
            self._slow_latched = False
            self._clear_count = 0

        return self._decision(
            LidarSafetyLevel.CLEAR,
            "forward_roi_clear",
            nearest,
            obstacle_points,
            frame_id,
            1.0,
        )

    def evaluate(self, *, now_monotonic: float | None = None) -> LidarSafetyDecision:
        now = self._monotonic_clock() if now_monotonic is None else now_monotonic
        if not math.isfinite(now):
            raise ValueError("now_monotonic must be finite")
        if self._latest is None or self._sample_at is None:
            return self._force_stop("lidar_not_ready", None, now)
        age = now - self._sample_at
        if age < 0.0:
            return self._force_stop("non_monotonic_lidar_time", self._latest.frame_id, now)
        if age >= self.config.stale_timeout_seconds:
            return self._force_stop("lidar_stale", self._latest.frame_id, now)
        return LidarSafetyDecision(
            level=self._latest.level,
            stop_required=self._latest.stop_required,
            speed_scale=self._latest.speed_scale,
            reason=self._latest.reason,
            nearest_distance=self._latest.nearest_distance,
            roi_point_count=self._latest.roi_point_count,
            sample_age_seconds=age,
            frame_id=self._latest.frame_id,
        )

    def snapshot(self, *, now_monotonic: float | None = None) -> LidarSafetyDecision:
        """Return current LiDAR readiness without changing guard latches."""

        now = self._monotonic_clock() if now_monotonic is None else now_monotonic
        if not math.isfinite(now):
            raise ValueError("now_monotonic must be finite")
        if self._latest is None or self._sample_at is None:
            return LidarSafetyDecision(
                level=LidarSafetyLevel.STOP,
                stop_required=True,
                speed_scale=0.0,
                reason="lidar_not_ready",
                nearest_distance=None,
                roi_point_count=0,
                sample_age_seconds=None,
                frame_id=None,
            )
        age = now - self._sample_at
        if age < 0.0:
            reason = "non_monotonic_lidar_time"
            level = LidarSafetyLevel.STOP
            stop_required = True
            speed_scale = 0.0
        elif age >= self.config.stale_timeout_seconds:
            reason = "lidar_stale"
            level = LidarSafetyLevel.STOP
            stop_required = True
            speed_scale = 0.0
        else:
            reason = self._latest.reason
            level = self._latest.level
            stop_required = self._latest.stop_required
            speed_scale = self._latest.speed_scale
        return LidarSafetyDecision(
            level=level,
            stop_required=stop_required,
            speed_scale=speed_scale,
            reason=reason,
            nearest_distance=self._latest.nearest_distance,
            roi_point_count=self._latest.roi_point_count,
            sample_age_seconds=age,
            frame_id=self._latest.frame_id,
        )

    def _force_stop(
        self,
        reason: str,
        frame_id: str | None,
        sample_at: float,
        nearest: float | None = None,
        point_count: int = 0,
    ) -> LidarSafetyDecision:
        self._ready = False
        self._slow_latched = False
        self._clear_count = 0
        self._sample_at = sample_at
        return self._decision(
            LidarSafetyLevel.STOP,
            reason,
            nearest,
            point_count,
            frame_id,
            0.0,
        )

    def _decision(
        self,
        level: LidarSafetyLevel,
        reason: str,
        nearest: float | None,
        point_count: int,
        frame_id: str | None,
        speed_scale: float,
    ) -> LidarSafetyDecision:
        decision = LidarSafetyDecision(
            level=level,
            stop_required=level is LidarSafetyLevel.STOP,
            speed_scale=speed_scale,
            reason=reason,
            nearest_distance=nearest,
            roi_point_count=point_count,
            sample_age_seconds=0.0,
            frame_id=frame_id,
        )
        self._latest = decision
        return decision
