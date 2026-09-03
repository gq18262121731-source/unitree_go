#!/usr/bin/env bash
set -eo pipefail

source /opt/ros/humble/setup.bash

pid_file="/home/go2/go2_validation/phase545_readonly_bridge.pid"
log_file="/home/go2/go2_validation/phase545_readonly_bridge.log"
mkdir -p /home/go2/go2_validation

if ros2 topic list 2>/dev/null | grep -Fxq /odom; then
  echo "/odom is already available; no additional bridge was started."
  exit 0
fi

if [[ -f "${pid_file}" ]]; then
  previous_pid="$(tr -cd '0-9' < "${pid_file}")"
  if [[ -n "${previous_pid}" ]] && kill -0 "${previous_pid}" 2>/dev/null; then
    echo "Read-only bridge is already running (PID ${previous_pid})."
    exit 0
  fi
  rm -f "${pid_file}"
fi

nohup bash /home/go2/phase542_start_bridge.sh > "${log_file}" 2>&1 &
bridge_pid=$!
printf '%s\n' "${bridge_pid}" > "${pid_file}"

for _ in $(seq 1 20); do
  if ros2 topic list 2>/dev/null | grep -Fxq /odom; then
    echo "Read-only sensor bridge ready (PID ${bridge_pid}); /odom is available."
    exit 0
  fi
  sleep 1
done

echo "ERROR: /odom did not appear within 20 seconds." >&2
tail -n 50 "${log_file}" >&2 || true
kill -INT "${bridge_pid}" 2>/dev/null || true
rm -f "${pid_file}"
exit 1
