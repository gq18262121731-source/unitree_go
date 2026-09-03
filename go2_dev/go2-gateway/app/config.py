from __future__ import annotations

import os
from dataclasses import dataclass


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return float(value)


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env(names: tuple[str, ...], default: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value != "":
            return value
    return default


def _float_env_alias(names: tuple[str, ...], default: float) -> float:
    return float(_env(names, str(default)))


def _int_env_alias(names: tuple[str, ...], default: int) -> int:
    return int(_env(names, str(default)))


def _bool_env_alias(names: tuple[str, ...], default: bool) -> bool:
    for name in names:
        value = os.getenv(name)
        if value is not None and value != "":
            return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


@dataclass(frozen=True)
class Settings:
    mode: str = os.getenv("GO2_MODE", "mock").lower()
    robot_id: str = os.getenv("GO2_ROBOT_ID", "go2-edu-001")
    robot_ip: str = _env(("UNITREE_ROBOT_IP", "GO2_ROBOT_IP"), "192.168.123.161")
    network_interface: str = _env(("UNITREE_NETWORK_INTERFACE", "GO2_NETWORK_INTERFACE"), "enp3s0")
    domain_id: int = _int_env_alias(("UNITREE_DOMAIN_ID", "GO2_DOMAIN_ID"), 0)
    dds_timeout_seconds: float = _float_env_alias(("UNITREE_DDS_TIMEOUT_SECONDS", "GO2_DDS_TIMEOUT_SECONDS"), 10.0)
    require_dds_state: bool = _bool_env_alias(("UNITREE_REQUIRE_DDS_STATE", "GO2_REQUIRE_DDS_STATE"), True)
    control_enabled: bool = _bool_env("GO2_CONTROL_ENABLED", True)
    read_only_mode: bool = _bool_env("GO2_READ_ONLY_MODE", False)
    sdk_timeout_seconds: float = _float_env("GO2_SDK_TIMEOUT_SECONDS", 3.0)
    state_stale_seconds: float = _float_env("GO2_STATE_STALE_SECONDS", 2.0)
    webrtc_reconnect_initial_seconds: float = _float_env(
        "GO2_WEBRTC_RECONNECT_INITIAL_SECONDS", 2.0
    )
    webrtc_reconnect_step_seconds: float = _float_env(
        "GO2_WEBRTC_RECONNECT_STEP_SECONDS", 2.0
    )
    webrtc_reconnect_max_seconds: float = _float_env(
        "GO2_WEBRTC_RECONNECT_MAX_SECONDS", 10.0
    )
    webrtc_reconnect_on_stale: bool = _bool_env(
        "GO2_WEBRTC_RECONNECT_ON_STALE", False
    )
    webrtc_stale_grace_seconds: float = _float_env(
        "GO2_WEBRTC_STALE_GRACE_SECONDS", 10.0
    )
    webrtc_enable_sport_state: bool = _bool_env(
        "GO2_WEBRTC_ENABLE_SPORT_STATE", False
    )
    webrtc_enable_uwb: bool = _bool_env("GO2_WEBRTC_ENABLE_UWB", False)
    webrtc_enable_multiple_state: bool = _bool_env(
        "GO2_WEBRTC_ENABLE_MULTIPLE_STATE", False
    )
    webrtc_enable_low_state: bool = _bool_env(
        "GO2_WEBRTC_ENABLE_LOW_STATE", False
    )
    webrtc_enable_audio: bool = _bool_env("GO2_WEBRTC_ENABLE_AUDIO", False)
    max_vx: float = _float_env("GO2_MAX_VX", 0.30)
    max_vy: float = _float_env("GO2_MAX_VY", 0.0)
    max_wz: float = _float_env("GO2_MAX_WZ", 0.30)
    max_move_duration: float = _float_env("GO2_MAX_MOVE_DURATION", 1.0)
    min_move_duration: float = _float_env("GO2_MIN_MOVE_DURATION", 0.05)
    control_watchdog_seconds: float = _float_env("GO2_CONTROL_WATCHDOG_SECONDS", 0.5)
    follow_simulation: bool = _bool_env("FOLLOW_SIMULATION", True)
    follow_execution_enabled: bool = _bool_env("FOLLOW_EXECUTION_ENABLED", False)
    phase7_motion_execution_enabled: bool = _bool_env(
        "PHASE7_MOTION_EXECUTION_ENABLED", False
    )
    phase7_require_external_risk_feed: bool = _bool_env(
        "PHASE7_REQUIRE_EXTERNAL_RISK_FEED", True
    )
    companion_config_path: str = os.getenv(
        "GO2_COMPANION_CONFIG", "configs/companion_follow_demo.yaml"
    )
    companion_startup_timeout_seconds: float = _float_env(
        "GO2_COMPANION_STARTUP_TIMEOUT_SECONDS", 3.0
    )
    companion_risk_events_path: str = os.getenv(
        "GO2_COMPANION_RISK_EVENTS_PATH", ""
    )
    companion_state_path: str = os.getenv(
        "GO2_COMPANION_STATE_PATH", "data/companion_lifecycle_state.json"
    )
    uwb_bearing_source: str = os.getenv(
        "UWB_BEARING_SOURCE", "orientation_est"
    ).lower()
    uwb_bearing_unit: str = os.getenv("UWB_BEARING_UNIT", "radians").lower()
    uwb_bearing_sign: int = _int_env("UWB_BEARING_SIGN", 1)
    uwb_bearing_zero_offset_rad: float = _float_env(
        "UWB_BEARING_ZERO_OFFSET_RAD", 0.55
    )
    follow_velocity_feedforward_enabled: bool = _bool_env(
        "FOLLOW_VELOCITY_FEEDFORWARD_ENABLED", False
    )
    follow_velocity_feedforward_gain: float = _float_env(
        "FOLLOW_VELOCITY_FEEDFORWARD_GAIN", 1.0
    )
    follow_velocity_filter_alpha: float = _float_env(
        "FOLLOW_VELOCITY_FILTER_ALPHA", 0.4
    )
    follow_max_estimated_target_speed: float = _float_env(
        "FOLLOW_MAX_ESTIMATED_TARGET_SPEED", 0.3
    )
    follow_max_plausible_target_speed: float = _float_env(
        "FOLLOW_MAX_PLAUSIBLE_TARGET_SPEED", 0.8
    )
    follow_target_forward_enabled: bool = _bool_env(
        "FOLLOW_TARGET_FORWARD_ENABLED", False
    )
    follow_target_monitoring_enabled: bool = _bool_env(
        "FOLLOW_TARGET_MONITORING_ENABLED", True
    )
    follow_target_forward_host: str = os.getenv(
        "FOLLOW_TARGET_FORWARD_HOST", ""
    ).strip()
    follow_target_forward_port: int = _int_env(
        "FOLLOW_TARGET_FORWARD_PORT", 8766
    )
    follow_target_forward_hz: float = _float_env(
        "FOLLOW_TARGET_FORWARD_HZ", 20.0
    )
    follow_target_forward_stale_seconds: float = _float_env(
        "FOLLOW_TARGET_FORWARD_STALE_SECONDS", 1.0
    )
    follow_target_forward_stats_interval_seconds: float = _float_env(
        "FOLLOW_TARGET_FORWARD_STATS_INTERVAL_SECONDS", 10.0
    )
    camera_timeout_seconds: float = _float_env("GO2_CAMERA_TIMEOUT_SECONDS", 3.0)
    camera_snapshot_url: str = os.getenv("GO2_CAMERA_SNAPSHOT_URL", "/api/camera/snapshot")
    camera_stream_url: str = os.getenv("GO2_CAMERA_STREAM_URL", "/api/camera/stream")
    camera_stream_interval_seconds: float = _float_env("GO2_CAMERA_STREAM_INTERVAL_SECONDS", 0.5)
    task_evidence_dir: str = os.getenv("GO2_TASK_EVIDENCE_DIR", "data/task_evidence")
    voice_mode: str = os.getenv("GO2_VOICE_MODE", "mock")
    fall_prompt: str = os.getenv("GO2_FALL_PROMPT", "\u60a8\u597d\uff0c\u8bf7\u95ee\u60a8\u73b0\u5728\u662f\u5426\u9700\u8981\u5e2e\u52a9\uff1f")
    voice_prompt_url: str = os.getenv("GO2_VOICE_PROMPT_URL", "")
    voice_prompt_timeout_seconds: float = _float_env("GO2_VOICE_PROMPT_TIMEOUT_SECONDS", 2.0)
    voice_prompt_retries: int = _int_env("GO2_VOICE_PROMPT_RETRIES", 1)
    voice_prompt_retry_delay_seconds: float = _float_env("GO2_VOICE_PROMPT_RETRY_DELAY_SECONDS", 0.2)
    elder_response_timeout_seconds: float = _float_env("GO2_ELDER_RESPONSE_TIMEOUT_SECONDS", 3.0)
    mock_confirm_fall_outcome: str = os.getenv("GO2_MOCK_CONFIRM_FALL_OUTCOME", "NO_RESPONSE").upper()
    task_audit_enabled: bool = _bool_env("GO2_TASK_AUDIT_ENABLED", True)
    task_audit_log_path: str = os.getenv("GO2_TASK_AUDIT_LOG_PATH", "logs/task-events.jsonl")
    location_motion_plans_json: str = os.getenv("GO2_LOCATION_MOTION_PLANS_JSON", "")
    health_new_callback_url: str = os.getenv("HEALTH_NEW_CALLBACK_URL", "")
    health_new_callback_token: str = os.getenv("HEALTH_NEW_CALLBACK_TOKEN", "")
    health_new_callback_timeout_seconds: float = _float_env("HEALTH_NEW_CALLBACK_TIMEOUT_SECONDS", 2.0)
    health_new_callback_retries: int = _int_env("HEALTH_NEW_CALLBACK_RETRIES", 2)
    health_new_callback_retry_delay_seconds: float = _float_env("HEALTH_NEW_CALLBACK_RETRY_DELAY_SECONDS", 0.2)
    log_level: str = os.getenv("GO2_LOG_LEVEL", "INFO")
    version: str = "0.1.0"


def load_settings() -> Settings:
    return Settings()
