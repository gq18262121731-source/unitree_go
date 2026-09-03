#!/usr/bin/env bash
set -o pipefail
source /opt/ros/humble/setup.bash
source /home/go2/phase545_pointlio_ws/install/setup.bash
set -u

unset CYCLONEDDS_URI
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export ROS_DOMAIN_ID=145
export ROS_LOCALHOST_ONLY=1

bag_dir="/home/go2/go2_validation/bags/phase545_20260728_135403_lab_demo_3x3"
result_dir="/home/go2/go2_validation/phase545_pointlio_result"
source_pcd="/home/go2/phase545_pointlio_ws/src/point_lio_ros2_src/PCD/scans.pcd"
mkdir -p "${result_dir}"

point_log="${result_dir}/pointlio.log"
play_log="${result_dir}/bag_play.log"
probe_log="${result_dir}/probe.log"
probe_json="${result_dir}/pointlio_odom.json"
status_file="${result_dir}/run_status.txt"

{
  echo "start=$(date --iso-8601=seconds)"
  echo "bag=${bag_dir}"
  echo "ros_domain_id=${ROS_DOMAIN_ID}"
  echo "ros_localhost_only=${ROS_LOCALHOST_ONLY}"
  echo "rmw=${RMW_IMPLEMENTATION}"
} > "${status_file}"

ros2 launch point_lio mapping_unilidar_l1.launch.py rviz:=false \
  > "${point_log}" 2>&1 &
point_pid=$!

python3 /home/go2/phase545_pointlio_probe.py "${probe_json}" \
  > "${probe_log}" 2>&1 &
probe_pid=$!

sleep 5
if ! kill -0 "${point_pid}" 2>/dev/null; then
  echo "pointlio_start=FAIL" >> "${status_file}"
  kill -INT "${probe_pid}" 2>/dev/null || true
  wait "${probe_pid}" 2>/dev/null || true
  exit 2
fi
echo "pointlio_start=PASS" >> "${status_file}"

ros2 bag play "${bag_dir}" \
  --topics /utlidar/cloud /utlidar/imu \
  > "${play_log}" 2>&1 &
play_pid=$!

point_failed=0
while kill -0 "${play_pid}" 2>/dev/null; do
  if ! kill -0 "${point_pid}" 2>/dev/null; then
    point_failed=1
    kill -INT "${play_pid}" 2>/dev/null || true
    break
  fi
  sleep 2
done

wait "${play_pid}"
play_status=$?
echo "bag_play_status=${play_status}" >> "${status_file}"
echo "point_failed_during_play=${point_failed}" >> "${status_file}"

sleep 10
kill -INT "${probe_pid}" 2>/dev/null || true
kill -INT "${point_pid}" 2>/dev/null || true
wait "${probe_pid}" 2>/dev/null || true
wait "${point_pid}" 2>/dev/null || true

if [[ -f "${source_pcd}" ]]; then
  cp "${source_pcd}" "${result_dir}/scans.pcd"
  echo "pcd_saved=PASS" >> "${status_file}"
  stat -c "pcd_bytes=%s" "${result_dir}/scans.pcd" >> "${status_file}"
else
  echo "pcd_saved=FAIL" >> "${status_file}"
fi

echo "end=$(date --iso-8601=seconds)" >> "${status_file}"
cat "${status_file}"

if (( point_failed != 0 )) || (( play_status != 0 )); then
  exit 3
fi
