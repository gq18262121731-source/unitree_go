#!/usr/bin/env python3
import json
import math
import sys
from pathlib import Path

import numpy as np
from rosbags.highlevel import AnyReader


def stamp_seconds(stamp) -> float:
    nanosec = getattr(stamp, "nanosec", getattr(stamp, "nsec", 0))
    return float(stamp.sec) + float(nanosec) * 1e-9


def scalar_stats(values):
    array = np.asarray(values, dtype=np.float64)
    if not len(array):
        return None
    return {
        "samples": int(len(array)),
        "min": float(np.min(array)),
        "p05": float(np.percentile(array, 5)),
        "median": float(np.median(array)),
        "mean": float(np.mean(array)),
        "p95": float(np.percentile(array, 95)),
        "max": float(np.max(array)),
        "std": float(np.std(array)),
    }


def vector_stats(values):
    array = np.asarray(values, dtype=np.float64)
    return {
        "samples": int(len(array)),
        "mean": np.mean(array, axis=0).tolist(),
        "median": np.median(array, axis=0).tolist(),
        "std": np.std(array, axis=0).tolist(),
        "p05": np.percentile(array, 5, axis=0).tolist(),
        "p95": np.percentile(array, 95, axis=0).tolist(),
        "norm": scalar_stats(np.linalg.norm(array, axis=1)),
    }


def quaternion_to_euler(quaternions):
    q = np.asarray(quaternions, dtype=np.float64)
    x, y, z, w = q.T
    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = np.arctan2(sinr_cosp, cosr_cosp)
    sinp = np.clip(2.0 * (w * y - z * x), -1.0, 1.0)
    pitch = np.arcsin(sinp)
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = np.arctan2(siny_cosp, cosy_cosp)
    return np.column_stack([roll, pitch, yaw])


def read_dataset(path, cloud_topic, imu_topic, odom_topic=None):
    imu_times = []
    imu_acc = []
    imu_gyro = []
    imu_quat = []
    imu_frames = set()
    imu_covariances = []

    cloud_times = []
    cloud_frames = set()
    cloud_fields = None
    cloud_point_step = None
    cloud_counts = []
    cloud_time_min = []
    cloud_time_max = []
    cloud_time_span = []
    cloud_time_first = []
    cloud_time_last = []
    cloud_ring_min = []
    cloud_ring_max = []
    cloud_ring_unique = []
    cloud_ring_transitions = []
    total_time_pairs = 0
    total_time_negative_jumps = 0
    total_time_equal_pairs = 0
    frames_with_time_decrease = 0
    frames_with_nonfinite_time = 0
    frames_starting_near_zero = 0
    negative_time_jump_ms = []
    negative_jumps_per_frame = []
    cloud_time_max_minus_last_ms = []
    frames_last_time_not_max = 0
    cloud_frame_has_time_decrease = []

    odom_times = []
    odom_linear = []
    odom_angular = []
    odom_quat = []
    odom_frames = set()
    odom_child_frames = set()

    topics = [cloud_topic, imu_topic]
    if odom_topic:
        topics.append(odom_topic)

    with AnyReader([Path(path)]) as reader:
        connections = [
            connection
            for connection in reader.connections
            if connection.topic in topics
        ]
        for connection, _, rawdata in reader.messages(connections=connections):
            message = reader.deserialize(rawdata, connection.msgtype)
            if connection.topic == imu_topic:
                imu_times.append(stamp_seconds(message.header.stamp))
                imu_frames.add(message.header.frame_id)
                imu_acc.append(
                    [
                        message.linear_acceleration.x,
                        message.linear_acceleration.y,
                        message.linear_acceleration.z,
                    ]
                )
                imu_gyro.append(
                    [
                        message.angular_velocity.x,
                        message.angular_velocity.y,
                        message.angular_velocity.z,
                    ]
                )
                imu_quat.append(
                    [
                        message.orientation.x,
                        message.orientation.y,
                        message.orientation.z,
                        message.orientation.w,
                    ]
                )
                imu_covariances.append(
                    [
                        *message.orientation_covariance,
                        *message.angular_velocity_covariance,
                        *message.linear_acceleration_covariance,
                    ]
                )
            elif connection.topic == cloud_topic:
                cloud_times.append(stamp_seconds(message.header.stamp))
                cloud_frames.add(message.header.frame_id)
                if cloud_fields is None:
                    cloud_fields = [
                        {
                            "name": field.name,
                            "offset": int(field.offset),
                            "datatype": int(field.datatype),
                            "count": int(field.count),
                        }
                        for field in message.fields
                    ]
                    cloud_point_step = int(message.point_step)
                field_map = {field.name: field for field in message.fields}
                time_field = field_map["time"]
                ring_field = field_map["ring"]
                count = int(message.width * message.height)
                cloud_counts.append(count)
                byte_order = ">" if message.is_bigendian else "<"
                dtype = np.dtype(
                    {
                        "names": ["time", "ring"],
                        "formats": [byte_order + "f4", byte_order + "u2"],
                        "offsets": [time_field.offset, ring_field.offset],
                        "itemsize": message.point_step,
                    }
                )
                points = np.frombuffer(
                    memoryview(message.data), dtype=dtype, count=count
                )
                times = points["time"].astype(np.float64, copy=False)
                rings = points["ring"]
                finite = np.isfinite(times)
                if not np.all(finite):
                    frames_with_nonfinite_time += 1
                valid_times = times[finite]
                cloud_time_min.append(float(np.min(valid_times)))
                cloud_time_max.append(float(np.max(valid_times)))
                cloud_time_span.append(
                    float(np.max(valid_times) - np.min(valid_times))
                )
                cloud_time_first.append(float(times[0]))
                cloud_time_last.append(float(times[-1]))
                max_minus_last_ms = float(
                    (np.max(valid_times) - times[-1]) * 1000.0
                )
                cloud_time_max_minus_last_ms.append(max_minus_last_ms)
                if max_minus_last_ms > 1e-6:
                    frames_last_time_not_max += 1
                if abs(float(times[0])) < 1e-7:
                    frames_starting_near_zero += 1
                diffs = np.diff(times)
                total_time_pairs += int(len(diffs))
                negative = int(np.count_nonzero(diffs < -1e-9))
                negative_jumps_per_frame.append(negative)
                if negative:
                    negative_time_jump_ms.extend(
                        (-diffs[diffs < -1e-9] * 1000.0).tolist()
                    )
                total_time_negative_jumps += negative
                total_time_equal_pairs += int(
                    np.count_nonzero(np.abs(diffs) <= 1e-9)
                )
                if negative:
                    frames_with_time_decrease += 1
                cloud_frame_has_time_decrease.append(bool(negative))
                cloud_ring_min.append(int(np.min(rings)))
                cloud_ring_max.append(int(np.max(rings)))
                cloud_ring_unique.append(int(len(np.unique(rings))))
                cloud_ring_transitions.append(
                    int(np.count_nonzero(np.diff(rings.astype(np.int32))))
                )
            else:
                odom_times.append(stamp_seconds(message.header.stamp))
                odom_frames.add(message.header.frame_id)
                odom_child_frames.add(message.child_frame_id)
                odom_linear.append(
                    [
                        message.twist.twist.linear.x,
                        message.twist.twist.linear.y,
                        message.twist.twist.linear.z,
                    ]
                )
                odom_angular.append(
                    [
                        message.twist.twist.angular.x,
                        message.twist.twist.angular.y,
                        message.twist.twist.angular.z,
                    ]
                )
                odom_quat.append(
                    [
                        message.pose.pose.orientation.x,
                        message.pose.pose.orientation.y,
                        message.pose.pose.orientation.z,
                        message.pose.pose.orientation.w,
                    ]
                )

    imu_times = np.asarray(imu_times, dtype=np.float64)
    imu_acc = np.asarray(imu_acc, dtype=np.float64)
    imu_gyro = np.asarray(imu_gyro, dtype=np.float64)
    imu_quat = np.asarray(imu_quat, dtype=np.float64)
    cloud_times = np.asarray(cloud_times, dtype=np.float64)

    imu_dt = np.diff(imu_times)
    cloud_dt = np.diff(cloud_times)
    first_imu = imu_times[0]
    static_duration = 60.0 if odom_topic else 5.0
    static_mask = imu_times <= first_imu + static_duration
    static_acc = imu_acc[static_mask]
    static_gyro = imu_gyro[static_mask]
    static_quat = imu_quat[static_mask]
    static_acc_mean = np.mean(static_acc, axis=0)
    gravity_tilt_from_positive_z = math.degrees(
        math.atan2(
            math.hypot(static_acc_mean[0], static_acc_mean[1]),
            static_acc_mean[2],
        )
    )

    cloud_span = np.asarray(cloud_time_span)
    cloud_frame_has_time_decrease = np.asarray(
        cloud_frame_has_time_decrease, dtype=bool
    )
    cloud_relative_times = cloud_times - cloud_times[0]
    anomaly_by_window = {}
    for duration in [1.0, 5.0, 10.0, 60.0, 180.0]:
        window = cloud_relative_times <= duration
        anomaly_by_window[str(duration)] = {
            "frames": int(np.count_nonzero(window)),
            "frames_with_decrease": int(
                np.count_nonzero(cloud_frame_has_time_decrease[window])
            ),
        }
    period_median = float(np.median(cloud_dt))
    result = {
        "topics": {
            "cloud": cloud_topic,
            "imu": imu_topic,
            "odom": odom_topic,
        },
        "imu": {
            "frames": sorted(imu_frames),
            "samples": int(len(imu_times)),
            "duration_seconds": float(imu_times[-1] - imu_times[0]),
            "frequency_hz_from_duration": float(
                (len(imu_times) - 1) / (imu_times[-1] - imu_times[0])
            ),
            "interval_ms": scalar_stats(imu_dt * 1000.0),
            "timestamp_backward": int(np.count_nonzero(imu_dt < 0.0)),
            "timestamp_duplicates": int(np.count_nonzero(imu_dt == 0.0)),
            "static_window_seconds": static_duration,
            "static_acceleration": vector_stats(static_acc),
            "static_angular_velocity": vector_stats(static_gyro),
            "static_gravity_tilt_from_positive_z_degrees": (
                gravity_tilt_from_positive_z
            ),
            "orientation_quaternion_norm": scalar_stats(
                np.linalg.norm(imu_quat, axis=1)
            ),
            "static_orientation_euler_degrees": vector_stats(
                np.degrees(quaternion_to_euler(static_quat))
            ),
            "covariance_unique_rows": int(
                len(np.unique(np.asarray(imu_covariances), axis=0))
            ),
        },
        "cloud": {
            "frames": sorted(cloud_frames),
            "samples": int(len(cloud_times)),
            "duration_seconds": float(cloud_times[-1] - cloud_times[0]),
            "frequency_hz_from_duration": float(
                (len(cloud_times) - 1) / (cloud_times[-1] - cloud_times[0])
            ),
            "interval_ms": scalar_stats(cloud_dt * 1000.0),
            "timestamp_backward": int(np.count_nonzero(cloud_dt < 0.0)),
            "timestamp_duplicates": int(np.count_nonzero(cloud_dt == 0.0)),
            "fields": cloud_fields,
            "point_step": cloud_point_step,
            "points_per_frame": scalar_stats(cloud_counts),
            "point_time_min_seconds": scalar_stats(cloud_time_min),
            "point_time_max_seconds": scalar_stats(cloud_time_max),
            "point_time_span_ms": scalar_stats(cloud_span * 1000.0),
            "point_time_first_seconds": scalar_stats(cloud_time_first),
            "point_time_last_seconds": scalar_stats(cloud_time_last),
            "median_span_to_median_header_period_ratio": float(
                np.median(cloud_span) / period_median
            ),
            "frames_starting_near_zero": frames_starting_near_zero,
            "frames_last_time_not_max": frames_last_time_not_max,
            "max_time_minus_last_time_ms": scalar_stats(
                cloud_time_max_minus_last_ms
            ),
            "frames_with_nonfinite_time": frames_with_nonfinite_time,
            "time_order": {
                "total_adjacent_pairs": total_time_pairs,
                "negative_jumps": total_time_negative_jumps,
                "equal_pairs": total_time_equal_pairs,
                "nondecreasing_ratio": float(
                    1.0 - total_time_negative_jumps / total_time_pairs
                ),
                "frames_with_decrease": frames_with_time_decrease,
                "frames_with_decrease_by_initial_window_seconds": (
                    anomaly_by_window
                ),
                "negative_jumps_per_frame": scalar_stats(
                    negative_jumps_per_frame
                ),
                "negative_jump_magnitude_ms": scalar_stats(
                    negative_time_jump_ms
                ),
            },
            "ring_min": scalar_stats(cloud_ring_min),
            "ring_max": scalar_stats(cloud_ring_max),
            "ring_unique_per_frame": scalar_stats(cloud_ring_unique),
            "ring_transitions_per_frame": scalar_stats(
                cloud_ring_transitions
            ),
        },
    }

    if odom_topic:
        odom_times = np.asarray(odom_times, dtype=np.float64)
        odom_linear = np.asarray(odom_linear, dtype=np.float64)
        odom_angular = np.asarray(odom_angular, dtype=np.float64)
        odom_quat = np.asarray(odom_quat, dtype=np.float64)
        motion_start = first_imu + 180.0
        motion_mask = (
            (imu_times >= motion_start)
            & (imu_times >= odom_times[0])
            & (imu_times <= odom_times[-1])
        )
        interpolated_angular = np.column_stack(
            [
                np.interp(
                    imu_times[motion_mask],
                    odom_times,
                    odom_angular[:, axis],
                )
                for axis in range(3)
            ]
        )
        selected_gyro = imu_gyro[motion_mask]
        strong = np.linalg.norm(interpolated_angular, axis=1) >= 0.05
        selected_gyro = selected_gyro[strong]
        interpolated_angular = interpolated_angular[strong]
        correlation = np.corrcoef(
            np.column_stack([selected_gyro, interpolated_angular]).T
        )[:3, 3:]
        design = np.column_stack(
            [selected_gyro, np.ones(len(selected_gyro))]
        )
        coefficients, _, _, _ = np.linalg.lstsq(
            design, interpolated_angular, rcond=None
        )
        predicted = design @ coefficients
        residual = interpolated_angular - predicted
        rmse = np.sqrt(np.mean(residual * residual, axis=0))
        result["odom"] = {
            "frames": sorted(odom_frames),
            "child_frames": sorted(odom_child_frames),
            "samples": int(len(odom_times)),
            "linear_velocity": vector_stats(odom_linear),
            "angular_velocity": vector_stats(odom_angular),
            "motion_correlation_samples": int(len(selected_gyro)),
            "imu_gyro_rows_vs_odom_angular_columns_correlation": (
                correlation.tolist()
            ),
            "least_squares_odom_angular_from_imu_gyro": {
                "matrix_rows_imu_xyz_columns_odom_xyz": coefficients[
                    :3, :
                ].tolist(),
                "bias_odom_xyz": coefficients[3, :].tolist(),
                "rmse_odom_xyz": rmse.tolist(),
            },
            "odom_orientation_euler_degrees": vector_stats(
                np.degrees(quaternion_to_euler(odom_quat))
            ),
        }
    return result


def main():
    go2_bag = sys.argv[1]
    official_bag = sys.argv[2]
    output_path = sys.argv[3]
    result = {
        "go2": read_dataset(
            go2_bag,
            "/utlidar/cloud",
            "/utlidar/imu",
            "/utlidar/robot_odom",
        ),
        "official_sample": read_dataset(
            official_bag,
            "/unilidar/cloud",
            "/unilidar/imu",
        ),
    }
    Path(output_path).write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": output_path,
                "go2_cloud_frames": result["go2"]["cloud"]["samples"],
                "go2_imu_samples": result["go2"]["imu"]["samples"],
                "official_cloud_frames": result["official_sample"]["cloud"][
                    "samples"
                ],
                "official_imu_samples": result["official_sample"]["imu"][
                    "samples"
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
