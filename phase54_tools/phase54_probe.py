#!/usr/bin/env python3
"""Read-only Phase 5.4 probe for ROS 2 frame and TF semantics."""

import json
import time
from collections import Counter, defaultdict

import rclpy
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import Imu, PointCloud2
from tf2_msgs.msg import TFMessage


def stamp_ns(stamp):
    return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)


class Phase54Probe(Node):
    def __init__(self):
        super().__init__("phase54_readonly_probe")
        self.started = time.monotonic()
        self.frames = defaultdict(Counter)
        self.counts = Counter()
        self.last_stamp = {}
        self.rollbacks = Counter()
        self.tf_edges = Counter()
        self.tf_static_edges = Counter()
        self.odom_stamps = set()
        self.odom_to_base_tf_stamps = set()
        self.last_tf_stamp = None
        self.tf_stamp_rollbacks = 0

        self.create_subscription(
            PointCloud2, "/sensor/lidar", self._lidar, qos_profile_sensor_data
        )
        self.create_subscription(
            PointCloud2,
            "/utlidar/cloud_base",
            lambda msg: self._record_header("/utlidar/cloud_base", msg.header),
            qos_profile_sensor_data,
        )
        self.create_subscription(
            PointCloud2,
            "/utlidar/cloud_deskewed",
            lambda msg: self._record_header("/utlidar/cloud_deskewed", msg.header),
            qos_profile_sensor_data,
        )
        self.create_subscription(Imu, "/sensor/imu", self._imu, qos_profile_sensor_data)
        self.create_subscription(
            Odometry, "/odom", self._odom, qos_profile_sensor_data
        )
        self.create_subscription(
            PoseStamped,
            "/utlidar/robot_pose",
            lambda msg: self._record_header("/utlidar/robot_pose", msg.header),
            qos_profile_sensor_data,
        )
        self.create_subscription(
            TFMessage, "/tf", self._tf, qos_profile_sensor_data
        )
        tf_static_qos = QoSProfile(depth=1)
        tf_static_qos.reliability = ReliabilityPolicy.RELIABLE
        tf_static_qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
        self.create_subscription(
            TFMessage, "/tf_static", self._tf_static, tf_static_qos
        )

    def _record_header(self, topic, header):
        self.counts[topic] += 1
        self.frames[topic][header.frame_id] += 1
        current = stamp_ns(header.stamp)
        previous = self.last_stamp.get(topic)
        if previous is not None and current < previous:
            self.rollbacks[topic] += 1
        self.last_stamp[topic] = current

    def _lidar(self, msg):
        self._record_header("/sensor/lidar", msg.header)

    def _imu(self, msg):
        self._record_header("/sensor/imu", msg.header)

    def _odom(self, msg):
        self._record_header("/odom", msg.header)
        self.frames["/odom child_frame_id"][msg.child_frame_id] += 1
        self.odom_stamps.add(stamp_ns(msg.header.stamp))

    def _tf(self, msg):
        self.counts["/tf messages"] += 1
        for transform in msg.transforms:
            self.tf_edges[
                (transform.header.frame_id, transform.child_frame_id)
            ] += 1
            if (
                transform.header.frame_id == "odom"
                and transform.child_frame_id == "base_link"
            ):
                current = stamp_ns(transform.header.stamp)
                if self.last_tf_stamp is not None and current < self.last_tf_stamp:
                    self.tf_stamp_rollbacks += 1
                self.last_tf_stamp = current
                self.odom_to_base_tf_stamps.add(current)

    def _tf_static(self, msg):
        self.counts["/tf_static messages"] += 1
        for transform in msg.transforms:
            self.tf_static_edges[
                (transform.header.frame_id, transform.child_frame_id)
            ] += 1

    def result(self):
        duration = time.monotonic() - self.started
        topics = {
            name: types for name, types in sorted(self.get_topic_names_and_types())
        }
        nodes = sorted(
            f"{namespace.rstrip('/')}/{name}".replace("//", "/")
            for name, namespace in self.get_node_names_and_namespaces()
        )
        return {
            "duration_seconds": duration,
            "topics": topics,
            "nodes": nodes,
            "sample_counts": dict(self.counts),
            "estimated_hz": {
                topic: count / duration
                for topic, count in self.counts.items()
                if topic.startswith("/sensor/") or topic == "/odom"
            },
            "frames": {
                topic: dict(counter) for topic, counter in self.frames.items()
            },
            "header_stamp_rollbacks": dict(self.rollbacks),
            "tf_edges": [
                {"parent": parent, "child": child, "samples": count}
                for (parent, child), count in sorted(self.tf_edges.items())
            ],
            "tf_static_edges": [
                {"parent": parent, "child": child, "samples": count}
                for (parent, child), count in sorted(self.tf_static_edges.items())
            ],
            "odom_to_base_tf_timestamp_check": {
                "odom_unique_stamps": len(self.odom_stamps),
                "tf_unique_stamps": len(self.odom_to_base_tf_stamps),
                "matched_unique_stamps": len(
                    self.odom_stamps & self.odom_to_base_tf_stamps
                ),
                "tf_match_ratio": (
                    len(self.odom_stamps & self.odom_to_base_tf_stamps)
                    / len(self.odom_to_base_tf_stamps)
                    if self.odom_to_base_tf_stamps
                    else 0.0
                ),
                "tf_stamp_rollbacks": self.tf_stamp_rollbacks,
            },
        }


def main():
    rclpy.init()
    node = Phase54Probe()
    end = time.monotonic() + 10.0
    try:
        while rclpy.ok() and time.monotonic() < end:
            rclpy.spin_once(node, timeout_sec=0.2)
        print(json.dumps(node.result(), indent=2, sort_keys=True))
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
