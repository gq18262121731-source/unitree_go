from __future__ import annotations

import threading

from .errors import ErrorCode, GatewayError


class ControlLock:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def acquire(self) -> None:
        if not self._lock.acquire(blocking=False):
            raise GatewayError(ErrorCode.CONTROL_BUSY, "Another motion command is running.", 409)

    def release(self) -> None:
        if self._lock.locked():
            self._lock.release()

    @property
    def busy(self) -> bool:
        return self._lock.locked()

