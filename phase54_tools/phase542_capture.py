#!/usr/bin/env python3
"""Phase 5.4.2 read-only capture for cloud_base motion validation."""

import json
import os
import sys
import time
from collections import Counter, defaultdict

# Pin the same read-only CycloneDDS interface used by the validated bridge.
# These must be set before importing/initializing rclpy.
os.environ["RMW_IMPLEMENTATION"] = "rmw_cyclonedds_cpp"
os.environ["CYCLONEDDS_URI"] = (
    "file:///home/go2/phase53_ros2_ws/src/"
    "unitree_sensor_bridge/config/cyclonedds_go2.xml"
)

import numpy as np
import rclpy
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


def schema(msg):
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


def arrays(msg):
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
    view = np.ndarray(
        shape=(msg.height, msg.width),
        dtype=dtype,
        buffer=msg.data,
        strides=(msg.row_step, msg.point_step),
    ).reshape(-1)
    return {name: np.array(view[name], copy=True) for name in names}


class Capture(Node):
    def __init__(self):
        super().__init__("phase542_readonly_capture")
        self.started = time.monotonic()
        self.schemas = {}
        self.stamps = defaultdict(list)
        self.receive_monotonic_ns = defaultdict(list)
        self.frames = defaultdict(Counter)
        self.point_counts = defaultdict(list)
        self.cache = {topic: {} for topic in PAIR_TOPICS}
        self.pair_count = 0
        self.pairs = []
        self.deskewed = []
        self.odom = defaultdict(list)

        for topic in CLOUD_TOPICS:
            self.create_subscription(
                PointCloud2,
                topic,
                lambda msg, topic=topic: self.on_cloud(topic, msg),
                qos_profile_sensor_data,
            )
        for topic in ("/utlidar/robot_odom", "/odom"):
            self.create_subscription(
                Odometry,
                topic,
                lambda msg, topic=topic: self.on_odom(topic, msg),
                qos_profile_sensor_data,
            )

    def record(self, topic, header):
        self.stamps[topic].append(stamp_ns(header.stamp))
        self.receive_monotonic_ns[topic].append(time.monotonic_ns())
        self.frames[topic][header.frame_id] += 1

    def on_cloud(self, topic, msg):
        self.record(topic, msg.header)
        self.schemas.setdefault(topic, schema(msg))
        self.point_counts[topic].append(msg.width * msg.height)
        key = stamp_ns(msg.header.stamp)

        if topic in PAIR_TOPICS:
            self.cache[topic][key] = msg
            self.try_pair(key)
            for values in self.cache.values():
                while len(values) > 50:
                    del values[min(values)]
        elif topic == "/utlidar/cloud_deskewed":
            # Roughly 1.5 Hz: enough for product comparison without a huge file.
            if len(self.stamps[topic]) % 10 == 1:
                self.deskewed.append(
                    {
                        "stamp_ns": key,
                        "frame_id": msg.header.frame_id,
                        "arrays": arrays(msg),
                    }
                )

    def try_pair(self, key):
        if not all(key in self.cache[topic] for topic in PAIR_TOPICS):
            return
        store = self.pair_count % 3 == 0
        self.pair_count += 1
        if not store:
            for topic in PAIR_TOPICS:
                self.cache[topic].pop(key)
            return
        item = {"stamp_ns": key, "topics": {}}
        for topic in PAIR_TOPICS:
            msg = self.cache[topic].pop(key)
            item["topics"][topic] = {
                "frame_id": msg.header.frame_id,
                "arrays": arrays(msg),
            }
        self.pairs.append(item)

    def on_odom(self, topic, msg):
        self.record(topic, msg.header)
        self.frames[topic + " child"][msg.child_frame_id] += 1
        self.odom[topic].append(
            {
                "stamp_ns": stamp_ns(msg.header.stamp),
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
        archive = {}
        pair_manifest = []
        for index, pair in enumerate(self.pairs):
            item = {"stamp_ns": pair["stamp_ns"], "topics": {}}
            for topic, value in pair["topics"].items():
                safe = topic.strip("/").replace("/", "_")
                fields = {}
                for name, array in value["arrays"].items():
                    key = f"p{index:04d}_{safe}_{name}"
                    archive[key] = array
                    fields[name] = key
                item["topics"][topic] = {
                    "frame_id": value["frame_id"],
                    "fields": fields,
                }
            pair_manifest.append(item)

        desk_manifest = []
        for index, item in enumerate(self.deskewed):
            fields = {}
            for name, array in item["arrays"].items():
                key = f"d{index:03d}_utlidar_cloud_deskewed_{name}"
                archive[key] = array
                fields[name] = key
            desk_manifest.append(
                {
                    "stamp_ns": item["stamp_ns"],
                    "frame_id": item["frame_id"],
                    "fields": fields,
                }
            )

        duration = time.monotonic() - self.started
        result = {
            "duration_seconds": duration,
            "schemas": self.schemas,
            "frames": {
                topic: dict(values) for topic, values in self.frames.items()
            },
            "counts": {topic: len(values) for topic, values in self.stamps.items()},
            "estimated_hz": {
                topic: len(values) / duration for topic, values in self.stamps.items()
            },
            "stamps_ns": dict(self.stamps),
            "receive_monotonic_ns": dict(self.receive_monotonic_ns),
            "point_counts": dict(self.point_counts),
            "odom": dict(self.odom),
            "raw_base_pairs": pair_manifest,
            "deskewed_samples": desk_manifest,
        }
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, sort_keys=True)
        np.savez_compressed(npz_path, **archive)


def main():
    rclpy.init()
    node = Capture()
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    prefix = sys.argv[2] if len(sys.argv) > 2 else "phase542_motion"
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
