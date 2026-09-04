from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from app.core.control_owner import ControlOwner
from app.follow.controller import SafetyGuard, SafetyState, VelocityCommand
from app.follow.planner import FollowOffset, FollowPlan


class FollowControlAlgorithm(str, Enum):
    """Algorithms available only to the software simulation experiment."""

    BASELINE_P = "baseline_p"
    PI = "pi"
    VELOCITY_FEEDFORWARD = "velocity_feedforward"
    PI_FEEDFORWARD = "pi_feedforward"


@dataclass(frozen=True)
class ExperimentalFollowControllerConfig:
    kp_x: float = 0.2
    ki_x: float = 0.07
    kp_yaw: float = 0.8
    max_vx: float = 0.3
    max_wz: float = 0.5
    integral_limit: float = 1.5
    feedforward_gain: float = 1.0
    velocity_filter_alpha: float = 0.4
    maximum_estimated_target_speed: float = 0.5

    def __post_init__(self) -> None:
        positive = (
            "kp_x",
            "ki_x",
            "kp_yaw",
            "max_vx",
            "max_wz",
            "integral_limit",
            "feedforward_gain",
            "maximum_estimated_target_speed",
        )
        for name in positive:
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and greater than zero")
        if (
            not math.isfinite(self.velocity_filter_alpha)
            or not 0.0 < self.velocity_filter_alpha <= 1.0
        ):
            raise ValueError("velocity_filter_alpha must be in (0, 1]")


class ExperimentalFollowController:
    """Stateful PI/feedforward controller used only by the simulator.

    The target forward speed estimate combines the change in UWB relative x
    with the previous robot command and rotational compensation. No synthetic
    value replaces a UWB field; the estimate is a derived control-layer value.
    """

    def __init__(
        self,
        algorithm: FollowControlAlgorithm,
        safety_guard: SafetyGuard,
        offset: FollowOffset,
        config: ExperimentalFollowControllerConfig | None = None,
    ) -> None:
        if algorithm is FollowControlAlgorithm.BASELINE_P:
            raise ValueError("baseline P must use the production FollowController")
        self.algorithm = algorithm
        self.safety_guard = safety_guard
        self.offset = offset
        self.config = config or ExperimentalFollowControllerConfig()
        self._integral_x = 0.0
        self._last_sample_monotonic: float | None = None
        self._previous_measured_x: float | None = None
        self._previous_measured_y: float | None = None
        self._estimated_target_vx = 0.0
        self._previous_command_vx = 0.0
        self._previous_command_wz = 0.0

    def calculate_velocity(
        self,
        plan: FollowPlan,
        *,
        control_owner: ControlOwner,
        measurement_age_seconds: float | None,
        sample_monotonic: float | None,
    ) -> VelocityCommand:
        decision = self.safety_guard.evaluate(
            plan,
            control_owner=control_owner,
            measurement_age_seconds=measurement_age_seconds,
        )
        if decision.stop_required:
            self.reset()
            return VelocityCommand(
                vx=0.0,
                vy=0.0,
                wz=0.0,
                safety_state=decision.state,
                simulation_mode=True,
            )

        new_sample = (
            sample_monotonic is not None
            and sample_monotonic != self._last_sample_monotonic
        )
        measured_x = plan.target_x + self.offset.back_distance
        measured_y = plan.target_y + self.offset.right_offset
        sample_dt: float | None = None
        if new_sample and self._last_sample_monotonic is not None:
            sample_dt = sample_monotonic - self._last_sample_monotonic
            if not math.isfinite(sample_dt) or sample_dt <= 0.0:
                sample_dt = None

        if new_sample:
            self._update_target_velocity_estimate(
                measured_x,
                measured_y,
                sample_dt,
            )

        feedforward = (
            self.config.feedforward_gain * self._estimated_target_vx
            if self.algorithm
            in (
                FollowControlAlgorithm.VELOCITY_FEEDFORWARD,
                FollowControlAlgorithm.PI_FEEDFORWARD,
            )
            else 0.0
        )
        use_integral = self.algorithm in (
            FollowControlAlgorithm.PI,
            FollowControlAlgorithm.PI_FEEDFORWARD,
        )
        if use_integral and new_sample and sample_dt is not None:
            candidate_integral = _clamp(
                self._integral_x + plan.target_x * sample_dt,
                -self.config.integral_limit,
                self.config.integral_limit,
            )
            candidate_vx = (
                self.config.kp_x * plan.target_x
                + self.config.ki_x * candidate_integral
                + feedforward
            )
            saturated_with_same_sign = (
                abs(candidate_vx) > self.config.max_vx
                and candidate_vx * plan.target_x > 0.0
            )
            if not saturated_with_same_sign:
                self._integral_x = candidate_integral

        raw_vx = (
            self.config.kp_x * plan.target_x
            + (self.config.ki_x * self._integral_x if use_integral else 0.0)
            + feedforward
        )
        raw_wz = self.config.kp_yaw * plan.target_y
        command = VelocityCommand(
            vx=(
                _clamp(raw_vx, -self.config.max_vx, self.config.max_vx)
                * decision.speed_scale
            ),
            vy=0.0,
            wz=(
                _clamp(raw_wz, -self.config.max_wz, self.config.max_wz)
                * decision.speed_scale
            ),
            safety_state=decision.state,
            simulation_mode=True,
        )
        self._previous_command_vx = command.vx
        self._previous_command_wz = command.wz
        if new_sample:
            self._last_sample_monotonic = sample_monotonic
            self._previous_measured_x = measured_x
            self._previous_measured_y = measured_y
        return command

    def reset(self) -> None:
        self._integral_x = 0.0
        self._last_sample_monotonic = None
        self._previous_measured_x = None
        self._previous_measured_y = None
        self._estimated_target_vx = 0.0
        self._previous_command_vx = 0.0
        self._previous_command_wz = 0.0

    def _update_target_velocity_estimate(
        self,
        measured_x: float,
        measured_y: float,
        sample_dt: float | None,
    ) -> None:
        if (
            sample_dt is None
            or self._previous_measured_x is None
            or self._previous_measured_y is None
        ):
            return
        relative_vx = (measured_x - self._previous_measured_x) / sample_dt
        rotational_compensation = -self._previous_command_wz * measured_y
        raw_target_vx = (
            relative_vx + self._previous_command_vx + rotational_compensation
        )
        raw_target_vx = _clamp(
            raw_target_vx,
            -self.config.maximum_estimated_target_speed,
            self.config.maximum_estimated_target_speed,
        )
        alpha = self.config.velocity_filter_alpha
        self._estimated_target_vx = (
            alpha * raw_target_vx + (1.0 - alpha) * self._estimated_target_vx
        )


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))
