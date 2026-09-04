from __future__ import annotations

import ctypes
import math
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from app.motion.arbiter import MotionArbiter, MotionArbiterConfig


class ManualRobotService(Protocol):
    def acquire_exclusive_control(self, owner: str) -> None: ...

    def release_exclusive_control(self, owner: str) -> None: ...

    def refresh_velocity(
        self, vx: float, vy: float, wz: float, source: str = "api"
    ) -> dict: ...

    def safe_stop(self, source: str = "api") -> int: ...


ManualEventCallback = Callable[[str, dict[str, object]], None]


@dataclass(frozen=True)
class ManualControlConfig:
    forward_mps: float = 0.49
    backward_mps: float = 0.42
    lateral_mps: float = 0.28
    yaw_radps: float = 1.32
    curve_forward_mps: float = 0.42
    curve_backward_mps: float = 0.35
    curve_yaw_radps: float = 1.08
    send_rate_hz: float = 5.0
    control_poll_seconds: float = 0.02
    deadman_seconds: float = 0.50

    def __post_init__(self) -> None:
        for name in (
            "forward_mps",
            "backward_mps",
            "lateral_mps",
            "yaw_radps",
            "curve_forward_mps",
            "curve_backward_mps",
            "curve_yaw_radps",
            "send_rate_hz",
            "control_poll_seconds",
            "deadman_seconds",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and greater than zero")
        if self.forward_mps > 0.49 or self.backward_mps > 0.42:
            raise ValueError("manual linear speed exceeds the frozen W/S limit")
        if self.lateral_mps > 0.28:
            raise ValueError("manual lateral speed exceeds the hard 0.28 m/s limit")
        if self.yaw_radps > 1.32:
            raise ValueError("manual yaw speed exceeds the hard 1.32 rad/s limit")
        if self.curve_forward_mps > self.forward_mps:
            raise ValueError("curve_forward_mps exceeds forward_mps")
        if self.curve_backward_mps > self.backward_mps:
            raise ValueError("curve_backward_mps exceeds backward_mps")
        if self.curve_yaw_radps > self.yaw_radps:
            raise ValueError("curve_yaw_radps exceeds yaw_radps")
        if not 1.0 <= self.send_rate_hz <= 5.0:
            raise ValueError("manual WebRTC send rate must be between 1 and 5 Hz")
        if self.control_poll_seconds > 0.05:
            raise ValueError("manual control polling must be at least 20 Hz")
        if not 0.30 <= self.deadman_seconds <= 0.50:
            raise ValueError("manual dead-man timeout must be between 0.30 and 0.50s")


class WindowsAsyncKeyState:
    """Read current key-down state instead of console key-repeat characters."""

    _VIRTUAL_KEYS = {
        "W": 0x57,
        "S": 0x53,
        "A": 0x41,
        "D": 0x44,
        "Q": 0x51,
        "E": 0x45,
        "SPACE": 0x20,
        "ESC": 0x1B,
    }

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("Windows key-state polling is required")
        get_async_key_state = ctypes.windll.user32.GetAsyncKeyState
        get_async_key_state.argtypes = [ctypes.c_int]
        get_async_key_state.restype = ctypes.c_short
        self._get_async_key_state = get_async_key_state

    def snapshot(self) -> frozenset[str]:
        return frozenset(
            name
            for name, virtual_key in self._VIRTUAL_KEYS.items()
            if int(self._get_async_key_state(virtual_key)) & 0x8000
        )


class LatestManualVelocityDispatcher:
    """Single-flight RobotService dispatcher with one latest pending slot."""

    def __init__(self, robot_service: ManualRobotService, *, source: str) -> None:
        self._robot_service = robot_service
        self._source = source
        self._condition = threading.Condition()
        self._pending: tuple[float, float, float] | None = None
        self._stop_pending = False
        self._stop_in_flight = False
        self._closed = False
        self._in_flight = False
        self._failure: Exception | None = None
        self._submitted = 0
        self._dispatched = 0
        self._replaced = 0
        self._stop_submitted = 0
        self._stop_dispatched = 0
        self._thread = threading.Thread(
            target=self._run,
            name="go2-manual-latest-command",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def submit(self, vx: float, vy: float, wz: float) -> None:
        with self._condition:
            self._raise_if_failed_locked()
            if self._closed:
                raise RuntimeError("manual velocity dispatcher is closed")
            if self._pending is not None:
                self._replaced += 1
            self._pending = (float(vx), float(vy), float(wz))
            self._submitted += 1
            self._condition.notify()

    def submit_stop(self, *, reason: str) -> bool:
        with self._condition:
            self._raise_if_failed_locked()
            if self._closed:
                return False
            self._pending = None
            if self._stop_pending or self._stop_in_flight:
                return False
            self._stop_pending = True
            self._stop_reason = str(reason or "manual_stop")
            self._stop_submitted += 1
            self._condition.notify()
            return True

    def raise_if_failed(self) -> None:
        with self._condition:
            self._raise_if_failed_locked()

    def wait_idle(self, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        with self._condition:
            while self._in_flight or self._pending is not None or self._stop_pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return False
                self._condition.wait(remaining)
            return True

    def snapshot(self) -> dict[str, int | bool | str | None]:
        with self._condition:
            return {
                "in_flight": self._in_flight,
                "pending": self._pending is not None,
                "stop_in_flight": self._stop_in_flight,
                "stop_pending": self._stop_pending,
                "submitted": self._submitted,
                "dispatched": self._dispatched,
                "replaced": self._replaced,
                "stop_submitted": self._stop_submitted,
                "stop_dispatched": self._stop_dispatched,
                "failed": self._failure is not None,
                "failure": None if self._failure is None else str(self._failure),
            }

    def close(self) -> None:
        with self._condition:
            self._pending = None
            self._closed = True
            self._condition.notify_all()
        self._thread.join(timeout=5.0)

    def _run(self) -> None:
        while True:
            with self._condition:
                while (
                    self._pending is None
                    and not self._stop_pending
                    and not self._closed
                ):
                    self._condition.wait()
                if self._closed and self._pending is None and not self._stop_pending:
                    return
                is_stop = self._stop_pending
                if is_stop:
                    self._stop_pending = False
                    self._stop_in_flight = True
                    self._stop_dispatched += 1
                    stop_reason = self._stop_reason
                    command = None
                else:
                    command = self._pending
                    self._pending = None
                    self._dispatched += 1
                    stop_reason = ""
                self._in_flight = True
            try:
                if is_stop:
                    self._robot_service.safe_stop(
                        f"{self._source}:{stop_reason}"
                    )
                else:
                    assert command is not None
                    self._robot_service.refresh_velocity(
                        command[0], command[1], command[2], source=self._source
                    )
            except Exception as exc:
                with self._condition:
                    self._failure = exc
                    self._pending = None
                    self._stop_pending = False
                    self._condition.notify_all()
                return
            finally:
                with self._condition:
                    self._in_flight = False
                    if is_stop:
                        self._stop_in_flight = False
                    self._condition.notify_all()

    def _raise_if_failed_locked(self) -> None:
        if self._failure is not None:
            raise self._failure


DispatcherFactory = Callable[
    [ManualRobotService, str], LatestManualVelocityDispatcher
]


class ManualKeyboardController:
    """Key-state controller with fixed-rate, single-flight WebRTC output."""

    CONTROL_OWNER = "wireless_manual"
    MOTION_KEYS = frozenset({"W", "S", "A", "D", "Q", "E"})

    def __init__(
        self,
        robot_service: ManualRobotService,
        config: ManualControlConfig | None = None,
        *,
        arbiter: MotionArbiter | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        event_callback: ManualEventCallback | None = None,
        dispatcher_factory: DispatcherFactory | None = None,
        start_background_threads: bool = True,
    ) -> None:
        self.robot_service = robot_service
        self.config = config or ManualControlConfig()
        self.arbiter = arbiter or MotionArbiter(
            MotionArbiterConfig(require_external_risk_feed=False)
        )
        self._clock = monotonic_clock
        self._event_callback = event_callback
        self._dispatcher_factory = dispatcher_factory or (
            lambda service, source: LatestManualVelocityDispatcher(
                service, source=source
            )
        )
        self._start_background_threads = bool(start_background_threads)
        self._lock = threading.RLock()
        self._active = False
        self._pressed = frozenset[str]()
        self._last_scan_at: float | None = None
        self._last_control_tick_at: float | None = None
        self._next_send_at = 0.0
        self._last_announced_command: tuple[float, float, float] | None = None
        self._stop_latched = True
        self._failure: Exception | None = None
        self._dispatcher: LatestManualVelocityDispatcher | None = None
        self._control_stop = threading.Event()
        self._control_thread: threading.Thread | None = None
        self._supervisor_stop = threading.Event()
        self._supervisor_thread: threading.Thread | None = None

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def acquire(self) -> None:
        with self._lock:
            if self._active:
                return
            self.robot_service.acquire_exclusive_control(self.CONTROL_OWNER)
            dispatcher = self._dispatcher_factory(
                self.robot_service, self.CONTROL_OWNER
            )
            try:
                dispatcher.start()
            except Exception:
                self.robot_service.release_exclusive_control(self.CONTROL_OWNER)
                raise
            self.arbiter.set_manual_takeover(True)
            now = self._clock()
            self._active = True
            self._pressed = frozenset()
            self._last_scan_at = now
            self._last_control_tick_at = now
            self._next_send_at = now
            self._last_announced_command = None
            self._stop_latched = True
            self._failure = None
            self._dispatcher = dispatcher
            self._control_stop.clear()
            self._supervisor_stop.clear()
            if self._start_background_threads:
                self._control_thread = threading.Thread(
                    target=self._control_loop,
                    name="go2-manual-control-loop",
                    daemon=True,
                )
                self._supervisor_thread = threading.Thread(
                    target=self._supervisor_loop,
                    name="go2-manual-control-supervisor",
                    daemon=True,
                )
                self._control_thread.start()
                self._supervisor_thread.start()
        self._emit("entered", authority="MANUAL")

    def update_pressed(self, keys: set[str] | frozenset[str]) -> dict[str, object]:
        normalized = frozenset(
            str(key).strip().upper()
            for key in keys
            if str(key).strip().upper() in self.MOTION_KEYS
        )
        emit_stop = False
        with self._lock:
            if not self._active:
                raise RuntimeError("manual control is not active")
            self._raise_if_failed_locked()
            previous = self._pressed
            self._pressed = normalized
            self._last_scan_at = self._clock()
            if previous and not normalized and not self._stop_latched:
                self._submit_stop_locked("keys_released")
                emit_stop = True
        if emit_stop:
            self._emit("stopped", reason="keys_released")
        return self.desired_command()

    def desired_command(self) -> dict[str, object]:
        with self._lock:
            vx, vy, wz = self._velocity_for_keys(self._pressed)
            return {
                "keys": sorted(self._pressed),
                "vx": vx,
                "vy": vy,
                "wz": wz,
                "authority": "MANUAL" if self._active else "NONE",
                "send_rate_hz": self.config.send_rate_hz,
                "single_flight": True,
            }

    def command(self, key: str) -> dict[str, object]:
        normalized = str(key or "").strip().upper()
        if normalized not in self.MOTION_KEYS:
            raise ValueError("manual key must be one of W/S/A/D/Q/E")
        return self.update_pressed({normalized})

    def control_tick(self, *, now_monotonic: float | None = None) -> bool:
        emit_command: tuple[float, float, float] | None = None
        emit_stop_reason: str | None = None
        with self._lock:
            if not self._active:
                return False
            self._raise_if_failed_locked()
            now = self._clock() if now_monotonic is None else now_monotonic
            if not math.isfinite(now):
                raise ValueError("now_monotonic must be finite")
            self._last_control_tick_at = now
            if (
                self._last_scan_at is None
                or now - self._last_scan_at >= self.config.deadman_seconds
            ):
                self._pressed = frozenset()
                if not self._stop_latched:
                    self._submit_stop_locked("keyboard_scan_stale")
                    emit_stop_reason = "keyboard_scan_stale"
                return_value = False
            else:
                vx, vy, wz = self._velocity_for_keys(self._pressed)
                if vx == 0.0 and vy == 0.0 and wz == 0.0:
                    return_value = False
                elif now < self._next_send_at:
                    return_value = False
                else:
                    decision = self.arbiter.decide_manual(vx=vx, vy=vy, wz=wz)
                    if decision.stop_required:
                        self._pressed = frozenset()
                        self._submit_stop_locked(decision.reason)
                        emit_stop_reason = decision.reason
                        return_value = False
                    else:
                        dispatcher = self._require_dispatcher_locked()
                        dispatcher.submit(decision.vx, decision.vy, decision.wz)
                        self._next_send_at = now + 1.0 / self.config.send_rate_hz
                        self._stop_latched = False
                        command = (decision.vx, decision.vy, decision.wz)
                        if command != self._last_announced_command:
                            self._last_announced_command = command
                            emit_command = command
                        return_value = True
        if emit_stop_reason is not None:
            self._emit("stopped", reason=emit_stop_reason)
        if emit_command is not None:
            self._emit(
                "command",
                vx=emit_command[0],
                vy=emit_command[1],
                wz=emit_command[2],
            )
        return return_value

    def stop(self, *, reason: str = "manual_space") -> int:
        display_reason = reason[7:] if reason.startswith("manual_") else reason
        with self._lock:
            if not self._active:
                return self.robot_service.safe_stop(
                    f"{self.CONTROL_OWNER}:{reason}"
                )
            self._pressed = frozenset()
            self._last_scan_at = self._clock()
            self._submit_stop_locked(reason)
        self._emit("stopped", reason=display_reason)
        return 0

    def release(self, *, reason: str = "manual_release") -> None:
        with self._lock:
            if not self._active:
                self.robot_service.safe_stop(f"{self.CONTROL_OWNER}:{reason}")
                return
            self._pressed = frozenset()
            dispatcher = self._dispatcher
            if dispatcher is not None:
                try:
                    dispatcher.submit_stop(reason=reason)
                except Exception:
                    pass
            self._active = False
            self._control_stop.set()
            self._supervisor_stop.set()
        current = threading.current_thread()
        for thread in (self._control_thread, self._supervisor_thread):
            if thread is not None and thread is not current:
                thread.join(timeout=1.0)
        if dispatcher is not None:
            dispatcher.wait_idle(3.0)
            dispatcher.close()
        try:
            # Redundant fail-safe: StopMove is always allowed and guarantees a
            # stop even if the dispatcher failed while draining.
            self.robot_service.safe_stop(f"{self.CONTROL_OWNER}:{reason}:final")
        finally:
            self.robot_service.release_exclusive_control(self.CONTROL_OWNER)
            self.arbiter.set_manual_takeover(False)
            with self._lock:
                self._dispatcher = None
                self._control_thread = None
                self._supervisor_thread = None
                self._last_announced_command = None
                self._stop_latched = True
        self._emit("exited", reason=reason)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            dispatcher = self._dispatcher
            return {
                "active": self._active,
                "pressed": sorted(self._pressed),
                "authority": "MANUAL" if self._active else "NONE",
                "send_rate_hz": self.config.send_rate_hz,
                "deadman_seconds": self.config.deadman_seconds,
                "single_flight": True,
                "failure": None if self._failure is None else str(self._failure),
                "dispatcher": None if dispatcher is None else dispatcher.snapshot(),
            }

    def close(self) -> None:
        if self.active:
            self.release(reason="manual_controller_close")

    def _control_loop(self) -> None:
        while not self._control_stop.wait(self.config.control_poll_seconds):
            try:
                self.control_tick()
                with self._lock:
                    dispatcher = self._dispatcher
                if dispatcher is not None:
                    dispatcher.raise_if_failed()
            except Exception as exc:
                with self._lock:
                    self._failure = exc
                    if self._active and not self._stop_latched:
                        try:
                            self._submit_stop_locked("control_loop_error")
                        except Exception:
                            pass
                self._emit("error", reason=f"control_loop:{type(exc).__name__}:{exc}")
                return

    def _supervisor_loop(self) -> None:
        poll = min(0.05, self.config.deadman_seconds / 4.0)
        while not self._supervisor_stop.wait(poll):
            emit = False
            with self._lock:
                if not self._active:
                    return
                now = self._clock()
                tick = self._last_control_tick_at
                if (
                    tick is not None
                    and now - tick >= self.config.deadman_seconds
                    and not self._stop_latched
                ):
                    self._pressed = frozenset()
                    try:
                        self._submit_stop_locked("control_loop_stale")
                    except Exception as exc:
                        self._failure = exc
                    emit = True
            if emit:
                self._emit("stopped", reason="control_loop_stale")

    def _submit_stop_locked(self, reason: str) -> None:
        dispatcher = self._require_dispatcher_locked()
        dispatcher.submit_stop(reason=reason)
        self._stop_latched = True
        self._last_announced_command = None

    def _require_dispatcher_locked(self) -> LatestManualVelocityDispatcher:
        dispatcher = self._dispatcher
        if dispatcher is None:
            raise RuntimeError("manual velocity dispatcher is unavailable")
        return dispatcher

    def _raise_if_failed_locked(self) -> None:
        if self._failure is not None:
            raise self._failure
        dispatcher = self._dispatcher
        if dispatcher is not None:
            dispatcher.raise_if_failed()

    def _velocity_for_keys(
        self, keys: frozenset[str]
    ) -> tuple[float, float, float]:
        forward = "W" in keys and "S" not in keys
        backward = "S" in keys and "W" not in keys
        left = "A" in keys and "D" not in keys
        right = "D" in keys and "A" not in keys
        lateral_left = "Q" in keys and "E" not in keys
        lateral_right = "E" in keys and "Q" not in keys
        turning = left or right

        vx = 0.0
        if forward:
            vx = (
                self.config.curve_forward_mps
                if turning
                else self.config.forward_mps
            )
        elif backward:
            vx = -(
                self.config.curve_backward_mps
                if turning
                else self.config.backward_mps
            )
        vy = (
            self.config.lateral_mps
            if lateral_left
            else -self.config.lateral_mps
            if lateral_right
            else 0.0
        )
        wz = (
            self.config.curve_yaw_radps
            if left and (forward or backward)
            else -self.config.curve_yaw_radps
            if right and (forward or backward)
            else self.config.yaw_radps
            if left
            else -self.config.yaw_radps
            if right
            else 0.0
        )
        return vx, vy, wz

    def _emit(self, event: str, **payload: object) -> None:
        callback = self._event_callback
        if callback is not None:
            callback(event, payload)


ManualPulseController = ManualKeyboardController
