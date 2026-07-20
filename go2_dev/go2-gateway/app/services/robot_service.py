from __future__ import annotations

import logging
import threading
import time

from app.adapters.base import RobotAdapter
from app.config import Settings
from app.core.control_lock import ControlLock
from app.core.errors import ErrorCode, GatewayError
from app.core.state_store import StateStore
from app.core.watchdog import ControlWatchdog


class RobotService:
    def __init__(self, adapter: RobotAdapter, settings: Settings, state_store: StateStore) -> None:
        self.adapter = adapter
        self.settings = settings
        self.state_store = state_store
        self.control_lock = ControlLock()
        self.watchdog = ControlWatchdog(adapter, settings.control_watchdog_seconds)
        self.logger = logging.getLogger("go2_gateway.robot")
        self._state_stop = threading.Event()
        self._state_thread: threading.Thread | None = None
        self._shutting_down = False

    def initialize(self) -> None:
        self.adapter.initialize()
        self.state_store.set_system(self.settings.version, self.adapter.sdk_version)
        self.state_store.update_control(enabled=self.settings.control_enabled)
        self.refresh_status()
        self.watchdog.start()
        self._start_state_loop()

    def close(self) -> None:
        self._shutting_down = True
        self.safe_stop("shutdown")
        self.watchdog.stop()
        self._state_stop.set()
        if self._state_thread:
            self._state_thread.join(timeout=1.0)
        self.adapter.close()

    def refresh_status(self) -> dict:
        status = self.adapter.get_status()
        self.state_store.update_status(status)
        self.state_store.update_control(enabled=self.settings.control_enabled, busy=self.control_lock.busy)
        return self.state_store.snapshot()

    def status(self) -> dict:
        self.refresh_status()
        return self.state_store.snapshot()

    def stand(self, source: str = "api") -> dict:
        return self._run_command("STAND", source, self.adapter.stand_up)

    def lie_down(self, source: str = "api") -> dict:
        def command() -> int:
            self.adapter.stop()
            return self.adapter.stand_down()
        return self._run_command("LIE_DOWN", source, command)

    def stop(self, source: str = "api") -> dict:
        code = self.safe_stop(source)
        return {"code": code}

    def emergency_stop(self, source: str = "api") -> dict:
        code = self.safe_stop(f"emergency:{source}")
        self.state_store.update_control(last_command="EMERGENCY_STOP")
        return {"code": code}

    def move(self, vx: float, vy: float, wz: float, duration: float, source: str = "api") -> dict:
        self._validate_motion(vx, vy, wz, duration)
        self._ensure_ready_for_motion()
        self.control_lock.acquire()
        self.state_store.update_control(busy=True, last_command="MOVE")
        self.watchdog.arm()
        started = time.monotonic()
        try:
            code = self.adapter.move(vx, vy, wz)
            end_time = time.monotonic() + duration
            while time.monotonic() < end_time:
                self.watchdog.heartbeat()
                time.sleep(min(0.05, max(0.0, end_time - time.monotonic())))
            elapsed = time.monotonic() - started
            self.logger.info("motion complete source=%s vx=%s vy=%s wz=%s duration=%s code=%s elapsed=%.3f", source, vx, vy, wz, duration, code, elapsed)
            if code != 0:
                raise GatewayError(ErrorCode.SDK_COMMAND_FAILED, f"Move failed, code={code}", 503)
            return {"code": code}
        except GatewayError:
            raise
        except Exception as exc:
            raise GatewayError(ErrorCode.SDK_COMMAND_FAILED, f"Move failed: {exc}", 503) from exc
        finally:
            try:
                self.adapter.stop()
            finally:
                self.watchdog.disarm()
                self.control_lock.release()
                self.state_store.update_control(busy=False, last_command="STOP")

    def safe_stop(self, source: str = "api") -> int:
        try:
            code = self.adapter.stop()
            self.state_store.update_control(last_command="STOP", busy=self.control_lock.busy)
            self.logger.info("stop source=%s code=%s", source, code)
            return code
        except Exception:
            self.logger.exception("StopMove failed source=%s", source)
            return -1

    def _run_command(self, name: str, source: str, command) -> dict:
        self._ensure_ready_for_motion()
        self.control_lock.acquire()
        self.state_store.update_control(busy=True, last_command=name)
        try:
            code = command()
            self.logger.info("command=%s source=%s code=%s", name, source, code)
            if code != 0:
                raise GatewayError(ErrorCode.SDK_COMMAND_FAILED, f"{name} failed, code={code}", 503)
            return {"code": code}
        finally:
            self.control_lock.release()
            self.state_store.update_control(busy=False)

    def _ensure_ready_for_motion(self) -> None:
        if not self.settings.control_enabled:
            raise GatewayError(ErrorCode.SDK_COMMAND_FAILED, "Motion control is disabled by GO2_CONTROL_ENABLED.", 403)
        if self._shutting_down:
            raise GatewayError(ErrorCode.SDK_COMMAND_FAILED, "Gateway is shutting down.", 503)
        if not self.adapter.is_initialized():
            raise GatewayError(ErrorCode.SDK_NOT_INITIALIZED, "Gateway is not initialized.", 503)
        status = self.refresh_status()
        if not status.get("online"):
            raise GatewayError(ErrorCode.ROBOT_OFFLINE, "Robot is offline.", 503)
        if status.get("stateStale"):
            raise GatewayError(ErrorCode.ROBOT_STATE_STALE, "Robot state is stale.", 503)

    def _validate_motion(self, vx: float, vy: float, wz: float, duration: float) -> None:
        if not -self.settings.max_vx <= vx <= self.settings.max_vx:
            raise GatewayError(ErrorCode.INVALID_MOTION_PARAMETER, "vx is outside the safe range.", 422)
        if not -self.settings.max_vy <= vy <= self.settings.max_vy:
            raise GatewayError(ErrorCode.INVALID_MOTION_PARAMETER, "vy is outside the safe range.", 422)
        if not -self.settings.max_wz <= wz <= self.settings.max_wz:
            raise GatewayError(ErrorCode.INVALID_MOTION_PARAMETER, "wz is outside the safe range.", 422)
        if not self.settings.min_move_duration <= duration <= self.settings.max_move_duration:
            raise GatewayError(ErrorCode.INVALID_MOTION_PARAMETER, "duration is outside the safe range.", 422)

    def _start_state_loop(self) -> None:
        if self._state_thread and self._state_thread.is_alive():
            return
        self._state_stop.clear()
        self._state_thread = threading.Thread(target=self._state_loop, name="go2-state-loop", daemon=True)
        self._state_thread.start()

    def _state_loop(self) -> None:
        while not self._state_stop.wait(1.0):
            try:
                self.refresh_status()
            except Exception:
                self.logger.exception("state refresh failed")
