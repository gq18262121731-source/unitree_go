from __future__ import annotations


LIDAR_TRANSPORT_DDS = "dds"

LIDAR_CANDIDATE_TOPICS = [
    "rt/utlidar/lidar_state",
    "rt/utlidar/voxel_map",
    "rt/utlidar/voxel_map_compressed",
    "/utlidar/cloud",
    "rt/uslam/frontend/cloud_world_ds",
    "rt/uslam/frontend/odom",
    "rt/uslam/cloud_map",
    "rt/uslam/localization/odom",
    "rt/uslam/navigation/global_path",
]

LIDAR_STATE_TOPIC = "rt/utlidar/lidar_state"

LIDAR_MIN_FREQUENCY_HZ = 1.0
LIDAR_MAX_PACKET_LOSS_RATE = 0.2
LIDAR_STALE_AFTER_MS = 2_000


def empty_lidar_topic_status(topic: str = LIDAR_STATE_TOPIC) -> dict:
    return {
        "topic": topic,
        "created": False,
        "discovered": False,
        "received": False,
        "sampleCount": 0,
        "firstSampleAt": None,
        "lastSampleAt": None,
        "frequencyHz": None,
        "packetLossRate": None,
        "cloudSize": None,
        "errorState": None,
        "timeout": None,
        "timeoutCode": None,
    }
