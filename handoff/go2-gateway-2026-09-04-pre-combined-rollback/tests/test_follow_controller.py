from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone

import pytest

from app.config import Settings
from app.follow import (
    FollowController,
    FollowControllerConfig,
    FollowDistanceMode,
    FollowOffset,
    FollowState,
    FollowTargetPlanner,
    SafetyGuard,
    SafetyGuardConfig,
    SafetyState,
)
from app.navigation.models import ControlOwner


TIMESTAMP = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
OFFSET = FollowOffset()


def make_plan(distance: float, yaw_degrees: float, *, sample_at: float = 10.0):
    planner = FollowTargetPlanner(OFFSET)
    return planner.process_measurement(
        distance,
        math.radians(yaw_degrees),
        sample_monotonic=sample_at,
        timestamp=TIMESTAMP,
    )


def make_controller(*, simulation_mode: bool = True) -> FollowController:
    return FollowController(
        FollowControllerConfig(simulation_mode=simulation_mode),
        SafetyGuard(SafetyGuardConfig()),
    )


def make_feedforward_controller() -> FollowController:
    return FollowController(
        FollowControllerConfig(
            simulation_mode=True,
            velocity_feedforward_enabled=True,
        ),
        SafetyGuard(SafetyGuardConfig()),
    )


def make_walking_controller() -> FollowController:
    return FollowController(
        FollowControllerConfig(
            simulation_mode=False,
            max_vx=0.30,
            max_wz=0.30,
            walking_speed_floor_enabled=True,
            minimum_walking_vx=0.20,
            forward_start_error=0.25,
            forward_stop_error=0.10,
        ),
        SafetyGuard(SafetyGuardConfig()),
    )


def make_plan_at_forward_error(error: float):
    measured_x = OFFSET.back_distance + error
    measured_y = OFFSET.right_offset
    distance = math.hypot(measured_x, measured_y)
    yaw = math.degrees(math.atan2(measured_y, measured_x))
    return make_plan(distance, yaw)


def test_follow_plan_converts_to_bounded_velocity_suggestion() -> None:
    command = make_controller().calculate_velocity(
        make_plan(3.0, 0.0),
        control_owner=ControlOwner.FOLLOW,
        measurement_age_seconds=0.0,
    )

    assert command.vx == pytest.approx(0.3)
    assert command.vy == 0.0
    assert command.wz == pytest.approx(-0.4)
    assert command.safety_state is SafetyState.SAFE
    assert command.simulation_mode is True


def test_right_rear_equilibrium_produces_zero_velocity() -> None:
    equilibrium_distance = math.hypot(1.5, 0.5)
    equilibrium_yaw = math.degrees(math.atan2(0.5, 1.5))

    command = make_controller().calculate_velocity(
        make_plan(equilibrium_distance, equilibrium_yaw),
        control_owner=ControlOwner.FOLLOW,
    )

    assert command.vx == pytest.approx(0.0, abs=1e-12)
    assert command.vy == 0.0
    assert command.wz == pytest.approx(0.0, abs=1e-12)


def test_walking_floor_keeps_small_forward_error_in_stop_band() -> None:
    command = make_walking_controller().calculate_velocity(
        make_plan_at_forward_error(0.24),
        control_owner=ControlOwner.FOLLOW,
    )

    assert command.vx == 0.0


def test_walking_floor_starts_at_validated_gait_speed() -> None:
    command = make_walking_controller().calculate_velocity(
        make_plan_at_forward_error(0.25),
        control_owner=ControlOwner.FOLLOW,
    )

    assert command.vx == pytest.approx(0.20)


def test_walking_floor_grows_and_remains_bounded() -> None:
    controller = make_walking_controller()
    middle = controller.calculate_velocity(
        make_plan_at_forward_error(1.25),
        control_owner=ControlOwner.FOLLOW,
    )
    far = controller.calculate_velocity(
        make_plan_at_forward_error(2.0),
        control_owner=ControlOwner.FOLLOW,
    )

    assert middle.vx == pytest.approx(0.25)
    assert far.vx == pytest.approx(0.30)


def test_walking_floor_uses_start_stop_hysteresis() -> None:
    controller = make_walking_controller()

    started = controller.calculate_velocity(
        make_plan_at_forward_error(0.25),
        control_owner=ControlOwner.FOLLOW,
    )
    within_hysteresis = controller.calculate_velocity(
        make_plan_at_forward_error(0.15),
        control_owner=ControlOwner.FOLLOW,
    )
    stopped = controller.calculate_velocity(
        make_plan_at_forward_error(0.10),
        control_owner=ControlOwner.FOLLOW,
    )

    assert started.vx == pytest.approx(0.20)
    assert within_hysteresis.vx == pytest.approx(0.20)
    assert stopped.vx == 0.0
    assert controller.distance_mode is FollowDistanceMode.HOLD_TOO_CLOSE
    assert controller.motion_reason == "distance_stop_threshold"

    held = controller.calculate_velocity(
        make_plan_at_forward_error(0.15),
        control_owner=ControlOwner.FOLLOW,
    )
    resumed = controller.calculate_velocity(
        make_plan_at_forward_error(0.25),
        control_owner=ControlOwner.FOLLOW,
    )

    assert held.vx == 0.0
    assert resumed.vx == pytest.approx(0.20)
    assert controller.distance_mode is FollowDistanceMode.ACTIVE
    assert controller.motion_reason == "auto_resume_distance_clear"


def test_safety_stop_clears_walking_hysteresis_and_requires_new_start_error() -> None:
    controller = make_walking_controller()
    controller.calculate_velocity(
        make_plan_at_forward_error(0.25),
        control_owner=ControlOwner.FOLLOW,
    )

    timeout = controller.calculate_velocity(
        make_plan_at_forward_error(0.25),
        control_owner=ControlOwner.FOLLOW,
        measurement_age_seconds=2.0,
    )
    after_timeout = controller.calculate_velocity(
        make_plan_at_forward_error(0.15),
        control_owner=ControlOwner.FOLLOW,
    )

    assert timeout.vx == 0.0
    assert timeout.safety_state is SafetyState.STOP_UWB_TIMEOUT
    assert after_timeout.vx == 0.0


def test_walking_floor_rejects_minimum_above_maximum() -> None:
    with pytest.raises(ValueError, match="minimum_walking_vx"):
        FollowControllerConfig(
            max_vx=0.15,
            walking_speed_floor_enabled=True,
            minimum_walking_vx=0.20,
        )


def test_distance_equilibrium_at_zero_yaw_keeps_distance_but_corrects_offset() -> None:
    command = make_controller().calculate_velocity(
        make_plan(1.5, 0.0),
        control_owner=ControlOwner.FOLLOW,
    )

    assert command.vx == pytest.approx(0.0)
    assert command.wz == pytest.approx(-0.4)


def test_distance_too_close_stops() -> None:
    command = make_controller().calculate_velocity(
        make_plan(0.8, 0.0),
        control_owner=ControlOwner.FOLLOW,
    )

    assert (command.vx, command.vy, command.wz) == (0.0, 0.0, 0.0)
    assert command.safety_state is SafetyState.STOP_DISTANCE_TOO_CLOSE


def test_uwb_age_timeout_stops_even_if_plan_was_tracking() -> None:
    command = make_controller().calculate_velocity(
        make_plan(2.0, 20.0),
        control_owner=ControlOwner.FOLLOW,
        measurement_age_seconds=2.0,
    )

    assert (command.vx, command.vy, command.wz) == (0.0, 0.0, 0.0)
    assert command.safety_state is SafetyState.STOP_UWB_TIMEOUT


def test_planner_target_lost_state_stops() -> None:
    planner = FollowTargetPlanner(OFFSET, lost_timeout_seconds=2.0)
    planner.process_measurement(
        2.0,
        math.radians(20.0),
        sample_monotonic=10.0,
        timestamp=TIMESTAMP,
    )
    lost_plan = planner.check_target_liveness(
        now_monotonic=12.0,
        timestamp=TIMESTAMP,
    )

    command = make_controller().calculate_velocity(
        lost_plan,
        control_owner=ControlOwner.FOLLOW,
    )

    assert (command.vx, command.vy, command.wz) == (0.0, 0.0, 0.0)
    assert command.safety_state is SafetyState.STOP_UWB_TIMEOUT


@pytest.mark.parametrize(
    "owner",
    [
        ControlOwner.NONE,
        ControlOwner.MANUAL,
        ControlOwner.NAVIGATION,
        ControlOwner.EMERGENCY_STOP,
    ],
)
def test_non_follow_control_owner_stops(owner: ControlOwner) -> None:
    command = make_controller().calculate_velocity(
        make_plan(3.0, 0.0),
        control_owner=owner,
    )

    assert (command.vx, command.vy, command.wz) == (0.0, 0.0, 0.0)
    assert command.safety_state is SafetyState.STOP_CONTROL_NOT_FOLLOW


def test_abnormal_yaw_limits_speed() -> None:
    plan = make_plan(3.0, 90.0)
    command = make_controller().calculate_velocity(
        plan,
        control_owner=ControlOwner.FOLLOW,
    )

    assert command.safety_state is SafetyState.LIMITED_ABNORMAL_YAW
    assert abs(command.vx) <= 0.15
    assert abs(command.wz) <= 0.25


def test_controller_emits_structured_json(caplog: pytest.LogCaptureFixture) -> None:
    plan = make_plan(2.0, 20.0)

    with caplog.at_level(logging.INFO, logger="app.follow.controller"):
        command = make_controller().calculate_velocity(
            plan,
            control_owner=ControlOwner.FOLLOW,
        )

    record = json.loads(caplog.messages[-1])
    assert record == {
        "timestamp": "2026-07-31T12:00:00+00:00",
        "uwb_distance": 2.0,
        "uwb_yaw": math.radians(20.0),
        "target_position": {
            "x": plan.target_x,
            "y": plan.target_y,
        },
        "velocity_command": {
            "vx": command.vx,
            "vy": command.vy,
            "wz": command.wz,
        },
        "safety_state": "SAFE",
        "simulation_mode": True,
    }


@pytest.mark.parametrize(
    ("distance", "yaw_degrees"),
    [
        (3.0, 0.0),
        (2.5, 0.0),
        (2.0, 20.0),
        (2.0, -20.0),
        (2.0, 45.0),
        (2.0, -45.0),
        (2.0, 70.0),
        (2.0, -70.0),
        (1.5, 0.0),
        (1.1, 20.0),
        (3.0, 90.0),
        (3.0, -90.0),
    ],
)
def test_simulation_matrix_is_bounded(distance: float, yaw_degrees: float) -> None:
    command = make_controller().calculate_velocity(
        make_plan(distance, yaw_degrees),
        control_owner=ControlOwner.FOLLOW,
        measurement_age_seconds=0.2,
    )

    assert command.simulation_mode is True
    assert -0.3 <= command.vx <= 0.3
    assert command.vy == 0.0
    assert -0.5 <= command.wz <= 0.5


def test_follow_simulation_defaults_to_enabled() -> None:
    settings = Settings(follow_simulation=True)
    controller = FollowController(FollowControllerConfig.from_settings(settings))

    assert controller.config.simulation_mode is True


def test_follow_simulation_setting_is_propagated_without_dispatching() -> None:
    settings = Settings(follow_simulation=False)
    controller = FollowController(FollowControllerConfig.from_settings(settings))

    command = controller.calculate_velocity(
        make_plan(3.0, 0.0),
        control_owner=ControlOwner.FOLLOW,
    )

    assert command.simulation_mode is False


def test_feedforward_is_disabled_by_default() -> None:
    controller = make_controller()

    command = controller.calculate_velocity(
        make_plan(2.0, 0.0),
        control_owner=ControlOwner.FOLLOW,
        sample_monotonic=10.0,
    )

    assert command.vx == pytest.approx(0.1)
    assert controller.last_feedforward_vx == 0.0
    assert controller.feedforward_status == "disabled"


def test_feedforward_estimates_target_speed_from_ordered_uwb_samples() -> None:
    controller = make_feedforward_controller()
    first = controller.calculate_velocity(
        make_plan(2.0, 0.0),
        control_owner=ControlOwner.FOLLOW,
        sample_monotonic=10.0,
    )
    second = controller.calculate_velocity(
        make_plan(2.01, 0.0),
        control_owner=ControlOwner.FOLLOW,
        sample_monotonic=10.2,
    )

    assert first.vx == pytest.approx(0.1)
    assert controller.feedforward_status == "updated"
    assert controller.last_feedforward_vx == pytest.approx(0.06)
    assert second.vx == pytest.approx(0.162)


def test_feedforward_requires_sample_timestamp_and_falls_back_to_p() -> None:
    controller = make_feedforward_controller()

    command = controller.calculate_velocity(
        make_plan(2.0, 0.0),
        control_owner=ControlOwner.FOLLOW,
    )

    assert command.vx == pytest.approx(0.1)
    assert controller.last_feedforward_vx == 0.0
    assert controller.feedforward_status == "missing_or_invalid_sample_time"


def test_feedforward_rejects_implausible_velocity_spike() -> None:
    controller = make_feedforward_controller()
    controller.calculate_velocity(
        make_plan(2.0, 0.0),
        control_owner=ControlOwner.FOLLOW,
        sample_monotonic=10.0,
    )

    command = controller.calculate_velocity(
        make_plan(3.0, 0.0),
        control_owner=ControlOwner.FOLLOW,
        sample_monotonic=10.2,
    )

    assert controller.last_feedforward_vx == 0.0
    assert controller.feedforward_status == "rejected_velocity_spike"
    assert command.vx == pytest.approx(0.3)


def test_safety_stop_clears_feedforward_estimator() -> None:
    controller = make_feedforward_controller()
    controller.calculate_velocity(
        make_plan(2.0, 0.0),
        control_owner=ControlOwner.FOLLOW,
        sample_monotonic=10.0,
    )
    controller.calculate_velocity(
        make_plan(2.01, 0.0),
        control_owner=ControlOwner.FOLLOW,
        sample_monotonic=10.2,
    )

    stopped = controller.calculate_velocity(
        make_plan(2.01, 0.0),
        control_owner=ControlOwner.FOLLOW,
        measurement_age_seconds=2.0,
        sample_monotonic=10.2,
    )
    resumed = controller.calculate_velocity(
        make_plan(2.0, 0.0),
        control_owner=ControlOwner.FOLLOW,
        sample_monotonic=20.0,
    )

    assert stopped.safety_state is SafetyState.STOP_UWB_TIMEOUT
    assert stopped.vx == 0.0
    assert controller.last_feedforward_vx == 0.0
    assert resumed.vx == pytest.approx(0.1)
    assert controller.feedforward_status == "priming"


def test_abnormal_yaw_suppresses_feedforward() -> None:
    controller = make_feedforward_controller()

    command = controller.calculate_velocity(
        make_plan(3.0, 90.0),
        control_owner=ControlOwner.FOLLOW,
        sample_monotonic=10.0,
    )

    assert command.safety_state is SafetyState.LIMITED_ABNORMAL_YAW
    assert controller.last_feedforward_vx == 0.0
    assert controller.feedforward_status == "suppressed_abnormal_yaw"


def test_feedforward_settings_are_opt_in() -> None:
    disabled = FollowControllerConfig.from_settings(Settings())
    enabled = FollowControllerConfig.from_settings(
        Settings(follow_velocity_feedforward_enabled=True)
    )

    assert disabled.velocity_feedforward_enabled is False
    assert enabled.velocity_feedforward_enabled is True


def test_feedforward_log_exposes_estimator_status(
    caplog: pytest.LogCaptureFixture,
) -> None:
    controller = make_feedforward_controller()

    with caplog.at_level(logging.INFO, logger="app.follow.controller"):
        controller.calculate_velocity(
            make_plan(2.0, 0.0),
            control_owner=ControlOwner.FOLLOW,
            sample_monotonic=10.0,
        )

    record = json.loads(caplog.messages[-1])
    assert record["velocity_feedforward"] == {
        "vx": 0.0,
        "status": "priming",
    }
