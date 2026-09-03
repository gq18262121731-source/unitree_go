from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import SetEnvironmentVariable
from launch_ros.actions import Node


def generate_launch_description():
    package_share = Path(get_package_share_directory("unitree_sensor_bridge"))
    cyclone_config = (package_share / "config" / "cyclonedds_go2.xml").as_uri()

    return LaunchDescription(
        [
            SetEnvironmentVariable(
                name="RMW_IMPLEMENTATION",
                value="rmw_cyclonedds_cpp",
            ),
            SetEnvironmentVariable(
                name="CYCLONEDDS_URI",
                value=cyclone_config,
            ),
            Node(
                package="unitree_sensor_bridge",
                executable="unitree_sensor_bridge_node",
                name="unitree_sensor_bridge",
                output="screen",
                parameters=[
                    {
                        "lidar_source": "/utlidar/cloud",
                        "imu_source": "/utlidar/imu",
                        "odom_source": "/utlidar/robot_odom",
                        "lidar_target": "/sensor/lidar",
                        "imu_target": "/sensor/imu",
                        "odom_target": "/odom",
                    }
                ],
            ),
        ]
    )
