#!/usr/bin/env bash
set -eo pipefail

source /opt/ros/humble/setup.bash
source /home/go2/phase541_msg_ws/install_corrected/setup.bash

export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=file:///home/go2/phase53_ros2_ws/src/unitree_sensor_bridge/config/cyclonedds_go2.xml

output="${1:-/home/go2/go2_validation/phase548_preflight.txt}"
mkdir -p "$(dirname "${output}")"

required_topics=(
  /utlidar/cloud
  /utlidar/imu
  /utlidar/robot_odom
)

{
  echo "PHASE_5_4_8_PREFLIGHT"
  echo "timestamp=$(date --iso-8601=ns)"
  echo "hostname=$(hostname)"
  echo "ros_distro=${ROS_DISTRO:-unset}"
  echo "rmw=${RMW_IMPLEMENTATION}"
  echo "cyclonedds_uri=${CYCLONEDDS_URI}"
  echo

  echo "[network]"
  ip -brief address
  ip route get 192.168.123.161 || true
  if ping -c 3 -W 1 192.168.123.161; then
    echo "go2_ping=PASS"
  else
    echo "go2_ping=FAIL"
  fi
  echo

  echo "[time]"
  timedatectl show \
    -p NTPSynchronized \
    -p SystemClockSynchronized \
    -p Timezone || true
  echo

  echo "[forbidden_processes]"
  forbidden="$(
    pgrep -af 'slam_toolbox|cartographer|point.?lio|nav2' |
      grep -v 'phase548_preflight' || true
  )"
  if [[ -n "${forbidden}" ]]; then
    printf '%s\n' "${forbidden}"
    echo "forbidden_process_gate=FAIL"
  else
    echo "forbidden_process_gate=PASS"
  fi
  echo

  echo "[topics]"
  topic_list="$(ros2 topic list 2>/dev/null || true)"
  printf '%s\n' "${topic_list}" |
    grep -E '^/utlidar/(cloud|imu|robot_odom|lidar_state)$' || true
  echo

  missing=0
  for topic in "${required_topics[@]}"; do
    if printf '%s\n' "${topic_list}" | grep -Fxq "${topic}"; then
      echo "${topic}=PASS"
    else
      echo "${topic}=MISSING"
      missing=1
    fi
  done
  echo

  echo "[types]"
  ros2 topic list -t 2>/dev/null |
    grep -E '^/utlidar/(cloud|imu|robot_odom|lidar_state)' || true
  echo

  echo "[rates]"
  for topic in /utlidar/cloud /utlidar/imu /utlidar/robot_odom; do
    echo "--- ${topic}"
    timeout 8s ros2 topic hz "${topic}" 2>&1 || true
  done
  echo

  if [[ -n "${forbidden}" ]] || (( missing != 0 )); then
    echo "phase548_preflight=FAIL"
    exit 3
  fi
  echo "phase548_preflight=PASS"
} | tee "${output}"
