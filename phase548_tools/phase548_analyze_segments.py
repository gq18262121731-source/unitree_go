#!/usr/bin/env python3
"""Analyze the seven labeled Phase 5.4.8 ROS 2 bags without running SLAM."""

import json
import math
import sys
from pathlib import Path

import numpy as np
from rosbags.highlevel import AnyReader


LABELS = (
    "level_static",
    "pitch_nose_down_hold",
    "pitch_nose_up_hold",
    "roll_left_down_hold",
    "roll_right_down_hold",
    "yaw_ccw_manual",
    "yaw_cw_manual",
)


def stamp_seconds(stamp):
    nanosec = getattr(stamp, "nanosec", getattr(stamp, "nsec", 0))
    return float(stamp.sec) + float(nanosec) * 1e-9


def scalar_stats(values):
    values = np.asarray(values, dtype=np.float64)
    if not len(values):
        return None
    return {
        "samples": int(len(values)),
        "min": float(np.min(values)),
        "p05": float(np.percentile(values, 5)),
        "median": float(np.median(values)),
        "mean": float(np.mean(values)),
        "p95": float(np.percentile(values, 95)),
        "max": float(np.max(values)),
        "std": float(np.std(values)),
    }


def vector_stats(values):
    values = np.asarray(values, dtype=np.float64)
    if not len(values):
        return None
    return {
        "samples": int(len(values)),
        "mean": np.mean(values, axis=0).tolist(),
        "median": np.median(values, axis=0).tolist(),
        "std": np.std(values, axis=0).tolist(),
        "p05": np.percentile(values, 5, axis=0).tolist(),
        "p95": np.percentile(values, 95, axis=0).tolist(),
        "norm": scalar_stats(np.linalg.norm(values, axis=1)),
    }


def quaternion_to_euler_degrees(quaternions):
    quaternions = np.asarray(quaternions, dtype=np.float64)
    x, y, z, w = quaternions.T
    norms = np.linalg.norm(quaternions, axis=1)
    valid = norms > 1e-12
    normalized = quaternions.copy()
    normalized[valid] /= norms[valid, None]
    x, y, z, w = normalized.T
    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = np.arctan2(sinr_cosp, cosr_cosp)
    sinp = np.clip(2.0 * (w * y - z * x), -1.0, 1.0)
    pitch = np.arcsin(sinp)
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = np.arctan2(siny_cosp, cosy_cosp)
    return np.degrees(np.column_stack([roll, pitch, yaw]))


def locate_bag(root, label):
    candidates = sorted(
        path
        for path in root.glob(f"phase548_*_{label}")
        if path.is_dir() and (path / "metadata.yaml").exists()
    )
    if not candidates:
        raise FileNotFoundError(f"missing Phase 5.4.8 bag for {label}")
    return candidates[-1]


def read_segment(path):
    imu_times = []
    acceleration = []
    gyro = []
    imu_quaternion = []
    imu_frames = set()
    odom_times = []
    odom_angular = []
    odom_quaternion = []
    cloud_times = []
    cloud_frames = set()
    cloud_counts = []
    cloud_spans = []
    ring_unique = []
    ring_min = []
    ring_max = []
    negative_jumps = 0
    frames_with_decrease = 0
    negative_jump_magnitude_ms = []
    fields = None
    point_step = None

    wanted = {
        "/utlidar/imu",
        "/utlidar/cloud",
        "/utlidar/robot_odom",
    }
    with AnyReader([path]) as reader:
        connections = [
            connection
            for connection in reader.connections
            if connection.topic in wanted
        ]
        for connection, _, rawdata in reader.messages(connections=connections):
            message = reader.deserialize(rawdata, connection.msgtype)
            if connection.topic == "/utlidar/imu":
                imu_times.append(stamp_seconds(message.header.stamp))
                imu_frames.add(message.header.frame_id)
                acceleration.append(
                    [
                        message.linear_acceleration.x,
                        message.linear_acceleration.y,
                        message.linear_acceleration.z,
                    ]
                )
                gyro.append(
                    [
                        message.angular_velocity.x,
                        message.angular_velocity.y,
                        message.angular_velocity.z,
                    ]
                )
                imu_quaternion.append(
                    [
                        message.orientation.x,
                        message.orientation.y,
                        message.orientation.z,
                        message.orientation.w,
                    ]
                )
            elif connection.topic == "/utlidar/robot_odom":
                odom_times.append(stamp_seconds(message.header.stamp))
                odom_angular.append(
                    [
                        message.twist.twist.angular.x,
                        message.twist.twist.angular.y,
                        message.twist.twist.angular.z,
                    ]
                )
                odom_quaternion.append(
                    [
                        message.pose.pose.orientation.x,
                        message.pose.pose.orientation.y,
                        message.pose.pose.orientation.z,
                        message.pose.pose.orientation.w,
                    ]
                )
            else:
                cloud_times.append(stamp_seconds(message.header.stamp))
                cloud_frames.add(message.header.frame_id)
                if fields is None:
                    fields = [
                        {
                            "name": field.name,
                            "datatype": int(field.datatype),
                            "offset": int(field.offset),
                            "count": int(field.count),
                        }
                        for field in message.fields
                    ]
                    point_step = int(message.point_step)
                field_map = {field.name: field for field in message.fields}
                count = int(message.width * message.height)
                cloud_counts.append(count)
                byte_order = ">" if message.is_bigendian else "<"
                dtype = np.dtype(
                    {
                        "names": ["time", "ring"],
                        "formats": [byte_order + "f4", byte_order + "u2"],
                        "offsets": [
                            field_map["time"].offset,
                            field_map["ring"].offset,
                        ],
                        "itemsize": int(message.point_step),
                    }
                )
                points = np.frombuffer(
                    memoryview(message.data), dtype=dtype, count=count
                )
                times = points["time"].astype(np.float64, copy=False)
                rings = points["ring"]
                diffs = np.diff(times)
                negative = diffs < -1e-9
                count_negative = int(np.count_nonzero(negative))
                negative_jumps += count_negative
                if count_negative:
                    frames_with_decrease += 1
                    negative_jump_magnitude_ms.extend(
                        (-diffs[negative] * 1000.0).tolist()
                    )
                cloud_spans.append(float(np.max(times) - np.min(times)))
                ring_unique.append(int(len(np.unique(rings))))
                ring_min.append(int(np.min(rings)))
                ring_max.append(int(np.max(rings)))

    imu_times = np.asarray(imu_times, dtype=np.float64)
    acceleration = np.asarray(acceleration, dtype=np.float64)
    gyro = np.asarray(gyro, dtype=np.float64)
    imu_quaternion = np.asarray(imu_quaternion, dtype=np.float64)
    odom_times = np.asarray(odom_times, dtype=np.float64)
    odom_angular = np.asarray(odom_angular, dtype=np.float64)
    odom_quaternion = np.asarray(odom_quaternion, dtype=np.float64)
    cloud_times = np.asarray(cloud_times, dtype=np.float64)
    if not len(imu_times) or not len(cloud_times):
        raise RuntimeError(f"required messages missing in {path}")

    # Exclude 10% at each end from static hold statistics.
    lo = imu_times[0] + 0.1 * (imu_times[-1] - imu_times[0])
    hi = imu_times[-1] - 0.1 * (imu_times[-1] - imu_times[0])
    middle = (imu_times >= lo) & (imu_times <= hi)
    imu_dt = np.diff(imu_times)
    cloud_dt = np.diff(cloud_times)
    gyro_integral = np.trapezoid(gyro, imu_times, axis=0)
    result = {
        "bag": str(path),
        "imu": {
            "frames": sorted(imu_frames),
            "samples": int(len(imu_times)),
            "duration_seconds": float(imu_times[-1] - imu_times[0]),
            "frequency_hz": float(
                (len(imu_times) - 1) / (imu_times[-1] - imu_times[0])
            ),
            "timestamp_backward": int(np.count_nonzero(imu_dt < 0.0)),
            "acceleration_middle_80_percent": vector_stats(
                acceleration[middle]
            ),
            "angular_velocity_middle_80_percent": vector_stats(gyro[middle]),
            "orientation_euler_degrees_middle_80_percent": vector_stats(
                quaternion_to_euler_degrees(imu_quaternion[middle])
            ),
            "orientation_quaternion_norm": scalar_stats(
                np.linalg.norm(imu_quaternion, axis=1)
            ),
            "gyro_integral_radians_xyz": gyro_integral.tolist(),
        },
        "cloud": {
            "frames": sorted(cloud_frames),
            "samples": int(len(cloud_times)),
            "frequency_hz": float(
                (len(cloud_times) - 1)
                / (cloud_times[-1] - cloud_times[0])
            ),
            "timestamp_backward": int(np.count_nonzero(cloud_dt < 0.0)),
            "fields": fields,
            "point_step": point_step,
            "points_per_frame": scalar_stats(cloud_counts),
            "point_time_span_ms": scalar_stats(
                np.asarray(cloud_spans) * 1000.0
            ),
            "frames_with_time_decrease": frames_with_decrease,
            "negative_time_jumps": negative_jumps,
            "negative_jump_magnitude_ms": scalar_stats(
                negative_jump_magnitude_ms
            ),
            "ring_unique_per_frame": scalar_stats(ring_unique),
            "ring_min": scalar_stats(ring_min),
            "ring_max": scalar_stats(ring_max),
        },
    }
    if len(odom_times):
        odom_integral = np.trapezoid(odom_angular, odom_times, axis=0)
        odom_lo = odom_times[0] + 0.1 * (odom_times[-1] - odom_times[0])
        odom_hi = odom_times[-1] - 0.1 * (odom_times[-1] - odom_times[0])
        odom_middle = (odom_times >= odom_lo) & (odom_times <= odom_hi)
        result["odom"] = {
            "samples": int(len(odom_times)),
            "angular_velocity": vector_stats(odom_angular),
            "angular_integral_radians_xyz": odom_integral.tolist(),
            "orientation_euler_degrees_middle_80_percent": vector_stats(
                quaternion_to_euler_degrees(odom_quaternion[odom_middle])
            ),
        }
    return result


def axis_name(index):
    return "xyz"[int(index)]


def compare_segments(segments):
    static_labels = [
        "level_static",
        "pitch_nose_down_hold",
        "pitch_nose_up_hold",
        "roll_left_down_hold",
        "roll_right_down_hold",
    ]
    means = {
        label: np.asarray(
            result["imu"]["acceleration_middle_80_percent"]["mean"],
            dtype=np.float64,
        )
        for label, result in segments.items()
    }
    pitch_delta = means["pitch_nose_down_hold"] - means[
        "pitch_nose_up_hold"
    ]
    roll_delta = means["roll_left_down_hold"] - means[
        "roll_right_down_hold"
    ]

    ccw_integral = np.asarray(
        segments["yaw_ccw_manual"]["imu"]["gyro_integral_radians_xyz"]
    )
    cw_integral = np.asarray(
        segments["yaw_cw_manual"]["imu"]["gyro_integral_radians_xyz"]
    )
    yaw_contrast = ccw_integral - cw_integral
    pitch_axis = int(np.argmax(np.abs(pitch_delta)))
    roll_axis = int(np.argmax(np.abs(roll_delta)))
    yaw_axis = int(np.argmax(np.abs(yaw_contrast)))
    static_acceleration = np.asarray(
        [means[label] for label in static_labels], dtype=np.float64
    )
    static_norms = np.linalg.norm(static_acceleration, axis=1)
    static_imu_pitch_degrees = np.asarray(
        [
            segments[label]["imu"][
                "orientation_euler_degrees_middle_80_percent"
            ]["mean"][1]
            for label in static_labels
        ],
        dtype=np.float64,
    )
    tangent_pitch = np.tan(np.radians(static_imu_pitch_degrees))
    design = np.column_stack(
        [tangent_pitch, np.ones_like(tangent_pitch)]
    )
    coefficients, _, _, _ = np.linalg.lstsq(
        design, static_acceleration[:, 0], rcond=None
    )
    predicted_ax = design @ coefficients
    residual_ax = static_acceleration[:, 0] - predicted_ax
    total_ax = static_acceleration[:, 0] - np.mean(
        static_acceleration[:, 0]
    )
    r_squared = 1.0 - (
        float(np.dot(residual_ax, residual_ax))
        / float(np.dot(total_ax, total_ax))
    )
    return {
        "gravity_means_xyz": {
            label: value.tolist() for label, value in means.items()
        },
        "pitch_nose_down_minus_up": pitch_delta.tolist(),
        "pitch_dominant_imu_axis": axis_name(pitch_axis),
        "pitch_axis_sign_for_nose_down_vs_up": (
            "positive" if pitch_delta[pitch_axis] > 0 else "negative"
        ),
        "roll_left_down_minus_right_down": roll_delta.tolist(),
        "roll_dominant_imu_axis": axis_name(roll_axis),
        "roll_axis_sign_for_left_down_vs_right_down": (
            "positive" if roll_delta[roll_axis] > 0 else "negative"
        ),
        "yaw_ccw_gyro_integral_xyz": ccw_integral.tolist(),
        "yaw_cw_gyro_integral_xyz": cw_integral.tolist(),
        "yaw_ccw_minus_cw_integral": yaw_contrast.tolist(),
        "yaw_dominant_imu_axis": axis_name(yaw_axis),
        "yaw_axis_sign_for_ccw_vs_cw": (
            "positive" if yaw_contrast[yaw_axis] > 0 else "negative"
        ),
        "distinct_dominant_axes": (
            len({pitch_axis, roll_axis, yaw_axis}) == 3
        ),
        "level_gravity_norm": float(
            np.linalg.norm(means["level_static"])
        ),
        "level_gravity_tilt_from_positive_z_degrees": float(
            math.degrees(
                math.atan2(
                    math.hypot(
                        means["level_static"][0],
                        means["level_static"][1],
                    ),
                    means["level_static"][2],
                )
            )
        ),
        "static_specific_force_invariance_check": {
            "segments": static_labels,
            "acceleration_means_xyz": static_acceleration.tolist(),
            "acceleration_norms": static_norms.tolist(),
            "z_mean": float(np.mean(static_acceleration[:, 2])),
            "z_std_across_segment_means": float(
                np.std(static_acceleration[:, 2])
            ),
            "imu_pitch_degrees": static_imu_pitch_degrees.tolist(),
            "fit_ax_equals_a_tan_pitch_plus_b": {
                "a": float(coefficients[0]),
                "b": float(coefficients[1]),
                "r_squared": r_squared,
                "rmse_meters_per_second_squared": float(
                    np.sqrt(np.mean(residual_ax * residual_ax))
                ),
            },
            "expected_for_raw_stationary_specific_force": (
                "vector norm remains approximately g as orientation changes"
            ),
        },
    }


def main():
    if len(sys.argv) != 3:
        raise SystemExit(
            "usage: phase548_analyze_segments.py BAG_ROOT OUTPUT_JSON"
        )
    root = Path(sys.argv[1])
    output = Path(sys.argv[2])
    segments = {
        label: read_segment(locate_bag(root, label)) for label in LABELS
    }
    result = {
        "phase": "5.4.8",
        "segments": segments,
        "axis_comparison": compare_segments(segments),
    }
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "segments": list(segments),
                "axis_comparison": result["axis_comparison"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
