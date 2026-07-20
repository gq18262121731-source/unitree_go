from __future__ import annotations

import logging
import threading
import time

from app.adapters.base import Stoppable


class ControlWatchdog:
    def __init__(self, adapter: Stoppable, timeout_seconds: float) -> None:
        self.adapter = adapter
        self.timeout_seconds = timeout_seconds
        self._last_heartbeat = time.monotonic()
        self._enabled = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.logger = logging.getLogger("go2_gateway.watchdog")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="go2-control-watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def arm(self) -> None:
        self._enabled = True
        self.heartbeat()

    def disarm(self) -> None:
        self._enabled = False

    def heartbeat(self) -> None:
        self._last_heartbeat = time.monotonic()

    def trigger_stop(self, reason: str) -> None:
        self.logger.warning("Watchdog stopping robot: %s", reason)
        try:
            self.adapter.stop()
        except Exception:
            self.logger.exception("Watchdog StopMove failed")

    def _run(self) -> None:
        while not self._stop_event.wait(0.05):
            if self._enabled and time.monotonic() - self._last_heartbeat > self.timeout_seconds:
                self.trigger_stop("control heartbeat timeout")
                self.disarm()

