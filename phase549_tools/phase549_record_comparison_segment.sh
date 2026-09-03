#!/usr/bin/env bash
set -eo pipefail

source /opt/ros/humble/setup.bash
source /home/go2/phase541_msg_ws/install_corrected/setup.bash

export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=file:///home/go2/phase53_ros2_ws/src/unitree_sensor_bridge/config/cyclonedds_go2.xml

label="${1:-}"
duration_seconds="${2:-20}"
base_dir="${3:-/home/go2/go2_validation/phase549}"
capture_bin="/home/go2/phase549_tools/build/phase549_lowstate_imu_capture"

allowed_labels=(
  level_static
  pitch_nose_down_hold
  pitch_nose_up_hold
  roll_left_down_hold
  roll_right_down_hold
  yaw_ccw_manual
  yaw_cw_manual
)

allowed=0
for candidate in "${allowed_labels[@]}"; do
  if [[ "${label}" == "${candidate}" ]]; then
    allowed=1
    break
  fi
done
if (( allowed == 0 )); then
  echo "ERROR: unsupported or missing label: ${label}" >&2
  exit 2
fi
if [[ ! "${duration_seconds}" =~ ^[0-9]+$ ]] ||
   (( duration_seconds < 10 || duration_seconds > 90 )); then
  echo "ERROR: duration must be an integer from 10 to 90 seconds" >&2
  exit 2
fi
if [[ ! -x "${capture_bin}" ]]; then
  echo "ERROR: LowState capture binary is unavailable" >&2
  exit 3
fi

forbidden="$(
  pgrep -af 'slam_toolbox|cartographer|point.?lio|nav2' |
    grep -v 'phase549_record_comparison_segment' || true
)"
if [[ -n "${forbidden}" ]]; then
  echo "ERROR: forbidden process found:" >&2
  printf '%s\n' "${forbidden}" >&2
  exit 4
fi

topic_list="$(ros2 topic list 2>/dev/null || true)"
for topic in /utlidar/imu /utlidar/cloud /utlidar/robot_odom; do
  if ! printf '%s\n' "${topic_list}" | grep -Fxq "${topic}"; then
    echo "ERROR: required topic unavailable: ${topic}" >&2
    exit 5
  fi
done

mkdir -p "${base_dir}"
timestamp="$(date +%Y%m%d_%H%M%S)"
prefix="${base_dir}/phase549_${timestamp}_${label}"
bag_dir="${prefix}_utlidar"
lowstate_csv="${prefix}_lowstate.csv"
manifest="${prefix}_manifest.txt"

{
  echo "phase=5.4.9"
  echo "label=${label}"
  echo "duration_seconds=${duration_seconds}"
  echo "start_time=$(date --iso-8601=ns)"
  echo "utlidar_bag=${bag_dir}"
  echo "lowstate_csv=${lowstate_csv}"
  echo "unilidar_imu=UNAVAILABLE_NO_DIRECT_L1_USB"
  echo "lowstate_role=DIAGNOSTIC_ONLY_NOT_L1_IMU"
  echo "control_publishers=NONE"
  echo "slam=NOT_STARTED"
} > "${manifest}"

echo "PHASE549_RECORD_BEGIN"
echo "label=${label}"
echo "duration_seconds=${duration_seconds}"

ros2 bag record \
  -o "${bag_dir}" \
  /utlidar/imu \
  /utlidar/cloud \
  /utlidar/robot_odom > "${prefix}_rosbag.log" 2>&1 &
bag_pid=$!

stop_bag() {
  if kill -0 "${bag_pid}" 2>/dev/null; then
    kill -INT "${bag_pid}" 2>/dev/null || true
    wait "${bag_pid}" 2>/dev/null || true
  fi
}
trap stop_bag INT TERM EXIT

sleep 1
set +e
"${capture_bin}" enp0s8 "${duration_seconds}" "${lowstate_csv}" |
  tee "${prefix}_lowstate.log"
lowstate_status=${PIPESTATUS[0]}
set -e
stop_bag
trap - INT TERM EXIT

{
  echo "end_time=$(date --iso-8601=ns)"
  echo "lowstate_exit_status=${lowstate_status}"
  echo "lowstate_rows=$(($(wc -l < "${lowstate_csv}") - 1))"
} >> "${manifest}"

ros2 bag info "${bag_dir}" | tee "${prefix}_bag_info.txt"
echo "PHASE549_RECORD_DONE"
echo "prefix=${prefix}"
exit "${lowstate_status}"
