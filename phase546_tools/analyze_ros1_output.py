#!/usr/bin/env python3
import json
import math
import sys

import rosbag


def main() -> None:
    bag_path = sys.argv[1]
    output_path = sys.argv[2]
    input_start = float(sys.argv[3])

    stamps = []
    positions = []
    receipt_times = []
    with rosbag.Bag(bag_path) as bag:
        for _, message, receipt_time in bag.read_messages(
            topics=["/pointlio/odom"]
        ):
            stamps.append(message.header.stamp.to_sec())
            positions.append(
                [
                    message.pose.pose.position.x,
                    message.pose.pose.position.y,
                    message.pose.pose.position.z,
                ]
            )
            receipt_times.append(receipt_time.to_sec())

    backward = sum(b < a for a, b in zip(stamps, stamps[1:]))
    duplicates = sum(b == a for a, b in zip(stamps, stamps[1:]))
    path_length = sum(
        math.dist(a, b) for a, b in zip(positions, positions[1:])
    )
    origin = positions[0]
    radii = [math.dist(origin, position) for position in positions]
    crossings = {}
    for threshold in [0.1, 1.0, 5.0, 10.0, 100.0, 1000.0, 10000.0]:
        crossing = next(
            (
                stamps[index] - stamps[0]
                for index, radius in enumerate(radii)
                if radius >= threshold
            ),
            None,
        )
        crossings[str(threshold)] = crossing
    result = {
        "message_count": len(stamps),
        "header_stamp": {
            "first": stamps[0],
            "last": stamps[-1],
            "duration": stamps[-1] - stamps[0],
            "last_relative_to_input_start": stamps[-1] - input_start,
            "backward": backward,
            "duplicates": duplicates,
        },
        "receipt_time": {
            "first": receipt_times[0],
            "last": receipt_times[-1],
            "duration": receipt_times[-1] - receipt_times[0],
        },
        "trajectory": {
            "path_length": path_length,
            "net_displacement": math.dist(positions[0], positions[-1]),
            "max_radius": max(radii),
            "min_xyz": [min(values) for values in zip(*positions)],
            "max_xyz": [max(values) for values in zip(*positions)],
            "last_position": positions[-1],
            "first_radius_crossings_seconds": crossings,
        },
    }
    with open(output_path, "w", encoding="utf-8") as output:
        json.dump(result, output, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
