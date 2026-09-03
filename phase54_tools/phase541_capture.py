#!/usr/bin/env python3
"""Read-only capture of Unitree's onboard LiDAR coordinate products."""

import json
import time
from collections import Counter, defaultdict

import numpy as np
import rclpy
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import PointCloud2, PointField


CLOUD_TOPICS = (
    "/utlidar/cloud",
    "/utlidar/cloud_base",
    "/utlidar/cloud_deskewed",
)
PAIR_TOPICS = ("/utlidar/cloud", "/utlidar/cloud_base")

DATATYPES = {
    PointField.INT8: "i1",
    PointField.UINT8: "u1",
    PointField.INT16: "i2",
    PointField.UINT16: "u2",
    PointField.INT32: "i4",
    PointField.UINT32: "u4",
    PointField.FLOAT32: "f4",
    PointField.FLOAT64: "f8",
}


def stamp_ns(stamp):
    return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)


def point_schema(msg):
    return {
        "frame_id": msg.header.frame_id,
        "height": msg.height,
        "width": msg.width,
        "is_bigendian": msg.is_bigendian,
        "point_step": msg.point_step,
        "row_step": msg.row_step,
        "is_dense": msg.is_dense,
        "fields": [
            {
                "name": field.name,
                "offset": field.offset,
                "datatype": field.datatype,
                "count": field.count,
            }
            for field in msg.fields
        ],
    }


def point_arrays(msg):
    endian = ">" if msg.is_bigendian else "<"
    names, formats, offsets = [], [], []
    for field in msg.fields:
        if field.datatype not in DATATYPES:
            continue
        names.append(field.name)
        base = endian + DATATYPES[field.datatype]
        formats.append(base if field.count == 1 else (base, (field.count,)))
        offsets.append(field.offset)
    dtype = np.dtype(
        {
            "names": names,
            "formats": formats,
            "offsets": offsets,
            "itemsize": msg.point_step,
        }
    )
    structured = np.ndarray(
        shape=(msg.height, msg.width),
        dtype=dtype,
        buffer=msg.data,
        strides=(msg.row_step, msg.point_step),
    ).reshape(-1)
    return {name: np.array(structured[name], copy=True) for name in names}


class Capture(Node):
    def __init__(self):
        super().__init__("phase541_readonly_capture")
        self.started = time.monotonic()
        self.schemas = {}
        self.stamps = defaultdict(list)
        self.frames = defaultdict(Counter)
        self.point_counts = defaultdict(list)
        self.cloud_cache = {topic: {} for topic in PAIR_TOPICS}
        self.raw_base_samples = []
        self.matched_pair_counter = 0
        self.deskewed_samples = []
        self.pose_samples = defaultdict(list)

        for topic in CLOUD_TOPICS:
            self.create_subscription(
                PointCloud2,
                topic,
                lambda msg, topic=topic: self.on_cloud(topic, msg),
                qos_profile_sensor_data,
            )
        self.create_subscription(
            PoseStamped,
            "/utlidar/robot_pose",
            self.on_robot_pose,
            qos_profile_sensor_data,
        )
        self.create_subscription(
            Odometry,
            "/utlidar/robot_odom",
            lambda msg: self.on_odom("/utlidar/robot_odom", msg),
            qos_profile_sensor_data,
        )
        self.create_subscription(
            Odometry,
            "/odom",
            lambda msg: self.on_odom("/odom", msg),
            qos_profile_sensor_data,
        )

    def record_header(self, topic, header):
        self.stamps[topic].append(stamp_ns(header.stamp))
        self.frames[topic][header.frame_id] += 1

    def on_cloud(self, topic, msg):
        self.record_header(topic, msg.header)
        self.schemas.setdefault(topic, point_schema(msg))
        self.point_counts[topic].append(msg.width * msg.height)
        key = stamp_ns(msg.header.stamp)
        if topic == "/utlidar/cloud_deskewed" and len(self.deskewed_samples) < 30:
            self.deskewed_samples.append(
                {
                    "stamp_ns": key,
                    "frame_id": msg.header.frame_id,
                    "arrays": point_arrays(msg),
                }
            )
        elif topic in PAIR_TOPICS and len(self.raw_base_samples) < 30:
            self.cloud_cache[topic][key] = msg
            self.try_match_raw_base(key)
            for cache in self.cloud_cache.values():
                while len(cache) > 40:
                    del cache[min(cache)]

    def try_match_raw_base(self, key):
        if not all(key in self.cloud_cache[topic] for topic in PAIR_TOPICS):
            return
        should_store = self.matched_pair_counter % 10 == 0
        self.matched_pair_counter += 1
        if not should_store:
            for topic in PAIR_TOPICS:
                self.cloud_cache[topic].pop(key)
            return
        index = len(self.raw_base_samples)
        sample = {"stamp_ns": key, "index": index, "topics": {}}
        for topic in PAIR_TOPICS:
            msg = self.cloud_cache[topic].pop(key)
            arrays = point_arrays(msg)
            sample["topics"][topic] = {
                "frame_id": msg.header.frame_id,
                "arrays": arrays,
            }
        self.raw_base_samples.append(sample)

    def on_robot_pose(self, msg):
        topic = "/utlidar/robot_pose"
        self.record_header(topic, msg.header)
        self.pose_samples[topic].append(
            {
                "stamp_ns": stamp_ns(msg.header.stamp),
                "frame_id": msg.header.frame_id,
                "position": [
                    msg.pose.position.x,
                    msg.pose.position.y,
                    msg.pose.position.z,
                ],
                "orientation_xyzw": [
                    msg.pose.orientation.x,
                    msg.pose.orientation.y,
                    msg.pose.orientation.z,
                    msg.pose.orientation.w,
                ],
            }
        )

    def on_odom(self, topic, msg):
        self.record_header(topic, msg.header)
        self.frames[topic + " child"][msg.child_frame_id] += 1
        self.pose_samples[topic].append(
            {
                "stamp_ns": stamp_ns(msg.header.stamp),
                "frame_id": msg.header.frame_id,
                "child_frame_id": msg.child_frame_id,
                "position": [
                    msg.pose.pose.position.x,
                    msg.pose.pose.position.y,
                    msg.pose.pose.position.z,
                ],
                "orientation_xyzw": [
                    msg.pose.pose.orientation.x,
                    msg.pose.pose.orientation.y,
                    msg.pose.pose.orientation.z,
                    msg.pose.pose.orientation.w,
                ],
                "linear_velocity": [
                    msg.twist.twist.linear.x,
                    msg.twist.twist.linear.y,
                    msg.twist.twist.linear.z,
                ],
                "angular_velocity": [
                    msg.twist.twist.angular.x,
                    msg.twist.twist.angular.y,
                    msg.twist.twist.angular.z,
                ],
            }
        )

    def save(self, json_path, npz_path):
        duration = time.monotonic() - self.started
        arrays = {}
        cloud_sample_manifest = []
        for sample in self.raw_base_samples:
            item = {"stamp_ns": sample["stamp_ns"], "topics": {}}
            for topic, topic_data in sample["topics"].items():
                safe_topic = topic.strip("/").replace("/", "_")
                item["topics"][topic] = {
                    "frame_id": topic_data["frame_id"],
                    "fields": {},
                }
                for field, array in topic_data["arrays"].items():
                    key = f"s{sample['index']:02d}_{safe_topic}_{field}"
                    arrays[key] = array
                    item["topics"][topic]["fields"][field] = key
            cloud_sample_manifest.append(item)
        deskewed_manifest = []
        for index, sample in enumerate(self.deskewed_samples):
            item = {
                "stamp_ns": sample["stamp_ns"],
                "frame_id": sample["frame_id"],
                "fields": {},
            }
            for field, array in sample["arrays"].items():
                key = f"d{index:02d}_utlidar_cloud_deskewed_{field}"
                arrays[key] = array
                item["fields"][field] = key
            deskewed_manifest.append(item)

        result = {
            "duration_seconds": duration,
            "schemas": self.schemas,
            "frames": {
                topic: dict(counter) for topic, counter in self.frames.items()
            },
            "counts": {topic: len(values) for topic, values in self.stamps.items()},
            "estimated_hz": {
                topic: len(values) / duration for topic, values in self.stamps.items()
            },
            "stamps_ns": dict(self.stamps),
            "point_counts": dict(self.point_counts),
            "pose_samples": dict(self.pose_samples),
            "raw_base_samples": cloud_sample_manifest,
            "deskewed_samples": deskewed_manifest,
        }
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, sort_keys=True)
        np.savez_compressed(npz_path, **arrays)


def main():
    rclpy.init()
    node = Capture()
    deadline = time.monotonic() + 20.0
    try:
        while rclpy.ok() and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.1)
        node.save(
            "/home/go2/go2_validation/phase541_capture.json",
            "/home/go2/go2_validation/phase541_clouds.npz",
        )
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
