#!/usr/bin/env python3
"""Read-only Phase 5.4.5 rosbag integrity and motion audit."""

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import rosbag2_py
from rclpy.serialization import deserialize_message
from rosidl_runtime_py.utilities import get_message


TRACKED = {
    "/utlidar/cloud",
    "/utlidar/cloud_base",
    "/utlidar/imu",
    "/utlidar/robot_odom",
    "/utlidar/lidar_state",
    "/odom",
    "/tf",
    "/tf_static",
}


def describe(values):
    data = np.asarray(values, dtype=np.float64)
    if data.size == 0:
        return {"samples": 0}
    return {
        "samples": int(data.size),
        "min": float(np.min(data)),
        "median": float(np.median(data)),
        "mean": float(np.mean(data)),
        "p95": float(np.percentile(data, 95)),
        "max": float(np.max(data)),
    }


def header_stamp_ns(message):
    stamp = message.header.stamp
    return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)


def yaw_from_quaternion(q):
    siny = 2.0 * (q.w * q.z + q.x * q.y)
    cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny, cosy)


def stamp_audit(values):
    if not values:
        return {"samples": 0, "backward_jumps": 0, "duplicates": 0}
    data = np.asarray(values, dtype=np.int64)
    delta = np.diff(data)
    return {
        "samples": int(data.size),
        "first_ns": int(data[0]),
        "last_ns": int(data[-1]),
        "backward_jumps": int(np.sum(delta < 0)),
        "duplicates": int(np.sum(delta == 0)),
        "interval_ms": describe(delta / 1e6),
    }


def nearest_delta_ms(reference, query):
    ref = np.sort(np.asarray(reference, dtype=np.int64))
    qry = np.asarray(query, dtype=np.int64)
    if not len(ref) or not len(qry):
        return []
    indices = np.searchsorted(ref, qry)
    right = np.clip(indices, 0, len(ref) - 1)
    left = np.clip(indices - 1, 0, len(ref) - 1)
    delta = np.minimum(np.abs(qry - ref[left]), np.abs(qry - ref[right]))
    return (delta / 1e6).tolist()


def motion_audit(samples, bag_start_ns, duration_s):
    if not samples:
        return {"samples": 0}

    times = np.asarray(
        [(item["bag_ns"] - bag_start_ns) / 1e9 for item in samples],
        dtype=np.float64,
    )
    positions = np.asarray([item["position"] for item in samples], dtype=np.float64)
    linear = np.asarray([item["linear_speed"] for item in samples], dtype=np.float64)
    angular = np.asarray([item["angular_speed"] for item in samples], dtype=np.float64)
    yaw = np.unwrap(np.asarray([item["yaw"] for item in samples], dtype=np.float64))

    sample_indices = [0]
    next_time = times[0] + 0.1
    for index, value in enumerate(times[1:], start=1):
        if value >= next_time:
            sample_indices.append(index)
            next_time = value + 0.1
    if sample_indices[-1] != len(times) - 1:
        sample_indices.append(len(times) - 1)
    sampled_positions = positions[np.asarray(sample_indices)]
    sampled_yaw = yaw[np.asarray(sample_indices)]

    moving = (linear > 0.02) | (angular > 0.08)
    bins = []
    for start in np.arange(0.0, math.ceil(duration_s), 10.0):
        mask = (times >= start) & (times < start + 10.0)
        if not np.any(mask):
            continue
        idx = np.flatnonzero(mask)
        segment_positions = positions[idx]
        bins.append(
            {
                "start_s": float(start),
                "end_s": float(min(start + 10.0, duration_s)),
                "samples": int(len(idx)),
                "moving_fraction": float(np.mean(moving[idx])),
                "linear_speed_mps": describe(linear[idx]),
                "angular_speed_radps": describe(angular[idx]),
                "position_delta_m": float(
                    np.linalg.norm(segment_positions[-1] - segment_positions[0])
                ),
                "yaw_delta_deg": float(np.degrees(yaw[idx[-1]] - yaw[idx[0]])),
            }
        )

    first_moving = np.flatnonzero(moving)
    first_motion_s = (
        float(times[first_moving[0]]) if first_moving.size else None
    )
    last_motion_s = float(times[first_moving[-1]]) if first_moving.size else None
    initial = times < 60.0
    final = times >= max(0.0, duration_s - 30.0)

    return {
        "samples": int(len(samples)),
        "path_length_10hz_m": float(
            np.sum(np.linalg.norm(np.diff(sampled_positions, axis=0), axis=1))
        ),
        "net_displacement_m": float(np.linalg.norm(positions[-1] - positions[0])),
        "position_span_m": (
            np.max(positions, axis=0) - np.min(positions, axis=0)
        ).tolist(),
        "net_yaw_change_deg": float(np.degrees(yaw[-1] - yaw[0])),
        "absolute_yaw_travel_10hz_deg": float(
            np.degrees(np.sum(np.abs(np.diff(sampled_yaw))))
        ),
        "linear_speed_mps": describe(linear),
        "angular_speed_radps": describe(angular),
        "moving_fraction": float(np.mean(moving)),
        "first_motion_s": first_motion_s,
        "last_motion_s": last_motion_s,
        "initial_60s_moving_fraction": (
            float(np.mean(moving[initial])) if np.any(initial) else None
        ),
        "final_30s_moving_fraction": (
            float(np.mean(moving[final])) if np.any(final) else None
        ),
        "ten_second_bins": bins,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("bag_dir")
    parser.add_argument("output_json")
    args = parser.parse_args()

    bag_dir = Path(args.bag_dir).resolve()
    output = Path(args.output_json).resolve()
    if not (bag_dir / "metadata.yaml").exists():
        raise SystemExit(f"metadata.yaml not found: {bag_dir}")

    reader = rosbag2_py.SequentialReader()
    reader.open(
        rosbag2_py.StorageOptions(uri=str(bag_dir), storage_id="sqlite3"),
        rosbag2_py.ConverterOptions("", ""),
    )
    topic_types = {
        item.name: item.type for item in reader.get_all_topics_and_types()
    }
    message_classes = {
        topic: get_message(type_name)
        for topic, type_name in topic_types.items()
        if topic in TRACKED
    }

    counts = Counter()
    bag_times = defaultdict(list)
    header_stamps = defaultdict(list)
    frames = defaultdict(Counter)
    point_counts = defaultdict(list)
    odom_samples = defaultdict(list)
    lidar_states = []
    bag_start_ns = None
    bag_end_ns = None

    total = 0
    while reader.has_next():
        topic, data, bag_ns = reader.read_next()
        if bag_start_ns is None:
            bag_start_ns = int(bag_ns)
        bag_end_ns = int(bag_ns)
        if topic not in TRACKED:
            continue
        total += 1
        counts[topic] += 1
        bag_times[topic].append(int(bag_ns))
        message = deserialize_message(data, message_classes[topic])

        if hasattr(message, "header"):
            header_stamps[topic].append(header_stamp_ns(message))
            frames[topic][message.header.frame_id] += 1

        if topic in ("/utlidar/cloud", "/utlidar/cloud_base"):
            point_counts[topic].append(int(message.width) * int(message.height))
        elif topic in ("/utlidar/robot_odom", "/odom"):
            p = message.pose.pose.position
            q = message.pose.pose.orientation
            lv = message.twist.twist.linear
            av = message.twist.twist.angular
            odom_samples[topic].append(
                {
                    "bag_ns": int(bag_ns),
                    "position": [float(p.x), float(p.y), float(p.z)],
                    "yaw": yaw_from_quaternion(q),
                    "linear_speed": math.sqrt(lv.x * lv.x + lv.y * lv.y + lv.z * lv.z),
                    "angular_speed": math.sqrt(av.x * av.x + av.y * av.y + av.z * av.z),
                }
            )
        elif topic == "/utlidar/lidar_state":
            lidar_states.append(
                {
                    "bag_ns": int(bag_ns),
                    "error_state": int(message.error_state),
                    "dirty_percentage": int(message.dirty_percentage),
                    "cloud_frequency": float(message.cloud_frequency),
                    "cloud_packet_loss_rate": float(message.cloud_packet_loss_rate),
                    "imu_frequency": float(message.imu_frequency),
                    "imu_packet_loss_rate": float(message.imu_packet_loss_rate),
                    "software_version": message.software_version,
                }
            )
        if total % 25000 == 0:
            print(f"processed={total}", flush=True)

    duration_s = (bag_end_ns - bag_start_ns) / 1e9
    raw_stamps = set(header_stamps["/utlidar/cloud"])
    base_stamps = set(header_stamps["/utlidar/cloud_base"])
    robot_odom_stamps = set(header_stamps["/utlidar/robot_odom"])
    bridge_odom_stamps = set(header_stamps["/odom"])

    result = {
        "bag_dir": str(bag_dir),
        "duration_seconds": duration_s,
        "topic_types": topic_types,
        "counts": dict(counts),
        "bag_timestamp_checks": {
            topic: stamp_audit(values) for topic, values in bag_times.items()
        },
        "header_timestamp_checks": {
            topic: stamp_audit(values) for topic, values in header_stamps.items()
        },
        "frames": {
            topic: dict(values) for topic, values in frames.items()
        },
        "point_counts": {
            topic: describe(values) for topic, values in point_counts.items()
        },
        "cross_topic_timing": {
            "raw_base_exact_matches": len(raw_stamps & base_stamps),
            "raw_base_match_ratio_of_base": (
                len(raw_stamps & base_stamps) / len(base_stamps)
                if base_stamps
                else None
            ),
            "robot_bridge_odom_exact_matches": len(
                robot_odom_stamps & bridge_odom_stamps
            ),
            "robot_bridge_odom_match_ratio_of_bridge": (
                len(robot_odom_stamps & bridge_odom_stamps)
                / len(bridge_odom_stamps)
                if bridge_odom_stamps
                else None
            ),
            "imu_nearest_cloud_delta_ms": describe(
                nearest_delta_ms(
                    header_stamps["/utlidar/imu"],
                    header_stamps["/utlidar/cloud"],
                )
            ),
        },
        "motion": {
            topic: motion_audit(values, bag_start_ns, duration_s)
            for topic, values in odom_samples.items()
        },
        "lidar_state": {
            "samples": len(lidar_states),
            "software_versions": sorted(
                {item["software_version"] for item in lidar_states}
            ),
            "nonzero_error_samples": sum(
                item["error_state"] != 0 for item in lidar_states
            ),
            "dirty_percentage": describe(
                [item["dirty_percentage"] for item in lidar_states]
            ),
            "cloud_frequency_hz": describe(
                [item["cloud_frequency"] for item in lidar_states]
            ),
            "cloud_packet_loss_rate": describe(
                [item["cloud_packet_loss_rate"] for item in lidar_states]
            ),
            "nonzero_cloud_packet_loss_samples": sum(
                item["cloud_packet_loss_rate"] != 0 for item in lidar_states
            ),
            "imu_frequency_hz": describe(
                [item["imu_frequency"] for item in lidar_states]
            ),
            "imu_packet_loss_rate": describe(
                [item["imu_packet_loss_rate"] for item in lidar_states]
            ),
            "nonzero_imu_packet_loss_samples": sum(
                item["imu_packet_loss_rate"] != 0 for item in lidar_states
            ),
        },
        "safety": {
            "tf_messages_recorded": counts.get("/tf", 0),
            "tf_static_messages_recorded": counts.get("/tf_static", 0),
            "control_topics_recorded": [],
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
