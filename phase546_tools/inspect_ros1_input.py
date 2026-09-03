#!/usr/bin/env python3
import json
import math
import struct
import sys

import rosbag


def main() -> None:
    bag_path = sys.argv[1]
    output_path = sys.argv[2]
    cloud_topic = sys.argv[3] if len(sys.argv) > 3 else "/utlidar/cloud"
    imu_topic = sys.argv[4] if len(sys.argv) > 4 else "/utlidar/imu"

    with rosbag.Bag(bag_path) as bag:
        cloud = next(bag.read_messages(topics=[cloud_topic]))[1]
        imu = next(bag.read_messages(topics=[imu_topic]))[1]

    fields = {
        field.name: {
            "offset": field.offset,
            "datatype": field.datatype,
            "count": field.count,
        }
        for field in cloud.fields
    }
    time_offset = fields["time"]["offset"]
    point_count = cloud.width * cloud.height
    first_time = struct.unpack_from("<f", cloud.data, time_offset)[0]
    last_time = struct.unpack_from(
        "<f",
        cloud.data,
        (point_count - 1) * cloud.point_step + time_offset,
    )[0]
    acceleration = [
        imu.linear_acceleration.x,
        imu.linear_acceleration.y,
        imu.linear_acceleration.z,
    ]
    angular_velocity = [
        imu.angular_velocity.x,
        imu.angular_velocity.y,
        imu.angular_velocity.z,
    ]
    result = {
        "topics": {"cloud": cloud_topic, "imu": imu_topic},
        "cloud": {
            "frame_id": cloud.header.frame_id,
            "fields": fields,
            "point_step": cloud.point_step,
            "width": cloud.width,
            "height": cloud.height,
            "is_dense": cloud.is_dense,
            "first_point_time_seconds": first_time,
            "last_point_time_seconds": last_time,
        },
        "imu": {
            "frame_id": imu.header.frame_id,
            "linear_acceleration": acceleration,
            "linear_acceleration_norm": math.sqrt(
                sum(value * value for value in acceleration)
            ),
            "angular_velocity": angular_velocity,
        },
    }
    with open(output_path, "w", encoding="utf-8") as output:
        json.dump(result, output, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
