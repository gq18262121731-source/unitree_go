from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    ROBOT_OFFLINE = "ROBOT_OFFLINE"
    ROBOT_STATE_STALE = "ROBOT_STATE_STALE"
    SDK_NOT_INITIALIZED = "SDK_NOT_INITIALIZED"
    SDK_TIMEOUT = "SDK_TIMEOUT"
    SDK_COMMAND_FAILED = "SDK_COMMAND_FAILED"
    CONTROL_BUSY = "CONTROL_BUSY"
    INVALID_MOTION_PARAMETER = "INVALID_MOTION_PARAMETER"
    CAMERA_UNAVAILABLE = "CAMERA_UNAVAILABLE"
    CAMERA_DECODE_FAILED = "CAMERA_DECODE_FAILED"
    EMERGENCY_STOPPED = "EMERGENCY_STOPPED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class GatewayError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str, http_status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status

