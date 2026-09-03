#!/usr/bin/env bash
set -eo pipefail

source /opt/ros/humble/setup.bash
source /home/go2/phase541_msg_ws/install_corrected/setup.bash

export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=file:///home/go2/phase53_ros2_ws/src/unitree_sensor_bridge/config/cyclonedds_go2.xml

label="${1:-}"
duration_seconds="${2:-20}"
base_dir="${3:-/home/go2/go2_validation/phase548}"

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
  echo "ERROR: unsupported or missing segment label: ${label}" >&2
  echo "Allowed labels:" >&2
  printf '  %s\n' "${allowed_labels[@]}" >&2
  exit 2
fi

if [[ ! "${duration_seconds}" =~ ^[0-9]+$ ]] ||
   (( duration_seconds < 10 || duration_seconds > 90 )); then
  echo "ERROR: duration must be an integer from 10 to 90 seconds" >&2
  exit 2
fi

forbidden="$(
  pgrep -af 'slam_toolbox|cartographer|point.?lio|nav2' |
    grep -v 'phase548_record_segment' || true
)"
if [[ -n "${forbidden}" ]]; then
  echo "ERROR: forbidden process found:" >&2
  printf '%s\n' "${forbidden}" >&2
  exit 3
fi

required_topics=(
  /utlidar/cloud
  /utlidar/imu
  /utlidar/robot_odom
)
topic_list="$(ros2 topic list 2>/dev/null || true)"
for topic in "${required_topics[@]}"; do
  if ! printf '%s\n' "${topic_list}" | grep -Fxq "${topic}"; then
    echo "ERROR: required topic is unavailable: ${topic}" >&2
    exit 4
  fi
done

mkdir -p "${base_dir}"
if [[ "$(df -Pk "${base_dir}" | awk 'NR==2 {print $4}')" -lt 2097152 ]]; then
  echo "ERROR: at least 2 GiB free space is required" >&2
  exit 5
fi

timestamp="$(date +%Y%m%d_%H%M%S)"
bag_dir="${base_dir}/phase548_${timestamp}_${label}"
manifest="${bag_dir}_manifest.txt"
pid_file="${base_dir}/phase548_record.pid"

if [[ -f "${pid_file}" ]]; then
  previous_pid="$(tr -cd '0-9' < "${pid_file}")"
  if [[ -n "${previous_pid}" ]] && kill -0 "${previous_pid}" 2>/dev/null; then
    echo "ERROR: another Phase 5.4.8 recorder is active (PID ${previous_pid})" >&2
    exit 6
  fi
  rm -f "${pid_file}"
fi

{
  echo "phase=5.4.8"
  echo "label=${label}"
  echo "duration_seconds=${duration_seconds}"
  echo "bag_dir=${bag_dir}"
  echo "start_time=$(date --iso-8601=ns)"
  echo "topics=/utlidar/cloud,/utlidar/imu,/utlidar/robot_odom"
  echo "control_topics=NONE"
  echo "slam=NOT_STARTED"
} > "${manifest}"

echo "Recording read-only segment: ${label}"
echo "Duration: ${duration_seconds} seconds"
echo "Output: ${bag_dir}"

ros2 bag record \
  -o "${bag_dir}" \
  /utlidar/cloud \
  /utlidar/imu \
  /utlidar/robot_odom &
recorder_pid=$!
printf '%s\n' "${recorder_pid}" > "${pid_file}"

timer_pid=""
stopping=0
stop_recorder() {
  if (( stopping == 1 )); then
    return
  fi
  stopping=1
  if [[ -n "${timer_pid}" ]]; then
    kill "${timer_pid}" 2>/dev/null || true
  fi
  if kill -0 "${recorder_pid}" 2>/dev/null; then
    kill -INT "${recorder_pid}" 2>/dev/null || true
  fi
}
trap stop_recorder INT TERM

(
  sleep "${duration_seconds}"
  kill -INT "${recorder_pid}" 2>/dev/null || true
) &
timer_pid=$!

set +e
wait "${recorder_pid}"
record_status=$?
set -e
kill "${timer_pid}" 2>/dev/null || true
wait "${timer_pid}" 2>/dev/null || true
rm -f "${pid_file}"
trap - INT TERM

{
  echo "end_time=$(date --iso-8601=ns)"
  echo "recorder_exit_status=${record_status}"
} >> "${manifest}"

ros2 bag info "${bag_dir}" | tee "${bag_dir}_info.txt"
echo "Completed: ${bag_dir}"
exit "${record_status}"
