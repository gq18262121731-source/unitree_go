#!/usr/bin/env python3
"""Build a strictly-offline Go2 community-adapter Point-LIO input bag.

This reproduces the calibration math in jizhang-cmu/autonomy_stack_go2 and
the sensor transformations consumed by its Go2 Point-LIO launch.  It never
imports ROS clients, opens sockets, or publishes messages.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
from rosbags.highlevel import AnyReader
from rosbags.rosbag2 import Writer
from rosbags.typesys import Stores, get_typestore


SOURCE_REPOSITORY = "https://github.com/jizhang-cmu/autonomy_stack_go2"
SOURCE_BRANCH = "foxy-humble"
SOURCE_COMMIT = "43d5f54b389b251713f0097893c30fa76c870d54"
SOURCE_CALIBRATION = (
    "src/utilities/calibrate_imu/src/calibrate_imu.cpp"
)
SOURCE_TRANSFORM = (
    "src/utilities/transform_sensors/transform_sensors/transform_everything.py"
)

ANGLE_DEGREES = 15.1
ANGLE_RADIANS = math.radians(ANGLE_DEGREES)
CAM_OFFSET_METERS = 0.046825
POINTCLOUD_PITCH_RADIANS = 2.87820258505555555556


@dataclass(frozen=True)
class Calibration:
    acc_bias_x: float
    acc_bias_y: float
    acc_bias_z: float
    ang_bias_x: float
    ang_bias_y: float
    ang_bias_z: float
    ang_z2x_proj: float
    ang_z2y_proj: float
    static_samples: int
    yaw_samples: int
    yaw_integrated_z_radians: float


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def bag_db3_files(path: Path) -> list[Path]:
    return sorted(path.glob("*.db3"))


def vector_transform(values: np.ndarray) -> np.ndarray:
    """Apply the exact Go2 IMU axis/sign and 15.1-degree transformation."""
    c = math.cos(ANGLE_RADIANS)
    s = math.sin(ANGLE_RADIANS)
    output = np.empty_like(values, dtype=np.float64)
    output[:, 0] = c * values[:, 0] + s * values[:, 2]
    output[:, 1] = -values[:, 1]
    output[:, 2] = s * values[:, 0] - c * values[:, 2]
    return output


def load_imu(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    typestore = get_typestore(Stores.ROS2_HUMBLE)
    timestamps: list[int] = []
    acceleration: list[tuple[float, float, float]] = []
    angular_velocity: list[tuple[float, float, float]] = []
    with AnyReader([path], default_typestore=typestore) as reader:
        connections = [c for c in reader.connections if c.topic == "/utlidar/imu"]
        if not connections:
            raise RuntimeError(f"No /utlidar/imu in {path}")
        for connection, timestamp, raw in reader.messages(connections=connections):
            msg = reader.deserialize(raw, connection.msgtype)
            timestamps.append(timestamp)
            acceleration.append(
                (msg.linear_acceleration.x, msg.linear_acceleration.y, msg.linear_acceleration.z)
            )
            angular_velocity.append(
                (msg.angular_velocity.x, msg.angular_velocity.y, msg.angular_velocity.z)
            )
    return (
        np.asarray(timestamps, dtype=np.int64),
        np.asarray(acceleration, dtype=np.float64),
        np.asarray(angular_velocity, dtype=np.float64),
    )


def derive_calibration(static_bag: Path, yaw_ccw_bag: Path) -> tuple[Calibration, dict]:
    static_t, static_acc, static_gyro = load_imu(static_bag)
    elapsed = (static_t - static_t[0]) / 1e9
    static_mask = (elapsed >= 5.0) & (elapsed <= 15.0)
    if int(static_mask.sum()) < 100:
        raise RuntimeError("Static calibration window contains too few samples")

    transformed_acc = vector_transform(static_acc[static_mask])
    transformed_gyro = vector_transform(static_gyro[static_mask])
    acc_mean = transformed_acc.mean(axis=0)
    gyro_mean = transformed_gyro.mean(axis=0)

    yaw_t, _, yaw_gyro = load_imu(yaw_ccw_bag)
    yaw_corrected = vector_transform(yaw_gyro) - gyro_mean
    yaw_mean = yaw_corrected.mean(axis=0)
    if abs(yaw_mean[2]) < 1e-6:
        raise RuntimeError("Yaw calibration has near-zero transformed z rate")
    if yaw_mean[2] <= 0:
        raise RuntimeError("Selected yaw bag is not the positive-z rotation expected by calibration")

    yaw_seconds = (yaw_t - yaw_t[0]) / 1e9
    integrated_z = float(np.trapezoid(yaw_corrected[:, 2], yaw_seconds))
    calibration = Calibration(
        acc_bias_x=float(acc_mean[0]),
        acc_bias_y=float(acc_mean[1]),
        acc_bias_z=float(acc_mean[2] - 9.81),
        ang_bias_x=float(gyro_mean[0]),
        ang_bias_y=float(gyro_mean[1]),
        ang_bias_z=float(gyro_mean[2]),
        ang_z2x_proj=float(-yaw_mean[0] / yaw_mean[2]),
        ang_z2y_proj=float(-yaw_mean[1] / yaw_mean[2]),
        static_samples=int(static_mask.sum()),
        yaw_samples=int(yaw_t.size),
        yaw_integrated_z_radians=integrated_z,
    )
    diagnostics = {
        "static_transformed_acc_mean": acc_mean.tolist(),
        "static_transformed_gyro_mean": gyro_mean.tolist(),
        "yaw_corrected_gyro_mean": yaw_mean.tolist(),
    }
    return calibration, diagnostics


def field_offset(msg, name: str) -> int:
    for field in msg.fields:
        if field.name == name:
            if field.datatype != 7 or field.count != 1:  # FLOAT32
                raise RuntimeError(f"Point field {name} is not one FLOAT32")
            return int(field.offset)
    raise RuntimeError(f"PointCloud2 is missing {name}")


def transform_cloud(msg):
    result = copy.deepcopy(msg)
    raw = msg.data.tobytes()
    npoints = int(msg.width * msg.height)
    expected = npoints * int(msg.point_step)
    if len(raw) < expected:
        raise RuntimeError("PointCloud2 data is shorter than dimensions require")

    x_offset = field_offset(msg, "x")
    y_offset = field_offset(msg, "y")
    z_offset = field_offset(msg, "z")
    kwargs = {"shape": (npoints,), "dtype": "<f4", "buffer": raw, "strides": (msg.point_step,)}
    x = np.ndarray(offset=x_offset, **kwargs).astype(np.float64)
    y = np.ndarray(offset=y_offset, **kwargs).astype(np.float64)
    z = np.ndarray(offset=z_offset, **kwargs).astype(np.float64)

    c = math.cos(POINTCLOUD_PITCH_RADIANS)
    s = math.sin(POINTCLOUD_PITCH_RADIANS)
    tx = c * x + s * z
    ty = y
    tz = -s * x + c * z - CAM_OFFSET_METERS

    # Exact exclusion box from transform_everything.py.
    inside_robot = (
        (tx > -0.7) & (tx < -0.1)
        & (ty > -0.3) & (ty < 0.3)
        & (tz > -0.646825) & (tz < -0.046825)
    )
    finite = np.isfinite(tx) & np.isfinite(ty) & np.isfinite(tz)
    keep = finite & ~inside_robot

    records = np.frombuffer(raw[:expected], dtype=np.dtype(f"V{msg.point_step}"), count=npoints)
    selected = records[keep].copy()
    out_count = int(selected.size)
    out_x = np.ndarray(
        shape=(out_count,), dtype="<f4", buffer=selected, offset=x_offset,
        strides=(msg.point_step,)
    )
    out_y = np.ndarray(
        shape=(out_count,), dtype="<f4", buffer=selected, offset=y_offset,
        strides=(msg.point_step,)
    )
    out_z = np.ndarray(
        shape=(out_count,), dtype="<f4", buffer=selected, offset=z_offset,
        strides=(msg.point_step,)
    )
    out_x[:] = tx[keep].astype(np.float32)
    out_y[:] = ty[keep].astype(np.float32)
    out_z[:] = tz[keep].astype(np.float32)

    result.header.frame_id = "body"
    result.height = 1
    result.width = out_count
    result.row_step = out_count * int(msg.point_step)
    result.data = np.frombuffer(selected.tobytes(), dtype=np.uint8).copy()
    result.is_dense = bool(msg.is_dense and finite.all())
    return result, npoints, out_count


def transform_imu(msg, calibration: Calibration):
    result = copy.deepcopy(msg)
    raw = np.array(
        [[msg.angular_velocity.x, msg.angular_velocity.y, msg.angular_velocity.z]],
        dtype=np.float64,
    )
    gyro = vector_transform(raw)[0]
    gyro -= np.array(
        [calibration.ang_bias_x, calibration.ang_bias_y, calibration.ang_bias_z]
    )
    gyro[0] += calibration.ang_z2x_proj * gyro[2]
    gyro[1] += calibration.ang_z2y_proj * gyro[2]

    result.header.frame_id = "body"
    result.orientation.x = 0.0
    result.orientation.y = 0.0
    result.orientation.z = 0.0
    result.orientation.w = 1.0
    result.angular_velocity.x = float(gyro[0])
    result.angular_velocity.y = float(gyro[1])
    result.angular_velocity.z = float(gyro[2])
    # This is intentional and matches the community stream consumed by Point-LIO.
    result.linear_acceleration.x = 0.0
    result.linear_acceleration.y = 0.0
    result.linear_acceleration.z = 0.0
    return result


def write_yaml(path: Path, calibration: Calibration) -> None:
    lines = [
        "# EXPERIMENTAL OFFLINE REPRODUCTION ONLY",
        "# Not official calibration; do not use to authorize online motion.",
        f"# Source: {SOURCE_REPOSITORY}@{SOURCE_COMMIT}",
    ]
    for key, value in asdict(calibration).items():
        lines.append(f"{key}: {value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_bag(input_bag: Path, output_bag: Path, calibration: Calibration) -> dict:
    if output_bag.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {output_bag}")
    output_bag.parent.mkdir(parents=True, exist_ok=True)
    typestore = get_typestore(Stores.ROS2_HUMBLE)
    counts = {
        "/utlidar/transformed_cloud": 0,
        "/utlidar/transformed_imu": 0,
        "/utlidar/robot_odom": 0,
        "/odom": 0,
    }
    points_in = 0
    points_out = 0

    try:
        with AnyReader([input_bag], default_typestore=typestore) as reader, Writer(output_bag) as writer:
            source_by_topic = {c.topic: c for c in reader.connections}
            required = ["/utlidar/cloud", "/utlidar/imu"]
            missing = [topic for topic in required if topic not in source_by_topic]
            if missing:
                raise RuntimeError(f"Input bag lacks required topics: {missing}")

            outputs = {
                "/utlidar/transformed_cloud": writer.add_connection(
                    "/utlidar/transformed_cloud",
                    source_by_topic["/utlidar/cloud"].msgtype,
                    typestore=typestore,
                ),
                "/utlidar/transformed_imu": writer.add_connection(
                    "/utlidar/transformed_imu",
                    source_by_topic["/utlidar/imu"].msgtype,
                    typestore=typestore,
                ),
            }
            for topic in ("/utlidar/robot_odom", "/odom"):
                if topic in source_by_topic:
                    outputs[topic] = writer.add_connection(
                        topic, source_by_topic[topic].msgtype, typestore=typestore
                    )

            selected_connections = [
                source_by_topic[t]
                for t in ("/utlidar/cloud", "/utlidar/imu", "/utlidar/robot_odom", "/odom")
                if t in source_by_topic
            ]
            for connection, timestamp, raw in reader.messages(connections=selected_connections):
                if connection.topic == "/utlidar/cloud":
                    msg = reader.deserialize(raw, connection.msgtype)
                    transformed, before, after = transform_cloud(msg)
                    payload = typestore.serialize_cdr(transformed, connection.msgtype)
                    writer.write(outputs["/utlidar/transformed_cloud"], timestamp, payload)
                    counts["/utlidar/transformed_cloud"] += 1
                    points_in += before
                    points_out += after
                elif connection.topic == "/utlidar/imu":
                    msg = reader.deserialize(raw, connection.msgtype)
                    transformed = transform_imu(msg, calibration)
                    payload = typestore.serialize_cdr(transformed, connection.msgtype)
                    writer.write(outputs["/utlidar/transformed_imu"], timestamp, payload)
                    counts["/utlidar/transformed_imu"] += 1
                else:
                    writer.write(outputs[connection.topic], timestamp, raw)
                    counts[connection.topic] += 1
    except Exception:
        # The output is newly created by this script and incomplete on failure.
        if output_bag.exists():
            shutil.rmtree(output_bag)
        raise

    return {
        "message_counts": counts,
        "cloud_points_input": points_in,
        "cloud_points_output": points_out,
        "cloud_keep_ratio": points_out / points_in if points_in else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--static-bag", type=Path, required=True)
    parser.add_argument("--yaw-ccw-bag", type=Path, required=True)
    parser.add_argument("--input-bag", type=Path, required=True)
    parser.add_argument("--output-bag", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args()

    for label, path in (
        ("static", args.static_bag),
        ("yaw_ccw", args.yaw_ccw_bag),
        ("input", args.input_bag),
    ):
        if not path.is_dir():
            raise FileNotFoundError(f"{label} bag directory not found: {path}")

    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    calibration, calibration_diagnostics = derive_calibration(
        args.static_bag, args.yaw_ccw_bag
    )
    yaml_path = args.artifact_dir / "phase672_experimental_imu_calib_data.yaml"
    write_yaml(yaml_path, calibration)
    transform_stats = build_bag(args.input_bag, args.output_bag, calibration)

    manifest = {
        "phase": "6.7.2",
        "mode": "strictly_offline_community_adapter_reproduction",
        "source": {
            "repository": SOURCE_REPOSITORY,
            "branch": SOURCE_BRANCH,
            "commit": SOURCE_COMMIT,
            "calibration_path": SOURCE_CALIBRATION,
            "transform_path": SOURCE_TRANSFORM,
        },
        "safety": {
            "robot_connected": False,
            "ros_publishers_created": False,
            "motion_api_called": False,
            "cmd_vel_published": False,
            "online_calibration_node_run": False,
        },
        "interpretation": (
            "Full community Go2 adapter reproduction: cloud transform/filter plus "
            "gyro transform/calibration and zero acceleration in the Point-LIO IMU stream. "
            "This is not proof that the device acceleration field has been physically corrected."
        ),
        "inputs": {
            "static_bag": str(args.static_bag),
            "yaw_ccw_bag": str(args.yaw_ccw_bag),
            "pointlio_failure_bag": str(args.input_bag),
            "input_db3_sha256": {
                path.name: sha256_file(path) for path in bag_db3_files(args.input_bag)
            },
        },
        "calibration": asdict(calibration),
        "calibration_diagnostics": calibration_diagnostics,
        "transform": {
            "imu_axis_rotation_degrees": ANGLE_DEGREES,
            "cloud_pitch_radians": POINTCLOUD_PITCH_RADIANS,
            "cloud_z_offset_meters": -CAM_OFFSET_METERS,
            "pointlio_imu_linear_acceleration": [0.0, 0.0, 0.0],
            **transform_stats,
        },
        "outputs": {
            "bag": str(args.output_bag),
            "db3_sha256": {
                path.name: sha256_file(path) for path in bag_db3_files(args.output_bag)
            },
            "experimental_calibration_yaml": str(yaml_path),
        },
    }
    manifest_path = args.artifact_dir / "phase672_transform_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
