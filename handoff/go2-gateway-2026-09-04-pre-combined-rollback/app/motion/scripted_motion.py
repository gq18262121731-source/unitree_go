from __future__ import annotations

import math
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterator, Protocol

import yaml


HARD_MAX_VX_MPS = 0.30
HARD_MAX_VY_MPS = 0.30
HARD_MAX_WZ_RADPS = 0.60
HARD_MAX_POSE_ROLL_DEG = 20.0
HARD_MAX_POSE_PITCH_DEG = 20.0
HARD_MAX_POSE_YAW_DEG = 20.0
HARD_MIN_BODY_HEIGHT_M = -0.18
HARD_MAX_BODY_HEIGHT_M = 0.03
HARD_MAX_POSE_DURATION_S = 10.0


class MotionActionFailure(RuntimeError):
    """Expected fail-closed action termination with a stable reason string."""


class ScriptedRobotService(Protocol):
    def acquire_exclusive_control(self, owner: str) -> None: ...

    def release_exclusive_control(self, owner: str) -> None: ...

    def refresh_velocity(
        self, vx: float, vy: float, wz: float, source: str = "api"
    ) -> dict: ...

    def safe_stop(self, source: str = "api") -> int: ...

    def emergency_stop(self, source: str = "api") -> dict: ...

    def get_motion_state(self) -> dict | None: ...

    def apply_pose(
        self,
        *,
        roll_rad: float,
        pitch_rad: float,
        yaw_rad: float,
        body_height_m: float,
        source: str = "api",
    ) -> dict: ...

    def reset_pose(self, source: str = "api") -> dict: ...

    def play_audio_file(self, path: str, source: str = "api") -> dict: ...

    def speak(self, text: str, source: str = "api") -> dict: ...


@dataclass(frozen=True)
class MotionPose:
    x: float
    y: float
    yaw: float
    received_monotonic: float
    source: str = "SportModeState"

    def to_dict(self) -> dict[str, float | str]:
        return asdict(self)


@dataclass
class MotionActionResult:
    action: str
    requested_value: float
    actual_value: float | None
    error: float | None
    unit: str
    duration_s: float
    completed: bool
    reason: str
    start_pose: MotionPose | None
    end_pose: MotionPose | None
    measurement_source: str = "SportModeState"

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "action": self.action,
            "requested_value": self.requested_value,
            "actual_value": self.actual_value,
            "error": self.error,
            "unit": self.unit,
            "duration_s": self.duration_s,
            "completed": self.completed,
            "reason": self.reason,
            "start_pose": self.start_pose.to_dict() if self.start_pose else None,
            "end_pose": self.end_pose.to_dict() if self.end_pose else None,
            "measurement_source": self.measurement_source,
        }
        suffix = "m" if self.unit == "m" else "deg" if self.unit == "deg" else "s"
        payload[f"requested_{suffix}"] = self.requested_value
        payload[f"actual_{suffix}"] = self.actual_value
        payload[f"error_{suffix}"] = self.error
        return payload


@dataclass(frozen=True)
class ScriptedMotionConfig:
    forward_speed_mps: float = 0.30
    backward_speed_mps: float = 0.18
    lateral_speed_mps: float = 0.30
    yaw_speed_radps: float = 0.60
    reduced_linear_speed_mps: float = 0.23
    reduced_lateral_speed_mps: float = 0.30
    reduced_yaw_speed_radps: float = 0.50
    max_vx_mps: float = HARD_MAX_VX_MPS
    max_vy_mps: float = HARD_MAX_VY_MPS
    max_wz_radps: float = HARD_MAX_WZ_RADPS
    distance_tolerance_m: float = 0.025
    angle_tolerance_deg: float = 2.0
    deceleration_distance_m: float = 0.20
    deceleration_angle_deg: float = 20.0
    translation_stall_window_s: float = 1.0
    translation_stall_min_progress_m: float = 0.01
    rotation_stall_window_s: float = 1.0
    rotation_stall_min_progress_deg: float = 1.0
    state_timeout_s: float = 0.50
    control_rate_hz: float = 5.0
    timeout_scale: float = 2.0
    timeout_margin_s: float = 2.0
    max_distance_m: float = 10.0
    max_angle_deg: float = 720.0
    allow_time_fallback: bool = False
    pose_settle_s: float = 0.25

    def validate(self) -> None:
        positive = {
            "forward_speed_mps": self.forward_speed_mps,
            "backward_speed_mps": self.backward_speed_mps,
            "lateral_speed_mps": self.lateral_speed_mps,
            "yaw_speed_radps": self.yaw_speed_radps,
            "reduced_linear_speed_mps": self.reduced_linear_speed_mps,
            "reduced_lateral_speed_mps": self.reduced_lateral_speed_mps,
            "reduced_yaw_speed_radps": self.reduced_yaw_speed_radps,
            "max_vx_mps": self.max_vx_mps,
            "max_vy_mps": self.max_vy_mps,
            "max_wz_radps": self.max_wz_radps,
            "distance_tolerance_m": self.distance_tolerance_m,
            "angle_tolerance_deg": self.angle_tolerance_deg,
            "deceleration_distance_m": self.deceleration_distance_m,
            "deceleration_angle_deg": self.deceleration_angle_deg,
            "state_timeout_s": self.state_timeout_s,
            "translation_stall_window_s": self.translation_stall_window_s,
            "translation_stall_min_progress_m": self.translation_stall_min_progress_m,
            "rotation_stall_window_s": self.rotation_stall_window_s,
            "rotation_stall_min_progress_deg": self.rotation_stall_min_progress_deg,
            "control_rate_hz": self.control_rate_hz,
            "timeout_scale": self.timeout_scale,
            "max_distance_m": self.max_distance_m,
            "max_angle_deg": self.max_angle_deg,
            "pose_settle_s": self.pose_settle_s,
        }
        for name, value in positive.items():
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and > 0")
        if self.timeout_margin_s < 0 or not math.isfinite(self.timeout_margin_s):
            raise ValueError("timeout_margin_s must be finite and >= 0")
        if self.max_vx_mps > HARD_MAX_VX_MPS:
            raise ValueError(f"max_vx_mps exceeds hard limit {HARD_MAX_VX_MPS}")
        if self.max_vy_mps > HARD_MAX_VY_MPS:
            raise ValueError(f"max_vy_mps exceeds hard limit {HARD_MAX_VY_MPS}")
        if self.max_wz_radps > HARD_MAX_WZ_RADPS:
            raise ValueError(f"max_wz_radps exceeds hard limit {HARD_MAX_WZ_RADPS}")
        if max(self.forward_speed_mps, self.backward_speed_mps) > self.max_vx_mps:
            raise ValueError("forward/backward speed exceeds max_vx_mps")
        if self.reduced_linear_speed_mps > self.max_vx_mps:
            raise ValueError("reduced_linear_speed_mps exceeds max_vx_mps")
        if self.lateral_speed_mps > self.max_vy_mps:
            raise ValueError("lateral_speed_mps exceeds max_vy_mps")
        if self.reduced_lateral_speed_mps > self.max_vy_mps:
            raise ValueError("reduced_lateral_speed_mps exceeds max_vy_mps")
        if self.yaw_speed_radps > self.max_wz_radps:
            raise ValueError("yaw_speed_radps exceeds max_wz_radps")
        if self.reduced_yaw_speed_radps > self.max_wz_radps:
            raise ValueError("reduced_yaw_speed_radps exceeds max_wz_radps")


def load_scripted_motion_config(path: str | Path) -> ScriptedMotionConfig:
    source = Path(path)
    try:
        payload = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ValueError(f"cannot read scripted motion config {source}: {exc}") from exc
    section = payload.get("scripted_motion")
    if not isinstance(section, dict):
        raise ValueError("config must contain a scripted_motion mapping")
    known = set(ScriptedMotionConfig.__dataclass_fields__)
    unknown = set(section) - known
    if unknown:
        raise ValueError(f"unknown scripted_motion settings: {sorted(unknown)}")
    config = ScriptedMotionConfig(**section)
    config.validate()
    return config


def wrap_to_pi(angle_rad: float) -> float:
    """Wrap an angle to [-pi, pi), including boundary-crossing deltas."""

    if not math.isfinite(angle_rad):
        raise ValueError("angle must be finite")
    return (angle_rad + math.pi) % (2.0 * math.pi) - math.pi


def forward_progress(start: MotionPose, current: MotionPose) -> float:
    dx = current.x - start.x
    dy = current.y - start.y
    return dx * math.cos(start.yaw) + dy * math.sin(start.yaw)


def lateral_progress(start: MotionPose, current: MotionPose) -> float:
    dx = current.x - start.x
    dy = current.y - start.y
    return -dx * math.sin(start.yaw) + dy * math.cos(start.yaw)


class ScriptedMotionController:
    """Closed-loop PC action layer over the existing RobotService writer.

    Translation is measured in the start-pose local frame. Rotation accumulates
    wrapped yaw increments so crossing +/-pi and targets over 180 degrees work.
    Every action has an unconditional StopMove cleanup path.
    """

    CONTROL_OWNER = "scripted_motion"

    def __init__(
        self,
        robot_service: ScriptedRobotService,
        config: ScriptedMotionConfig | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self.robot_service = robot_service
        self.config = config or ScriptedMotionConfig()
        self.config.validate()
        self._clock = clock
        self._sleep = sleep
        self._progress_callback = progress_callback
        self._abort = threading.Event()
        self._ownership_lock = threading.RLock()
        self._ownership_depth = 0

    def __enter__(self) -> ScriptedMotionController:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            self.stop()
        finally:
            self.release()

    def acquire(self) -> None:
        with self._ownership_lock:
            if self._ownership_depth == 0:
                self.robot_service.acquire_exclusive_control(self.CONTROL_OWNER)
            self._ownership_depth += 1

    def release(self) -> None:
        with self._ownership_lock:
            if self._ownership_depth <= 0:
                return
            self._ownership_depth -= 1
            if self._ownership_depth == 0:
                self.robot_service.release_exclusive_control(self.CONTROL_OWNER)

    def clear_emergency_stop(self) -> None:
        self._abort.clear()

    def forward(self, distance_m: float) -> MotionActionResult:
        return self._run_linear("forward", distance_m, axis="forward", direction=1)

    def backward(self, distance_m: float) -> MotionActionResult:
        return self._run_linear("backward", distance_m, axis="forward", direction=-1)

    def move_left(self, distance_m: float) -> MotionActionResult:
        return self._run_linear("move_left", distance_m, axis="lateral", direction=1)

    def move_right(self, distance_m: float) -> MotionActionResult:
        return self._run_linear("move_right", distance_m, axis="lateral", direction=-1)

    def turn_left(self, angle_deg: float) -> MotionActionResult:
        return self._run_turn("turn_left", angle_deg, direction=1)

    def turn_right(self, angle_deg: float) -> MotionActionResult:
        return self._run_turn("turn_right", angle_deg, direction=-1)

    def turn_clockwise(self, angle_deg: float) -> MotionActionResult:
        """Turn clockwise using the established negative-wz convention."""

        return self._run_turn("turn_clockwise", angle_deg, direction=-1)

    def wait(self, seconds: float) -> MotionActionResult:
        self._validate_positive(seconds, "seconds", maximum=86400.0)
        started = self._clock()
        failure_reason: str | None = None
        with self._action_ownership():
            stop_code = self._safe_stop("wait:start")
            deadline = started + seconds
            try:
                if stop_code != 0:
                    raise MotionActionFailure("preflight_stop_failed")
                while self._clock() < deadline:
                    self._check_abort()
                    self._sleep(min(1.0 / self.config.control_rate_hz, deadline - self._clock()))
            except MotionActionFailure as exc:
                failure_reason = str(exc)
            finally:
                final_stop = self._safe_stop("wait:complete")
        duration = self._clock() - started
        completed = stop_code == 0 and final_stop == 0 and failure_reason is None
        return MotionActionResult(
            action="wait",
            requested_value=seconds,
            actual_value=duration,
            error=duration - seconds,
            unit="s",
            duration_s=duration,
            completed=completed,
            reason=(
                "target_reached"
                if completed
                else failure_reason
                if failure_reason is not None
                else "stop_failed"
            ),
            start_pose=None,
            end_pose=None,
            measurement_source="monotonic_clock",
        )

    def stop(self) -> int:
        return self._safe_stop("operator_stop")

    def emergency_stop(self) -> int:
        self._abort.set()
        try:
            result = self.robot_service.emergency_stop(source=self.CONTROL_OWNER)
            return int(result.get("code", -1))
        except Exception:
            return self._safe_stop("emergency_stop_fallback")

    def status(self) -> dict[str, object]:
        pose = self._try_read_pose(require_fresh=False)
        return {
            "owner": self.CONTROL_OWNER if self._ownership_depth else None,
            "emergency_stop_latched": self._abort.is_set(),
            "pose": pose.to_dict() if pose else None,
        }

    def pose(
        self,
        *,
        roll_deg: float,
        pitch_deg: float,
        yaw_deg: float,
        body_height_m: float,
        duration_s: float | None = None,
    ) -> MotionActionResult:
        duration = 1.5 if duration_s is None else float(duration_s)
        self._validate_pose(
            roll_deg=roll_deg,
            pitch_deg=pitch_deg,
            yaw_deg=yaw_deg,
            body_height_m=body_height_m,
            duration_s=duration,
        )
        started = self._clock()
        hold_started: float | None = None
        completed = False
        reason = "unknown"
        reset_code = -1
        final_stop = -1
        with self._action_ownership():
            try:
                if self._safe_stop("pose:preflight") != 0:
                    raise MotionActionFailure("preflight_stop_failed")
                self._interruptible_sleep(self.config.pose_settle_s)
                response = self.robot_service.apply_pose(
                    roll_rad=math.radians(float(roll_deg)),
                    pitch_rad=math.radians(float(pitch_deg)),
                    yaw_rad=math.radians(float(yaw_deg)),
                    body_height_m=float(body_height_m),
                    source=self.CONTROL_OWNER,
                )
                if int(response.get("code", -1)) != 0:
                    raise MotionActionFailure("pose_command_failed")
                hold_started = self._clock()
                self._interruptible_sleep(duration)
                completed = True
                reason = "pose_completed"
            except MotionActionFailure as exc:
                reason = str(exc)
            except Exception as exc:
                reason = f"exception:{type(exc).__name__}:{exc}"
            finally:
                try:
                    response = self.robot_service.reset_pose(source=self.CONTROL_OWNER)
                    reset_code = int(response.get("code", -1))
                except Exception:
                    reset_code = -1
                final_stop = self._safe_stop("pose:complete")
        if reset_code != 0:
            completed = False
            reason = "neutral_pose_failed"
        elif final_stop != 0:
            completed = False
            reason = "stop_failed"
        actual_hold = (
            0.0
            if hold_started is None
            else min(duration, max(0.0, self._clock() - hold_started))
        )
        return MotionActionResult(
            action="pose",
            requested_value=duration,
            actual_value=actual_hold,
            error=actual_hold - duration,
            unit="s",
            duration_s=self._clock() - started,
            completed=completed,
            reason=reason,
            start_pose=None,
            end_pose=None,
            measurement_source="monotonic_clock+WebRTC_Sport_API",
        )

    def play_audio(self, path: str) -> None:
        normalized = str(path or "").strip()
        if not normalized:
            raise ValueError("audio path must not be empty")
        with self._action_ownership():
            try:
                if self._safe_stop("audio:preflight") != 0:
                    raise MotionActionFailure("preflight_stop_failed")
                response = self.robot_service.play_audio_file(
                    normalized, source=self.CONTROL_OWNER
                )
                if int(response.get("code", -1)) != 0:
                    raise MotionActionFailure("audio_command_failed")
            finally:
                self._safe_stop("audio:complete")

    def speak(self, text: str) -> None:
        normalized = str(text or "").strip()
        if not normalized:
            raise ValueError("speech text must not be empty")
        if len(normalized) > 200:
            raise ValueError("speech text must be at most 200 characters")
        with self._action_ownership():
            try:
                if self._safe_stop("speak:preflight") != 0:
                    raise MotionActionFailure("preflight_stop_failed")
                response = self.robot_service.speak(
                    normalized, source=self.CONTROL_OWNER
                )
                if int(response.get("code", -1)) != 0:
                    raise MotionActionFailure("speak_command_failed")
            finally:
                self._safe_stop("speak:complete")

    def _run_linear(
        self, action: str, requested: float, *, axis: str, direction: int
    ) -> MotionActionResult:
        self._validate_positive(requested, "distance_m", self.config.max_distance_m)
        nominal = (
            self.config.forward_speed_mps
            if action == "forward"
            else self.config.backward_speed_mps
            if action == "backward"
            else self.config.lateral_speed_mps
        )
        started = self._clock()
        start_pose: MotionPose | None = None
        end_pose: MotionPose | None = None
        actual: float | None = 0.0
        completed = False
        reason = "unknown"
        measurement = "SportModeState"
        with self._action_ownership():
            try:
                if self._safe_stop(f"{action}:preflight") != 0:
                    raise MotionActionFailure("preflight_stop_failed")
                start_pose = self._read_pose()
            except MotionActionFailure as exc:
                if not self.config.allow_time_fallback or str(exc) != "state_unavailable":
                    reason = str(exc)
                else:
                    measurement = "estimated_time_fallback"
                    actual, completed, reason = self._run_timed_velocity(
                        action,
                        requested / nominal,
                        vx=(direction * nominal if axis == "forward" else 0.0),
                        vy=(direction * nominal if axis == "lateral" else 0.0),
                        wz=0.0,
                    )
            else:
                stall_checkpoint_at = self._clock()
                stall_checkpoint_progress = 0.0
                timeout = requested / nominal * self.config.timeout_scale + self.config.timeout_margin_s
                deadline = started + timeout
                try:
                    while True:
                        self._check_abort()
                        if self._clock() >= deadline:
                            reason = "timeout"
                            break
                        end_pose = self._read_pose()
                        projection = (
                            forward_progress(start_pose, end_pose)
                            if axis == "forward"
                            else lateral_progress(start_pose, end_pose)
                        )
                        actual = direction * projection
                        remaining = requested - actual
                        self._emit_progress(action, actual, requested, end_pose)
                        if remaining <= self.config.distance_tolerance_m:
                            completed = True
                            reason = "target_reached"
                            break
                        now = self._clock()
                        if now - stall_checkpoint_at >= self.config.translation_stall_window_s:
                            progress_since_checkpoint = actual - stall_checkpoint_progress
                            if progress_since_checkpoint < self.config.translation_stall_min_progress_m:
                                reason = "translation_stalled"
                                break
                            stall_checkpoint_at = now
                            stall_checkpoint_progress = actual
                        speed = nominal
                        if remaining <= self.config.deceleration_distance_m:
                            reduced_speed = (
                                self.config.reduced_lateral_speed_mps
                                if axis == "lateral"
                                else self.config.reduced_linear_speed_mps
                            )
                            speed = min(nominal, reduced_speed)
                        self._refresh(
                            direction * speed if axis == "forward" else 0.0,
                            direction * speed if axis == "lateral" else 0.0,
                            0.0,
                            action,
                        )
                        self._sleep(1.0 / self.config.control_rate_hz)
                except MotionActionFailure as exc:
                    reason = str(exc)
                except Exception as exc:
                    reason = f"exception:{type(exc).__name__}:{exc}"
            stop_code = self._safe_stop(f"{action}:complete")
        if stop_code != 0:
            completed = False
            reason = "stop_failed"
        if start_pose is not None:
            end_pose = self._try_read_pose(require_fresh=False) or end_pose or start_pose
            projection = (
                forward_progress(start_pose, end_pose)
                if axis == "forward"
                else lateral_progress(start_pose, end_pose)
            )
            actual = direction * projection
        error = actual - requested if actual is not None else None
        return MotionActionResult(
            action=action,
            requested_value=requested,
            actual_value=actual,
            error=error,
            unit="m",
            duration_s=self._clock() - started,
            completed=completed,
            reason=reason,
            start_pose=start_pose,
            end_pose=end_pose,
            measurement_source=measurement,
        )

    def _run_turn(self, action: str, requested_deg: float, *, direction: int) -> MotionActionResult:
        self._validate_positive(requested_deg, "angle_deg", self.config.max_angle_deg)
        target = math.radians(requested_deg)
        nominal = self.config.yaw_speed_radps
        started = self._clock()
        start_pose: MotionPose | None = None
        end_pose: MotionPose | None = None
        accumulated = 0.0
        actual_deg: float | None = 0.0
        completed = False
        reason = "unknown"
        measurement = "SportModeState"
        with self._action_ownership():
            try:
                if self._safe_stop(f"{action}:preflight") != 0:
                    raise MotionActionFailure("preflight_stop_failed")
                start_pose = self._read_pose()
            except MotionActionFailure as exc:
                if not self.config.allow_time_fallback or str(exc) != "state_unavailable":
                    reason = str(exc)
                else:
                    measurement = "estimated_time_fallback"
                    actual_deg, completed, reason = self._run_timed_velocity(
                        action,
                        target / nominal,
                        vx=0.0,
                        vy=0.0,
                        wz=direction * nominal,
                    )
            else:
                last_yaw = start_pose.yaw
                stall_checkpoint_at = self._clock()
                stall_checkpoint_yaw = 0.0
                timeout = target / nominal * self.config.timeout_scale + self.config.timeout_margin_s
                deadline = started + timeout
                try:
                    while True:
                        self._check_abort()
                        if self._clock() >= deadline:
                            reason = "timeout"
                            break
                        end_pose = self._read_pose()
                        accumulated += wrap_to_pi(end_pose.yaw - last_yaw)
                        last_yaw = end_pose.yaw
                        directed = direction * accumulated
                        actual_deg = math.degrees(directed)
                        remaining = target - directed
                        self._emit_progress(action, actual_deg, requested_deg, end_pose)
                        if remaining <= math.radians(self.config.angle_tolerance_deg):
                            completed = True
                            reason = "target_reached"
                            break
                        now = self._clock()
                        if now - stall_checkpoint_at >= self.config.rotation_stall_window_s:
                            progress_since_checkpoint = directed - stall_checkpoint_yaw
                            if progress_since_checkpoint < math.radians(
                                self.config.rotation_stall_min_progress_deg
                            ):
                                reason = "rotation_stalled"
                                break
                            stall_checkpoint_at = now
                            stall_checkpoint_yaw = directed
                        speed = nominal
                        if remaining <= math.radians(self.config.deceleration_angle_deg):
                            speed = min(nominal, self.config.reduced_yaw_speed_radps)
                        self._refresh(0.0, 0.0, direction * speed, action)
                        self._sleep(1.0 / self.config.control_rate_hz)
                except MotionActionFailure as exc:
                    reason = str(exc)
                except Exception as exc:
                    reason = f"exception:{type(exc).__name__}:{exc}"
            stop_code = self._safe_stop(f"{action}:complete")
        if stop_code != 0:
            completed = False
            reason = "stop_failed"
        end_pose = self._try_read_pose(require_fresh=False) or end_pose or start_pose
        if measurement == "SportModeState":
            actual_deg = math.degrees(direction * accumulated)
        error = actual_deg - requested_deg if actual_deg is not None else None
        return MotionActionResult(
            action=action,
            requested_value=requested_deg,
            actual_value=actual_deg,
            error=error,
            unit="deg",
            duration_s=self._clock() - started,
            completed=completed,
            reason=reason,
            start_pose=start_pose,
            end_pose=end_pose,
            measurement_source=measurement,
        )

    def _run_timed_velocity(
        self, action: str, duration: float, *, vx: float, vy: float, wz: float
    ) -> tuple[None, bool, str]:
        deadline = self._clock() + duration
        while self._clock() < deadline:
            self._check_abort()
            self._refresh(vx, vy, wz, action)
            self._sleep(min(1.0 / self.config.control_rate_hz, deadline - self._clock()))
        return None, True, "time_fallback_completed_unmeasured"

    def _refresh(self, vx: float, vy: float, wz: float, action: str) -> None:
        vx = max(-self.config.max_vx_mps, min(self.config.max_vx_mps, vx))
        vy = max(-self.config.max_vy_mps, min(self.config.max_vy_mps, vy))
        wz = max(-self.config.max_wz_radps, min(self.config.max_wz_radps, wz))
        self.robot_service.refresh_velocity(vx, vy, wz, source=self.CONTROL_OWNER)

    def _read_pose(self) -> MotionPose:
        return self._motion_pose(require_fresh=True)

    def _try_read_pose(self, *, require_fresh: bool) -> MotionPose | None:
        try:
            return self._motion_pose(require_fresh=require_fresh)
        except MotionActionFailure:
            if require_fresh:
                raise
            return None

    def _motion_pose(self, *, require_fresh: bool) -> MotionPose:
        try:
            state = self.robot_service.get_motion_state()
        except Exception as exc:
            raise MotionActionFailure(f"state_provider_error:{type(exc).__name__}") from exc
        if state is None:
            raise MotionActionFailure("state_unavailable")
        if not isinstance(state, dict):
            raise MotionActionFailure("invalid_pose")
        try:
            pose = MotionPose(
                x=float(state["x"]),
                y=float(state["y"]),
                yaw=float(state["yaw"]),
                received_monotonic=float(state["received_monotonic"]),
                source=str(state.get("source") or "SportModeState"),
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            raise MotionActionFailure("invalid_pose")
        if not all(math.isfinite(v) for v in (pose.x, pose.y, pose.yaw, pose.received_monotonic)):
            raise MotionActionFailure("invalid_pose")
        if require_fresh:
            age = self._clock() - pose.received_monotonic
            if age < 0:
                raise MotionActionFailure("state_timestamp_invalid")
            if age > self.config.state_timeout_s:
                raise MotionActionFailure("state_stale")
        return pose

    def _check_abort(self) -> None:
        if self._abort.is_set():
            raise MotionActionFailure("action_aborted")

    def _interruptible_sleep(self, seconds: float) -> None:
        deadline = self._clock() + seconds
        while self._clock() < deadline:
            self._check_abort()
            self._sleep(
                min(1.0 / self.config.control_rate_hz, deadline - self._clock())
            )

    def _safe_stop(self, reason: str) -> int:
        try:
            return int(self.robot_service.safe_stop(f"{self.CONTROL_OWNER}:{reason}"))
        except Exception:
            return -1

    def _emit_progress(
        self, action: str, actual: float, target: float, pose: MotionPose
    ) -> None:
        if self._progress_callback is None:
            return
        self._progress_callback(
            {
                "action": action,
                "progress": actual,
                "target": target,
                "pose": pose.to_dict(),
            }
        )

    @contextmanager
    def _action_ownership(self) -> Iterator[None]:
        acquired_here = self._ownership_depth == 0
        if acquired_here:
            self.acquire()
        try:
            yield
        finally:
            # This second, idempotent stop is intentional: it also runs for
            # BaseException paths such as KeyboardInterrupt and SystemExit.
            self._safe_stop("action_scope_cleanup")
            if acquired_here:
                self.release()

    @staticmethod
    def _validate_positive(value: float, name: str, maximum: float) -> None:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a number") from exc
        if not math.isfinite(number) or number <= 0 or number > maximum:
            raise ValueError(f"{name} must be finite and in (0, {maximum}]")

    @staticmethod
    def _validate_pose(
        *,
        roll_deg: float,
        pitch_deg: float,
        yaw_deg: float,
        body_height_m: float,
        duration_s: float,
    ) -> None:
        values = {
            "roll_deg": (roll_deg, -HARD_MAX_POSE_ROLL_DEG, HARD_MAX_POSE_ROLL_DEG),
            "pitch_deg": (
                pitch_deg,
                -HARD_MAX_POSE_PITCH_DEG,
                HARD_MAX_POSE_PITCH_DEG,
            ),
            "yaw_deg": (yaw_deg, -HARD_MAX_POSE_YAW_DEG, HARD_MAX_POSE_YAW_DEG),
            "body_height_m": (
                body_height_m,
                HARD_MIN_BODY_HEIGHT_M,
                HARD_MAX_BODY_HEIGHT_M,
            ),
            "duration_s": (duration_s, 0.0, HARD_MAX_POSE_DURATION_S),
        }
        for name, (value, minimum, maximum) in values.items():
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} must be a number") from exc
            lower_ok = number > minimum if name == "duration_s" else number >= minimum
            if not math.isfinite(number) or not lower_ok or number > maximum:
                bracket = "(" if name == "duration_s" else "["
                raise ValueError(
                    f"{name} must be finite and in {bracket}{minimum}, {maximum}]"
                )
