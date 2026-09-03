from __future__ import annotations

from app.motion import LidarSafetyConfig
from tools.probe_lidar_safety_phase7_1c import parse_args as parse_single_args
from tools.probe_lidar_safety_phase7_1c_session import (
    CalibrationSession,
    parse_args as parse_session_args,
    summarize_capture,
)


def decision(index: int, level: str, distance: float) -> dict[str, object]:
    return {
        "received_at": index / 15.0,
        "level": level,
        "reason": "obstacle_in_slow_zone",
        "nearest_distance": distance,
        "roi_point_count": 10,
        "frame_id": "base_link",
    }


def test_continuous_capture_accepts_stable_slow_after_clear_priming() -> None:
    report = summarize_capture(
        label="0.80m",
        expected_distance=0.8,
        decisions=[decision(index, "SLOW", 0.9) for index in range(40)],
        minimum_samples=30,
        config=LidarSafetyConfig(),
        distance_tolerance=0.15,
    )

    assert report["stable_level"] == "SLOW"
    assert report["level_pass"] is True
    assert report["distance_pass"] is True
    assert report["motion_calls"] == 0


def test_continuous_capture_holds_wrong_level() -> None:
    report = summarize_capture(
        label="0.65m",
        expected_distance=0.65,
        decisions=[decision(index, "SLOW", 0.7) for index in range(35)],
        minimum_samples=30,
        config=LidarSafetyConfig(),
        distance_tolerance=0.15,
    )

    assert report["expected_level"] == "STOP"
    assert report["level_pass"] is False
    assert report["verdict"] == "HOLD_LEVEL"


def test_readonly_probes_accept_candidate_threshold_overrides() -> None:
    single = parse_single_args(
        ["--stop-distance", "0.80", "--slow-distance", "1.40"]
    )
    session_args = parse_session_args(
        ["--stop-distance", "0.80", "--slow-distance", "1.40"]
    )
    session = CalibrationSession(session_args)

    assert single.stop_distance == 0.80
    assert single.slow_distance == 1.40
    assert session.config.stop_distance == 0.80
    assert session.config.slow_distance == 1.40


def test_candidate_thresholds_change_expected_physical_levels() -> None:
    config = LidarSafetyConfig(stop_distance=0.80, slow_distance=1.40)

    slow_report = summarize_capture(
        label="physical_1.20m",
        expected_distance=1.20,
        decisions=[decision(index, "SLOW", 1.33) for index in range(40)],
        minimum_samples=30,
        config=config,
        distance_tolerance=0.15,
    )
    stop_report = summarize_capture(
        label="physical_0.65m",
        expected_distance=0.65,
        decisions=[decision(index, "STOP", 0.73) for index in range(40)],
        minimum_samples=30,
        config=config,
        distance_tolerance=0.15,
    )

    assert slow_report["expected_level"] == "SLOW"
    assert slow_report["level_pass"] is True
    assert stop_report["expected_level"] == "STOP"
    assert stop_report["level_pass"] is True
