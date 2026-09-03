#!/usr/bin/env python3
"""Offline analysis for Phase 5.4.2 cloud_base motion suitability."""

import json
import math
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "phase542_motion_capture.json"
CLOUDS = ROOT / "phase542_motion_clouds.npz"
BASELINE = ROOT / "phase541_analysis.json"
OUTPUT = ROOT / "phase542_motion_analysis.json"


def describe(values):
    array = np.asarray(values, dtype=np.float64)
    if array.size == 0:
        return {"count": 0}
    return {
        "count": int(array.size),
        "min": float(np.min(array)),
        "mean": float(np.mean(array)),
        "median": float(np.median(array)),
        "p95": float(np.percentile(array, 95)),
        "max": float(np.max(array)),
        "std": float(np.std(array)),
    }


def quat_rotation(value):
    x, y, z, w = np.asarray(value, dtype=np.float64)
    x, y, z, w = np.asarray([x, y, z, w]) / np.linalg.norm([x, y, z, w])
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def rotation_angle(value):
    cosine = np.clip((np.trace(value) - 1.0) / 2.0, -1.0, 1.0)
    return float(math.acos(cosine))


def nearest(samples, stamp):
    stamps = np.asarray([item["stamp_ns"] for item in samples], dtype=np.int64)
    index = int(np.argmin(np.abs(stamps - stamp)))
    return samples[index], int(stamps[index] - stamp)


def xyz(fields, archive, indices=None):
    value = np.column_stack([archive[fields[key]] for key in ("x", "y", "z")])
    return value if indices is None else value[indices]


def keys(fields, archive):
    return np.column_stack(
        (
            archive[fields["ring"]].astype(np.uint64),
            archive[fields["time"]].view(np.uint32).astype(np.uint64),
            archive[fields["intensity"]].view(np.uint32).astype(np.uint64),
        )
    )


def rigid_residual(source, target, rotation, translation):
    return np.linalg.norm(source @ rotation.T + translation - target, axis=1)


def nearest_distances(query, reference, chunk=100):
    values = []
    for start in range(0, len(query), chunk):
        part = query[start : start + chunk]
        squared = np.sum(
            (part[:, None, :] - reference[None, :, :]) ** 2,
            axis=2,
        )
        values.append(np.sqrt(np.min(squared, axis=1)))
    return np.concatenate(values)


def main():
    capture = json.loads(CAPTURE.read_text(encoding="utf-8"))
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    archive = np.load(CLOUDS)
    transform = baseline["raw_to_base_fixed_transform"]
    rotation = np.asarray(transform["rotation"], dtype=np.float64)
    translation = np.asarray(transform["translation_m"], dtype=np.float64)
    odom = capture["odom"]["/utlidar/robot_odom"]

    positions = np.asarray([item["position"] for item in odom])
    rotations = [quat_rotation(item["orientation_xyzw"]) for item in odom]
    linear_speed = np.asarray(
        [np.linalg.norm(item["linear_velocity"]) for item in odom]
    )
    angular_speed = np.asarray(
        [np.linalg.norm(item["angular_velocity"]) for item in odom]
    )
    position_steps = np.linalg.norm(np.diff(positions, axis=0), axis=1)
    rotation_steps = np.asarray(
        [rotation_angle(rotations[i - 1].T @ rotations[i]) for i in range(1, len(rotations))]
    )

    pair_results = []
    continuity = []
    previous_odom_points = None
    previous_stamp = None
    all_errors = []
    moving_errors = []
    stationary_errors = []
    for pair in capture["raw_base_pairs"]:
        raw_fields = pair["topics"]["/utlidar/cloud"]["fields"]
        base_fields = pair["topics"]["/utlidar/cloud_base"]["fields"]
        raw_keys = keys(raw_fields, archive)
        base_keys = keys(base_fields, archive)
        lookup = {tuple(key): index for index, key in enumerate(raw_keys)}
        raw_indices = np.asarray([lookup.get(tuple(key), -1) for key in base_keys])
        matched = raw_indices >= 0
        source = xyz(raw_fields, archive, raw_indices[matched]).astype(np.float64)
        target = xyz(base_fields, archive, np.flatnonzero(matched)).astype(np.float64)
        finite = np.isfinite(source).all(axis=1) & np.isfinite(target).all(axis=1)
        source, target = source[finite], target[finite]
        errors = rigid_residual(source, target, rotation, translation)
        all_errors.extend(errors)

        odom_item, odom_delta = nearest(odom, pair["stamp_ns"])
        speed = np.linalg.norm(odom_item["linear_velocity"])
        turn_rate = np.linalg.norm(odom_item["angular_velocity"])
        moving = bool(speed > 0.05 or turn_rate > 0.10)
        (moving_errors if moving else stationary_errors).extend(errors)

        raw_time = archive[raw_fields["time"]]
        base_time = archive[base_fields["time"]]
        matched_time = raw_time[raw_indices[matched]]
        time_bits_equal = (
            matched_time.view(np.uint32) == base_time[matched].view(np.uint32)
        )
        pair_results.append(
            {
                "stamp_ns": pair["stamp_ns"],
                "raw_points": int(len(raw_keys)),
                "base_points": int(len(base_keys)),
                "match_ratio": float(np.mean(matched)),
                "retained_ratio": float(len(base_keys) / len(raw_keys)),
                "matched_time_bit_ratio": float(np.mean(time_bits_equal)),
                "scan_time_span_ms": float(
                    (np.nanmax(base_time) - np.nanmin(base_time)) * 1000.0
                ),
                "odom_delta_ms": odom_delta / 1e6,
                "moving": moving,
                "linear_speed_mps": float(speed),
                "angular_speed_radps": float(turn_rate),
                "baseline_transform_residual_m": describe(errors),
            }
        )

        pose_rotation = quat_rotation(odom_item["orientation_xyzw"])
        pose_translation = np.asarray(odom_item["position"])
        base_points = target
        if len(base_points) > 700:
            selection = np.linspace(0, len(base_points) - 1, 700).astype(int)
            base_points = base_points[selection]
        odom_points = base_points @ pose_rotation.T + pose_translation
        if previous_odom_points is not None:
            distances = nearest_distances(odom_points, previous_odom_points)
            continuity.append(
                {
                    "stamp_ns": pair["stamp_ns"],
                    "delta_ms": (pair["stamp_ns"] - previous_stamp) / 1e6,
                    "moving": moving,
                    "nearest_neighbor_m": describe(distances),
                    "fraction_within_5cm": float(np.mean(distances <= 0.05)),
                    "fraction_within_10cm": float(np.mean(distances <= 0.10)),
                }
            )
        previous_odom_points = odom_points
        previous_stamp = pair["stamp_ns"]

    stamps = capture["stamps_ns"]
    stamp_checks = {}
    for topic, values in stamps.items():
        diffs = np.diff(np.asarray(values, dtype=np.int64))
        stamp_checks[topic] = {
            "samples": len(values),
            "backward_jumps": int(np.sum(diffs < 0)),
            "duplicates": int(np.sum(diffs == 0)),
            "interval_ms": describe(diffs / 1e6),
        }

    raw_set = set(stamps["/utlidar/cloud"])
    base_set = set(stamps["/utlidar/cloud_base"])
    scan_linear_motion = [
        item["linear_speed_mps"] * item["scan_time_span_ms"] / 1000.0
        for item in pair_results
    ]
    scan_angular_motion = [
        item["angular_speed_radps"] * item["scan_time_span_ms"] / 1000.0
        for item in pair_results
    ]
    moving_scan_linear_motion = [
        value
        for value, item in zip(scan_linear_motion, pair_results)
        if item["moving"]
    ]
    moving_scan_angular_motion = [
        value
        for value, item in zip(scan_angular_motion, pair_results)
        if item["moving"]
    ]
    result = {
        "capture_duration_seconds": capture["duration_seconds"],
        "counts": capture["counts"],
        "estimated_hz": capture["estimated_hz"],
        "frames": capture["frames"],
        "schemas": capture["schemas"],
        "stamp_checks": stamp_checks,
        "raw_base_exact_stamp_matches": len(raw_set & base_set),
        "raw_base_stamp_match_ratio": len(raw_set & base_set) / len(base_set),
        "motion_coverage": {
            "net_displacement_m": float(np.linalg.norm(positions[-1] - positions[0])),
            "position_span_m": (np.max(positions, axis=0) - np.min(positions, axis=0)).tolist(),
            "path_length_m": float(np.sum(position_steps)),
            "total_rotation_rad": float(np.sum(rotation_steps)),
            "max_position_step_m": float(np.max(position_steps)),
            "max_rotation_step_rad": float(np.max(rotation_steps)),
            "linear_speed_mps": describe(linear_speed),
            "angular_speed_radps": describe(angular_speed),
            "fraction_reported_moving": float(
                np.mean((linear_speed > 0.05) | (angular_speed > 0.10))
            ),
            "gate": {
                "path_length_at_least_0_5m": bool(np.sum(position_steps) >= 0.5),
                "rotation_at_least_30deg": bool(np.sum(rotation_steps) >= math.radians(30)),
                "moving_fraction_at_least_10pct": bool(
                    np.mean((linear_speed > 0.05) | (angular_speed > 0.10)) >= 0.10
                ),
            },
        },
        "raw_base_pair_checks": {
            "pairs": len(pair_results),
            "metadata_match_ratio": describe([item["match_ratio"] for item in pair_results]),
            "time_bit_match_ratio": describe(
                [item["matched_time_bit_ratio"] for item in pair_results]
            ),
            "retained_raw_ratio": describe(
                [item["retained_ratio"] for item in pair_results]
            ),
            "scan_time_span_ms": describe(
                [item["scan_time_span_ms"] for item in pair_results]
            ),
            "nearest_odom_delta_ms": describe(
                [item["odom_delta_ms"] for item in pair_results]
            ),
            "baseline_transform_residual_all_m": describe(all_errors),
            "baseline_transform_residual_moving_m": describe(moving_errors),
            "baseline_transform_residual_stationary_m": describe(stationary_errors),
            "potential_uncompensated_scan_motion": {
                "all_linear_displacement_m": describe(scan_linear_motion),
                "all_angular_displacement_rad": describe(scan_angular_motion),
                "moving_linear_displacement_m": describe(
                    moving_scan_linear_motion
                ),
                "moving_angular_displacement_rad": describe(
                    moving_scan_angular_motion
                ),
                "note": (
                    "Speed times per-point time span is a conservative motion "
                    "exposure estimate, not a direct measurement of distortion."
                ),
            },
            "per_pair": pair_results,
        },
        "odom_compensated_interframe_continuity": {
            "pairs": len(continuity),
            "median_nearest_neighbor_m": describe(
                [item["nearest_neighbor_m"]["median"] for item in continuity]
            ),
            "p95_nearest_neighbor_m": describe(
                [item["nearest_neighbor_m"]["p95"] for item in continuity]
            ),
            "fraction_within_5cm": describe(
                [item["fraction_within_5cm"] for item in continuity]
            ),
            "fraction_within_10cm": describe(
                [item["fraction_within_10cm"] for item in continuity]
            ),
            "moving_median_nearest_neighbor_m": describe(
                [
                    item["nearest_neighbor_m"]["median"]
                    for item in continuity
                    if item["moving"]
                ]
            ),
            "stationary_median_nearest_neighbor_m": describe(
                [
                    item["nearest_neighbor_m"]["median"]
                    for item in continuity
                    if not item["moving"]
                ]
            ),
            "moving_fraction_within_10cm": describe(
                [
                    item["fraction_within_10cm"]
                    for item in continuity
                    if item["moving"]
                ]
            ),
            "stationary_fraction_within_10cm": describe(
                [
                    item["fraction_within_10cm"]
                    for item in continuity
                    if not item["moving"]
                ]
            ),
            "per_pair": continuity,
        },
        "interpretation_limits": [
            "The capture is read-only and does not command robot motion.",
            "Inter-frame nearest-neighbor continuity depends on scene geometry and occlusion.",
            "Raw/base exact correspondence validates the fixed transform but does not by itself prove motion deskew.",
            "A passing motion gate does not turn an observed extrinsic into official calibration.",
        ],
    }
    OUTPUT.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
