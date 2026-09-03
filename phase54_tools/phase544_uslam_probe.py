#!/usr/bin/env python3
"""Read-only ROS 2 graph and message probe for Phase 5.4.4."""

import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import PointCloud2
from std_msgs.msg import String


USLAM_TYPES = {
    "/uslam/cloud_map": PointCloud2,
    "/uslam/frontend/cloud_world_ds": PointCloud2,
    "/uslam/frontend/odom": Odometry,
    "/uslam/localization/cloud_world": PointCloud2,
    "/uslam/localization/odom": Odometry,
    "/uslam/map_file_pub": PointCloud2,
    "/uslam/map_file_sub": PointCloud2,
    "/uslam/navigation/global_path": PointCloud2,
    "/uslam/server_log": String,
}

REFERENCE_TYPES = {
    "/utlidar/cloud_base": PointCloud2,
    "/utlidar/cloud_deskewed": PointCloud2,
    "/utlidar/robot_odom": Odometry,
}

GRAPH_ONLY_TOPICS = {
    "/uslam/client_command",
    "/utlidar/client_command",
    "/utlidar/mapping_cmd",
    "/utlidar/switch",
}


def stamp_ns(header):
    return int(header.stamp.sec) * 1_000_000_000 + int(header.stamp.nanosec)


class Probe(Node):
    def __init__(self):
        super().__init__("phase544_uslam_readonly_probe")
        self.start = time.monotonic()
        self.samples = defaultdict(list)
        self.frames = defaultdict(Counter)
        self.child_frames = defaultdict(Counter)
        self.point_counts = defaultdict(list)
        self.text_samples = defaultdict(list)
        self.subscriptions_live = []

        for topic, message_type in {**USLAM_TYPES, **REFERENCE_TYPES}.items():
            subscription = self.create_subscription(
                message_type,
                topic,
                lambda message, name=topic: self.on_message(name, message),
                qos_profile_sensor_data,
            )
            self.subscriptions_live.append(subscription)

    def on_message(self, topic, message):
        receive_ns = time.monotonic_ns()
        record = {"receive_monotonic_ns": receive_ns}
        if hasattr(message, "header"):
            record["stamp_ns"] = stamp_ns(message.header)
            record["frame_id"] = message.header.frame_id
            self.frames[topic][message.header.frame_id] += 1
        if isinstance(message, PointCloud2):
            points = int(message.width) * int(message.height)
            record["points"] = points
            self.point_counts[topic].append(points)
        elif isinstance(message, Odometry):
            record["child_frame_id"] = message.child_frame_id
            self.child_frames[topic][message.child_frame_id] += 1
        elif isinstance(message, String) and len(self.text_samples[topic]) < 10:
            self.text_samples[topic].append(message.data[:500])
        self.samples[topic].append(record)

    def graph(self):
        topic_types = {
            name: types for name, types in self.get_topic_names_and_types()
        }
        publishers = {}
        subscriptions = {}
        targets = sorted(
            set(USLAM_TYPES) | set(REFERENCE_TYPES) | GRAPH_ONLY_TOPICS
        )
        for topic in targets:
            entries = []
            for info in self.get_publishers_info_by_topic(topic):
                entries.append(
                    {
                        "node_name": info.node_name,
                        "node_namespace": info.node_namespace,
                        "topic_type": info.topic_type,
                        "qos": {
                            "reliability": int(info.qos_profile.reliability),
                            "durability": int(info.qos_profile.durability),
                            "history": int(info.qos_profile.history),
                            "depth": int(info.qos_profile.depth),
                        },
                    }
                )
            publishers[topic] = entries
            entries = []
            for info in self.get_subscriptions_info_by_topic(topic):
                entries.append(
                    {
                        "node_name": info.node_name,
                        "node_namespace": info.node_namespace,
                        "topic_type": info.topic_type,
                        "qos": {
                            "reliability": int(info.qos_profile.reliability),
                            "durability": int(info.qos_profile.durability),
                            "history": int(info.qos_profile.history),
                            "depth": int(info.qos_profile.depth),
                        },
                    }
                )
            subscriptions[topic] = entries
        return {
            "nodes": sorted(
                [
                    {"name": name, "namespace": namespace}
                    for name, namespace in self.get_node_names_and_namespaces()
                ],
                key=lambda item: (item["namespace"], item["name"]),
            ),
            "topic_types": {
                name: topic_types.get(name, [])
                for name in sorted(topic_types)
                if name.startswith("/uslam/") or name.startswith("/utlidar/")
            },
            "publishers": publishers,
            "subscriptions": subscriptions,
        }

    def result(self, duration):
        output = {
            "duration_seconds": duration,
            "graph": self.graph(),
            "topics": {},
            "safety": {
                "publishers_created": 0,
                "command_topics_subscribed": [],
                "command_topics_published": [],
                "slam_started": False,
            },
        }
        for topic in sorted(set(USLAM_TYPES) | set(REFERENCE_TYPES)):
            values = self.samples.get(topic, [])
            stamps = [
                item["stamp_ns"] for item in values if "stamp_ns" in item
            ]
            differences = [
                stamps[index] - stamps[index - 1]
                for index in range(1, len(stamps))
            ]
            points = self.point_counts.get(topic, [])
            output["topics"][topic] = {
                "samples": len(values),
                "estimated_hz": len(values) / duration,
                "frames": dict(self.frames.get(topic, {})),
                "child_frames": dict(self.child_frames.get(topic, {})),
                "stamp_backward_jumps": sum(value < 0 for value in differences),
                "stamp_duplicates": sum(value == 0 for value in differences),
                "point_count": {
                    "min": min(points) if points else None,
                    "max": max(points) if points else None,
                    "mean": sum(points) / len(points) if points else None,
                },
                "text_samples": self.text_samples.get(topic, []),
            }
        return output


def main():
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    output = (
        Path(sys.argv[2])
        if len(sys.argv) > 2
        else Path.home() / "go2_validation" / "phase544_uslam_probe.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    rclpy.init()
    node = Probe()
    begin = time.monotonic()
    try:
        while time.monotonic() - begin < duration:
            rclpy.spin_once(node, timeout_sec=0.1)
        elapsed = time.monotonic() - begin
        output.write_text(
            json.dumps(node.result(elapsed), indent=2, sort_keys=True),
            encoding="utf-8",
        )
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
