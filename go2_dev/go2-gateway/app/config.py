from __future__ import annotations

import os
from dataclasses import dataclass


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return float(value)


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    mode: str = os.getenv("GO2_MODE", "mock").lower()
    robot_id: str = os.getenv("GO2_ROBOT_ID", "go2-edu-001")
    network_interface: str = os.getenv("GO2_NETWORK_INTERFACE", "enp3s0")
    control_enabled: bool = _bool_env("GO2_CONTROL_ENABLED", True)
    sdk_timeout_seconds: float = _float_env("GO2_SDK_TIMEOUT_SECONDS", 3.0)
    state_stale_seconds: float = _float_env("GO2_STATE_STALE_SECONDS", 2.0)
    max_vx: float = _float_env("GO2_MAX_VX", 0.20)
    max_vy: float = _float_env("GO2_MAX_VY", 0.15)
    max_wz: float = _float_env("GO2_MAX_WZ", 0.35)
    max_move_duration: float = _float_env("GO2_MAX_MOVE_DURATION", 1.0)
    min_move_duration: float = _float_env("GO2_MIN_MOVE_DURATION", 0.05)
    control_watchdog_seconds: float = _float_env("GO2_CONTROL_WATCHDOG_SECONDS", 0.5)
    camera_timeout_seconds: float = _float_env("GO2_CAMERA_TIMEOUT_SECONDS", 3.0)
    log_level: str = os.getenv("GO2_LOG_LEVEL", "INFO")
    version: str = "0.1.0"


def load_settings() -> Settings:
    return Settings()
