#!/usr/bin/env python3
"""Compare Phase 5.4.3-B static and manually driven online captures."""

import json
from pathlib import Path

import numpy as np

from phase542_analyze import (
    describe,
    keys,
    nearest,
    nearest_distances,
    quat_rotation,
    rigid_residual,
    rotation_angle,
    xyz,
)


DATA_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = DATA_DIR.parent
BASELINE = json.loads(
    (PROJECT_ROOT / "phase541_analysis.json").read_text(encoding="utf-8")
)
ROTATION = np.asarray(
    BASELINE["raw_to_base_fixed_transform"]["rotation"], dtype=np.float64
)
TRANSLATION = np.asarray(
    BASELINE["raw_to_base_fixed_transform"]["translation_m"], dtype=np.float64
)


def motion_metrics(odom):
    positions = np.asarray([item["position"] for item in odom], dtype=np.float64)
    rotations = [quat_rotation(item["orientation_xyzw"]) for item in odom]
    stamps = np.asarray([item["stamp_ns"] for item in odom], dtype=np.int64)
    position_steps = np.linalg.norm(np.diff(positions, axis=0), axis=1)
    rotation_steps = [
        rotation_angle(rotations[index - 1].T @ rotations[index])
        for index in range(1, len(rotations))
    ]
    linear = [np.linalg.norm(item["linear_velocity"]) for item in odom]
    angular = [np.linalg.norm(item["angular_velocity"]) for item in odom]
    sample_indices = [0]
    next_stamp = stamps[0] + 100_000_000
    for index, stamp in enumerate(stamps[1:], start=1):
        if stamp >= next_stamp:
            sample_indices.append(index)
            next_stamp = stamp + 100_000_000
    if sample_indices[-1] != len(stamps) - 1:
        sample_indices.append(len(stamps) - 1)
    sampled_positions = positions[np.asarray(sample_indices)]
    sampled_path = np.linalg.norm(
        np.diff(sampled_positions, axis=0), axis=1
    )
    yaw = np.unwrap(
        np.asarray([np.arctan2(item[1, 0], item[0, 0]) for item in rotations])
    )
    sampled_yaw = yaw[np.asarray(sample_indices)]
    return {
        "net_displacement_m": float(np.linalg.norm(positions[-1] - positions[0])),
        "path_length_m": float(np.sum(position_steps)),
        "path_length_10hz_m": float(np.sum(sampled_path)),
        "position_span_m": (
            np.max(positions, axis=0) - np.min(positions, axis=0)
        ).tolist(),
        "total_rotation_rad": float(np.sum(rotation_steps)),
        "net_orientation_change_deg": float(
            np.degrees(rotation_angle(rotations[0].T @ rotations[-1]))
        ),
        "net_yaw_change_deg": float(np.degrees(yaw[-1] - yaw[0])),
        "yaw_span_deg": float(np.degrees(np.max(yaw) - np.min(yaw))),
        "absolute_yaw_travel_10hz_deg": float(
            np.degrees(np.sum(np.abs(np.diff(sampled_yaw))))
        ),
        "linear_speed_mps": describe(linear),
        "angular_speed_radps": describe(angular),
        "moving_fraction": float(
            np.mean((np.asarray(linear) > 0.05) | (np.asarray(angular) > 0.10))
        ),
    }


def analyze_capture(prefix):
    capture = json.loads(
        (DATA_DIR / f"{prefix}_capture.json").read_text(encoding="utf-8")
    )
    archive = np.load(DATA_DIR / f"{prefix}_clouds.npz")
    odom = capture["odom"]["/utlidar/robot_odom"]
    pair_results = []
    all_errors = []
    continuity = []
    previous_points = None
    previous_stamp = None

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
        errors = rigid_residual(source, target, ROTATION, TRANSLATION)
        all_errors.extend(errors)

        odom_item, odom_delta = nearest(odom, pair["stamp_ns"])
        pose_rotation = quat_rotation(odom_item["orientation_xyzw"])
        pose_translation = np.asarray(odom_item["position"])
        if len(target) > 700:
            selected = np.linspace(0, len(target) - 1, 700).astype(int)
            target = target[selected]
        common_points = target @ pose_rotation.T + pose_translation
        if previous_points is not None:
            distances = nearest_distances(common_points, previous_points)
            continuity.append(
                {
                    "delta_ms": (pair["stamp_ns"] - previous_stamp) / 1e6,
                    "nearest_neighbor_m": describe(distances),
                    "fraction_within_10cm": float(np.mean(distances <= 0.10)),
                }
            )
        previous_points = common_points
        previous_stamp = pair["stamp_ns"]
        pair_results.append(
            {
                "metadata_match_ratio": float(np.mean(matched)),
                "odom_delta_ms": odom_delta / 1e6,
                "residual_m": describe(errors),
            }
        )

    stamp_checks = {}
    for topic, values in capture["stamps_ns"].items():
        differences = np.diff(np.asarray(values, dtype=np.int64))
        stamp_checks[topic] = {
            "samples": len(values),
            "backward_jumps": int(np.sum(differences < 0)),
            "duplicates": int(np.sum(differences == 0)),
            "interval_ms": describe(differences / 1e6),
        }

    states = capture.get("lidar_states", [])
    raw_stamps = set(capture["stamps_ns"]["/utlidar/cloud"])
    base_stamps = set(capture["stamps_ns"]["/utlidar/cloud_base"])
    return {
        "duration_seconds": capture["duration_seconds"],
        "counts": capture["counts"],
        "estimated_hz": capture["estimated_hz"],
        "frames": capture["frames"],
        "point_counts": {
            topic: describe(values) for topic, values in capture["point_counts"].items()
        },
        "stamp_checks": stamp_checks,
        "raw_base_exact_stamp_matches": len(raw_stamps & base_stamps),
        "raw_base_stamp_match_ratio": len(raw_stamps & base_stamps) / len(base_stamps),
        "motion": motion_metrics(odom),
        "pair_checks": {
            "pairs": len(pair_results),
            "metadata_match_ratio": describe(
                [item["metadata_match_ratio"] for item in pair_results]
            ),
            "nearest_odom_delta_ms": describe(
                [item["odom_delta_ms"] for item in pair_results[1:]]
            ),
            "fixed_transform_residual_m": describe(all_errors),
        },
        "odom_compensated_continuity": {
            "pairs": len(continuity),
            "median_nearest_neighbor_m": describe(
                [item["nearest_neighbor_m"]["median"] for item in continuity]
            ),
            "p95_nearest_neighbor_m": describe(
                [item["nearest_neighbor_m"]["p95"] for item in continuity]
            ),
            "fraction_within_10cm": describe(
                [item["fraction_within_10cm"] for item in continuity]
            ),
        },
        "lidar_state": {
            "samples": len(states),
            "software_versions": sorted(
                {item["software_version"] for item in states}
            ),
            "nonzero_error_samples": sum(
                item["error_state"] != 0 for item in states
            ),
            "dirty_percentage": describe(
                [item["dirty_percentage"] for item in states]
            ),
            "cloud_frequency_hz": describe(
                [item["cloud_frequency"] for item in states]
            ),
            "cloud_packet_loss_rate": describe(
                [item["cloud_packet_loss_rate"] for item in states]
            ),
            "nonzero_cloud_packet_loss_samples": sum(
                item["cloud_packet_loss_rate"] != 0 for item in states
            ),
            "imu_frequency_hz": describe(
                [item["imu_frequency"] for item in states]
            ),
            "imu_packet_loss_rate": describe(
                [item["imu_packet_loss_rate"] for item in states]
            ),
        },
    }


def main():
    result = {
        "static": analyze_capture("phase543b_static"),
        "motion": analyze_capture("phase543b_motion"),
        "notes": [
            "All robot motion was performed by the onsite user.",
            "No control topic, SLAM, Nav2, or new TF was published.",
            "Nearest-neighbor continuity is scene dependent and is not a direct deskew metric.",
        ],
    }
    (PROJECT_ROOT / "phase543b_online_analysis.json").write_text(
        json.dumps(result, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
