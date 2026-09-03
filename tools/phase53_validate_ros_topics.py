#!/usr/bin/env python3

import json
import sys
import time
from dataclasses import dataclass, field

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Imu, PointCloud2


def stamp_ns(message) -> int:
    stamp = message.header.stamp
    return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)


@dataclass
class TopicStats:
    source_stamps: set[int] = field(default_factory=set)
    target_stamps: set[int] = field(default_factory=set)
    target_count: int = 0
    target_backward_jumps: int = 0
    target_zero_stamps: int = 0
    last_target_stamp: int | None = None
    frames: set[str] = field(default_factory=set)

    def record_source(self, message) -> None:
        self.source_stamps.add(stamp_ns(message))

    def record_target(self, message) -> None:
        current = stamp_ns(message)
        self.target_count += 1
        self.target_stamps.add(current)
        self.frames.add(message.header.frame_id)
        if current == 0:
            self.target_zero_stamps += 1
        if self.last_target_stamp is not None and current < self.last_target_stamp:
            self.target_backward_jumps += 1
        self.last_target_stamp = current

    def report(self, elapsed: float) -> dict:
        matched = len(self.source_stamps & self.target_stamps)
        match_ratio = matched / len(self.target_stamps) if self.target_stamps else 0.0
        return {
            "target_samples": self.target_count,
            "target_hz": self.target_count / elapsed,
            "unique_target_stamps": len(self.target_stamps),
            "source_target_stamp_matches": matched,
            "source_target_stamp_match_ratio": match_ratio,
            "target_backward_jumps": self.target_backward_jumps,
            "target_zero_stamps": self.target_zero_stamps,
            "frames": sorted(self.frames),
        }


class BridgeValidator(Node):
    def __init__(self) -> None:
        super().__init__("phase53_bridge_validator")
        self.stats = {
            "lidar": TopicStats(),
            "imu": TopicStats(),
            "odom": TopicStats(),
        }
        self.reader_handles = [
            self.create_subscription(
                PointCloud2,
                "/utlidar/cloud",
                self.stats["lidar"].record_source,
                qos_profile_sensor_data,
            ),
            self.create_subscription(
                PointCloud2,
                "/sensor/lidar",
                self.stats["lidar"].record_target,
                qos_profile_sensor_data,
            ),
            self.create_subscription(
                Imu,
                "/utlidar/imu",
                self.stats["imu"].record_source,
                qos_profile_sensor_data,
            ),
            self.create_subscription(
                Imu,
                "/sensor/imu",
                self.stats["imu"].record_target,
                qos_profile_sensor_data,
            ),
            self.create_subscription(
                Odometry,
                "/utlidar/robot_odom",
                self.stats["odom"].record_source,
                qos_profile_sensor_data,
            ),
            self.create_subscription(
                Odometry,
                "/odom",
                self.stats["odom"].record_target,
                qos_profile_sensor_data,
            ),
        ]


def main() -> int:
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    rclpy.init()
    node = BridgeValidator()
    started = time.monotonic()
    try:
        while rclpy.ok() and time.monotonic() - started < duration:
            rclpy.spin_once(node, timeout_sec=0.1)
    finally:
        elapsed = time.monotonic() - started
        report = {
            name: stats.report(elapsed)
            for name, stats in node.stats.items()
        }
        report["duration_seconds"] = elapsed
        report["publisher_count"] = 0
        print(json.dumps(report, indent=2, sort_keys=True))
        node.destroy_node()
        rclpy.shutdown()

    passed = all(
        values["target_samples"] > 0
        and values["target_backward_jumps"] == 0
        and values["target_zero_stamps"] == 0
        and values["source_target_stamp_match_ratio"] >= 0.90
        for name, values in report.items()
        if name in {"lidar", "imu", "odom"}
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
