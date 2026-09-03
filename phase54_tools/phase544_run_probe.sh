#!/usr/bin/env bash
set -eo pipefail

source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=file:///home/go2/phase53_ros2_ws/src/unitree_sensor_bridge/config/cyclonedds_go2.xml

duration="${1:-30}"
output="${2:-/home/go2/go2_validation/phase544_uslam_probe.json}"

exec python3 /home/go2/phase544_uslam_probe.py "${duration}" "${output}"
