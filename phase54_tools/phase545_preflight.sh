#!/usr/bin/env bash
set -o pipefail
source /opt/ros/humble/setup.bash
source /home/go2/phase541_msg_ws/install_corrected/setup.bash
set -u

export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=file:///home/go2/phase53_ros2_ws/src/unitree_sensor_bridge/config/cyclonedds_go2.xml

output="${1:-/home/go2/go2_validation/phase545_preflight.txt}"
mkdir -p "$(dirname "${output}")"

required_topics=(
  /utlidar/cloud
  /utlidar/cloud_base
  /utlidar/imu
  /utlidar/robot_odom
  /utlidar/lidar_state
)

{
  echo "PHASE_5_4_5_PREFLIGHT"
  echo "timestamp=$(date --iso-8601=seconds)"
  echo "hostname=$(hostname)"
  echo "ros_distro=${ROS_DISTRO:-unset}"
  echo "rmw=${RMW_IMPLEMENTATION}"
  echo "cyclonedds_uri=${CYCLONEDDS_URI}"
  echo

  echo "[system]"
  lsb_release -ds 2>/dev/null || true
  uname -r
  timedatectl show \
    -p NTPSynchronized \
    -p SystemClockSynchronized \
    -p Timezone \
    --value 2>/dev/null || true
  df -h /home/go2
  echo

  echo "[rosbag2]"
  if ros2 bag record --help >/dev/null 2>&1; then
    echo "record_command=PASS"
  else
    echo "record_command=FAIL"
  fi
  ros2 pkg executables rosbag2_transport 2>/dev/null || true
  echo

  echo "[forbidden_process_check]"
  if pgrep -af 'slam_toolbox|cartographer|point.?lio|nav2' | grep -v phase545_preflight; then
    echo "forbidden_processes=FOUND"
  else
    echo "forbidden_processes=NONE"
  fi
  echo

  echo "[relevant_topics]"
  topic_list="$(ros2 topic list 2>/dev/null || true)"
  printf '%s\n' "${topic_list}" | grep -E '^(/utlidar/|/odom$|/tf$|/tf_static$)' || true
  echo

  echo "[required_topic_gate]"
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

  echo "[topic_types]"
  ros2 topic list -t 2>/dev/null |
    grep -E '^/utlidar/(cloud|cloud_base|imu|robot_odom|lidar_state)|^/(odom|tf|tf_static)' ||
    true
  echo

  echo "[lidar_state_once]"
  timeout 8s ros2 topic echo --once /utlidar/lidar_state 2>&1 || true
  echo

  echo "[short_rate_checks]"
  for topic in /utlidar/cloud /utlidar/cloud_base /utlidar/imu /utlidar/robot_odom; do
    echo "--- ${topic}"
    timeout 8s ros2 topic hz "${topic}" 2>&1 || true
  done

  echo
  if [[ "${missing}" -eq 0 ]]; then
    echo "required_topic_gate=PASS"
  else
    echo "required_topic_gate=FAIL"
  fi
} | tee "${output}"
