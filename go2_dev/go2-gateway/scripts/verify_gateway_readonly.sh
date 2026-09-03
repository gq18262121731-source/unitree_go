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

echo "== read-only dispatch guard =="
python - "$HOST" "$PORT" <<'PY'
import json
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

host, port = sys.argv[1], sys.argv[2]
base = f"http://{host}:{port}"

def request_json(path, method="GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(base + path, data=data, headers={"Content-Type": "application/json"}, method=method)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))

status, body = request_json("/health")
data = body.get("data") or {}
if status != 200 or data.get("ready") is not False or data.get("controlEnabled") is not False:
    raise SystemExit(f"health should report read-only not-ready state, got status={status} body={body}")
print("GET /health -> ready=false controlEnabled=false")

checks = [
    ("GET", "/api/readiness", None),
    ("POST", "/api/robot/move", {"vx": 0.05, "vy": 0, "wz": 0, "duration": 0.05}),
    ("POST", "/api/tasks/confirm-fall", {"task": "confirm_fall", "elder_id": "readonly", "location": "bedroom", "confidence": 0.95}),
    ("POST", "/api/events/fall", {"event": "fall_detected", "elder_id": "readonly", "location": "bedroom", "confidence": 0.95}),
    ("POST", "/api/tasks/target-move", {"location": "bedroom"}),
]
for method, path, payload in checks:
    status, body = request_json(path, method, payload)
    if status != 403 or body.get("code") != "CONTROL_DISABLED":
        raise SystemExit(f"{method} {path} expected CONTROL_DISABLED/403, got status={status} body={body}")
    print(f"{method} {path} -> {body['code']}")

status, body = request_json("/api/tasks")
tasks = body.get("data")
if status != 200 or tasks != []:
    raise SystemExit(f"read-only guard should not create tasks, got status={status} body={body}")
print("task list remains empty")
PY

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
