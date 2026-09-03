from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from enum import Enum

from app.config import Settings
from app.core.control_owner import ControlOwner
from app.follow.planner import FollowPlan, FollowState


LOGGER = logging.getLogger(__name__)


class SafetyState(str, Enum):
    SAFE = "SAFE"
    LIMITED_ABNORMAL_YAW = "LIMITED_ABNORMAL_YAW"
    STOP_UWB_TIMEOUT = "STOP_UWB_TIMEOUT"
    STOP_DISTANCE_TOO_CLOSE = "STOP_DISTANCE_TOO_CLOSE"
    STOP_CONTROL_NOT_FOLLOW = "STOP_CONTROL_NOT_FOLLOW"
    STOP_WAITING_FOR_TARGET = "STOP_WAITING_FOR_TARGET"
    STOP_INVALID_MEASUREMENT = "STOP_INVALID_MEASUREMENT"
    STOP_PLANNER_REQUEST = "STOP_PLANNER_REQUEST"


class FollowDistanceMode(str, Enum):
    WAITING_TO_START = "WAITING_TO_START"
    ACTIVE = "ACTIVE"
    HOLD_TOO_CLOSE = "HOLD_TOO_CLOSE"
    SAFETY_STOP = "SAFETY_STOP"


@dataclass(frozen=True)
class FollowControllerConfig:
    """Bounded controller configuration; feedforward is opt-in and fail-safe."""

    kx: float = 0.2
    ky: float = 0.8
    max_vx: float = 0.3
    max_wz: float = 0.5
    simulation_mode: bool = True
    velocity_feedforward_enabled: bool = False
    velocity_feedforward_gain: float = 1.0
    velocity_filter_alpha: float = 0.4
    max_estimated_target_speed: float = 0.3
    max_plausible_target_speed: float = 0.8
    minimum_sample_dt: float = 0.10
    maximum_sample_dt: float = 0.50
    walking_speed_floor_enabled: bool = False
    minimum_walking_vx: float = 0.20
    forward_start_error: float = 0.25
    forward_stop_error: float = 0.10
    distance_deadband_enabled: bool = False
    follow_start_distance: float = 1.80
    follow_stop_distance: float = 1.70
    bearing_deadband_radians: float = 0.0
    desired_bearing_radians: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "kx",
            "ky",
            "max_vx",
            "max_wz",
            "velocity_feedforward_gain",
            "max_estimated_target_speed",
            "max_plausible_target_speed",
            "minimum_sample_dt",
            "maximum_sample_dt",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and greater than zero")
        if (
            not math.isfinite(self.velocity_filter_alpha)
            or not 0.0 < self.velocity_filter_alpha <= 1.0
        ):
            raise ValueError("velocity_filter_alpha must be in (0, 1]")
        if self.maximum_sample_dt <= self.minimum_sample_dt:
            raise ValueError("maximum_sample_dt must be greater than minimum_sample_dt")
        if self.max_estimated_target_speed > self.max_plausible_target_speed:
            raise ValueError(
                "max_estimated_target_speed must not exceed max_plausible_target_speed"
            )
        for name in ("walking_speed_floor_enabled", "distance_deadband_enabled"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")
        for name in (
            "minimum_walking_vx",
            "forward_start_error",
            "forward_stop_error",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.minimum_walking_vx <= 0.0:
            raise ValueError("minimum_walking_vx must be greater than zero")
        if self.forward_start_error <= self.forward_stop_error:
            raise ValueError(
                "forward_start_error must be greater than forward_stop_error"
            )
        for name in ("follow_start_distance", "follow_stop_distance"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and greater than zero")
        if self.follow_start_distance <= self.follow_stop_distance:
            raise ValueError(
                "follow_start_distance must be greater than follow_stop_distance"
            )
        if (
            not math.isfinite(self.bearing_deadband_radians)
            or not 0.0 <= self.bearing_deadband_radians < math.pi
        ):
            raise ValueError("bearing_deadband_radians must be within [0, pi)")
        if not math.isfinite(self.desired_bearing_radians):
            raise ValueError("desired_bearing_radians must be finite")
        if (
            self.walking_speed_floor_enabled
            and self.minimum_walking_vx > self.max_vx
        ):
            raise ValueError(
                "minimum_walking_vx must not exceed max_vx when the walking "
                "speed floor is enabled"
            )

    @classmethod
    def from_settings(cls, settings: Settings) -> FollowControllerConfig:
        return cls(
            simulation_mode=settings.follow_simulation,
            velocity_feedforward_enabled=(
                settings.follow_velocity_feedforward_enabled
            ),
            velocity_feedforward_gain=settings.follow_velocity_feedforward_gain,
            velocity_filter_alpha=settings.follow_velocity_filter_alpha,
            max_estimated_target_speed=(
                settings.follow_max_estimated_target_speed
            ),
            max_plausible_target_speed=(
                settings.follow_max_plausible_target_speed
            ),
        )


@dataclass(frozen=True)
class SafetyGuardConfig:
    min_distance: float = 1.0
    uwb_timeout_seconds: float = 2.0
    abnormal_yaw_threshold: float = math.radians(60.0)
    abnormal_yaw_speed_scale: float = 0.5

    def __post_init__(self) -> None:
        positive_values = {
            "min_distance": self.min_distance,
            "uwb_timeout_seconds": self.uwb_timeout_seconds,
            "abnormal_yaw_threshold": self.abnormal_yaw_threshold,
        }
        for name, value in positive_values.items():
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and greater than zero")
        if (
            not math.isfinite(self.abnormal_yaw_speed_scale)
            or not 0.0 < self.abnormal_yaw_speed_scale <= 1.0
        ):
            raise ValueError("abnormal_yaw_speed_scale must be in (0, 1]")


@dataclass(frozen=True)
class SafetyDecision:
    state: SafetyState
    stop_required: bool
    speed_scale: float


@dataclass(frozen=True)
class VelocityCommand:
    """A velocity suggestion only; this type has no dispatch method."""

    vx: float
    vy: float
    wz: float
    safety_state: SafetyState
    simulation_mode: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "vx": self.vx,
            "vy": self.vy,
            "wz": self.wz,
            "safety_state": self.safety_state.value,
            "simulation_mode": self.simulation_mode,
        }


class SafetyGuard:
    """Apply follow-specific safety gates before producing a suggestion."""

    def __init__(self, config: SafetyGuardConfig | None = None) -> None:
        self.config = config or SafetyGuardConfig()

    def evaluate(
        self,
        plan: FollowPlan,
        *,
        control_owner: ControlOwner,
        measurement_age_seconds: float | None = None,
    ) -> SafetyDecision:
        if control_owner is not ControlOwner.FOLLOW:
            return self._stop(SafetyState.STOP_CONTROL_NOT_FOLLOW)

        if measurement_age_seconds is not None:
            if not math.isfinite(measurement_age_seconds) or measurement_age_seconds < 0.0:
                return self._stop(SafetyState.STOP_INVALID_MEASUREMENT)
            if measurement_age_seconds >= self.config.uwb_timeout_seconds:
                return self._stop(SafetyState.STOP_UWB_TIMEOUT)

        if plan.current_state is FollowState.FOLLOW_TARGET_LOST:
            return self._stop(SafetyState.STOP_UWB_TIMEOUT)
        if plan.current_state is FollowState.FOLLOW_WAITING_FOR_TARGET:
            return self._stop(SafetyState.STOP_WAITING_FOR_TARGET)

        if plan.uwb_distance is None or not math.isfinite(plan.uwb_distance):
            return self._stop(SafetyState.STOP_INVALID_MEASUREMENT)
        if (
            plan.current_state is FollowState.FOLLOW_TOO_CLOSE
            or plan.uwb_distance < self.config.min_distance
        ):
            return self._stop(SafetyState.STOP_DISTANCE_TOO_CLOSE)
        if plan.stop_required:
            return self._stop(SafetyState.STOP_PLANNER_REQUEST)

        if plan.uwb_yaw is None or not math.isfinite(plan.uwb_yaw):
            return self._stop(SafetyState.STOP_INVALID_MEASUREMENT)

        principal_yaw = math.atan2(math.sin(plan.uwb_yaw), math.cos(plan.uwb_yaw))
        if abs(principal_yaw) > self.config.abnormal_yaw_threshold:
            return SafetyDecision(
                state=SafetyState.LIMITED_ABNORMAL_YAW,
                stop_required=False,
                speed_scale=self.config.abnormal_yaw_speed_scale,
            )
        return SafetyDecision(
            state=SafetyState.SAFE,
            stop_required=False,
            speed_scale=1.0,
        )

    @staticmethod
    def _stop(state: SafetyState) -> SafetyDecision:
        return SafetyDecision(state=state, stop_required=True, speed_scale=0.0)


class FollowController:
    """Convert ``FollowPlan`` into a bounded, non-dispatched velocity suggestion."""

    def __init__(
        self,
        config: FollowControllerConfig | None = None,
        safety_guard: SafetyGuard | None = None,
        *,
        logger: logging.Logger = LOGGER,
    ) -> None:
        self.config = config or FollowControllerConfig.from_settings(Settings())
        self.safety_guard = safety_guard or SafetyGuard()
        self._logger = logger
        self._feedforward = _SafeVelocityFeedforwardEstimator(self.config)
        self._previous_command_vx = 0.0
        self._previous_command_wz = 0.0
        self._forward_walk_active = False
        self._distance_hold_latched = False
        self._distance_mode = FollowDistanceMode.WAITING_TO_START
        self._motion_reason = "initialized"
        self._last_feedforward_vx = 0.0
        self._feedforward_status = (
            "waiting_for_sample"
            if self.config.velocity_feedforward_enabled
            else "disabled"
        )

    @property
    def last_feedforward_vx(self) -> float:
        return self._last_feedforward_vx

    @property
    def feedforward_status(self) -> str:
        return self._feedforward_status

    @property
    def distance_mode(self) -> FollowDistanceMode:
        return self._distance_mode

    @property
    def motion_reason(self) -> str:
        return self._motion_reason

    def calculate_velocity(
        self,
        plan: FollowPlan,
        *,
        control_owner: ControlOwner,
        measurement_age_seconds: float | None = None,
        sample_monotonic: float | None = None,
    ) -> VelocityCommand:
        decision = self.safety_guard.evaluate(
            plan,
            control_owner=control_owner,
            measurement_age_seconds=measurement_age_seconds,
        )

        if decision.stop_required:
            self.reset_dynamic_state("safety_stop")
            self._distance_mode = FollowDistanceMode.SAFETY_STOP
            self._motion_reason = decision.state.value
            command = VelocityCommand(
                vx=0.0,
                vy=0.0,
                wz=0.0,
                safety_state=decision.state,
                simulation_mode=self.config.simulation_mode,
            )
        else:
            feedforward_vx, feedforward_status = self._feedforward.update(
                plan,
                sample_monotonic=sample_monotonic,
                previous_command_vx=self._previous_command_vx,
                previous_command_wz=self._previous_command_wz,
            )
            if decision.state is SafetyState.LIMITED_ABNORMAL_YAW:
                feedforward_vx = 0.0
                feedforward_status = "suppressed_abnormal_yaw"
            self._last_feedforward_vx = feedforward_vx
            self._feedforward_status = feedforward_status
            vx = _clamp(
                self.config.kx * plan.target_x + feedforward_vx,
                -self.config.max_vx,
                self.config.max_vx,
            )
            vx = self._apply_walking_speed_policy(
                vx,
                plan.target_x,
                plan.uwb_distance,
            )
            wz = _clamp(
                self.config.ky * plan.target_y,
                -self.config.max_wz,
                self.config.max_wz,
            )
            if self._bearing_is_in_deadband(plan.uwb_yaw):
                wz = 0.0
            command = VelocityCommand(
                vx=vx * decision.speed_scale,
                vy=0.0,
                wz=wz * decision.speed_scale,
                safety_state=decision.state,
                simulation_mode=self.config.simulation_mode,
            )
            if decision.state is not SafetyState.SAFE:
                self._motion_reason = decision.state.value

        self._previous_command_vx = command.vx
        self._previous_command_wz = command.wz

        self._emit(plan, command)
        return command

    def reset_dynamic_state(self, reason: str = "external_reset") -> None:
        self._feedforward.reset()
        self._previous_command_vx = 0.0
        self._previous_command_wz = 0.0
        self._last_feedforward_vx = 0.0
        self._feedforward_status = reason
        self._forward_walk_active = False
        self._distance_hold_latched = False
        self._distance_mode = FollowDistanceMode.WAITING_TO_START
        self._motion_reason = reason

    def _apply_walking_speed_policy(
        self,
        candidate_vx: float,
        forward_error: float,
        measured_distance: float | None,
    ) -> float:
        """Map positive follow demand into Go2's validated walking range.

        The policy is opt-in so the frozen Mock behavior remains unchanged.
        It intentionally does not command reverse motion.  Hysteresis keeps a
        walking gait active until the target reaches the tighter stop band.
        Downstream LiDAR/risk arbitration remains free to scale or zero this
        result.
        """

        if not self.config.walking_speed_floor_enabled:
            self._distance_mode = (
                FollowDistanceMode.ACTIVE
                if abs(candidate_vx) > 1e-9
                else FollowDistanceMode.WAITING_TO_START
            )
            self._motion_reason = "proportional_control"
            return candidate_vx

        comparison_epsilon = 1e-9
        if self.config.distance_deadband_enabled:
            if measured_distance is None or not math.isfinite(measured_distance):
                self._forward_walk_active = False
                self._distance_hold_latched = False
                self._distance_mode = FollowDistanceMode.SAFETY_STOP
                self._motion_reason = "invalid_control_distance"
                return 0.0
            start_value = self.config.follow_start_distance
            stop_value = self.config.follow_stop_distance
            policy_value = measured_distance
        else:
            start_value = self.config.forward_start_error
            stop_value = self.config.forward_stop_error
            policy_value = forward_error

        if self._forward_walk_active:
            if policy_value <= stop_value + comparison_epsilon:
                self._forward_walk_active = False
                self._distance_hold_latched = True
                self._distance_mode = FollowDistanceMode.HOLD_TOO_CLOSE
                self._motion_reason = "distance_stop_threshold"
            else:
                self._distance_mode = FollowDistanceMode.ACTIVE
                self._motion_reason = "following_active"
        elif self._distance_hold_latched:
            if policy_value >= start_value - comparison_epsilon:
                self._forward_walk_active = True
                self._distance_hold_latched = False
                self._distance_mode = FollowDistanceMode.ACTIVE
                self._motion_reason = "auto_resume_distance_clear"
            else:
                self._distance_mode = FollowDistanceMode.HOLD_TOO_CLOSE
                self._motion_reason = "holding_until_resume_distance"
        elif policy_value >= start_value - comparison_epsilon:
            self._forward_walk_active = True
            self._distance_mode = FollowDistanceMode.ACTIVE
            self._motion_reason = "distance_start_threshold"
        elif policy_value <= stop_value + comparison_epsilon:
            self._distance_hold_latched = True
            self._distance_mode = FollowDistanceMode.HOLD_TOO_CLOSE
            self._motion_reason = "distance_too_close_hold"
        else:
            self._distance_mode = FollowDistanceMode.WAITING_TO_START
            self._motion_reason = "waiting_for_start_distance"

        if not self._forward_walk_active:
            return 0.0
        if candidate_vx <= 0.0:
            # With distance-based hysteresis the gait remains active until the
            # tighter stop threshold, even if proportional position error has
            # crossed zero slightly earlier. This prevents a hidden third
            # threshold from defeating the configured start/stop band.
            if self.config.distance_deadband_enabled:
                return min(self.config.minimum_walking_vx, self.config.max_vx)
            return 0.0
        return _clamp(
            max(candidate_vx, self.config.minimum_walking_vx),
            0.0,
            self.config.max_vx,
        )

    def _bearing_is_in_deadband(self, measured_bearing: float | None) -> bool:
        if self.config.bearing_deadband_radians <= 0.0:
            return False
        if measured_bearing is None or not math.isfinite(measured_bearing):
            return False
        error = math.atan2(
            math.sin(measured_bearing - self.config.desired_bearing_radians),
            math.cos(measured_bearing - self.config.desired_bearing_radians),
        )
        return abs(error) <= self.config.bearing_deadband_radians

    def _emit(self, plan: FollowPlan, command: VelocityCommand) -> None:
        record = {
            "timestamp": plan.timestamp,
            "uwb_distance": plan.uwb_distance,
            "uwb_yaw": plan.uwb_yaw,
            "target_position": {
                "x": plan.target_x,
                "y": plan.target_y,
            },
            "velocity_command": {
                "vx": command.vx,
                "vy": command.vy,
                "wz": command.wz,
            },
            "safety_state": command.safety_state.value,
            "simulation_mode": command.simulation_mode,
        }
        if self.config.velocity_feedforward_enabled:
            record["velocity_feedforward"] = {
                "vx": self._last_feedforward_vx,
                "status": self._feedforward_status,
            }
        self._logger.info(
            json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        )


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


class _SafeVelocityFeedforwardEstimator:
    """Estimate target forward velocity only from validated, ordered samples."""

    def __init__(self, config: FollowControllerConfig) -> None:
        self.config = config
        self._last_sample_monotonic: float | None = None
        self._previous_measured_x: float | None = None
        self._previous_measured_y: float | None = None
        self._filtered_target_vx = 0.0

    def update(
        self,
        plan: FollowPlan,
        *,
        sample_monotonic: float | None,
        previous_command_vx: float,
        previous_command_wz: float,
    ) -> tuple[float, str]:
        if not self.config.velocity_feedforward_enabled:
            return 0.0, "disabled"
        if sample_monotonic is None or not math.isfinite(sample_monotonic):
            self.reset()
            return 0.0, "missing_or_invalid_sample_time"

        if plan.uwb_distance is None or plan.uwb_yaw is None:
            self.reset()
            return 0.0, "missing_measurement"
        measured_x = plan.uwb_distance * math.cos(plan.uwb_yaw)
        measured_y = plan.uwb_distance * math.sin(plan.uwb_yaw)
        if self._last_sample_monotonic is None:
            self._prime(sample_monotonic, measured_x, measured_y)
            return 0.0, "priming"
        if sample_monotonic == self._last_sample_monotonic:
            return (
                self.config.velocity_feedforward_gain * self._filtered_target_vx,
                "holding",
            )

        dt = sample_monotonic - self._last_sample_monotonic
        if (
            not math.isfinite(dt)
            or dt < self.config.minimum_sample_dt
            or dt > self.config.maximum_sample_dt
        ):
            self.reset()
            self._prime(sample_monotonic, measured_x, measured_y)
            return 0.0, "rejected_sample_interval"

        if self._previous_measured_x is None or self._previous_measured_y is None:
            self._prime(sample_monotonic, measured_x, measured_y)
            return 0.0, "priming"
        relative_vx = (measured_x - self._previous_measured_x) / dt
        target_vx = (
            relative_vx
            + previous_command_vx
            - previous_command_wz * measured_y
        )
        self._prime(sample_monotonic, measured_x, measured_y)
        if (
            not math.isfinite(target_vx)
            or abs(target_vx) > self.config.max_plausible_target_speed
        ):
            self._filtered_target_vx = 0.0
            return 0.0, "rejected_velocity_spike"

        target_vx = _clamp(
            target_vx,
            -self.config.max_estimated_target_speed,
            self.config.max_estimated_target_speed,
        )
        alpha = self.config.velocity_filter_alpha
        self._filtered_target_vx = (
            alpha * target_vx + (1.0 - alpha) * self._filtered_target_vx
        )
        return (
            self.config.velocity_feedforward_gain * self._filtered_target_vx,
            "updated",
        )

    def reset(self) -> None:
        self._last_sample_monotonic = None
        self._previous_measured_x = None
        self._previous_measured_y = None
        self._filtered_target_vx = 0.0

    def _prime(
        self,
        sample_monotonic: float,
        measured_x: float,
        measured_y: float,
    ) -> None:
        self._last_sample_monotonic = sample_monotonic
        self._previous_measured_x = measured_x
        self._previous_measured_y = measured_y
