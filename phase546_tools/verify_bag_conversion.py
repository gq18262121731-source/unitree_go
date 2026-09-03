#!/usr/bin/env python3
import hashlib
import json
import struct
import sys
from pathlib import Path

from rosbags.highlevel import AnyReader


TOPICS = ["/utlidar/cloud", "/utlidar/imu"]


def stamp_parts(stamp):
    nanosec = getattr(stamp, "nanosec", getattr(stamp, "nsec", None))
    return int(stamp.sec), int(nanosec)


def digest_bag(path: Path):
    result = {}
    with AnyReader([path]) as reader:
        connections = [
            connection
            for connection in reader.connections
            if connection.topic in TOPICS
        ]
        for topic in TOPICS:
            result[topic] = {
                "count": 0,
                "header_stamps_sha256": hashlib.sha256(),
                "payload_sha256": hashlib.sha256(),
            }

        for connection, _, rawdata in reader.messages(connections=connections):
            message = reader.deserialize(rawdata, connection.msgtype)
            entry = result[connection.topic]
            entry["count"] += 1
            sec, nanosec = stamp_parts(message.header.stamp)
            entry["header_stamps_sha256"].update(
                struct.pack("<qI", sec, nanosec)
            )
            if connection.topic == "/utlidar/cloud":
                entry["payload_sha256"].update(bytes(message.data))
                entry["payload_sha256"].update(
                    struct.pack(
                        "<III??",
                        message.height,
                        message.width,
                        message.point_step,
                        message.is_bigendian,
                        message.is_dense,
                    )
                )
            else:
                values = [
                    message.orientation.x,
                    message.orientation.y,
                    message.orientation.z,
                    message.orientation.w,
                    message.angular_velocity.x,
                    message.angular_velocity.y,
                    message.angular_velocity.z,
                    message.linear_acceleration.x,
                    message.linear_acceleration.y,
                    message.linear_acceleration.z,
                    *message.orientation_covariance,
                    *message.angular_velocity_covariance,
                    *message.linear_acceleration_covariance,
                ]
                entry["payload_sha256"].update(
                    struct.pack("<" + "d" * len(values), *values)
                )

    return {
        topic: {
            "count": entry["count"],
            "header_stamps_sha256": entry[
                "header_stamps_sha256"
            ].hexdigest(),
            "payload_sha256": entry["payload_sha256"].hexdigest(),
        }
        for topic, entry in result.items()
    }


def main():
    ros2_path = Path(sys.argv[1])
    ros1_path = Path(sys.argv[2])
    output_path = Path(sys.argv[3])
    ros2 = digest_bag(ros2_path)
    ros1 = digest_bag(ros1_path)
    result = {
        "ros2": ros2,
        "ros1": ros1,
        "exact_match": {
            topic: ros2[topic] == ros1[topic] for topic in TOPICS
        },
    }
    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
