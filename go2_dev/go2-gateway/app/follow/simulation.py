from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Tuple

from app.core.control_owner import ControlOwner
from app.follow.controller import (
    FollowController,
    FollowControllerConfig,
    SafetyGuard,
    SafetyGuardConfig,
)
from app.follow.experimental_controller import (
    ExperimentalFollowController,
    ExperimentalFollowControllerConfig,
    FollowControlAlgorithm,
)
from app.follow.planner import FollowOffset, FollowTargetPlanner


@dataclass(frozen=True)
class Pose2D:
    """Planar pose in a fixed simulation world frame."""

    x: float
    y: float
    yaw: float = 0.0


PersonPath = Callable[[float], Pose2D]
UwbAvailability = Callable[[float], bool]
ControlOwnerPath = Callable[[float], ControlOwner]
UwbMeasurementModel = Callable[[float, float, float], Tuple[float, float]]


@dataclass(frozen=True)
class FollowSimulationConfig:
    """Deterministic software-only timing and initial-state configuration."""

    duration_seconds: float = 15.0
    integration_step_seconds: float = 0.05
    uwb_frequency_hz: float = 5.0
    robot_start: Pose2D = Pose2D(0.0, 0.0, 0.0)

    def __post_init__(self) -> None:
        positive = {
            "duration_seconds": self.duration_seconds,
            "integration_step_seconds": self.integration_step_seconds,
            "uwb_frequency_hz": self.uwb_frequency_hz,
        }
        for name, value in positive.items():
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and greater than zero")
        if self.uwb_frequency_hz > 5.0:
            raise ValueError("uwb_frequency_hz must not exceed the measured 5 Hz rate")
        if self.integration_step_seconds > 1.0 / self.uwb_frequency_hz:
            raise ValueError("integration step must not exceed the UWB sample period")


@dataclass(frozen=True)
class FollowSimulationScenario:
    name: str
    person_path: PersonPath
    config: FollowSimulationConfig = FollowSimulationConfig()
    uwb_available: UwbAvailability = lambda _time_seconds: True
    control_owner: ControlOwnerPath = lambda _time_seconds: ControlOwner.FOLLOW
    uwb_measurement: UwbMeasurementModel = (
        lambda _time_seconds, distance, yaw: (distance, yaw)
    )


@dataclass(frozen=True)
class FollowSimulationSample:
    time_seconds: float
    robot_pose: Pose2D
    person_pose: Pose2D
    uwb_available: bool
    uwb_distance: float | None
    uwb_yaw: float | None
    target_x: float
    target_y: float
    vx: float
    wz: float
    safety_state: str
    control_owner: str

    def to_dict(self) -> dict[str, object]:
        return {
            "time_seconds": self.time_seconds,
            "robot_pose": _pose_dict(self.robot_pose),
            "person_pose": _pose_dict(self.person_pose),
            "uwb_available": self.uwb_available,
            "uwb_distance": self.uwb_distance,
            "uwb_yaw": self.uwb_yaw,
            "target_x": self.target_x,
            "target_y": self.target_y,
            "vx": self.vx,
            "wz": self.wz,
            "safety_state": self.safety_state,
            "control_owner": self.control_owner,
        }


@dataclass(frozen=True)
class FollowSimulationResult:
    scenario: str
    algorithm: str
    samples: tuple[FollowSimulationSample, ...]
    final_robot_pose: Pose2D
    final_person_pose: Pose2D
    final_target_error_x: float
    final_target_error_y: float
    final_target_error_norm: float
    minimum_true_distance: float
    maximum_abs_vx: float
    maximum_abs_wz: float
    safety_counts: dict[str, int]

    def summary(self) -> dict[str, object]:
        return {
            "scenario": self.scenario,
            "algorithm": self.algorithm,
            "sample_count": len(self.samples),
            "final_robot_pose": _pose_dict(self.final_robot_pose),
            "final_person_pose": _pose_dict(self.final_person_pose),
            "final_target_error": {
                "x": self.final_target_error_x,
                "y": self.final_target_error_y,
                "norm": self.final_target_error_norm,
            },
            "minimum_true_distance": self.minimum_true_distance,
            "maximum_abs_vx": self.maximum_abs_vx,
            "maximum_abs_wz": self.maximum_abs_wz,
            "safety_counts": dict(self.safety_counts),
        }

    def to_dict(self, *, include_samples: bool = True) -> dict[str, object]:
        result = self.summary()
        if include_samples:
            result["samples"] = [sample.to_dict() for sample in self.samples]
        return result


class FollowSimulation:
    """Software-only closed loop for the real follow planner and controller.

    This module intentionally has no imports from DDS, FollowExecutor,
    RobotService, Go2Gateway, adapters, or Unitree SDK2. The robot is a simple
    unicycle model, while UWB measurements are generated from exact geometry.
    """

    def __init__(
        self,
        offset: FollowOffset | None = None,
        controller_config: FollowControllerConfig | None = None,
        safety_config: SafetyGuardConfig | None = None,
        experimental_config: ExperimentalFollowControllerConfig | None = None,
        *,
        lost_timeout_seconds: float = 2.0,
        algorithm: FollowControlAlgorithm = FollowControlAlgorithm.BASELINE_P,
    ) -> None:
        self.offset = offset or FollowOffset()
        self.controller_config = controller_config or FollowControllerConfig(
            simulation_mode=True
        )
        if not self.controller_config.simulation_mode:
            raise ValueError("follow simulation requires simulation_mode=True")
        self.safety_config = safety_config or SafetyGuardConfig(
            min_distance=self.offset.min_distance,
            uwb_timeout_seconds=lost_timeout_seconds,
        )
        self.experimental_config = experimental_config
        self.lost_timeout_seconds = lost_timeout_seconds
        self.algorithm = algorithm

    def run(self, scenario: FollowSimulationScenario) -> FollowSimulationResult:
        planner = FollowTargetPlanner(
            self.offset,
            lost_timeout_seconds=self.lost_timeout_seconds,
        )
        safety_guard = SafetyGuard(self.safety_config)
        if self.algorithm in (
            FollowControlAlgorithm.BASELINE_P,
            FollowControlAlgorithm.VELOCITY_FEEDFORWARD,
        ):
            if self.algorithm is FollowControlAlgorithm.VELOCITY_FEEDFORWARD:
                experiment = (
                    self.experimental_config
                    or ExperimentalFollowControllerConfig()
                )
                production_config = FollowControllerConfig(
                    kx=experiment.kp_x,
                    ky=experiment.kp_yaw,
                    max_vx=experiment.max_vx,
                    max_wz=experiment.max_wz,
                    simulation_mode=True,
                    velocity_feedforward_enabled=True,
                    velocity_feedforward_gain=experiment.feedforward_gain,
                    velocity_filter_alpha=experiment.velocity_filter_alpha,
                    max_estimated_target_speed=min(
                        experiment.maximum_estimated_target_speed,
                        0.3,
                    ),
                    max_plausible_target_speed=0.8,
                )
            else:
                production_config = self.controller_config
            controller = FollowController(production_config, safety_guard)
        else:
            controller = ExperimentalFollowController(
                self.algorithm,
                safety_guard,
                self.offset,
                self.experimental_config,
            )
        config = scenario.config
        robot = config.robot_start
        uwb_period = 1.0 / config.uwb_frequency_hz
        next_uwb_at = 0.0
        last_uwb_at: float | None = None
        samples: list[FollowSimulationSample] = []
        minimum_distance = math.inf
        maximum_abs_vx = 0.0
        maximum_abs_wz = 0.0
        safety_counts: Counter[str] = Counter()
        step_count = int(math.ceil(config.duration_seconds / config.integration_step_seconds))

        for step_index in range(step_count + 1):
            time_seconds = min(
                step_index * config.integration_step_seconds,
                config.duration_seconds,
            )
            person = scenario.person_path(time_seconds)
            true_distance, true_yaw = _relative_measurement(robot, person)
            minimum_distance = min(minimum_distance, true_distance)
            available = bool(scenario.uwb_available(time_seconds))

            if time_seconds + 1e-12 >= next_uwb_at:
                if available:
                    measured_distance, measured_yaw = scenario.uwb_measurement(
                        time_seconds,
                        true_distance,
                        true_yaw,
                    )
                    plan = planner.process_measurement(
                        measured_distance,
                        measured_yaw,
                        sample_monotonic=time_seconds,
                    )
                    last_uwb_at = time_seconds
                else:
                    plan = planner.check_target_liveness(now_monotonic=time_seconds)
                while next_uwb_at <= time_seconds + 1e-12:
                    next_uwb_at += uwb_period
            else:
                plan = planner.check_target_liveness(now_monotonic=time_seconds)

            measurement_age = (
                None if last_uwb_at is None else time_seconds - last_uwb_at
            )
            owner = scenario.control_owner(time_seconds)
            if isinstance(controller, ExperimentalFollowController):
                command = controller.calculate_velocity(
                    plan,
                    control_owner=owner,
                    measurement_age_seconds=measurement_age,
                    sample_monotonic=last_uwb_at,
                )
            else:
                command = controller.calculate_velocity(
                    plan,
                    control_owner=owner,
                    measurement_age_seconds=measurement_age,
                    sample_monotonic=last_uwb_at,
                )
            maximum_abs_vx = max(maximum_abs_vx, abs(command.vx))
            maximum_abs_wz = max(maximum_abs_wz, abs(command.wz))
            safety_counts[command.safety_state.value] += 1
            samples.append(
                FollowSimulationSample(
                    time_seconds=time_seconds,
                    robot_pose=robot,
                    person_pose=person,
                    uwb_available=available,
                    uwb_distance=plan.uwb_distance,
                    uwb_yaw=plan.uwb_yaw,
                    target_x=plan.target_x,
                    target_y=plan.target_y,
                    vx=command.vx,
                    wz=command.wz,
                    safety_state=command.safety_state.value,
                    control_owner=owner.value,
                )
            )

            if time_seconds >= config.duration_seconds:
                break
            dt = min(
                config.integration_step_seconds,
                config.duration_seconds - time_seconds,
            )
            robot = _integrate_unicycle(robot, command.vx, command.wz, dt)

        final_person = scenario.person_path(config.duration_seconds)
        _, final_yaw = _relative_measurement(robot, final_person)
        final_distance, _ = _relative_measurement(robot, final_person)
        measured_x = final_distance * math.cos(final_yaw)
        measured_y = final_distance * math.sin(final_yaw)
        error_x = measured_x - self.offset.back_distance
        error_y = measured_y - self.offset.right_offset

        return FollowSimulationResult(
            scenario=scenario.name,
            algorithm=self.algorithm.value,
            samples=tuple(samples),
            final_robot_pose=robot,
            final_person_pose=final_person,
            final_target_error_x=error_x,
            final_target_error_y=error_y,
            final_target_error_norm=math.hypot(error_x, error_y),
            minimum_true_distance=minimum_distance,
            maximum_abs_vx=maximum_abs_vx,
            maximum_abs_wz=maximum_abs_wz,
            safety_counts=dict(safety_counts),
        )


def standard_simulation_scenarios() -> tuple[FollowSimulationScenario, ...]:
    """Deterministic scenarios covering tracking, noise, and safety behavior."""

    return (
        FollowSimulationScenario(
            "stationary_front_3m",
            lambda _t: Pose2D(3.0, 0.0),
            FollowSimulationConfig(duration_seconds=20.0),
        ),
        FollowSimulationScenario(
            "stationary_left_20deg",
            lambda _t: _polar_pose(2.0, math.radians(20.0)),
            FollowSimulationConfig(duration_seconds=15.0),
        ),
        FollowSimulationScenario(
            "stationary_right_20deg",
            lambda _t: _polar_pose(2.0, math.radians(-20.0)),
            FollowSimulationConfig(duration_seconds=15.0),
        ),
        FollowSimulationScenario(
            "moving_straight",
            lambda t: Pose2D(3.0 + 0.15 * t, 0.0),
            FollowSimulationConfig(duration_seconds=20.0),
        ),
        FollowSimulationScenario(
            "moving_straight_noisy",
            lambda t: Pose2D(3.0 + 0.15 * t, 0.0),
            FollowSimulationConfig(duration_seconds=20.0),
            uwb_measurement=_deterministic_noisy_uwb,
        ),
        FollowSimulationScenario(
            "moving_left_turn",
            lambda t: (
                Pose2D(3.0 + 0.15 * t, 0.0)
                if t <= 8.0
                else Pose2D(4.2, 0.15 * (t - 8.0))
            ),
            FollowSimulationConfig(duration_seconds=20.0),
        ),
        FollowSimulationScenario(
            "equilibrium",
            lambda _t: Pose2D(1.5, 0.5),
            FollowSimulationConfig(duration_seconds=3.0),
        ),
        FollowSimulationScenario(
            "too_close",
            lambda _t: Pose2D(0.8, 0.0),
            FollowSimulationConfig(duration_seconds=3.0),
        ),
        FollowSimulationScenario(
            "uwb_dropout",
            lambda _t: Pose2D(3.0, 0.0),
            FollowSimulationConfig(duration_seconds=5.0),
            uwb_available=lambda t: t < 1.0,
        ),
        FollowSimulationScenario(
            "manual_takeover",
            lambda _t: Pose2D(3.0, 0.0),
            FollowSimulationConfig(duration_seconds=4.0),
            control_owner=lambda t: (
                ControlOwner.FOLLOW if t < 1.0 else ControlOwner.MANUAL
            ),
        ),
        FollowSimulationScenario(
            "abnormal_yaw_90deg",
            lambda _t: Pose2D(0.0, 3.0),
            FollowSimulationConfig(duration_seconds=2.0),
        ),
    )


def _relative_measurement(robot: Pose2D, person: Pose2D) -> tuple[float, float]:
    dx_world = person.x - robot.x
    dy_world = person.y - robot.y
    cos_yaw = math.cos(robot.yaw)
    sin_yaw = math.sin(robot.yaw)
    relative_x = cos_yaw * dx_world + sin_yaw * dy_world
    relative_y = -sin_yaw * dx_world + cos_yaw * dy_world
    distance = math.hypot(relative_x, relative_y)
    yaw = math.atan2(relative_y, relative_x)
    return distance, yaw


def _integrate_unicycle(pose: Pose2D, vx: float, wz: float, dt: float) -> Pose2D:
    yaw_midpoint = pose.yaw + 0.5 * wz * dt
    return Pose2D(
        x=pose.x + vx * math.cos(yaw_midpoint) * dt,
        y=pose.y + vx * math.sin(yaw_midpoint) * dt,
        yaw=_normalize_angle(pose.yaw + wz * dt),
    )


def _normalize_angle(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def _polar_pose(distance: float, yaw: float) -> Pose2D:
    return Pose2D(distance * math.cos(yaw), distance * math.sin(yaw))


def _pose_dict(pose: Pose2D) -> dict[str, float]:
    return {"x": pose.x, "y": pose.y, "yaw": pose.yaw}


def _deterministic_noisy_uwb(
    time_seconds: float,
    distance: float,
    yaw: float,
) -> tuple[float, float]:
    """Repeatable 3 cm / 1 degree disturbance for robustness comparisons."""

    distance_noise = 0.03 * math.sin(2.0 * math.pi * 0.7 * time_seconds)
    yaw_noise = math.radians(1.0) * math.sin(
        2.0 * math.pi * 0.45 * time_seconds + 0.3
    )
    return max(0.0, distance + distance_noise), yaw + yaw_noise
