#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PATH="${GO2_VENV_PATH:-$HOME/.venvs/go2-gateway}"
PORT="${GO2_GATEWAY_PORT:-8090}"
HOST="${GO2_GATEWAY_HOST:-127.0.0.1}"
SNAPSHOT_PATH="${GO2_GATEWAY_SNAPSHOT_PATH:-$HOME/go2-gateway-snapshot.jpg}"

cd "$PROJECT_DIR"
source "$VENV_PATH/bin/activate"

export GO2_MODE="${GO2_MODE:-real}"
export GO2_ROBOT_ID="${GO2_ROBOT_ID:-go2-edu-001}"
export GO2_NETWORK_INTERFACE="${GO2_NETWORK_INTERFACE:-eth0}"
export GO2_CONTROL_ENABLED="${GO2_CONTROL_ENABLED:-false}"

if ps aux | grep -E "uvicorn app.main:app|go2-gateway" | grep -v grep >/dev/null; then
  echo "Another Gateway-like process is already running. Stop it before this check." >&2
  ps aux | grep -E "uvicorn app.main:app|go2-gateway" | grep -v grep >&2
  exit 2
fi

uvicorn app.main:app --host "$HOST" --port "$PORT" --workers 1 > /tmp/go2-gateway-real.log 2>&1 &
pid=$!

cleanup() {
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
}
trap cleanup EXIT

for _ in {1..30}; do
  if curl -fsS "http://$HOST:$PORT/health" >/tmp/go2-health.json 2>/dev/null; then
    break
  fi
  sleep 0.5
  if ! kill -0 "$pid" 2>/dev/null; then
    cat /tmp/go2-gateway-real.log >&2
    exit 1
  fi
done

echo "== health =="
curl -fsS "http://$HOST:$PORT/health"
echo

echo "== status =="
curl -fsS "http://$HOST:$PORT/api/robot/status"
echo

echo "== snapshot =="
curl -fsS "http://$HOST:$PORT/api/robot/camera/snapshot" --output "$SNAPSHOT_PATH"
python - "$SNAPSHOT_PATH" <<'PY'
from pathlib import Path
import sys

import cv2

path = Path(sys.argv[1])
image = cv2.imread(str(path))
if image is None:
    raise SystemExit(f"failed to decode {path}")
h, w = image.shape[:2]
print(f"saved={path} size={w}x{h} bytes={path.stat().st_size}")
PY

echo "Gateway read-only verification finished. GO2_CONTROL_ENABLED=$GO2_CONTROL_ENABLED"
