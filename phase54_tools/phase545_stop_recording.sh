#!/usr/bin/env bash
set -euo pipefail

pid_file="/home/go2/go2_validation/phase545_record.pid"

if [[ ! -f "${pid_file}" ]]; then
  echo "No active Phase 5.4.5 recording PID file."
  exit 0
fi

pid="$(tr -cd '0-9' < "${pid_file}")"
if [[ -z "${pid}" ]]; then
  echo "ERROR: invalid PID file" >&2
  exit 2
fi

command_line="$(ps -p "${pid}" -o args= 2>/dev/null || true)"
if [[ "${command_line}" != *"ros2 bag record"* ]]; then
  echo "ERROR: PID ${pid} is not a ros2 bag record process; refusing to signal it." >&2
  exit 3
fi

kill -INT "${pid}"
echo "Sent SIGINT to Phase 5.4.5 recorder PID ${pid}; waiting for metadata flush."

