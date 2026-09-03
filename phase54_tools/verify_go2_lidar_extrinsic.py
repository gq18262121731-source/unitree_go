#!/usr/bin/env python3
"""Validate the official Go2 URDF lidar pose against onboard cloud_base."""

import json
import math
import time

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import PointCloud2, PointField


OFFICIAL_BASE_TO_RADAR_XYZ = np.array([0.28945, 0.0, -0.046825])
OFFICIAL_BASE_TO_RADAR_RPY = np.array([0.0, 2.8782, 0.0])


def stamp_ns(msg):
    return int(msg.header.stamp.sec) * 1_000_000_000 + int(msg.header.stamp.nanosec)


def rotation_from_rpy(roll, pitch, yaw):
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    return rz @ ry @ rx


def xyz_array(msg):
    fields = {field.name: field for field in msg.fields}
    for name in ("x", "y", "z"):
        if name not in fields or fields[name].datatype != PointField.FLOAT32:
            raise ValueError(f"{name} must be FLOAT32")

    dtype = np.dtype(
        {
            "names": ["x", "y", "z"],
            "formats": ["<f4", "<f4", "<f4"],
            "offsets": [fields[name].offset for name in ("x", "y", "z")],
            "itemsize": msg.point_step,
        }
    )
    structured = np.ndarray(
        shape=(msg.height, msg.width),
        dtype=dtype,
        buffer=msg.data,
        strides=(msg.row_step, msg.point_step),
    )
    return np.column_stack(
        (
            structured["x"].reshape(-1),
            structured["y"].reshape(-1),
            structured["z"].reshape(-1),
        )
    ).astype(np.float64, copy=False)


class Validator(Node):
    def __init__(self):
        super().__init__("phase54_go2_lidar_extrinsic_validator")
        self.raw = {}
        self.base = {}
        self.result = None
        self.create_subscription(
            PointCloud2, "/utlidar/cloud", self.on_raw, qos_profile_sensor_data
        )
        self.create_subscription(
            PointCloud2, "/utlidar/cloud_base", self.on_base, qos_profile_sensor_data
        )

    def on_raw(self, msg):
        self.raw[stamp_ns(msg)] = msg
        self.try_match(stamp_ns(msg))
        self.trim()

    def on_base(self, msg):
        self.base[stamp_ns(msg)] = msg
        self.try_match(stamp_ns(msg))
        self.trim()

    def trim(self):
        for cache in (self.raw, self.base):
            while len(cache) > 10:
                del cache[min(cache)]

    def try_match(self, key):
        if self.result is not None or key not in self.raw or key not in self.base:
            return
        raw_msg, base_msg = self.raw[key], self.base[key]
        raw_xyz, base_xyz = xyz_array(raw_msg), xyz_array(base_msg)
        rotation = rotation_from_rpy(*OFFICIAL_BASE_TO_RADAR_RPY)
        expected = raw_xyz @ rotation.T + OFFICIAL_BASE_TO_RADAR_XYZ
        expected = expected[np.isfinite(expected).all(axis=1)]
        base_valid = base_xyz[np.isfinite(base_xyz).all(axis=1)]
        nearest = []
        for begin in range(0, base_valid.shape[0], 256):
            chunk = base_valid[begin : begin + 256]
            squared = np.sum(
                (chunk[:, np.newaxis, :] - expected[np.newaxis, :, :]) ** 2,
                axis=2,
            )
            nearest.append(np.sqrt(np.min(squared, axis=1)))
        errors = np.concatenate(nearest) if nearest else np.array([])
        self.result = {
            "pass": bool(errors.size and np.sqrt(np.mean(errors**2)) <= 0.002),
            "source": (
                "unitreerobotics/unitree_ros "
                "robots/go2_description/urdf/go2_description.urdf"
            ),
            "source_joint": "radar_joint",
            "base_to_radar_xyz_m": OFFICIAL_BASE_TO_RADAR_XYZ.tolist(),
            "base_to_radar_rpy_rad": OFFICIAL_BASE_TO_RADAR_RPY.tolist(),
            "raw_frame": raw_msg.header.frame_id,
            "base_frame": base_msg.header.frame_id,
            "matched_stamp_ns": key,
            "raw_points": int(raw_xyz.shape[0]),
            "base_points": int(base_xyz.shape[0]),
            "comparison": "nearest transformed raw point for each cloud_base point",
            "valid_base_points": int(errors.size),
            "error_mean_m": float(np.mean(errors)),
            "error_rms_m": float(np.sqrt(np.mean(errors**2))),
            "error_p95_m": float(np.percentile(errors, 95)),
            "error_max_m": float(np.max(errors)),
            "threshold_rms_m": 0.002,
        }


def main():
    rclpy.init()
    node = Validator()
    deadline = time.monotonic() + 15.0
    try:
        while rclpy.ok() and node.result is None and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.2)
        result = node.result or {
            "pass": False,
            "reason": "no timestamp-matched raw/cloud_base pair within 15 seconds",
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        raise SystemExit(0 if result["pass"] else 1)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
