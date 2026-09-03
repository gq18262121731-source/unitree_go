#!/usr/bin/env bash
set -euo pipefail

pid_file="/home/go2/go2_validation/phase545_readonly_bridge.pid"

if [[ ! -f "${pid_file}" ]]; then
  echo "No Phase 5.4.5 read-only bridge PID file."
  exit 0
fi

pid="$(tr -cd '0-9' < "${pid_file}")"
command_line="$(ps -p "${pid}" -o args= 2>/dev/null || true)"
if [[ -z "${pid}" ]] || [[ "${command_line}" != *"unitree_sensor_bridge"* ]]; then
  echo "ERROR: PID is not the expected read-only bridge; refusing to signal it." >&2
  exit 2
fi

kill -INT "${pid}"
rm -f "${pid_file}"
echo "Stopped Phase 5.4.5 read-only sensor bridge PID ${pid}."

