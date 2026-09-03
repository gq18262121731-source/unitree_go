from __future__ import annotations

import logging
import threading
import time

from app.config import Settings
from app.core.control_lock import ControlLock
from app.core.errors import ErrorCode, GatewayError
from app.core.state_store import StateStore
from app.core.watchdog import ControlWatchdog
from app.gateway.go2_gateway import Go2Gateway


class RobotService:
    def __init__(self, gateway: Go2Gateway, settings: Settings, state_store: StateStore) -> None:
        self.gateway = gateway
        self.settings = settings
        self.state_store = state_store
        self.control_lock = ControlLock()
        self._owner_lock = threading.RLock()
        self._exclusive_control_owner: str | None = None
        self.watchdog = ControlWatchdog(gateway, settings.control_watchdog_seconds)
        self.logger = logging.getLogger("go2_gateway.robot")
        self._state_stop = threading.Event()
        self._state_thread: threading.Thread | None = None
        self._shutting_down = False
        self.last_connection_error: str | None = None

    def initialize(self) -> None:
        self._shutting_down = False
        try:
            self.gateway.connect()
            self.state_store.set_system(self.settings.version, self.gateway.sdk_version)
            self.state_store.update_control(enabled=self.settings.control_enabled)
            self.refresh_status()
            self.watchdog.start()
            self._start_state_loop()
            self.last_connection_error = None
        except GatewayError as exc:
            self.last_connection_error = str(exc)
            raise
        except Exception as exc:
            self.last_connection_error = str(exc)
            raise GatewayError(ErrorCode.SDK_NOT_INITIALIZED, f"Gateway initialization failed: {exc}", 503) from exc

    def close(self) -> None:
        self._shutting_down = True
        self.safe_stop("shutdown")
        self.watchdog.stop()
        self._state_stop.set()
        if self._state_thread:
            self._state_thread.join(timeout=1.0)
        self.gateway.close()
        with self._owner_lock:
            self._exclusive_control_owner = None

    @property
    def exclusive_control_owner(self) -> str | None:
        with self._owner_lock:
            return self._exclusive_control_owner

    def acquire_exclusive_control(self, owner: str) -> None:
        normalized = str(owner or "").strip()
        if not normalized:
            raise ValueError("exclusive control owner is required")
        with self._owner_lock:
            current = self._exclusive_control_owner
            if current is not None and current != normalized:
                raise GatewayError(
                    ErrorCode.CONTROL_BUSY,
                    f"Exclusive robot control is owned by {current}.",
                    409,
                )
            self._exclusive_control_owner = normalized

    def release_exclusive_control(self, owner: str) -> None:
        normalized = str(owner or "").strip()
        with self._owner_lock:
            if self._exclusive_control_owner == normalized:
                self._exclusive_control_owner = None

    def reconnect(self, source: str = "api") -> dict:
        self._ensure_source_allowed(source)
        if self.control_lock.busy:
            raise GatewayError(ErrorCode.CONTROL_BUSY, "Cannot reconnect while motion control is running.", 409)
        self.watchdog.stop()
        self._state_stop.set()
        if self._state_thread:
            self._state_thread.join(timeout=1.0)
        self._state_thread = None
        try:
            self.gateway.close()
        finally:
            self._shutting_down = False
        self.initialize()
        return self.status()

    def refresh_status(self) -> dict:
        status = self.gateway.get_status()
        self.state_store.update_status(status)
        self.state_store.update_control(enabled=self.settings.control_enabled, busy=self.control_lock.busy)
        return self.state_store.snapshot()

    def status(self) -> dict:
        self.refresh_status()
        return self.state_store.snapshot()

    def get_motion_state(self) -> dict | None:
        """Expose the adapter's existing read-only SportModeState snapshot."""

        return self.gateway.get_motion_state()

    def stand(self, source: str = "api") -> dict:
        return self._run_command("STAND", source, self.gateway.stand)

    def lie_down(self, source: str = "api") -> dict:
        def command() -> int:
            self.gateway.stop()
            return self.gateway.lie_down()
        return self._run_command("LIE_DOWN", source, command)

    def sit(self, source: str = "api") -> dict:
        def command() -> int:
            self.gateway.stop()
            return self.gateway.sit()
        return self._run_command("SIT", source, command)

    def stop(self, source: str = "api") -> dict:
        code = self.safe_stop(source)
        return {"code": code}

    def emergency_stop(self, source: str = "api") -> dict:
        code = self.safe_stop(f"emergency:{source}")
        self.state_store.update_control(last_command="EMERGENCY_STOP")
        return {"code": code}

    def switch_joystick(self, enabled: bool, source: str = "api") -> dict:
        command_name = "JOYSTICK_ENABLE" if enabled else "JOYSTICK_DISABLE"
        return self._run_command(
            command_name,
            source,
            lambda: self.gateway.switch_joystick(enabled),
        )

    def apply_pose(
        self,
        *,
        roll_rad: float,
        pitch_rad: float,
        yaw_rad: float,
        body_height_m: float,
        source: str = "api",
    ) -> dict:
        return self._run_command(
            "APPLY_POSE",
            source,
            lambda: self.gateway.apply_pose(
                roll_rad=roll_rad,
                pitch_rad=pitch_rad,
                yaw_rad=yaw_rad,
                body_height_m=body_height_m,
            ),
        )

    def reset_pose(self, source: str = "api") -> dict:
        return self._run_command("RESET_POSE", source, self.gateway.reset_pose)

    def play_audio_file(self, path: str, source: str = "api") -> dict:
        return self._run_command(
            "PLAY_AUDIO_FILE", source, lambda: self.gateway.play_audio_file(path)
        )

    def speak(self, text: str, source: str = "api") -> dict:
        return self._run_command("SPEAK", source, lambda: self.gateway.speak(text))

    def safe_switch_joystick(self, enabled: bool, source: str = "api") -> int:
        try:
            code = self.gateway.switch_joystick(enabled)
            self.logger.info(
                "joystick enabled=%s source=%s code=%s",
                enabled,
                source,
                code,
            )
            return code
        except Exception:
            self.logger.exception(
                "joystick switch failed enabled=%s source=%s",
                enabled,
                source,
            )
            return -1

    def ensure_ready_for_task_dispatch(self, source: str = "api") -> None:
        if self.control_lock.busy:
            raise GatewayError(ErrorCode.CONTROL_BUSY, "Robot control is busy.", 409)
        self._ensure_ready_for_motion(source)

    def ensure_ready_for_task_acceptance(self, source: str = "api") -> None:
        self._ensure_ready_for_motion(source)

    def move(self, vx: float, vy: float, wz: float, duration: float, source: str = "api") -> dict:
        self._validate_motion(vx, vy, wz, duration)
        self._ensure_ready_for_motion(source)
        self.control_lock.acquire()
        self.state_store.update_control(busy=True, last_command="MOVE")
        self.watchdog.arm()
        started = time.monotonic()
        try:
            code = self._move_with_ack_supervision(vx, vy, wz)
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
                self.gateway.stop()
            finally:
                self.watchdog.disarm()
                self.control_lock.release()
                self.state_store.update_control(busy=False, last_command="STOP")

    def refresh_velocity(
        self,
        vx: float,
        vy: float,
        wz: float,
        source: str = "api",
    ) -> dict:
        """Refresh a supervised velocity command without stopping the gait.

        This method is reserved for a watchdog-backed control loop. The caller
        must refresh it frequently and call ``safe_stop`` on every unsafe
        transition. The watchdog provides the independent fail-safe when the
        refresh loop stalls.
        """

        self._validate_velocity(vx, vy, wz)
        self._ensure_ready_for_motion(source)
        self.control_lock.acquire()
        self.state_store.update_control(busy=True, last_command="MOVE_REFRESH")
        self.watchdog.arm()
        try:
            code = self._move_with_ack_supervision(vx, vy, wz)
            self.watchdog.heartbeat()
            self.logger.info(
                "velocity refresh source=%s vx=%s vy=%s wz=%s code=%s",
                source,
                vx,
                vy,
                wz,
                code,
            )
            if code != 0:
                raise GatewayError(
                    ErrorCode.SDK_COMMAND_FAILED,
                    f"Move refresh failed, code={code}",
                    503,
                )
            return {"code": code}
        except GatewayError:
            self.safe_stop(f"{source}:refresh_error")
            raise
        except Exception as exc:
            self.safe_stop(f"{source}:refresh_error")
            raise GatewayError(
                ErrorCode.SDK_COMMAND_FAILED,
                f"Move refresh failed: {exc}",
                503,
            ) from exc
        finally:
            self.control_lock.release()
            self.state_store.update_control(busy=False)

    def refresh_control_heartbeat(self, source: str) -> None:
        """Mark an exclusive supervised control loop as healthy.

        This is deliberately separate from a successful Move response so a
        healthy wireless loop refreshes the watchdog before entering an RPC.
        It does not arm a stopped robot or bypass exclusive-writer checks.
        """

        self._ensure_source_allowed(source)
        self.watchdog.heartbeat()

    def _move_with_ack_supervision(self, vx: float, vy: float, wz: float) -> int:
        """Wait for one Move ACK under a separate, bounded watchdog budget."""

        # Go2WirelessRuntime waits sdk_timeout + 0.5 s on the thread-safe
        # future. Keep the heartbeat watchdog out of that known bounded wait;
        # the transport timeout remains responsible for failing the command.
        ack_budget = max(0.1, self.settings.sdk_timeout_seconds + 0.75)
        self.watchdog.begin_ack_wait(ack_budget)
        try:
            return self.gateway.move(vx, vy, wz)
        finally:
            self.watchdog.end_ack_wait()

    def safe_stop(self, source: str = "api") -> int:
        self.watchdog.disarm()
        try:
            code = self.gateway.stop()
            self.state_store.update_control(last_command="STOP", busy=self.control_lock.busy)
            self.logger.info("stop source=%s code=%s", source, code)
            return code
        except Exception:
            self.logger.exception("StopMove failed source=%s", source)
            return -1

    def _run_command(self, name: str, source: str, command) -> dict:
        self._ensure_ready_for_motion(source)
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

    def _ensure_ready_for_motion(self, source: str = "api") -> None:
        self._ensure_source_allowed(source)
        if not self.settings.control_enabled:
            raise GatewayError(ErrorCode.CONTROL_DISABLED, "Motion control is disabled by GO2_CONTROL_ENABLED.", 403)
        if self.settings.read_only_mode:
            raise GatewayError(ErrorCode.READ_ONLY_MODE, "Gateway is in read-only mode; motion control is locked.", 403)
        if self._shutting_down:
            raise GatewayError(ErrorCode.SDK_COMMAND_FAILED, "Gateway is shutting down.", 503)
        if not self.gateway.is_initialized():
            raise GatewayError(ErrorCode.SDK_NOT_INITIALIZED, "Gateway is not initialized.", 503)
        status = self.refresh_status()
        # WebRTC command readiness is independent from SportModeState.  This
        # permits manual operator control while companion telemetry is in
        # standby, but only after the DataChannel has been positively verified.
        # DDS adapters return False and retain their existing state gate.
        transport_ready = self.gateway.motion_transport_ready()
        if not status.get("online") and not transport_ready:
            if self.settings.mode == "real" and self.gateway.is_initialized():
                raise GatewayError(
                    ErrorCode.DDS_NOT_READY,
                    "Go2 network is reachable or SDK is initialized, but no real DDS state has been received. Motion control is locked.",
                    503,
                )
            raise GatewayError(ErrorCode.ROBOT_OFFLINE, "Robot is offline.", 503)
        if status.get("stateStale") and not transport_ready:
            raise GatewayError(ErrorCode.ROBOT_STATE_STALE, "Robot state is stale.", 503)

    def _ensure_source_allowed(self, source: str) -> None:
        normalized = str(source or "api").strip() or "api"
        with self._owner_lock:
            owner = self._exclusive_control_owner
        if owner is not None and owner != normalized:
            raise GatewayError(
                ErrorCode.CONTROL_BUSY,
                f"Exclusive robot control is owned by {owner}.",
                409,
            )

    def _validate_motion(self, vx: float, vy: float, wz: float, duration: float) -> None:
        self._validate_velocity(vx, vy, wz)
        if not self.settings.min_move_duration <= duration <= self.settings.max_move_duration:
            raise GatewayError(ErrorCode.INVALID_MOTION_PARAMETER, "duration is outside the safe range.", 422)

    def _validate_velocity(self, vx: float, vy: float, wz: float) -> None:
        if not -self.settings.max_vx <= vx <= self.settings.max_vx:
            raise GatewayError(ErrorCode.INVALID_MOTION_PARAMETER, "vx is outside the safe range.", 422)
        if not -self.settings.max_vy <= vy <= self.settings.max_vy:
            raise GatewayError(ErrorCode.INVALID_MOTION_PARAMETER, "vy is outside the safe range.", 422)
        if not -self.settings.max_wz <= wz <= self.settings.max_wz:
            raise GatewayError(ErrorCode.INVALID_MOTION_PARAMETER, "wz is outside the safe range.", 422)

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
