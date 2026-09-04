#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PATH="${GO2_VENV_PATH:-$HOME/.venvs/go2-gateway}"
ROBOT_IP="${GO2_ROBOT_IP:-192.168.123.161}"
IFACE="${GO2_NETWORK_INTERFACE:-eth0}"

if [[ ! -f "$VENV_PATH/bin/activate" ]]; then
  echo "Virtual environment not found: $VENV_PATH" >&2
  exit 2
fi

cd "$PROJECT_DIR"
source "$VENV_PATH/bin/activate"

export GO2_MODE="${GO2_MODE:-real}"
export GO2_ROBOT_ID="${GO2_ROBOT_ID:-go2-edu-001}"
export GO2_NETWORK_INTERFACE="$IFACE"
export GO2_CONTROL_ENABLED="${GO2_CONTROL_ENABLED:-false}"

echo "== Network preflight =="
ip -br addr show "$IFACE"
ip route get "$ROBOT_IP"
ping -I "$IFACE" -c 4 "$ROBOT_IP"

echo "== Environment =="
python scripts/check_environment.py

echo "== Read-only state =="
python scripts/verify_state.py

if [[ "${1:-}" == "--camera" ]]; then
  echo "== Read-only camera =="
  python "$PROJECT_DIR/scripts/verify_camera_burst.py" --count "${GO2_CAMERA_BURST_COUNT:-10}" --output-dir "$HOME/go2-camera-test"
fi

echo "Read-only verification finished. Motion control remained disabled: $GO2_CONTROL_ENABLED"
