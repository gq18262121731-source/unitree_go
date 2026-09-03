#!/usr/bin/env python3
"""Phase 5.4.3-B online read-only capture with corrected LidarState."""

import json
import sys
import time

import rclpy
from rclpy.qos import qos_profile_sensor_data
from unitree_go.msg import LidarState

from phase542_capture import Capture


class OnlineCapture(Capture):
    def __init__(self):
        super().__init__()
        self.lidar_states = []
        self.create_subscription(
            LidarState,
            "/utlidar/lidar_state",
            self.on_lidar_state,
            qos_profile_sensor_data,
        )

    def on_lidar_state(self, msg):
        self.lidar_states.append(
            {
                "receive_monotonic_ns": time.monotonic_ns(),
                "stamp": float(msg.stamp),
                "firmware_version": msg.firmware_version,
                "software_version": msg.software_version,
                "sdk_version": msg.sdk_version,
                "sys_rotation_speed": float(msg.sys_rotation_speed),
                "com_rotation_speed": float(msg.com_rotation_speed),
                "error_state": int(msg.error_state),
                "dirty_percentage": int(msg.dirty_percentage),
                "cloud_frequency": float(msg.cloud_frequency),
                "cloud_packet_loss_rate": float(msg.cloud_packet_loss_rate),
                "cloud_size": int(msg.cloud_size),
                "cloud_scan_num": int(msg.cloud_scan_num),
                "imu_frequency": float(msg.imu_frequency),
                "imu_packet_loss_rate": float(msg.imu_packet_loss_rate),
                "imu_rpy": [float(value) for value in msg.imu_rpy],
                "serial_recv_stamp": float(msg.serial_recv_stamp),
                "serial_buffer_size": int(msg.serial_buffer_size),
                "serial_buffer_read": int(msg.serial_buffer_read),
            }
        )

    def save(self, json_path, npz_path):
        super().save(json_path, npz_path)
        with open(json_path, "r", encoding="utf-8") as handle:
            result = json.load(handle)
        result["lidar_states"] = self.lidar_states
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, sort_keys=True)


def main():
    duration = float(sys.argv[1])
    prefix = sys.argv[2]
    rclpy.init()
    node = OnlineCapture()
    deadline = time.monotonic() + duration
    try:
        while rclpy.ok() and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.1)
        node.save(
            f"/home/go2/go2_validation/{prefix}_capture.json",
            f"/home/go2/go2_validation/{prefix}_clouds.npz",
        )
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
