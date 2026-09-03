#!/usr/bin/env bash
set -eo pipefail

# Read-only visualization of the Go2 firmware's ROS2-compatible DDS topics.
# This does not launch the USB LiDAR driver or any robot control node.
source /opt/ros/humble/setup.bash
set -u
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export ROS_DOMAIN_ID=0
export ROS_LOCALHOST_ONLY=0
export CYCLONEDDS_URI="file:///mnt/e/笨笨狗/go2_dev/unitree_ros2/cyclonedds_go2_eth0_readonly.xml"

exec rviz2 -d "/mnt/e/笨笨狗/go2_dev/unilidar_sdk/unitree_lidar_ros2/src/unitree_lidar_ros2/rviz/view.rviz"
