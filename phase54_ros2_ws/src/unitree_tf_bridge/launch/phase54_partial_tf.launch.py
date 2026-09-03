from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription(
        [
            Node(
                package="unitree_tf_bridge",
                executable="unitree_tf_bridge_node",
                name="unitree_tf_bridge",
                output="screen",
                parameters=[
                    {
                        "odom_topic": "/odom",
                        "expected_parent_frame": "odom",
                        "expected_child_frame": "base_link",
                        "publish_lidar_imu_static": True,
                    }
                ],
            )
        ]
    )
