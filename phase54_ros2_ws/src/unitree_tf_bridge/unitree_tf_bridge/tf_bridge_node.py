#!/usr/bin/env python3
"""Publish only TF edges backed by validated source semantics."""

import rclpy
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from tf2_ros import StaticTransformBroadcaster, TransformBroadcaster


class UnitreeTfBridge(Node):
    """Convert odometry pose to TF and publish the official L1 IMU offset."""

    def __init__(self):
        super().__init__("unitree_tf_bridge")
        self.declare_parameter("odom_topic", "/odom")
        self.declare_parameter("expected_parent_frame", "odom")
        self.declare_parameter("expected_child_frame", "base_link")
        self.declare_parameter("publish_lidar_imu_static", True)

        self._expected_parent = (
            self.get_parameter("expected_parent_frame").get_parameter_value().string_value
        )
        self._expected_child = (
            self.get_parameter("expected_child_frame").get_parameter_value().string_value
        )
        odom_topic = self.get_parameter("odom_topic").get_parameter_value().string_value

        self._dynamic_broadcaster = TransformBroadcaster(self)
        self._static_broadcaster = StaticTransformBroadcaster(self)
        self._forwarded = 0
        self._rejected = 0

        self.create_subscription(
            Odometry, odom_topic, self._on_odom, qos_profile_sensor_data
        )
        self.create_timer(5.0, self._report)

        if (
            self.get_parameter("publish_lidar_imu_static")
            .get_parameter_value()
            .bool_value
        ):
            self._publish_lidar_imu_static()

        self.get_logger().info(
            "read-only TF bridge started: %s -> %s from %s"
            % (self._expected_parent, self._expected_child, odom_topic)
        )
        self.get_logger().warning(
            "base_link -> utlidar_lidar is intentionally NOT published: "
            "the official URDF radar pose failed validation against cloud_base"
        )

    def _publish_lidar_imu_static(self):
        transform = TransformStamped()
        transform.header.stamp = self.get_clock().now().to_msg()
        transform.header.frame_id = "utlidar_lidar"
        transform.child_frame_id = "utlidar_imu"
        transform.transform.translation.x = -0.007698
        transform.transform.translation.y = -0.014655
        transform.transform.translation.z = 0.00667
        transform.transform.rotation.w = 1.0
        self._static_broadcaster.sendTransform(transform)
        self.get_logger().info(
            "published official Unitree L1 static TF: "
            "utlidar_lidar -> utlidar_imu"
        )

    def _on_odom(self, message):
        if (
            message.header.frame_id != self._expected_parent
            or message.child_frame_id != self._expected_child
        ):
            self._rejected += 1
            self.get_logger().error(
                "rejected odometry frame pair %r -> %r; expected %r -> %r"
                % (
                    message.header.frame_id,
                    message.child_frame_id,
                    self._expected_parent,
                    self._expected_child,
                ),
                throttle_duration_sec=5.0,
            )
            return

        transform = TransformStamped()
        transform.header = message.header
        transform.child_frame_id = message.child_frame_id
        transform.transform.translation.x = message.pose.pose.position.x
        transform.transform.translation.y = message.pose.pose.position.y
        transform.transform.translation.z = message.pose.pose.position.z
        transform.transform.rotation = message.pose.pose.orientation
        self._dynamic_broadcaster.sendTransform(transform)
        self._forwarded += 1

    def _report(self):
        self.get_logger().info(
            "TF totals: odom_to_base_forwarded=%d rejected=%d"
            % (self._forwarded, self._rejected)
        )


def main(args=None):
    rclpy.init(args=args)
    node = UnitreeTfBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
