#!/usr/bin/env bash
set -eo pipefail

source /opt/ros/humble/setup.bash
source /home/test1/phase672_pointlio_ws/install/setup.bash
set -u

export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export ROS_DOMAIN_ID=172
# This runner is launched inside an isolated Linux network namespace. dummy0
# exists only for the lifetime of that namespace and has no external route.
unset ROS_LOCALHOST_ONLY
export CYCLONEDDS_URI="file:///mnt/e/笨笨狗/phase672_artifacts/phase672_cyclonedds_netns.xml"

bag_dir="/mnt/e/笨笨狗/phase672_bags/phase545_go2_community_transform"
params_file="/mnt/e/笨笨狗/phase672_artifacts/phase672_utlidar.yaml"
result_dir="/mnt/e/笨笨狗/phase672_pointlio_result"
probe_script="/mnt/e/笨笨狗/phase54_tools/phase545_pointlio_probe.py"
source_pcd="/home/test1/phase672_pointlio_ws/src/point_lio/PCD/scans.pcd"

if [[ -e "${result_dir}" ]]; then
  echo "Refusing to overwrite existing result directory: ${result_dir}" >&2
  exit 10
fi
mkdir -p "${result_dir}"

point_log="${result_dir}/pointlio.log"
play_log="${result_dir}/bag_play.log"
probe_log="${result_dir}/probe.log"
probe_json="${result_dir}/pointlio_odom.json"
status_file="${result_dir}/run_status.txt"

cleanup() {
  set +e
  [[ -n "${play_pid:-}" ]] && kill -INT "${play_pid}" 2>/dev/null
  [[ -n "${probe_pid:-}" ]] && kill -INT "${probe_pid}" 2>/dev/null
  [[ -n "${point_pid:-}" ]] && kill -INT "${point_pid}" 2>/dev/null
  wait "${play_pid:-}" 2>/dev/null
  wait "${probe_pid:-}" 2>/dev/null
  wait "${point_pid:-}" 2>/dev/null
}
trap cleanup EXIT INT TERM

{
  echo "phase=6.7.2"
  echo "mode=strictly_offline"
  echo "start=$(date --iso-8601=seconds)"
  echo "bag=${bag_dir}"
  echo "params=${params_file}"
  echo "ros_domain_id=${ROS_DOMAIN_ID}"
  echo "network_interface=dummy0_isolated_netns"
  echo "cyclonedds_uri=${CYCLONEDDS_URI}"
  echo "rmw=${RMW_IMPLEMENTATION}"
  echo "robot_connected=false"
  echo "motion_publishers=false"
} > "${status_file}"

"/home/test1/phase672_pointlio_ws/install/point_lio/lib/point_lio/pointlio_mapping" \
  --ros-args --params-file "${params_file}" \
  > "${point_log}" 2>&1 &
point_pid=$!

python3 "${probe_script}" "${probe_json}" > "${probe_log}" 2>&1 &
probe_pid=$!

sleep 5
if ! kill -0 "${point_pid}" 2>/dev/null; then
  echo "pointlio_start=FAIL" >> "${status_file}"
  exit 11
fi
echo "pointlio_start=PASS" >> "${status_file}"

ros2 bag play "${bag_dir}" --clock \
  --topics /utlidar/transformed_cloud /utlidar/transformed_imu \
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

set +e
wait "${play_pid}"
play_status=$?
set -e
unset play_pid
echo "bag_play_status=${play_status}" >> "${status_file}"
echo "point_failed_during_play=${point_failed}" >> "${status_file}"

sleep 10
kill -INT "${probe_pid}" 2>/dev/null || true
kill -INT "${point_pid}" 2>/dev/null || true
set +e
wait "${probe_pid}" 2>/dev/null
wait "${point_pid}" 2>/dev/null
set -e
unset probe_pid point_pid

if [[ -f "${source_pcd}" ]]; then
  cp "${source_pcd}" "${result_dir}/scans.pcd"
  echo "pcd_saved=PASS" >> "${status_file}"
  stat -c "pcd_bytes=%s" "${result_dir}/scans.pcd" >> "${status_file}"
else
  echo "pcd_saved=FAIL" >> "${status_file}"
fi

echo "end=$(date --iso-8601=seconds)" >> "${status_file}"
trap - EXIT INT TERM
cat "${status_file}"

if (( point_failed != 0 )) || (( play_status != 0 )); then
  exit 12
fi
