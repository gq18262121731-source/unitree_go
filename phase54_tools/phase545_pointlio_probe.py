#!/usr/bin/env python3
"""Capture Point-LIO odometry from the isolated offline ROS domain."""

import json
import math
import signal
import sys
import time

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node


class PointLioProbe(Node):
    def __init__(self, output_path):
        super().__init__("phase545_pointlio_probe")
        self.output_path = output_path
        self.started_wall = time.time()
        self.samples = []
        self.subscription = self.create_subscription(
            Odometry, "/aft_mapped_to_init", self.on_odom, 1000
        )

    def on_odom(self, message):
        p = message.pose.pose.position
        q = message.pose.pose.orientation
        self.samples.append(
            {
                "stamp_ns": (
                    int(message.header.stamp.sec) * 1_000_000_000
                    + int(message.header.stamp.nanosec)
                ),
                "position": [float(p.x), float(p.y), float(p.z)],
                "orientation_xyzw": [
                    float(q.x),
                    float(q.y),
                    float(q.z),
                    float(q.w),
                ],
            }
        )

    def save(self):
        result = {
            "started_wall": self.started_wall,
            "ended_wall": time.time(),
            "samples": self.samples,
        }
        with open(self.output_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle)
        self.get_logger().info(
            f"saved {len(self.samples)} odometry samples to {self.output_path}"
        )


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: phase545_pointlio_probe.py OUTPUT_JSON")
    rclpy.init()
    node = PointLioProbe(sys.argv[1])
    stop = False

    def request_stop(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        while rclpy.ok() and not stop:
            rclpy.spin_once(node, timeout_sec=0.2)
    finally:
        node.save()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
