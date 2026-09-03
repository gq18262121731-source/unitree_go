#!/usr/bin/env bash
set -eo pipefail
source /opt/ros/humble/setup.bash
set -u
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export ROS_DOMAIN_ID=172
unset ROS_LOCALHOST_ONLY
export CYCLONEDDS_URI="file:///mnt/e/笨笨狗/phase672_artifacts/phase672_cyclonedds_netns.xml"

script="/mnt/e/笨笨狗/phase672_tools/phase672_dds_loopback_probe.py"
python3 "${script}" sub &
sub_pid=$!
sleep 1
python3 "${script}" pub &
pub_pid=$!
wait "${sub_pid}"
sub_status=$?
wait "${pub_pid}" || true
exit "${sub_status}"
