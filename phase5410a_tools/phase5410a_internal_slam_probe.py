#!/usr/bin/env python3
"""Passive Go2 X EDU internal SLAM/USLAM interface probe.

This probe creates subscriptions only. It never publishes to SLAM, LiDAR,
motion, TF, or control topics.
"""

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
from unitree_api.msg import Response


OUTPUT_TYPES = {
    "/api/slam_operate/response": Response,
    "/lio_sam_ros2/mapping/odometry": Odometry,
    "/slam_info": String,
    "/slam_key_info": String,
    "/uslam/cloud_map": PointCloud2,
    "/uslam/frontend/cloud_world_ds": PointCloud2,
    "/uslam/frontend/odom": Odometry,
    "/uslam/localization/cloud_world": PointCloud2,
    "/uslam/localization/odom": Odometry,
    "/uslam/map_file_pub": PointCloud2,
    "/uslam/navigation/global_path": PointCloud2,
    "/uslam/server_log": String,
}

GRAPH_ONLY_TOPICS = {
    "/api/slam_operate/request",
    "/uslam/client_command",
    "/utlidar/client_command",
    "/utlidar/mapping_cmd",
    "/utlidar/switch",
}


def stamp_ns(message):
    if not hasattr(message, "header"):
        return None
    stamp = message.header.stamp
    return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)


class PassiveProbe(Node):
    def __init__(self):
        super().__init__("phase5410a_internal_slam_passive_probe")
        self.samples = defaultdict(list)
        self.frames = defaultdict(Counter)
        self.child_frames = defaultdict(Counter)
        self.text_samples = defaultdict(list)
        self.subscriptions_live = []

        for topic, message_type in OUTPUT_TYPES.items():
            subscription = self.create_subscription(
                message_type,
                topic,
                lambda message, name=topic: self.on_message(name, message),
                qos_profile_sensor_data,
            )
            self.subscriptions_live.append(subscription)

    def on_message(self, topic, message):
        record = {"receive_monotonic_ns": time.monotonic_ns()}
        message_stamp = stamp_ns(message)
        if message_stamp is not None:
            record["stamp_ns"] = message_stamp
            record["frame_id"] = message.header.frame_id
            self.frames[topic][message.header.frame_id] += 1

        if isinstance(message, PointCloud2):
            record["points"] = int(message.width) * int(message.height)
        elif isinstance(message, Odometry):
            record["child_frame_id"] = message.child_frame_id
            self.child_frames[topic][message.child_frame_id] += 1
        elif isinstance(message, String) and len(self.text_samples[topic]) < 20:
            self.text_samples[topic].append(message.data[:2000])
        elif isinstance(message, Response) and len(self.text_samples[topic]) < 20:
            self.text_samples[topic].append(
                json.dumps(
                    {
                        "api_id": int(message.header.identity.api_id),
                        "status": {
                            "code": int(message.header.status.code),
                        },
                        "data": message.data[:2000],
                        "binary_size": len(message.binary),
                    },
                    ensure_ascii=False,
                )
            )
        self.samples[topic].append(record)

    def endpoint_info(self, topic):
        publishers = [
            {
                "node_name": info.node_name,
                "node_namespace": info.node_namespace,
                "topic_type": info.topic_type,
            }
            for info in self.get_publishers_info_by_topic(topic)
        ]
        subscriptions = [
            {
                "node_name": info.node_name,
                "node_namespace": info.node_namespace,
                "topic_type": info.topic_type,
            }
            for info in self.get_subscriptions_info_by_topic(topic)
        ]
        return {
            "publishers": publishers,
            "subscriptions": subscriptions,
        }

    def result(self, elapsed):
        topic_types = dict(self.get_topic_names_and_types())
        output = {
            "phase": "5.4.10-A",
            "duration_seconds": elapsed,
            "safety": {
                "publishers_created": 0,
                "request_topics_published": [],
                "motion_control": "NOT_USED",
                "slam_started": False,
                "tf_published": False,
            },
            "observed_graph_types": {
                name: topic_types.get(name, [])
                for name in sorted(set(OUTPUT_TYPES) | GRAPH_ONLY_TOPICS)
            },
            "endpoints": {
                name: self.endpoint_info(name)
                for name in sorted(set(OUTPUT_TYPES) | GRAPH_ONLY_TOPICS)
            },
            "topics": {},
        }

        for topic in sorted(OUTPUT_TYPES):
            values = self.samples.get(topic, [])
            stamps = [
                item["stamp_ns"] for item in values if "stamp_ns" in item
            ]
            intervals = [
                second - first
                for first, second in zip(stamps, stamps[1:])
            ]
            points = [
                item["points"] for item in values if "points" in item
            ]
            output["topics"][topic] = {
                "samples": len(values),
                "estimated_hz": len(values) / elapsed if elapsed else 0.0,
                "frames": dict(self.frames.get(topic, {})),
                "child_frames": dict(self.child_frames.get(topic, {})),
                "timestamp_backward": sum(value < 0 for value in intervals),
                "point_count": {
                    "min": min(points) if points else None,
                    "max": max(points) if points else None,
                },
                "text_samples": self.text_samples.get(topic, []),
            }
        return output


def main():
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    output = (
        Path(sys.argv[2])
        if len(sys.argv) > 2
        else Path.home()
        / "go2_validation"
        / "phase5410a_internal_slam_probe.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    rclpy.init()
    node = PassiveProbe()
    begin = time.monotonic()
    try:
        while time.monotonic() - begin < duration:
            rclpy.spin_once(node, timeout_sec=0.1)
        elapsed = time.monotonic() - begin
        output.write_text(
            json.dumps(node.result(elapsed), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
