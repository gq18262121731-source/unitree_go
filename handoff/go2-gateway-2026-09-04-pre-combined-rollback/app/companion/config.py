from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.follow.controller import FollowControllerConfig
from app.follow.planner import FollowOffset


@dataclass(frozen=True)
class FollowProfile:
    """All field-tunable UWB follow behavior for Go2 Companion V1."""

    control_frequency_hz: float = 10.0
    target_distance: float = 1.75
    target_bearing_radians: float = math.atan2(0.5, 1.5)
    min_distance: float = 1.0
    max_distance: float = 2.5
    follow_start_distance: float = 1.90
    follow_stop_distance: float = 1.70
    bearing_deadband_radians: float = math.radians(12.0)
    person_stop_hold_seconds: float = 1.5
    uwb_timeout_seconds: float = 2.0
    walk_min: float = 0.20
    vx_max: float = 0.30
    wz_max: float = 0.30
    kx: float = 0.20
    ky: float = 0.80

    def __post_init__(self) -> None:
        positive = (
            "control_frequency_hz",
            "target_distance",
            "min_distance",
            "max_distance",
            "follow_start_distance",
            "follow_stop_distance",
            "bearing_deadband_radians",
            "person_stop_hold_seconds",
            "uwb_timeout_seconds",
            "walk_min",
            "vx_max",
            "wz_max",
            "kx",
            "ky",
        )
        for name in positive:
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and greater than zero")
        if not math.isfinite(self.target_bearing_radians):
            raise ValueError("target_bearing_radians must be finite")
        if abs(self.target_bearing_radians) >= math.pi / 2.0:
            raise ValueError("target_bearing_radians must keep the target in front")
        if self.max_distance <= self.min_distance:
            raise ValueError("max_distance must be greater than min_distance")
        if self.follow_start_distance <= self.follow_stop_distance:
            raise ValueError(
                "follow_start_distance must be greater than follow_stop_distance"
            )
        if not self.min_distance < self.follow_stop_distance < self.max_distance:
            raise ValueError("follow_stop_distance must be within the safe range")
        if self.follow_start_distance >= self.max_distance:
            raise ValueError("follow_start_distance must be below max_distance")
        if not self.min_distance < self.target_distance < self.max_distance:
            raise ValueError("target_distance must be within the safe range")
        if self.walk_min > self.vx_max:
            raise ValueError("walk_min must not exceed vx_max")
        if self.bearing_deadband_radians >= math.pi:
            raise ValueError("bearing_deadband_radians must be below pi")

        self.follow_offset()

    @property
    def desired_bearing_radians(self) -> float:
        """Backward-compatible name used by the normal follow controller."""

        return self.target_bearing_radians

    @property
    def back_distance(self) -> float:
        return self.target_distance * math.cos(self.target_bearing_radians)

    @property
    def right_offset(self) -> float:
        return self.target_distance * math.sin(self.target_bearing_radians)

    def follow_offset(self) -> FollowOffset:
        return FollowOffset(
            back_distance=self.back_distance,
            right_offset=self.right_offset,
            min_distance=self.min_distance,
            max_distance=self.max_distance,
        )

    def controller_config(self, *, simulation_mode: bool) -> FollowControllerConfig:
        return FollowControllerConfig(
            kx=self.kx,
            ky=self.ky,
            max_vx=self.vx_max,
            max_wz=self.wz_max,
            simulation_mode=simulation_mode,
            velocity_feedforward_enabled=False,
            walking_speed_floor_enabled=True,
            minimum_walking_vx=self.walk_min,
            distance_deadband_enabled=True,
            follow_start_distance=self.follow_start_distance,
            follow_stop_distance=self.follow_stop_distance,
            bearing_deadband_radians=self.bearing_deadband_radians,
            desired_bearing_radians=self.target_bearing_radians,
        )


@dataclass(frozen=True)
class ViewAdjustConfig:
    enabled: bool = True
    max_wz: float = 0.20
    target_bearing_radians: float = 0.0
    deadband_radians: float = math.radians(8.0)
    gain: float = 0.80

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("view_adjust.enabled must be a boolean")
        for name in ("max_wz", "deadband_radians", "gain"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"view_adjust.{name} must be finite and positive")
        if not math.isfinite(self.target_bearing_radians):
            raise ValueError("view_adjust.target_bearing_radians must be finite")
        if self.deadband_radians >= math.pi:
            raise ValueError("view_adjust.deadband_radians must be below pi")


@dataclass(frozen=True)
class CompanionConfig:
    stationary_distance_delta: float = 0.05
    stationary_bearing_delta_radians: float = math.radians(5.0)
    moving_distance_delta: float = 0.10
    moving_bearing_delta_radians: float = math.radians(10.0)
    view_adjust: ViewAdjustConfig = field(default_factory=ViewAdjustConfig)

    def __post_init__(self) -> None:
        for name in (
            "stationary_distance_delta",
            "stationary_bearing_delta_radians",
            "moving_distance_delta",
            "moving_bearing_delta_radians",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and greater than zero")
        if self.moving_distance_delta <= self.stationary_distance_delta:
            raise ValueError(
                "moving_distance_delta must exceed stationary_distance_delta"
            )
        if self.moving_bearing_delta_radians <= self.stationary_bearing_delta_radians:
            raise ValueError(
                "moving_bearing_delta_radians must exceed "
                "stationary_bearing_delta_radians"
            )


@dataclass(frozen=True)
class CompanionSafetyConfig:
    require_manual_resume_after_preempt: bool = True
    watchdog_seconds: float = 0.5

    def __post_init__(self) -> None:
        if self.require_manual_resume_after_preempt is not True:
            raise ValueError(
                "require_manual_resume_after_preempt must remain true for V1"
            )
        if not math.isfinite(self.watchdog_seconds) or self.watchdog_seconds <= 0.0:
            raise ValueError("watchdog_seconds must be finite and greater than zero")
