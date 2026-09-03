from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone

import pytest

from app.follow import (
    FollowOffset,
    FollowState,
    FollowTargetPlanner,
    calculate_follow_target,
)


OFFSET = FollowOffset(
    back_distance=1.5,
    right_offset=0.5,
    max_distance=2.5,
    min_distance=1.0,
)
TIMESTAMP = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


def test_calculate_follow_target_for_three_metres_straight_ahead() -> None:
    target_x, target_y = calculate_follow_target(3.0, 0.0, OFFSET)

    assert target_x == pytest.approx(1.5)
    assert target_y == pytest.approx(-0.5)


def test_calculate_follow_target_for_two_metres_at_twenty_degrees() -> None:
    yaw_est = math.radians(20.0)

    target_x, target_y = calculate_follow_target(2.0, yaw_est, OFFSET)

    assert target_x == pytest.approx(0.3793852416)
    assert target_y == pytest.approx(0.1840402867)


def test_distance_over_maximum_allows_target_planning_without_velocity() -> None:
    planner = FollowTargetPlanner(OFFSET)

    plan = planner.process_measurement(
        3.0,
        0.0,
        sample_monotonic=10.0,
        timestamp=TIMESTAMP,
    )

    assert plan.current_state is FollowState.FOLLOW_TOO_FAR
    assert plan.stop_required is False
    assert plan.cmd_velocity is None
    assert plan.target_x == pytest.approx(1.5)
    assert plan.target_y == pytest.approx(-0.5)


def test_distance_below_minimum_requests_stop() -> None:
    planner = FollowTargetPlanner(OFFSET)

    plan = planner.process_measurement(
        0.8,
        0.0,
        sample_monotonic=10.0,
        timestamp=TIMESTAMP,
    )

    assert plan.current_state is FollowState.FOLLOW_TOO_CLOSE
    assert plan.stop_required is True
    assert plan.target_x == 0.0
    assert plan.target_y == 0.0
    assert plan.cmd_velocity == {"vx": 0.0, "vy": 0.0, "wz": 0.0}


def test_uwb_loss_requests_safe_stop_after_timeout() -> None:
    planner = FollowTargetPlanner(OFFSET, lost_timeout_seconds=2.0)
    planner.process_measurement(
        2.0,
        math.radians(20.0),
        sample_monotonic=10.0,
        timestamp=TIMESTAMP,
    )

    plan = planner.check_target_liveness(
        now_monotonic=12.0,
        timestamp=TIMESTAMP,
    )

    assert plan.current_state is FollowState.FOLLOW_TARGET_LOST
    assert plan.stop_required is True
    assert plan.uwb_distance is None
    assert plan.uwb_yaw is None
    assert plan.target_x == 0.0
    assert plan.target_y == 0.0
    assert plan.cmd_velocity == {"vx": 0.0, "vy": 0.0, "wz": 0.0}


def test_log_record_contains_required_debug_fields(caplog: pytest.LogCaptureFixture) -> None:
    planner = FollowTargetPlanner(OFFSET)

    with caplog.at_level(logging.INFO, logger="app.follow.planner"):
        plan = planner.process_measurement(
            2.0,
            math.radians(20.0),
            sample_monotonic=10.0,
            timestamp=TIMESTAMP,
        )

    assert plan.to_log_record() == {
        "timestamp": "2026-07-31T12:00:00+00:00",
        "uwb_distance": 2.0,
        "uwb_yaw": math.radians(20.0),
        "target_x": pytest.approx(0.3793852416),
        "target_y": pytest.approx(0.1840402867),
        "current_state": "FOLLOW_TRACKING",
        "cmd_velocity": None,
    }
    assert json.loads(caplog.messages[-1]) == plan.to_log_record()


def test_follow_module_has_no_motion_dependency() -> None:
    # Importing and constructing the planning model must remain hardware-free.
    assert FollowOffset() == OFFSET
