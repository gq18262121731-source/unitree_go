#!/usr/bin/env python3
"""Offline-only PointCloud2 -> LaserScan quality analysis for Phase 5.4.3-A.

This program reads previously captured NPZ/JSON evidence.  It does not import
ROS, create nodes, publish topics, contact the robot, or write device settings.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
CAPTURE_PATH = ROOT / "phase542_motion_capture.json"
POINTS_PATH = ROOT / "phase542_motion_clouds.npz"
MOTION_ANALYSIS_PATH = ROOT / "phase542_motion_analysis.json"
OUTPUT_PATH = ROOT / "phase543a_laserscan_analysis.json"
PARAMS_PATH = ROOT / "phase54_tools" / "pointcloud_to_laserscan_phase543a.yaml"
EXTRINSIC_PATH = ROOT / "hardware_observed_lidar_extrinsic.yaml"

Z_WINDOWS = [
    (-0.25, 0.05),
    (-0.20, 0.10),
    (-0.15, 0.15),
    (-0.10, 0.20),
    (-0.05, 0.25),
    (0.00, 0.30),
    (-0.20, 0.30),
    (-0.10, 0.40),
]
RANGE_MINS = [0.30, 0.40, 0.50]
RANGE_MAXES = [5.0, 8.0]
ANGLE_INCREMENTS_DEG = [0.5, 1.0]
ANGLE_WINDOWS_DEG = [
    (-180.0, 180.0),
    (-135.0, 135.0),
    (-120.0, 120.0),
    (-110.0, 110.0),
    (-90.0, 90.0),
]


def describe(values: list[float] | np.ndarray) -> dict[str, float | int | None]:
    array = np.asarray(values, dtype=np.float64)
    array = array[np.isfinite(array)]
    if array.size == 0:
        return {
            "count": 0,
            "min": None,
            "median": None,
            "mean": None,
            "p95": None,
            "max": None,
        }
    return {
        "count": int(array.size),
        "min": float(np.min(array)),
        "median": float(np.median(array)),
        "mean": float(np.mean(array)),
        "p95": float(np.percentile(array, 95)),
        "max": float(np.max(array)),
    }


def longest_false_run(valid: np.ndarray, circular: bool) -> int:
    if valid.size == 0 or np.all(valid):
        return 0
    if not np.any(valid):
        return int(valid.size)
    doubled = np.concatenate((~valid, ~valid)) if circular else ~valid
    best = current = 0
    for value in doubled:
        if value:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return min(best, int(valid.size))


def scan_from_points(
    points: np.ndarray,
    z_min: float,
    z_max: float,
    range_min: float,
    range_max: float,
    angle_increment_deg: float,
    angle_min_deg: float,
    angle_max_deg: float,
) -> tuple[np.ndarray, dict[str, float | int]]:
    x = points[:, 0]
    y = points[:, 1]
    z = points[:, 2]
    ranges_xy = np.hypot(x, y)
    finite = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
    selected = (
        finite
        & (z >= z_min)
        & (z <= z_max)
        & (ranges_xy >= range_min)
        & (ranges_xy <= range_max)
    )
    increment = math.radians(angle_increment_deg)
    angle_min = math.radians(angle_min_deg)
    angle_max = math.radians(angle_max_deg)
    bins = int(round((angle_max - angle_min) / increment))
    scan = np.full(bins, np.inf, dtype=np.float32)
    selected_in_view = 0
    if np.any(selected):
        angles = np.arctan2(y[selected], x[selected])
        in_view = (angles >= angle_min) & (angles < angle_max)
        angles = angles[in_view]
        selected_ranges = ranges_xy[selected][in_view]
        selected_in_view = int(selected_ranges.size)
        indices = np.floor((angles - angle_min) / increment).astype(np.int64)
        indices = np.clip(indices, 0, bins - 1)
        np.minimum.at(scan, indices, selected_ranges.astype(np.float32))
    return scan, {
        "input_points": int(points.shape[0]),
        "selected_points": selected_in_view,
        "finite_points": int(np.count_nonzero(finite)),
    }


def scan_metrics(
    scan: np.ndarray, increment_deg: float, full_circle: bool
) -> dict[str, float | int]:
    valid = np.isfinite(scan)
    valid_count = int(np.count_nonzero(valid))
    adjacent = valid & np.roll(valid, -1)
    adjacent_count = int(np.count_nonzero(adjacent))
    if adjacent_count:
        jumps = np.abs(scan[adjacent] - np.roll(scan, -1)[adjacent])
        smooth_10cm = float(np.mean(jumps <= 0.10))
        smooth_25cm = float(np.mean(jumps <= 0.25))
        smooth_50cm = float(np.mean(jumps <= 0.50))
    else:
        smooth_10cm = smooth_25cm = smooth_50cm = 0.0
    if valid_count:
        valid_ranges = scan[valid]
        near_45cm = float(np.mean(valid_ranges < 0.45))
        median_range = float(np.median(valid_ranges))
        max_range = float(np.max(valid_ranges))
    else:
        near_45cm = median_range = max_range = 0.0
    return {
        "valid_beams": valid_count,
        "occupied_ratio": valid_count / scan.size,
        "hole_ratio": 1.0 - valid_count / scan.size,
        "longest_gap_deg": longest_false_run(valid, full_circle) * increment_deg,
        "adjacent_valid_pairs": adjacent_count,
        "adjacent_smooth_10cm_ratio": smooth_10cm,
        "adjacent_smooth_25cm_ratio": smooth_25cm,
        "adjacent_smooth_50cm_ratio": smooth_50cm,
        "nearer_than_45cm_ratio": near_45cm,
        "median_range_m": median_range,
        "max_range_m": max_range,
    }


def temporal_metrics(previous: np.ndarray, current: np.ndarray) -> dict[str, float]:
    previous_valid = np.isfinite(previous)
    current_valid = np.isfinite(current)
    overlap = previous_valid & current_valid
    union = previous_valid | current_valid
    overlap_count = int(np.count_nonzero(overlap))
    if overlap_count:
        deltas = np.abs(previous[overlap] - current[overlap])
        median_delta = float(np.median(deltas))
        p95_delta = float(np.percentile(deltas, 95))
    else:
        median_delta = p95_delta = math.nan
    return {
        "beam_jaccard": (
            overlap_count / int(np.count_nonzero(union)) if np.any(union) else 0.0
        ),
        "overlap_fraction_of_previous": (
            overlap_count / int(np.count_nonzero(previous_valid))
            if np.any(previous_valid)
            else 0.0
        ),
        "median_range_delta_m": median_delta,
        "p95_range_delta_m": p95_delta,
    }


def nearest_motion_labels(capture: dict[str, Any], stamps: np.ndarray) -> np.ndarray:
    odom = capture["odom"]["/utlidar/robot_odom"]
    odom_stamps = np.asarray([item["stamp_ns"] for item in odom], dtype=np.int64)
    linear = np.asarray(
        [np.linalg.norm(item["linear_velocity"][:2]) for item in odom],
        dtype=np.float64,
    )
    angular = np.asarray(
        [abs(item["angular_velocity"][2]) for item in odom], dtype=np.float64
    )
    indices = np.searchsorted(odom_stamps, stamps)
    indices = np.clip(indices, 1, len(odom_stamps) - 1)
    before = indices - 1
    choose_before = (
        np.abs(stamps - odom_stamps[before])
        <= np.abs(odom_stamps[indices] - stamps)
    )
    nearest = np.where(choose_before, before, indices)
    return (linear[nearest] > 0.05) | (angular[nearest] > 0.10)


def load_cloud_base() -> tuple[dict[str, Any], list[np.ndarray], np.ndarray]:
    capture = json.loads(CAPTURE_PATH.read_text(encoding="utf-8"))
    archive = np.load(POINTS_PATH, mmap_mode="r")
    clouds: list[np.ndarray] = []
    stamps: list[int] = []
    for pair in capture["raw_base_pairs"]:
        topic = pair["topics"]["/utlidar/cloud_base"]
        fields = topic["fields"]
        cloud = np.column_stack(
            (
                np.asarray(archive[fields["x"]], dtype=np.float32),
                np.asarray(archive[fields["y"]], dtype=np.float32),
                np.asarray(archive[fields["z"]], dtype=np.float32),
            )
        )
        clouds.append(cloud)
        stamps.append(int(pair["stamp_ns"]))
    return capture, clouds, np.asarray(stamps, dtype=np.int64)


def summarize_candidate(
    clouds: list[np.ndarray],
    moving: np.ndarray,
    params: dict[str, float],
) -> dict[str, Any]:
    scans: list[np.ndarray] = []
    per_scan: list[dict[str, float | int]] = []
    selected_points: list[float] = []
    retained_ratios: list[float] = []
    for cloud in clouds:
        scan, counts = scan_from_points(cloud, **params)
        scans.append(scan)
        metrics = scan_metrics(
            scan,
            params["angle_increment_deg"],
            math.isclose(
                params["angle_max_deg"] - params["angle_min_deg"], 360.0
            ),
        )
        per_scan.append(metrics)
        selected_points.append(float(counts["selected_points"]))
        retained_ratios.append(
            counts["selected_points"] / counts["finite_points"]
            if counts["finite_points"]
            else 0.0
        )

    temporal = [
        temporal_metrics(scans[index - 1], scans[index])
        for index in range(1, len(scans))
    ]
    moving_pairs = moving[1:] | moving[:-1]
    stationary_pairs = ~moving_pairs

    def scan_field(name: str) -> list[float]:
        return [float(item[name]) for item in per_scan]

    def temporal_field(name: str, mask: np.ndarray | None = None) -> list[float]:
        values = np.asarray([item[name] for item in temporal], dtype=np.float64)
        if mask is not None:
            values = values[mask]
        return values[np.isfinite(values)].tolist()

    occupied_mean = float(np.mean(scan_field("occupied_ratio")))
    smooth_mean = float(np.mean(scan_field("adjacent_smooth_25cm_ratio")))
    near_mean = float(np.mean(scan_field("nearer_than_45cm_ratio")))
    jaccard_mean = float(np.mean(temporal_field("beam_jaccard")))
    longest_gap_mean = float(np.mean(scan_field("longest_gap_deg")))
    fov_deg = params["angle_max_deg"] - params["angle_min_deg"]
    fov_fraction = fov_deg / 360.0
    globalized_occupancy = occupied_mean * fov_fraction

    # Ranking aid only. Each component is also reported independently.
    score = (
        0.35 * globalized_occupancy
        + 0.15 * occupied_mean
        + 0.20 * smooth_mean
        + 0.20 * jaccard_mean
        + 0.10 * max(0.0, 1.0 - longest_gap_mean / fov_deg)
        - 0.10 * near_mean
    )

    return {
        "parameters": params,
        "ranking_score": float(score),
        "ranking_score_note": (
            "Heuristic ranking aid, not a SLAM pass/fail metric: "
            "35% full-circle-equivalent occupancy + 15% local occupancy + "
            "20% adjacent smoothness + 20% temporal beam Jaccard + 10% "
            "gap term - 10% near-range penalty."
        ),
        "selected_points_per_scan": describe(selected_points),
        "retained_input_ratio": describe(retained_ratios),
        "valid_beams": describe(scan_field("valid_beams")),
        "occupied_ratio": describe(scan_field("occupied_ratio")),
        "hole_ratio": describe(scan_field("hole_ratio")),
        "longest_gap_deg": describe(scan_field("longest_gap_deg")),
        "adjacent_smooth_10cm_ratio": describe(
            scan_field("adjacent_smooth_10cm_ratio")
        ),
        "adjacent_smooth_25cm_ratio": describe(
            scan_field("adjacent_smooth_25cm_ratio")
        ),
        "adjacent_smooth_50cm_ratio": describe(
            scan_field("adjacent_smooth_50cm_ratio")
        ),
        "nearer_than_45cm_ratio": describe(
            scan_field("nearer_than_45cm_ratio")
        ),
        "median_range_m": describe(scan_field("median_range_m")),
        "max_range_m": describe(scan_field("max_range_m")),
        "temporal_all": {
            "beam_jaccard": describe(temporal_field("beam_jaccard")),
            "overlap_fraction_of_previous": describe(
                temporal_field("overlap_fraction_of_previous")
            ),
            "median_range_delta_m": describe(
                temporal_field("median_range_delta_m")
            ),
            "p95_range_delta_m": describe(temporal_field("p95_range_delta_m")),
        },
        "temporal_moving": {
            "pairs": int(np.count_nonzero(moving_pairs)),
            "beam_jaccard": describe(
                temporal_field("beam_jaccard", moving_pairs)
            ),
            "median_range_delta_m": describe(
                temporal_field("median_range_delta_m", moving_pairs)
            ),
        },
        "temporal_stationary": {
            "pairs": int(np.count_nonzero(stationary_pairs)),
            "beam_jaccard": describe(
                temporal_field("beam_jaccard", stationary_pairs)
            ),
            "median_range_delta_m": describe(
                temporal_field("median_range_delta_m", stationary_pairs)
            ),
        },
    }


def main() -> None:
    capture, clouds, stamps = load_cloud_base()
    moving = nearest_motion_labels(capture, stamps)

    all_points = np.concatenate(clouds, axis=0)
    finite = np.all(np.isfinite(all_points), axis=1)
    all_points = all_points[finite]
    xy_range = np.hypot(all_points[:, 0], all_points[:, 1])

    candidates: list[dict[str, Any]] = []
    for z_min, z_max in Z_WINDOWS:
        for range_min in RANGE_MINS:
            for range_max in RANGE_MAXES:
                for angle_increment_deg in ANGLE_INCREMENTS_DEG:
                    for angle_min_deg, angle_max_deg in ANGLE_WINDOWS_DEG:
                        params = {
                            "z_min": z_min,
                            "z_max": z_max,
                            "range_min": range_min,
                            "range_max": range_max,
                            "angle_increment_deg": angle_increment_deg,
                            "angle_min_deg": angle_min_deg,
                            "angle_max_deg": angle_max_deg,
                        }
                        candidates.append(
                            summarize_candidate(clouds, moving, params)
                        )
    candidates.sort(key=lambda item: item["ranking_score"], reverse=True)

    # The highest-ranked candidate must also retain useful obstacle height and
    # avoid relying on unobserved range.  The grid already excludes the floor
    # band centered near z=-0.4 m.
    recommended = candidates[0]
    motion = json.loads(MOTION_ANALYSIS_PATH.read_text(encoding="utf-8"))
    result = {
        "phase": "5.4.3-A",
        "mode": "offline_readonly_evidence_analysis",
        "inputs": {
            "capture": CAPTURE_PATH.name,
            "points": POINTS_PATH.name,
            "motion_analysis": MOTION_ANALYSIS_PATH.name,
            "cloud_topic": "/utlidar/cloud_base",
            "frame_id": "base_link",
            "saved_pairs": len(clouds),
            "capture_duration_seconds": capture["duration_seconds"],
            "reported_moving_fraction": float(np.mean(moving)),
        },
        "input_distribution": {
            "finite_points": int(all_points.shape[0]),
            "points_per_scan": describe([len(cloud) for cloud in clouds]),
            "z_m": {
                str(percentile): float(
                    np.percentile(all_points[:, 2], percentile)
                )
                for percentile in [0, 1, 5, 10, 25, 50, 75, 90, 95, 99, 100]
            },
            "xy_range_m": {
                str(percentile): float(np.percentile(xy_range, percentile))
                for percentile in [0, 1, 5, 10, 25, 50, 75, 90, 95, 99, 100]
            },
        },
        "motion_evidence": {
            "path_length_m": motion["motion_coverage"]["path_length_m"],
            "total_rotation_rad": motion["motion_coverage"][
                "total_rotation_rad"
            ],
            "motion_gate": motion["motion_coverage"]["gate"],
            "raw_base_stamp_match_ratio": motion[
                "raw_base_stamp_match_ratio"
            ],
            "baseline_transform_residual_all_m": motion[
                "raw_base_pair_checks"
            ]["baseline_transform_residual_all_m"],
            "baseline_transform_residual_moving_m": motion[
                "raw_base_pair_checks"
            ]["baseline_transform_residual_moving_m"],
        },
        "search_space": {
            "z_windows_m": Z_WINDOWS,
            "range_min_m": RANGE_MINS,
            "range_max_m": RANGE_MAXES,
            "angle_increment_deg": ANGLE_INCREMENTS_DEG,
            "angle_windows_deg": ANGLE_WINDOWS_DEG,
            "candidate_count": len(candidates),
        },
        "recommended_offline_candidate": recommended,
        "top_candidates": candidates[:10],
        "all_candidates": candidates,
        "provisional_quality_gate": {
            "thresholds_are_project_specific_not_official_slam_requirements": True,
            "mean_occupied_ratio_at_least_0_25": bool(
                recommended["occupied_ratio"]["mean"] >= 0.25
            ),
            "mean_longest_gap_at_most_60deg": bool(
                recommended["longest_gap_deg"]["mean"] <= 60.0
            ),
            "stationary_beam_jaccard_at_least_0_35": bool(
                recommended["temporal_stationary"]["beam_jaccard"]["mean"]
                >= 0.35
            ),
            "stationary_median_range_delta_at_most_0_15m": bool(
                recommended["temporal_stationary"]["median_range_delta_m"][
                    "mean"
                ]
                <= 0.15
            ),
        },
        "assessment": {
            "offline_conversion_pipeline": "PASS",
            "motion_data_sufficient_for_offline_quality_comparison": True,
            "single_frame_laserscan_quality": "PROVISIONAL_FAIL",
            "official_extrinsic_found": False,
            "phase_5_4_hold_released": False,
            "phase_5_5_slam_authorized": False,
            "next_gate": (
                "Phase 5.4.3-B online read-only replay/visual validation after "
                "the robot is powered; retain Phase 5.4 HOLD until the "
                "base_link->utlidar_lidar provenance decision is resolved."
            ),
        },
        "limits": [
            "No ROS node, TF publisher, SLAM, Nav2, or robot connection was used.",
            "The ranking score compares this capture only and is not a SLAM acceptance test.",
            "No rosbag was available; conversion was reproduced numerically from saved PointCloud2 fields.",
            "The observed maximum XY range is capture/environment limited.",
            "Wall identity and geometric correctness require RViz/online validation.",
            "The base_link frame in cloud_base is firmware-reported; the underlying extrinsic remains observed, not official.",
        ],
    }
    OUTPUT_PATH.write_text(
        json.dumps(result, indent=2, sort_keys=True), encoding="utf-8"
    )

    params = recommended["parameters"]
    PARAMS_PATH.write_text(
        "\n".join(
            [
                "# OFFLINE_CANDIDATE_ONLY: Phase 5.4.3-A",
                "# Do not launch on the robot and do not treat this as TF calibration.",
                "# Generated from phase542_motion_clouds.npz; requires online/RViz validation.",
                "pointcloud_to_laserscan:",
                "  ros__parameters:",
                "    target_frame: base_link",
                "    transform_tolerance: 0.05",
                f"    min_height: {params['z_min']:.3f}",
                f"    max_height: {params['z_max']:.3f}",
                f"    angle_min: {math.radians(params['angle_min_deg']):.9f}",
                f"    angle_max: {math.radians(params['angle_max_deg']):.9f}",
                f"    angle_increment: {math.radians(params['angle_increment_deg']):.9f}",
                "    scan_time: 0.066",
                f"    range_min: {params['range_min']:.3f}",
                f"    range_max: {params['range_max']:.3f}",
                "    use_inf: true",
                "    inf_epsilon: 1.0",
                "",
            ]
        ),
        encoding="utf-8",
    )

    EXTRINSIC_PATH.write_text(
        "\n".join(
            [
                "# EXPERIMENTAL OBSERVATION ONLY - DO NOT PUBLISH AS TF",
                "source: observed_from_cloud_base",
                "confidence: experimental",
                "official_calibration: false",
                "publish_tf: false",
                "parent_frame: base_link",
                "child_frame: utlidar_lidar",
                "translation_xyz_m: [0.2821600275, 0.0000000170, -0.0000000349]",
                "quaternion_xyzw: [-0.8713116353, 0.4730810194, -0.1146184836, 0.0622333233]",
                "rpy_rad: [-2.920720112, -0.141323991, -1.010529934]",
                "rotation_matrix_row_major:",
                "  - [0.526113905, -0.810135815, 0.258619645]",
                "  - [-0.838668172, -0.544642725, 0.000001579]",
                "  - [0.140854029, -0.216896895, -0.965979233]",
                "evidence:",
                "  capture: phase542_motion_clouds.npz",
                "  analysis: phase542_motion_analysis.json",
                "  exact_stamp_match_ratio: 1.0",
                "  residual_mean_m: 0.000000090234",
                "  residual_max_m: 0.000001442140",
                "limitations:",
                "  - Not sourced from official calibration/API/IDL.",
                "  - Matrix maps lidar-frame points into base_link coordinates (p_base = R * p_lidar + t).",
                "  - TF semantics would be parent base_link, child utlidar_lidar; publication remains forbidden.",
                "  - Do not invert or publish until provenance and online validation gates pass.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(OUTPUT_PATH),
                "recommended": recommended["parameters"],
                "score": recommended["ranking_score"],
                "occupied_ratio_mean": recommended["occupied_ratio"]["mean"],
                "valid_beams_mean": recommended["valid_beams"]["mean"],
                "longest_gap_deg_mean": recommended["longest_gap_deg"]["mean"],
                "stationary_jaccard_mean": recommended["temporal_stationary"][
                    "beam_jaccard"
                ]["mean"],
                "moving_jaccard_mean": recommended["temporal_moving"][
                    "beam_jaccard"
                ]["mean"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
