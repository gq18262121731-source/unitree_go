#!/usr/bin/env bash
set -eo pipefail
source /opt/ros/humble/setup.bash
source /home/go2/phase541_msg_ws/install_corrected/setup.bash
set -u

export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=file:///home/go2/phase53_ros2_ws/src/unitree_sensor_bridge/config/cyclonedds_go2.xml

label="${1:-target_scene}"
duration_seconds="${2:-300}"
base_dir="${3:-/home/go2/go2_validation/bags}"

if [[ ! "${label}" =~ ^[A-Za-z0-9_-]+$ ]]; then
  echo "ERROR: label must contain only A-Z, a-z, 0-9, _ or -" >&2
  exit 2
fi
if [[ ! "${duration_seconds}" =~ ^[0-9]+$ ]] ||
   (( duration_seconds < 60 || duration_seconds > 1800 )); then
  echo "ERROR: duration must be an integer from 60 to 1800 seconds" >&2
  exit 2
fi

required_topics=(
  /utlidar/cloud
  /utlidar/cloud_base
  /utlidar/imu
  /utlidar/robot_odom
  /utlidar/lidar_state
  /odom
)
record_topics=(
  /utlidar/cloud
  /utlidar/cloud_base
  /utlidar/imu
  /utlidar/robot_odom
  /utlidar/lidar_state
  /odom
  /tf
  /tf_static
)

topic_list="$(ros2 topic list)"
for topic in "${required_topics[@]}"; do
  if ! printf '%s\n' "${topic_list}" | grep -Fxq "${topic}"; then
    echo "ERROR: required topic is not available: ${topic}" >&2
    exit 3
  fi
done

available_kb="$(df -Pk "${base_dir%/bags}" | awk 'NR==2 {print $4}')"
if [[ -z "${available_kb}" ]] || (( available_kb < 15728640 )); then
  echo "ERROR: at least 15 GiB free space is required before recording" >&2
  exit 4
fi

timestamp="$(date +%Y%m%d_%H%M%S)"
bag_dir="${base_dir}/phase545_${timestamp}_${label}"
pid_file="/home/go2/go2_validation/phase545_record.pid"
session_file="/home/go2/go2_validation/phase545_active_session.txt"
mkdir -p "${base_dir}"

if [[ -f "${pid_file}" ]]; then
  previous_pid="$(tr -cd '0-9' < "${pid_file}")"
  if [[ -n "${previous_pid}" ]] && kill -0 "${previous_pid}" 2>/dev/null; then
    echo "ERROR: another Phase 5.4.5 recorder is active (PID ${previous_pid})" >&2
    exit 5
  fi
  rm -f "${pid_file}"
fi

cat > "${session_file}" <<EOF
label=${label}
duration_seconds=${duration_seconds}
bag_dir=${bag_dir}
start_time=$(date --iso-8601=seconds)
core_input=/utlidar/cloud,/utlidar/imu
validation_topics=/utlidar/cloud_base,/utlidar/robot_odom,/utlidar/lidar_state,/odom,/tf,/tf_static
control_topics=NONE
EOF

echo "Recording ${bag_dir}"
echo "Duration limit: ${duration_seconds} seconds"
echo "Robot movement must remain manual low-speed remote control."

ros2 bag record -o "${bag_dir}" "${record_topics[@]}" &
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
  echo "end_time=$(date --iso-8601=seconds)"
  echo "recorder_exit_status=${record_status}"
} >> "${session_file}"

ros2 bag info "${bag_dir}" | tee "${bag_dir}_info.txt"
echo "Completed: ${bag_dir}"
exit "${record_status}"
