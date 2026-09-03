from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.companion.config import (
    CompanionConfig,
    CompanionSafetyConfig,
    FollowProfile,
    ViewAdjustConfig,
)
from app.motion.lidar_safety import LidarSafetyConfig


class CompanionConfigError(ValueError):
    """Raised when a field profile is missing, ambiguous, or unsafe."""


@dataclass(frozen=True)
class CompanionDemoConfig:
    follow: FollowProfile
    companion: CompanionConfig
    lidar: LidarSafetyConfig
    safety: CompanionSafetyConfig
    source_path: Path

    def report(self) -> dict[str, object]:
        view = self.companion.view_adjust
        return {
            "source_path": str(self.source_path),
            "follow": {
                "target_distance_m": self.follow.target_distance,
                "target_bearing_deg": math.degrees(
                    self.follow.target_bearing_radians
                ),
                "start_distance_m": self.follow.follow_start_distance,
                "stop_distance_m": self.follow.follow_stop_distance,
                "walk_min_mps": self.follow.walk_min,
                "vx_max_mps": self.follow.vx_max,
                "wz_max_radps": self.follow.wz_max,
                "bearing_deadband_deg": math.degrees(
                    self.follow.bearing_deadband_radians
                ),
                "person_stop_hold_s": self.follow.person_stop_hold_seconds,
                "uwb_timeout_s": self.follow.uwb_timeout_seconds,
            },
            "lidar": {
                "slow_distance_m": self.lidar.slow_distance,
                "stop_distance_m": self.lidar.stop_distance,
                "slow_scale": self.lidar.slow_speed_scale,
                "roi_min_z_m": self.lidar.roi_min_z,
                "roi_width_m": self.lidar.roi_half_width * 2.0,
            },
            "view_adjust": {
                "enabled": view.enabled,
                "max_wz_radps": view.max_wz,
                "target_bearing_deg": math.degrees(view.target_bearing_radians),
                "deadband_deg": math.degrees(view.deadband_radians),
                "gain": view.gain,
            },
            "safety": {
                "require_manual_resume_after_preempt": (
                    self.safety.require_manual_resume_after_preempt
                ),
                "watchdog_s": self.safety.watchdog_seconds,
            },
        }


def load_companion_demo_config(path: str | Path) -> CompanionDemoConfig:
    try:
        return _load_companion_demo_config(path)
    except CompanionConfigError:
        raise
    except ValueError as exc:
        raise CompanionConfigError(f"invalid companion config: {exc}") from exc


def _load_companion_demo_config(path: str | Path) -> CompanionDemoConfig:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise CompanionConfigError(f"companion config does not exist: {source}")
    try:
        payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise CompanionConfigError(f"cannot read companion config: {exc}") from exc

    root = _mapping(payload, "root")
    _reject_unknown(root, {"follow", "lidar", "view_adjust", "safety"}, "root")
    _require_keys(root, {"follow", "lidar", "view_adjust", "safety"}, "root")

    follow_values = _mapping(root["follow"], "follow")
    _reject_unknown(
        follow_values,
        {
            "target_distance_m",
            "target_bearing_deg",
            "start_distance_m",
            "stop_distance_m",
            "walk_min_mps",
            "vx_max_mps",
            "wz_max_radps",
            "bearing_deadband_deg",
            "person_stop_hold_s",
            "uwb_timeout_s",
            "control_frequency_hz",
            "hard_min_distance_m",
            "hard_max_distance_m",
            "kx",
            "ky",
        },
        "follow",
    )
    _require_keys(
        follow_values,
        {
            "target_distance_m",
            "start_distance_m",
            "stop_distance_m",
            "walk_min_mps",
            "vx_max_mps",
            "wz_max_radps",
            "bearing_deadband_deg",
            "person_stop_hold_s",
            "uwb_timeout_s",
        },
        "follow",
    )
    follow = FollowProfile(
        control_frequency_hz=_number(
            follow_values.get("control_frequency_hz", 10.0),
            "follow.control_frequency_hz",
        ),
        target_distance=_number(
            follow_values["target_distance_m"], "follow.target_distance_m"
        ),
        target_bearing_radians=math.radians(
            _number(
                follow_values.get(
                    "target_bearing_deg", math.degrees(math.atan2(0.5, 1.5))
                ),
                "follow.target_bearing_deg",
            )
        ),
        min_distance=_number(
            follow_values.get("hard_min_distance_m", 1.0),
            "follow.hard_min_distance_m",
        ),
        max_distance=_number(
            follow_values.get("hard_max_distance_m", 2.5),
            "follow.hard_max_distance_m",
        ),
        follow_start_distance=_number(
            follow_values["start_distance_m"], "follow.start_distance_m"
        ),
        follow_stop_distance=_number(
            follow_values["stop_distance_m"], "follow.stop_distance_m"
        ),
        bearing_deadband_radians=math.radians(
            _number(
                follow_values["bearing_deadband_deg"],
                "follow.bearing_deadband_deg",
            )
        ),
        person_stop_hold_seconds=_number(
            follow_values["person_stop_hold_s"], "follow.person_stop_hold_s"
        ),
        uwb_timeout_seconds=_number(
            follow_values["uwb_timeout_s"], "follow.uwb_timeout_s"
        ),
        walk_min=_number(follow_values["walk_min_mps"], "follow.walk_min_mps"),
        vx_max=_number(follow_values["vx_max_mps"], "follow.vx_max_mps"),
        wz_max=_number(follow_values["wz_max_radps"], "follow.wz_max_radps"),
        kx=_number(follow_values.get("kx", 0.20), "follow.kx"),
        ky=_number(follow_values.get("ky", 0.80), "follow.ky"),
    )

    lidar_values = _mapping(root["lidar"], "lidar")
    _reject_unknown(
        lidar_values,
        {
            "slow_distance_m",
            "stop_distance_m",
            "slow_scale",
            "roi_min_z_m",
            "roi_width_m",
            "roi_min_x_m",
            "roi_max_x_m",
            "roi_max_z_m",
            "stale_timeout_s",
            "minimum_cloud_points",
            "minimum_obstacle_points",
            "clear_samples_required",
        },
        "lidar",
    )
    _require_keys(
        lidar_values,
        {
            "slow_distance_m",
            "stop_distance_m",
            "slow_scale",
            "roi_min_z_m",
            "roi_width_m",
        },
        "lidar",
    )
    roi_width = _number(lidar_values["roi_width_m"], "lidar.roi_width_m")
    lidar = LidarSafetyConfig(
        slow_distance=_number(
            lidar_values["slow_distance_m"], "lidar.slow_distance_m"
        ),
        stop_distance=_number(
            lidar_values["stop_distance_m"], "lidar.stop_distance_m"
        ),
        slow_speed_scale=_number(lidar_values["slow_scale"], "lidar.slow_scale"),
        roi_min_z=_number(lidar_values["roi_min_z_m"], "lidar.roi_min_z_m"),
        roi_half_width=roi_width / 2.0,
        roi_min_x=_number(lidar_values.get("roi_min_x_m", 0.10), "lidar.roi_min_x_m"),
        roi_max_x=_number(lidar_values.get("roi_max_x_m", 2.00), "lidar.roi_max_x_m"),
        roi_max_z=_number(lidar_values.get("roi_max_z_m", 0.65), "lidar.roi_max_z_m"),
        stale_timeout_seconds=_number(
            lidar_values.get("stale_timeout_s", 0.35), "lidar.stale_timeout_s"
        ),
        minimum_cloud_points=_integer(
            lidar_values.get("minimum_cloud_points", 10),
            "lidar.minimum_cloud_points",
        ),
        minimum_obstacle_points=_integer(
            lidar_values.get("minimum_obstacle_points", 3),
            "lidar.minimum_obstacle_points",
        ),
        clear_samples_required=_integer(
            lidar_values.get("clear_samples_required", 3),
            "lidar.clear_samples_required",
        ),
    )

    view_values = _mapping(root["view_adjust"], "view_adjust")
    _reject_unknown(
        view_values,
        {"enabled", "max_wz_radps", "target_bearing_deg", "deadband_deg", "gain"},
        "view_adjust",
    )
    _require_keys(
        view_values,
        {"enabled", "max_wz_radps", "target_bearing_deg", "deadband_deg"},
        "view_adjust",
    )
    view_adjust = ViewAdjustConfig(
        enabled=_boolean(view_values["enabled"], "view_adjust.enabled"),
        max_wz=_number(view_values["max_wz_radps"], "view_adjust.max_wz_radps"),
        target_bearing_radians=math.radians(
            _number(
                view_values["target_bearing_deg"],
                "view_adjust.target_bearing_deg",
            )
        ),
        deadband_radians=math.radians(
            _number(view_values["deadband_deg"], "view_adjust.deadband_deg")
        ),
        gain=_number(view_values.get("gain", 0.80), "view_adjust.gain"),
    )

    safety_values = _mapping(root["safety"], "safety")
    _reject_unknown(
        safety_values,
        {"require_manual_resume_after_preempt", "watchdog_s"},
        "safety",
    )
    _require_keys(
        safety_values,
        {"require_manual_resume_after_preempt", "watchdog_s"},
        "safety",
    )
    safety = CompanionSafetyConfig(
        require_manual_resume_after_preempt=_boolean(
            safety_values["require_manual_resume_after_preempt"],
            "safety.require_manual_resume_after_preempt",
        ),
        watchdog_seconds=_number(safety_values["watchdog_s"], "safety.watchdog_s"),
    )
    return CompanionDemoConfig(
        follow=follow,
        companion=CompanionConfig(view_adjust=view_adjust),
        lidar=lidar,
        safety=safety,
        source_path=source,
    )


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CompanionConfigError(f"{name} must be a YAML mapping")
    if not all(isinstance(key, str) for key in value):
        raise CompanionConfigError(f"{name} keys must be strings")
    return value


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CompanionConfigError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise CompanionConfigError(f"{name} must be finite")
    return result


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CompanionConfigError(f"{name} must be an integer")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise CompanionConfigError(f"{name} must be a boolean")
    return value


def _reject_unknown(
    values: Mapping[str, object], allowed: set[str], section: str
) -> None:
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise CompanionConfigError(
            f"{section} contains unknown keys: {', '.join(unknown)}"
        )


def _require_keys(
    values: Mapping[str, object], required: set[str], section: str
) -> None:
    missing = sorted(required - set(values))
    if missing:
        raise CompanionConfigError(
            f"{section} is missing required keys: {', '.join(missing)}"
        )
