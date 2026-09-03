#!/usr/bin/env python3
"""Offline analysis of the read-only Phase 5.4.1 capture."""

import json
import math
from pathlib import Path

import numpy as np


ROOT = Path(r"E:\笨笨狗")
CAPTURE_JSON = ROOT / "phase541_capture.json"
CAPTURE_NPZ = ROOT / "phase541_clouds.npz"
OUTPUT_JSON = ROOT / "phase541_analysis.json"


def describe(values):
    array = np.asarray(values, dtype=np.float64)
    return {
        "count": int(array.size),
        "min": float(np.min(array)),
        "mean": float(np.mean(array)),
        "median": float(np.median(array)),
        "p95": float(np.percentile(array, 95)),
        "max": float(np.max(array)),
        "std": float(np.std(array)),
    }


def stamp_stats(stamps):
    array = np.asarray(stamps, dtype=np.int64)
    diffs = np.diff(array)
    return {
        "count": int(array.size),
        "backward_jumps": int(np.sum(diffs < 0)),
        "duplicate_adjacent": int(np.sum(diffs == 0)),
        "interval_ms": describe(diffs / 1e6),
    }


def xyz(fields, archive, indices=None):
    result = np.column_stack([archive[fields[name]] for name in ("x", "y", "z")])
    return result if indices is None else result[indices]


def exact_point_keys(fields, archive):
    return np.column_stack(
        (
            archive[fields["ring"]].astype(np.uint64),
            archive[fields["time"]].view(np.uint32).astype(np.uint64),
            archive[fields["intensity"]].view(np.uint32).astype(np.uint64),
        )
    )


def solve_rigid(source, target):
    source_center = np.mean(source, axis=0)
    target_center = np.mean(target, axis=0)
    u, _, vt = np.linalg.svd(
        (source - source_center).T @ (target - target_center)
    )
    rotation = vt.T @ u.T
    if np.linalg.det(rotation) < 0:
        vt[-1, :] *= -1
        rotation = vt.T @ u.T
    translation = target_center - rotation @ source_center
    errors = np.linalg.norm(source @ rotation.T + translation - target, axis=1)
    return rotation, translation, errors


def rotation_angle(rotation):
    cosine = np.clip((np.trace(rotation) - 1.0) / 2.0, -1.0, 1.0)
    return float(math.acos(cosine))


def rpy_from_rotation(rotation):
    pitch = math.asin(float(np.clip(-rotation[2, 0], -1.0, 1.0)))
    roll = math.atan2(rotation[2, 1], rotation[2, 2])
    yaw = math.atan2(rotation[1, 0], rotation[0, 0])
    return [roll, pitch, yaw]


def quaternion_from_rotation(rotation):
    trace = np.trace(rotation)
    if trace > 0:
        scale = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * scale
        x = (rotation[2, 1] - rotation[1, 2]) / scale
        y = (rotation[0, 2] - rotation[2, 0]) / scale
        z = (rotation[1, 0] - rotation[0, 1]) / scale
    else:
        index = int(np.argmax(np.diag(rotation)))
        if index == 0:
            scale = math.sqrt(1.0 + rotation[0, 0] - rotation[1, 1] - rotation[2, 2]) * 2
            w = (rotation[2, 1] - rotation[1, 2]) / scale
            x = 0.25 * scale
            y = (rotation[0, 1] + rotation[1, 0]) / scale
            z = (rotation[0, 2] + rotation[2, 0]) / scale
        elif index == 1:
            scale = math.sqrt(1.0 + rotation[1, 1] - rotation[0, 0] - rotation[2, 2]) * 2
            w = (rotation[0, 2] - rotation[2, 0]) / scale
            x = (rotation[0, 1] + rotation[1, 0]) / scale
            y = 0.25 * scale
            z = (rotation[1, 2] + rotation[2, 1]) / scale
        else:
            scale = math.sqrt(1.0 + rotation[2, 2] - rotation[0, 0] - rotation[1, 1]) * 2
            w = (rotation[1, 0] - rotation[0, 1]) / scale
            x = (rotation[0, 2] + rotation[2, 0]) / scale
            y = (rotation[1, 2] + rotation[2, 1]) / scale
            z = 0.25 * scale
    quaternion = np.array([x, y, z, w])
    quaternion /= np.linalg.norm(quaternion)
    if quaternion[3] < 0:
        quaternion *= -1
    return quaternion.tolist()


def rotation_from_quaternion(quaternion):
    x, y, z, w = np.asarray(quaternion, dtype=np.float64)
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def nearest_sample(samples, stamp):
    stamps = np.asarray([sample["stamp_ns"] for sample in samples], dtype=np.int64)
    index = int(np.argmin(np.abs(stamps - stamp)))
    return samples[index], int(stamps[index] - stamp)


def nearest_distances(query, reference, chunk_size=64):
    results = []
    for start in range(0, query.shape[0], chunk_size):
        chunk = query[start : start + chunk_size]
        squared = np.sum(
            (chunk[:, np.newaxis, :] - reference[np.newaxis, :, :]) ** 2,
            axis=2,
        )
        results.append(np.sqrt(np.min(squared, axis=1)))
    return np.concatenate(results)


def pose_error(first, second):
    position = np.asarray(first["position"]) - np.asarray(second["position"])
    r1 = rotation_from_quaternion(first["orientation_xyzw"])
    r2 = rotation_from_quaternion(second["orientation_xyzw"])
    return float(np.linalg.norm(position)), rotation_angle(r1 @ r2.T)


def main():
    capture = json.loads(CAPTURE_JSON.read_text(encoding="utf-8"))
    archive = np.load(CAPTURE_NPZ)

    per_frame = []
    all_source, all_target = [], []
    for sample in capture["raw_base_samples"]:
        raw_fields = sample["topics"]["/utlidar/cloud"]["fields"]
        base_fields = sample["topics"]["/utlidar/cloud_base"]["fields"]
        raw_keys = exact_point_keys(raw_fields, archive)
        base_keys = exact_point_keys(base_fields, archive)
        lookup = {tuple(key): index for index, key in enumerate(raw_keys)}
        raw_indices = np.array([lookup.get(tuple(key), -1) for key in base_keys])
        matched = raw_indices >= 0
        source = xyz(raw_fields, archive, raw_indices[matched]).astype(np.float64)
        target = xyz(base_fields, archive, np.flatnonzero(matched)).astype(np.float64)
        finite = np.isfinite(source).all(axis=1) & np.isfinite(target).all(axis=1)
        source, target = source[finite], target[finite]
        rotation, translation, errors = solve_rigid(source, target)
        all_source.append(source)
        all_target.append(target)
        per_frame.append(
            {
                "stamp_ns": sample["stamp_ns"],
                "raw_points": int(raw_keys.shape[0]),
                "base_points": int(base_keys.shape[0]),
                "metadata_exact_matches": int(np.sum(matched)),
                "metadata_match_ratio": float(np.mean(matched)),
                "retained_raw_ratio": float(base_keys.shape[0] / raw_keys.shape[0]),
                "rotation": rotation.tolist(),
                "translation_m": translation.tolist(),
                "rpy_rad": rpy_from_rotation(rotation),
                "quaternion_xyzw": quaternion_from_rotation(rotation),
                "residual_m": describe(errors),
            }
        )

    aggregate_source = np.concatenate(all_source)
    aggregate_target = np.concatenate(all_target)
    rotation, translation, errors = solve_rigid(aggregate_source, aggregate_target)
    translations = np.asarray([frame["translation_m"] for frame in per_frame])
    rotation_deviations = [
        rotation_angle(np.asarray(frame["rotation"]) @ rotation.T)
        for frame in per_frame
    ]

    radar_rotation = np.array(
        [
            [math.cos(2.8782), 0.0, math.sin(2.8782)],
            [0.0, 1.0, 0.0],
            [-math.sin(2.8782), 0.0, math.cos(2.8782)],
        ]
    )
    radar_translation = np.array([0.28945, 0.0, -0.046825])

    source_odom = capture["pose_samples"]["/utlidar/robot_odom"]
    bridged_odom = capture["pose_samples"]["/odom"]
    bridged_by_stamp = {sample["stamp_ns"]: sample for sample in bridged_odom}
    exact_odom_errors = [
        pose_error(sample, bridged_by_stamp[sample["stamp_ns"]])
        for sample in source_odom
        if sample["stamp_ns"] in bridged_by_stamp
    ]

    robot_pose = capture["pose_samples"]["/utlidar/robot_pose"]
    robot_pose_deltas, robot_pose_position_errors, robot_pose_angle_errors = [], [], []
    for sample in robot_pose:
        nearest, delta = nearest_sample(source_odom, sample["stamp_ns"])
        position_error, angle_error = pose_error(sample, nearest)
        robot_pose_deltas.append(delta / 1e6)
        robot_pose_position_errors.append(position_error)
        robot_pose_angle_errors.append(angle_error)

    stamps = capture["stamps_ns"]
    raw_stamps = set(stamps["/utlidar/cloud"])
    base_stamps = set(stamps["/utlidar/cloud_base"])
    deskewed_to_base_delta_ms = []
    deskewed_to_pose_delta_ms = []
    deskewed_to_odom_delta_ms = []
    for stamp in stamps["/utlidar/cloud_deskewed"]:
        _, base_delta = nearest_sample(
            [{"stamp_ns": value} for value in stamps["/utlidar/cloud_base"]], stamp
        )
        _, pose_delta = nearest_sample(robot_pose, stamp)
        _, odom_delta = nearest_sample(source_odom, stamp)
        deskewed_to_base_delta_ms.append(base_delta / 1e6)
        deskewed_to_pose_delta_ms.append(pose_delta / 1e6)
        deskewed_to_odom_delta_ms.append(odom_delta / 1e6)

    inclusion_frames = []
    raw_base_samples = capture["raw_base_samples"]
    for deskewed in capture["deskewed_samples"][:10]:
        base_manifest, base_delta = nearest_sample(
            raw_base_samples, deskewed["stamp_ns"]
        )
        odom_sample, odom_delta = nearest_sample(
            source_odom, base_manifest["stamp_ns"]
        )
        base_fields = base_manifest["topics"]["/utlidar/cloud_base"]["fields"]
        base_points = xyz(base_fields, archive).astype(np.float64)
        desk_points = xyz(deskewed["fields"], archive).astype(np.float64)
        base_points = base_points[np.isfinite(base_points).all(axis=1)]
        desk_points = desk_points[np.isfinite(desk_points).all(axis=1)]
        odom_rotation = rotation_from_quaternion(odom_sample["orientation_xyzw"])
        odom_translation = np.asarray(odom_sample["position"])
        expected = base_points @ odom_rotation.T + odom_translation
        distances = nearest_distances(expected, desk_points)
        inclusion_frames.append(
            {
                "deskewed_stamp_ns": deskewed["stamp_ns"],
                "base_stamp_delta_ms": base_delta / 1e6,
                "odom_stamp_delta_ms": odom_delta / 1e6,
                "base_points": int(base_points.shape[0]),
                "deskewed_points": int(desk_points.shape[0]),
                "nearest_error_m": describe(distances),
                "fraction_within_1mm": float(np.mean(distances <= 0.001)),
                "fraction_within_5mm": float(np.mean(distances <= 0.005)),
                "fraction_within_20mm": float(np.mean(distances <= 0.020)),
            }
        )

    result = {
        "capture_duration_seconds": capture["duration_seconds"],
        "topic_counts": capture["counts"],
        "estimated_hz": capture["estimated_hz"],
        "frames": capture["frames"],
        "schemas": capture["schemas"],
        "stamp_statistics": {
            topic: stamp_stats(values) for topic, values in stamps.items()
        },
        "raw_base_timestamp_relation": {
            "raw_samples": len(raw_stamps),
            "base_samples": len(base_stamps),
            "exact_stamp_matches": len(raw_stamps & base_stamps),
            "raw_match_ratio": len(raw_stamps & base_stamps) / len(raw_stamps),
            "base_match_ratio": len(raw_stamps & base_stamps) / len(base_stamps),
        },
        "raw_to_base_fixed_transform": {
            "frames_analyzed": len(per_frame),
            "total_correspondences": int(aggregate_source.shape[0]),
            "metadata_match_ratio": describe(
                [frame["metadata_match_ratio"] for frame in per_frame]
            ),
            "retained_raw_ratio": describe(
                [frame["retained_raw_ratio"] for frame in per_frame]
            ),
            "rotation": rotation.tolist(),
            "translation_m": translation.tolist(),
            "rpy_rad": rpy_from_rotation(rotation),
            "quaternion_xyzw": quaternion_from_rotation(rotation),
            "residual_m": describe(errors),
            "translation_frame_std_m": np.std(translations, axis=0).tolist(),
            "translation_frame_range_m": (
                np.max(translations, axis=0) - np.min(translations, axis=0)
            ).tolist(),
            "rotation_frame_deviation_rad": describe(rotation_deviations),
            "per_frame": per_frame,
        },
        "official_radar_candidate_difference": {
            "translation_difference_m": (
                translation - radar_translation
            ).tolist(),
            "translation_difference_norm_m": float(
                np.linalg.norm(translation - radar_translation)
            ),
            "rotation_difference_rad": rotation_angle(rotation @ radar_rotation.T),
        },
        "robot_odom_to_bridge_odom": {
            "source_samples": len(source_odom),
            "bridge_samples": len(bridged_odom),
            "exact_stamp_matches": len(exact_odom_errors),
            "position_error_m": describe([value[0] for value in exact_odom_errors]),
            "orientation_error_rad": describe(
                [value[1] for value in exact_odom_errors]
            ),
        },
        "robot_pose_to_nearest_robot_odom": {
            "samples": len(robot_pose),
            "stamp_delta_ms": describe(robot_pose_deltas),
            "position_error_m": describe(robot_pose_position_errors),
            "orientation_error_rad": describe(robot_pose_angle_errors),
        },
        "deskewed_time_relation": {
            "nearest_base_stamp_delta_ms": describe(deskewed_to_base_delta_ms),
            "nearest_robot_pose_stamp_delta_ms": describe(
                deskewed_to_pose_delta_ms
            ),
            "nearest_robot_odom_stamp_delta_ms": describe(
                deskewed_to_odom_delta_ms
            ),
        },
        "deskewed_contains_current_base_scan_test": inclusion_frames,
    }
    OUTPUT_JSON.write_text(
        json.dumps(result, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
