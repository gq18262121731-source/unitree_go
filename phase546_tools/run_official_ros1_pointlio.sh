#!/usr/bin/env bash
set -eo pipefail

WS=/home/est1/phase546_ros1_ws
INPUT=${INPUT:-/mnt/e/笨笨狗/phase546_ros1_input/phase545_cloud_imu_ros1.bag}
OUT=${OUT:-/mnt/e/笨笨狗/phase546_ros1_result}
PLAY_MODE=${PLAY_MODE:-go2}
PLAY_DURATION=${PLAY_DURATION:-}
PCD_SOURCE="$WS/src/point_lio_unilidar/PCD/scans.pcd"

export ROS_MASTER_URI=http://127.0.0.1:11346
export ROS_HOSTNAME=127.0.0.1
export ROS_IP=127.0.0.1

source /opt/ros/noetic/setup.bash
source "$WS/devel/setup.bash"
set -u

if [[ -e "$OUT" ]]; then
  echo "Refusing to overwrite existing output directory: $OUT" >&2
  exit 20
fi
mkdir -p "$OUT"
rm -f "$PCD_SOURCE"

MASTER_PID=
POINT_PID=
RECORD_PID=

cleanup() {
  set +e
  if [[ -n "${RECORD_PID:-}" ]] && kill -0 "$RECORD_PID" 2>/dev/null; then
    kill -INT "$RECORD_PID" 2>/dev/null
    wait "$RECORD_PID" 2>/dev/null
  fi
  if [[ -n "${POINT_PID:-}" ]] && kill -0 "$POINT_PID" 2>/dev/null; then
    kill -INT "$POINT_PID" 2>/dev/null
    for _ in $(seq 1 30); do
      kill -0 "$POINT_PID" 2>/dev/null || break
      sleep 1
    done
    kill -TERM "$POINT_PID" 2>/dev/null || true
    wait "$POINT_PID" 2>/dev/null || true
  fi
  pkill -INT -f "$WS/devel/lib/point_lio_unilidar/pointlio_mapping" 2>/dev/null || true
  sleep 2
  if [[ -n "${MASTER_PID:-}" ]] && kill -0 "$MASTER_PID" 2>/dev/null; then
    kill -INT "$MASTER_PID" 2>/dev/null
    wait "$MASTER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

roscore -p 11346 >"$OUT/roscore.log" 2>&1 &
MASTER_PID=$!
for _ in $(seq 1 30); do
  rosparam list >/dev/null 2>&1 && break
  sleep 1
done
rosparam list >/dev/null 2>&1

roslaunch point_lio_unilidar mapping_unilidar_l1.launch rviz:=false \
  >"$OUT/pointlio.log" 2>&1 &
POINT_PID=$!

for _ in $(seq 1 60); do
  if rosnode list 2>/dev/null | grep -qx /laserMapping; then
    break
  fi
  kill -0 "$POINT_PID"
  sleep 1
done
rosnode list | grep -qx /laserMapping

rosbag record --lz4 -O "$OUT/pointlio_output.bag" \
  /pointlio/odom /pointlio/path >"$OUT/recorder.log" 2>&1 &
RECORD_PID=$!
sleep 2

set +e
PLAY_ARGS=(--delay=2)
if [[ -n "$PLAY_DURATION" ]]; then
  PLAY_ARGS+=(--duration="$PLAY_DURATION")
fi
PLAY_ARGS+=("$INPUT")
if [[ "$PLAY_MODE" == "go2" ]]; then
  PLAY_ARGS+=(/utlidar/cloud:=/unilidar/cloud)
  PLAY_ARGS+=(/utlidar/imu:=/unilidar/imu)
fi
rosbag play "${PLAY_ARGS[@]}" >"$OUT/bag_play.log" 2>&1
PLAY_STATUS=$?
set -e
echo "$PLAY_STATUS" >"$OUT/bag_play_status.txt"

sleep 8
kill -INT "$RECORD_PID" 2>/dev/null || true
wait "$RECORD_PID" 2>/dev/null || true
RECORD_PID=

kill -INT "$POINT_PID" 2>/dev/null || true
for _ in $(seq 1 45); do
  kill -0 "$POINT_PID" 2>/dev/null || break
  sleep 1
done
if kill -0 "$POINT_PID" 2>/dev/null; then
  pkill -INT -f "$WS/devel/lib/point_lio_unilidar/pointlio_mapping" 2>/dev/null || true
  sleep 5
fi
wait "$POINT_PID" 2>/dev/null || true
POINT_PID=

if [[ -f "$PCD_SOURCE" ]]; then
  cp -a "$PCD_SOURCE" "$OUT/scans.pcd"
  echo "pcd_saved=PASS" >"$OUT/run_status.txt"
else
  echo "pcd_saved=FAIL" >"$OUT/run_status.txt"
fi
echo "bag_play_status=$PLAY_STATUS" >>"$OUT/run_status.txt"
rosbag info "$OUT/pointlio_output.bag" >"$OUT/pointlio_output_info.txt" 2>&1 || true

cleanup
trap - EXIT INT TERM

if [[ "$PLAY_STATUS" -ne 0 ]]; then
  exit "$PLAY_STATUS"
fi
