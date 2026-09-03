#!/usr/bin/env python3
"""Minimal local-only inter-process DDS discovery probe."""

import sys
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


def publisher() -> int:
    rclpy.init()
    node = Node("phase672_local_publisher")
    pub = node.create_publisher(String, "/phase672/local_probe", 10)
    deadline = time.monotonic() + 10.0
    count = 0
    try:
        while time.monotonic() < deadline:
            pub.publish(String(data=f"offline-{count}"))
            count += 1
            rclpy.spin_once(node, timeout_sec=0.05)
            time.sleep(0.05)
    finally:
        node.destroy_node()
        rclpy.shutdown()
    return 0


def subscriber() -> int:
    rclpy.init()
    node = Node("phase672_local_subscriber")
    received = []
    node.create_subscription(
        String, "/phase672/local_probe", lambda msg: received.append(msg.data), 10
    )
    deadline = time.monotonic() + 12.0
    try:
        while time.monotonic() < deadline and not received:
            rclpy.spin_once(node, timeout_sec=0.2)
    finally:
        node.destroy_node()
        rclpy.shutdown()
    if not received:
        print("LOCAL_DDS_FAIL")
        return 2
    print(f"LOCAL_DDS_PASS {received[0]}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"pub", "sub"}:
        raise SystemExit("usage: phase672_dds_loopback_probe.py pub|sub")
    raise SystemExit(publisher() if sys.argv[1] == "pub" else subscriber())
