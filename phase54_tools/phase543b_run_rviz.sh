#!/usr/bin/env bash
set -eo pipefail

source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=file:///home/go2/phase53_ros2_ws/src/unitree_sensor_bridge/config/cyclonedds_go2.xml

exec rviz2 -d /home/go2/phase543b_cloud_base.rviz
