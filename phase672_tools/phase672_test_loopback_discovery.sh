#!/usr/bin/env bash
set -eo pipefail
source /opt/ros/humble/setup.bash
source /home/test1/phase672_pointlio_ws/install/setup.bash
set -u

export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export ROS_DOMAIN_ID=172
unset ROS_LOCALHOST_ONLY
export CYCLONEDDS_URI="file:///mnt/e/笨笨狗/phase672_artifacts/phase672_cyclonedds_loopback.xml"

log="/tmp/phase672_discovery_node.log"
"/home/test1/phase672_pointlio_ws/install/point_lio/lib/point_lio/pointlio_mapping" \
  --ros-args --params-file "/mnt/e/笨笨狗/phase672_artifacts/phase672_utlidar.yaml" \
  > "${log}" 2>&1 &
pid=$!
cleanup() {
  kill -TERM "${pid}" 2>/dev/null || true
  wait "${pid}" 2>/dev/null || true
}
trap cleanup EXIT
sleep 4
if ! kill -0 "${pid}" 2>/dev/null; then
  cat "${log}"
  exit 2
fi
echo "NODES"
ros2 node list
echo "TOPICS"
ros2 topic list -t
