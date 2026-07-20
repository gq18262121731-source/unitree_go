from __future__ import annotations

import os
from dataclasses import dataclass


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    return default if value is None or value == "" else float(value)


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None or value == "" else int(value)


@dataclass(frozen=True)
class Settings:
    robot_id: str = os.getenv("GO2_ROBOT_ID", "go2-edu-001")
    camera_id: str = os.getenv("GO2_CAMERA_ID", "go2_front")
    network_interface: str = os.getenv("GO2_NETWORK_INTERFACE", "")
    wireless_ip: str = os.getenv("GO2_WIRELESS_IP", "")
    sdk_timeout_seconds: float = _float_env("GO2_SDK_TIMEOUT_SECONDS", 3.0)
    frame_stale_seconds: float = _float_env("GO2_FRAME_STALE_SECONDS", 3.0)
    reconnect_initial_seconds: float = _float_env("GO2_RECONNECT_INITIAL_SECONDS", 1.0)
    reconnect_max_seconds: float = _float_env("GO2_RECONNECT_MAX_SECONDS", 5.0)
    capture_fps: float = _float_env("GO2_CAPTURE_FPS", 15.0)
    mjpeg_fps: float = _float_env("GO2_MJPEG_FPS", 10.0)
    jpeg_quality: int = _int_env("GO2_JPEG_QUALITY", 70)
    upload_enabled: bool = _bool_env("GO2_UPLOAD_ENABLED", False)
    upload_url: str = os.getenv("GO2_UPLOAD_URL", "http://127.0.0.1:8092/api/video/frame")
    upload_token: str = os.getenv("GO2_UPLOAD_TOKEN", "local-test-token")
    upload_fps: float = _float_env("GO2_UPLOAD_FPS", 2.0)
    upload_timeout_seconds: float = _float_env("GO2_UPLOAD_TIMEOUT_SECONDS", 1.0)
    upload_max_bytes: int = _int_env("GO2_UPLOAD_MAX_BYTES", 512000)
    heartbeat_url: str = os.getenv("GO2_HEARTBEAT_URL", "http://127.0.0.1:8092/api/video/heartbeat")
    heartbeat_interval_seconds: float = _float_env("GO2_HEARTBEAT_INTERVAL_SECONDS", 5.0)


def load_settings() -> Settings:
    return Settings()
