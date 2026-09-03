#!/usr/bin/env bash
set -eo pipefail

source /opt/ros/humble/setup.bash
source /home/go2/phase53_ros2_ws/install/setup.bash
export CYCLONEDDS_URI=file:///home/go2/phase53_ros2_ws/src/unitree_sensor_bridge/config/cyclonedds_go2.xml
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp

exec ros2 launch unitree_sensor_bridge unitree_sensor_bridge.launch.py
