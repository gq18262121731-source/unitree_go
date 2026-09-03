from __future__ import annotations

from app.follow.experimental_controller import FollowControlAlgorithm
from app.follow.simulation import FollowSimulation, standard_simulation_scenarios


SCENARIOS = {scenario.name: scenario for scenario in standard_simulation_scenarios()}


def run(algorithm: FollowControlAlgorithm, scenario: str):
    return FollowSimulation(algorithm=algorithm).run(SCENARIOS[scenario])


def test_feedforward_removes_most_constant_speed_lag() -> None:
    baseline = run(FollowControlAlgorithm.BASELINE_P, "moving_straight")
    feedforward = run(FollowControlAlgorithm.VELOCITY_FEEDFORWARD, "moving_straight")

    assert baseline.final_target_error_norm > 0.70
    assert feedforward.final_target_error_norm < 0.10
    assert feedforward.final_target_error_norm < baseline.final_target_error_norm * 0.10


def test_feedforward_remains_effective_with_repeatable_uwb_noise() -> None:
    result = run(
        FollowControlAlgorithm.VELOCITY_FEEDFORWARD,
        "moving_straight_noisy",
    )

    assert result.final_target_error_norm < 0.10
    assert result.minimum_true_distance >= 1.50
    assert result.maximum_abs_vx <= 0.3
    assert result.maximum_abs_wz <= 0.5


def test_pi_feedforward_improves_turn_tracking() -> None:
    baseline = run(FollowControlAlgorithm.BASELINE_P, "moving_left_turn")
    combined = run(FollowControlAlgorithm.PI_FEEDFORWARD, "moving_left_turn")

    assert combined.final_target_error_norm < 0.15
    assert combined.final_target_error_norm < baseline.final_target_error_norm * 0.30


def test_all_algorithms_keep_existing_safety_stops() -> None:
    for algorithm in FollowControlAlgorithm:
        too_close = run(algorithm, "too_close")
        dropout = run(algorithm, "uwb_dropout")
        manual = run(algorithm, "manual_takeover")

        assert too_close.maximum_abs_vx == 0.0
        assert too_close.maximum_abs_wz == 0.0
        assert dropout.safety_counts.get("STOP_UWB_TIMEOUT", 0) > 0
        assert manual.safety_counts.get("STOP_CONTROL_NOT_FOLLOW", 0) > 0
