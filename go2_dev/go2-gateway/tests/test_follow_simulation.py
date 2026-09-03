from __future__ import annotations

import math
import ast
import inspect

import pytest

from app.follow.controller import SafetyState
from app.follow.simulation import (
    FollowSimulation,
    FollowSimulationConfig,
    FollowSimulationScenario,
    Pose2D,
    standard_simulation_scenarios,
)


def result_for(name: str):
    scenario = next(
        scenario for scenario in standard_simulation_scenarios() if scenario.name == name
    )
    return FollowSimulation().run(scenario)


def test_standard_suite_contains_at_least_ten_scenarios() -> None:
    assert len(standard_simulation_scenarios()) >= 10


def test_stationary_target_converges_to_right_rear_offset() -> None:
    result = result_for("stationary_front_3m")

    assert result.final_target_error_norm < 0.12
    assert result.maximum_abs_vx <= 0.3
    assert result.maximum_abs_wz <= 0.5


def test_equilibrium_remains_stationary() -> None:
    result = result_for("equilibrium")

    assert result.final_target_error_norm == pytest.approx(0.0, abs=1e-10)
    assert result.maximum_abs_vx == pytest.approx(0.0, abs=1e-10)
    assert result.maximum_abs_wz == pytest.approx(0.0, abs=1e-10)


def test_current_p_controller_moving_lag_is_measured() -> None:
    result = result_for("moving_straight")

    # At 0.15 m/s, kx=0.2 needs roughly 0.75 m of longitudinal error to
    # generate matching speed. Keep this limitation visible before hardware use.
    assert 0.65 < result.final_target_error_norm < 0.90


def test_too_close_stops_for_entire_scenario() -> None:
    result = result_for("too_close")

    assert result.maximum_abs_vx == 0.0
    assert result.maximum_abs_wz == 0.0
    assert set(result.safety_counts) == {SafetyState.STOP_DISTANCE_TOO_CLOSE.value}


def test_uwb_dropout_enters_timeout_stop() -> None:
    result = result_for("uwb_dropout")
    timeout_samples = [
        sample
        for sample in result.samples
        if sample.safety_state == SafetyState.STOP_UWB_TIMEOUT.value
    ]

    assert timeout_samples
    assert timeout_samples[0].time_seconds >= 2.8
    assert all(sample.vx == 0.0 and sample.wz == 0.0 for sample in timeout_samples)


def test_manual_takeover_stops_follow_output() -> None:
    result = result_for("manual_takeover")
    manual_samples = [sample for sample in result.samples if sample.control_owner == "MANUAL"]

    assert manual_samples
    assert all(
        sample.safety_state == SafetyState.STOP_CONTROL_NOT_FOLLOW.value
        and sample.vx == 0.0
        and sample.wz == 0.0
        for sample in manual_samples
    )


def test_abnormal_yaw_applies_reduced_limits() -> None:
    result = result_for("abnormal_yaw_90deg")
    limited_samples = [
        sample
        for sample in result.samples
        if sample.safety_state == SafetyState.LIMITED_ABNORMAL_YAW.value
    ]

    assert limited_samples
    assert max(abs(sample.vx) for sample in limited_samples) <= 0.15
    assert max(abs(sample.wz) for sample in limited_samples) <= 0.25


def test_simulated_uwb_is_generated_from_true_geometry() -> None:
    scenario = FollowSimulationScenario(
        "geometry",
        lambda _t: Pose2D(0.0, 2.0),
        FollowSimulationConfig(
            duration_seconds=0.2,
            robot_start=Pose2D(0.0, 0.0, math.radians(90.0)),
        ),
    )

    first = FollowSimulation().run(scenario).samples[0]

    assert first.uwb_distance == pytest.approx(2.0)
    assert first.uwb_yaw == pytest.approx(0.0, abs=1e-12)


def test_simulation_module_has_no_hardware_imports() -> None:
    module = __import__("app.follow.simulation", fromlist=["FollowSimulation"])
    tree = ast.parse(inspect.getsource(module))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    forbidden = {
        "app.follow.executor",
        "app.services.robot_service",
        "app.gateway.go2_gateway",
        "app.adapters.unitree_adapter",
        "unitree_sdk2py",
    }
    assert imports.isdisjoint(forbidden)
