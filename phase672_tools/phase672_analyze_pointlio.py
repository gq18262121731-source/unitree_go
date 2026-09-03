#!/usr/bin/env python3
"""Compare the Phase 6.7.2 trajectory with the frozen Phase 5.4.5 baseline."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np


ROOT = Path(r"E:\笨笨狗")
NEW_ODOM = ROOT / "phase672_pointlio_result" / "pointlio_odom.json"
NEW_PCD = ROOT / "phase672_pointlio_result" / "scans.pcd"
BASELINE = ROOT / "phase545_pointlio_analysis.json"
REFERENCE_AUDIT = ROOT / "phase545_formal_audit.json"
OUTPUT = ROOT / "phase672_artifacts" / "phase672_pointlio_ab_analysis.json"


def quaternion_yaw(q):
    x, y, z, w = q
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def analyze(path: Path) -> dict:
    samples = json.loads(path.read_text(encoding="utf-8"))["samples"]
    unique = []
    previous = None
    duplicates = 0
    backwards = 0
    for item in samples:
        stamp = int(item["stamp_ns"])
        if stamp == previous:
            duplicates += 1
            continue
        if previous is not None and stamp < previous:
            backwards += 1
        previous = stamp
        unique.append(item)
    if len(unique) < 2:
        raise RuntimeError(f"Too few unique odometry samples: {len(unique)}")

    stamps = np.asarray([int(item["stamp_ns"]) for item in unique], dtype=np.int64)
    positions = np.asarray([item["position"] for item in unique], dtype=np.float64)
    yaws = np.unwrap(
        np.asarray([quaternion_yaw(item["orientation_xyzw"]) for item in unique])
    )
    indices = [0]
    next_stamp = stamps[0] + 100_000_000
    for index, stamp in enumerate(stamps[1:], start=1):
        if stamp >= next_stamp:
            indices.append(index)
            next_stamp = stamp + 100_000_000
    if indices[-1] != len(stamps) - 1:
        indices.append(len(stamps) - 1)
    indices = np.asarray(indices, dtype=np.int64)
    sampled = positions[indices]
    steps = np.linalg.norm(np.diff(sampled, axis=0), axis=1)
    radius = np.linalg.norm(sampled - sampled[0], axis=1)
    relative = (stamps[indices] - stamps[indices][0]) / 1e9
    thresholds = {}
    for threshold in (1.0, 5.0, 10.0, 100.0, 1000.0, 10000.0):
        hits = np.flatnonzero(radius > threshold)
        thresholds[str(threshold)] = float(relative[hits[0]]) if hits.size else None
    return {
        "raw_samples": len(samples),
        "unique_samples": len(unique),
        "duplicate_stamps": duplicates,
        "backward_jumps": backwards,
        "duration_seconds": float((stamps[-1] - stamps[0]) / 1e9),
        "finite_positions": bool(np.isfinite(positions).all()),
        "net_displacement_m": float(np.linalg.norm(sampled[-1] - sampled[0])),
        "path_length_10hz_m": float(steps.sum()),
        "max_radius_from_start_m": float(radius.max()),
        "position_span_m": (positions.max(axis=0) - positions.min(axis=0)).tolist(),
        "net_yaw_change_deg": float(np.degrees(yaws[-1] - yaws[0])),
        "first_radius_threshold_crossing_s": thresholds,
    }


def analyze_pcd(path: Path) -> dict:
    with path.open("rb") as stream:
        header = {}
        while True:
            line = stream.readline()
            if not line:
                raise RuntimeError("PCD header ended before DATA")
            decoded = line.decode("ascii").strip()
            if decoded and not decoded.startswith("#"):
                key, *values = decoded.split()
                header[key] = values
            if decoded.startswith("DATA "):
                offset = stream.tell()
                break
    if header.get("DATA") != ["binary"]:
        raise RuntimeError(f"Expected binary PCD, got {header.get('DATA')}")
    points = int(header["POINTS"][0])
    fields = header["FIELDS"]
    raw = np.memmap(path, dtype="<f4", mode="r", offset=offset, shape=(points, len(fields)))
    xyz = np.asarray(raw[:, :3])
    finite_mask = np.isfinite(xyz).all(axis=1)
    finite = xyz[finite_mask]
    low = np.percentile(finite, 0.1, axis=0)
    high = np.percentile(finite, 99.9, axis=0)
    radius = np.linalg.norm(finite, axis=1)
    return {
        "points": points,
        "finite_ratio": float(finite_mask.mean()),
        "robust_p0_1_m": low.tolist(),
        "robust_p99_9_m": high.tolist(),
        "robust_span_m": (high - low).tolist(),
        "fraction_within_radius_m": {
            str(value): float((radius <= value).mean())
            for value in (5.0, 10.0, 20.0, 100.0)
        },
    }


def main() -> None:
    new = analyze(NEW_ODOM)
    new_map = analyze_pcd(NEW_PCD)
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))["trajectory"]
    reference = json.loads(REFERENCE_AUDIT.read_text(encoding="utf-8"))["motion"][
        "/utlidar/robot_odom"
    ]
    comparison = {
        "phase": "6.7.2",
        "same_source_bag": True,
        "baseline": {
            "route": "raw /utlidar/cloud + /utlidar/imu",
            "net_displacement_m": baseline["net_displacement_m"],
            "path_length_10hz_m": baseline["path_length_10hz_m"],
            "max_radius_from_start_m": baseline["max_radius_from_start_m"],
            "first_radius_threshold_crossing_s": baseline[
                "first_radius_threshold_crossing_s"
            ],
        },
        "community_adapter": new,
        "community_adapter_map": new_map,
        "bag_robot_odom_reference": {
            "role": "onboard_odometry_reference_not_ground_truth",
            "net_displacement_m": reference["net_displacement_m"],
            "path_length_10hz_m": reference["path_length_10hz_m"],
            "position_span_m": reference["position_span_m"],
            "net_yaw_change_deg": reference["net_yaw_change_deg"],
            "absolute_yaw_travel_10hz_deg": reference[
                "absolute_yaw_travel_10hz_deg"
            ],
        },
    }
    baseline_net = comparison["baseline"]["net_displacement_m"]
    comparison["ratios"] = {
        "net_displacement_new_over_baseline": new["net_displacement_m"] / baseline_net,
        "net_displacement_reduction_percent":
            (1.0 - new["net_displacement_m"] / baseline_net) * 100.0,
        "path_length_new_over_robot_odom":
            new["path_length_10hz_m"] / reference["path_length_10hz_m"],
        "closed_loop_error_new_over_robot_odom":
            new["net_displacement_m"] / reference["net_displacement_m"],
    }
    comparison["gate"] = {
        "catastrophic_divergence_eliminated": bool(
            new["max_radius_from_start_m"] < 100.0
            and new["net_displacement_m"] < baseline_net * 0.01
        ),
        "trajectory_consistency_with_robot_odom": False,
        "phase_5_4_hold_released": False,
        "phase_5_5_authorized": False,
        "decision": "PARTIAL_IMPROVEMENT_HOLD",
        "reason": (
            "The community adapter removes the 29 km explosion, but the closed-loop "
            "error, path length, and yaw remain inconsistent with onboard odometry."
        ),
    }
    OUTPUT.write_text(
        json.dumps(comparison, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(comparison, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
