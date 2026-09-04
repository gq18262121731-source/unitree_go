from __future__ import annotations

from app.navigation.models import SafetyChecks, SafetyInterlockResult


_BLOCK_CODES = {
    "robot_online": "ROBOT_OFFLINE",
    "emergency_stop_clear": "EMERGENCY_STOP_ACTIVE",
    "localization_valid": "LOCALIZATION_INVALID",
    "map_loaded": "MAP_NOT_LOADED",
    "path_plannable": "PATH_NOT_PLANNABLE",
    "robot_stationary": "ROBOT_NOT_STATIONARY",
    "control_available": "CONTROL_NOT_AVAILABLE",
}


def evaluate_safety_interlock(checks: SafetyChecks) -> SafetyInterlockResult:
    blocked_by = [
        _BLOCK_CODES[name]
        for name, passed in checks.model_dump().items()
        if not passed
    ]
    return SafetyInterlockResult(
        passed=not blocked_by,
        checks=checks,
        blocked_by=blocked_by,
    )


def evaluate_mapping_interlock(checks: SafetyChecks) -> SafetyInterlockResult:
    required = ("robot_online", "emergency_stop_clear", "robot_stationary", "control_available")
    blocked_by = [_BLOCK_CODES[name] for name in required if not getattr(checks, name)]
    return SafetyInterlockResult(passed=not blocked_by, checks=checks, blocked_by=blocked_by)
