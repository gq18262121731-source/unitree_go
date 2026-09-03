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
        self._ack_wait_deadline: float | None = None
        self._state_lock = threading.Lock()
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
        with self._state_lock:
            self._enabled = True
            self._ack_wait_deadline = None
            self._last_heartbeat = time.monotonic()

    def disarm(self) -> None:
        with self._state_lock:
            self._enabled = False
            self._ack_wait_deadline = None

    def heartbeat(self) -> None:
        with self._state_lock:
            self._last_heartbeat = time.monotonic()

    def begin_ack_wait(self, maximum_seconds: float) -> None:
        """Supervise one bounded transport ACK wait without declaring a stall."""

        now = time.monotonic()
        with self._state_lock:
            self._last_heartbeat = now
            self._ack_wait_deadline = now + max(0.0, float(maximum_seconds))

    def end_ack_wait(self) -> None:
        with self._state_lock:
            self._ack_wait_deadline = None
            self._last_heartbeat = time.monotonic()

    def trigger_stop(self, reason: str) -> None:
        self.logger.warning("Watchdog stopping robot: %s", reason)
        try:
            self.adapter.stop()
        except Exception:
            self.logger.exception("Watchdog StopMove failed")

    def _run(self) -> None:
        while not self._stop_event.wait(0.05):
            now = time.monotonic()
            with self._state_lock:
                enabled = self._enabled
                heartbeat_age = now - self._last_heartbeat
                ack_wait_deadline = self._ack_wait_deadline
            ack_wait_is_bounded_and_active = bool(
                ack_wait_deadline is not None and now <= ack_wait_deadline
            )
            if (
                enabled
                and heartbeat_age > self.timeout_seconds
                and not ack_wait_is_bounded_and_active
            ):
                self.trigger_stop("control heartbeat timeout")
                self.disarm()
