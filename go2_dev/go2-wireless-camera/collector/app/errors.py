from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    CAMERA_FRAME_UNAVAILABLE = "CAMERA_FRAME_UNAVAILABLE"
    SDK_INIT_FAILED = "SDK_INIT_FAILED"
    SDK_CAPTURE_FAILED = "SDK_CAPTURE_FAILED"
    INVALID_CONFIGURATION = "INVALID_CONFIGURATION"


class CollectorError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str, http_status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
