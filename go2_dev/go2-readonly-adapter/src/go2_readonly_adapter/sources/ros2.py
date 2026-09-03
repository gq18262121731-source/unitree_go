from __future__ import annotations

import time
from collections import deque
from typing import Any, Iterable

from ..events import SensorEvent


def _stamp_ns(message: Any) -> int | None:
    header = getattr(message, "header", None)
    stamp = getattr(header, "stamp", None)
    if stamp is None:
        return None
    return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)


def _frame(message: Any) -> str | None:
    return getattr(getattr(message, "header", None), "frame_id", None) or None


class Ros2ReadonlySource:
    """ROS2 subscription-only source for the Phase 5.3 bridge topics."""

    source_name = "ros2"
    lidar_topic = "/sensor/lidar"
    imu_topic = "/sensor/imu"
    odometry_topic = "/odom"

    def events(self, duration_seconds: float) -> Iterable[SensorEvent]:
        try:
            import rclpy
            from nav_msgs.msg import Odometry
            from rclpy.node import Node
            from rclpy.qos import qos_profile_sensor_data
            from sensor_msgs.msg import Imu, PointCloud2
        except ImportError as exc:
            raise RuntimeError(
                "ROS2 source requires an Ubuntu ROS2 Humble environment with "
                f"rclpy and standard messages: {exc}"
            ) from exc

        queue: deque[SensorEvent] = deque()
        lidar_topic = self.lidar_topic
        imu_topic = self.imu_topic
        odometry_topic = self.odometry_topic

        class _ReadonlyNode(Node):
            def __init__(self) -> None:
                super().__init__("unitree_readonly_provider")
                self._subscriptions = [
                    self.create_subscription(
                        PointCloud2,
                        lidar_topic,
                        self._lidar,
                        qos_profile_sensor_data,
                    ),
                    self.create_subscription(
                        Imu,
                        imu_topic,
                        self._imu,
                        qos_profile_sensor_data,
                    ),
                    self.create_subscription(
                        Odometry,
                        odometry_topic,
                        self._odometry,
                        qos_profile_sensor_data,
                    ),
                ]

            def _lidar(self, message: Any) -> None:
                queue.append(
                    SensorEvent(
                        kind="lidar",
                        topic=lidar_topic,
                        received_timestamp_ns=time.time_ns(),
                        source_timestamp_ns=_stamp_ns(message),
                        frame_id=_frame(message),
                        payload={
                            "points": int(message.width) * int(message.height),
                            "point_step": int(message.point_step),
                            "fields": [field.name for field in message.fields],
                        },
                    )
                )

            def _imu(self, message: Any) -> None:
                queue.append(
                    SensorEvent(
                        kind="imu",
                        topic=imu_topic,
                        received_timestamp_ns=time.time_ns(),
                        source_timestamp_ns=_stamp_ns(message),
                        frame_id=_frame(message),
                        payload={
                            "angular_velocity": [
                                message.angular_velocity.x,
                                message.angular_velocity.y,
                                message.angular_velocity.z,
                            ],
                            "linear_acceleration": [
                                message.linear_acceleration.x,
                                message.linear_acceleration.y,
                                message.linear_acceleration.z,
                            ],
                        },
                    )
                )

            def _odometry(self, message: Any) -> None:
                position = message.pose.pose.position
                queue.append(
                    SensorEvent(
                        kind="odometry",
                        topic=odometry_topic,
                        received_timestamp_ns=time.time_ns(),
                        source_timestamp_ns=_stamp_ns(message),
                        frame_id=_frame(message),
                        payload={
                            "child_frame_id": message.child_frame_id,
                            "position": [position.x, position.y, position.z],
                        },
                    )
                )

        rclpy.init()
        node = _ReadonlyNode()
        deadline = time.monotonic() + max(duration_seconds, 0.0)
        try:
            while time.monotonic() < deadline:
                rclpy.spin_once(node, timeout_sec=0.1)
                while queue:
                    yield queue.popleft()
        finally:
            node.destroy_node()
            rclpy.shutdown()
