#!/usr/bin/env bash
set -euo pipefail

GATEWAY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SDK_ROOT="${UNITREE_SDK2_PYTHON_ROOT:-/mnt/e/笨笨狗/go2_dev/unitree_sdk2_python}"
ROBOT_IP="${UNITREE_ROBOT_IP:-192.168.123.161}"
NETWORK_INTERFACE="${UNITREE_NETWORK_INTERFACE:-}"
RISK_EVENTS=""

usage() {
  echo "Usage: $0 [--risk-events <append-only-jsonl>] [--interface <name>]" >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --risk-events)
      [[ $# -ge 2 ]] || { usage; exit 2; }
      RISK_EVENTS="$2"
      shift 2
      ;;
    --interface)
      [[ $# -ge 2 ]] || { usage; exit 2; }
      NETWORK_INTERFACE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -n "$RISK_EVENTS" && ! -f "$RISK_EVENTS" ]]; then
  echo "REAL_GATEWAY_REJECTED: risk JSONL does not exist: $RISK_EVENTS" >&2
  exit 2
fi
[[ -d "$SDK_ROOT" ]] || {
  echo "REAL_GATEWAY_REJECTED: Unitree SDK2 Python source not found: $SDK_ROOT" >&2
  exit 2
}

if [[ -z "$NETWORK_INTERFACE" ]]; then
  mapfile -t candidates < <(
    ip -o -4 addr show up scope global \
      | awk '$4 ~ /^192\.168\.123\./ {print $2}' \
      | sort -u
  )
  if [[ ${#candidates[@]} -ne 1 ]]; then
    echo "REAL_GATEWAY_REJECTED: expected exactly one UP interface on 192.168.123.0/24; found ${#candidates[@]}" >&2
    ip -brief -4 addr >&2
    exit 2
  fi
  NETWORK_INTERFACE="${candidates[0]}"
fi

ip -o -4 addr show dev "$NETWORK_INTERFACE" up \
  | awk '{print $4}' \
  | grep -q '^192\.168\.123\.' || {
    echo "REAL_GATEWAY_REJECTED: $NETWORK_INTERFACE is not UP on 192.168.123.0/24" >&2
    ip -brief -4 addr >&2
    exit 2
  }

ping -I "$NETWORK_INTERFACE" -c 1 -W 1 "$ROBOT_IP" >/dev/null || {
  echo "REAL_GATEWAY_REJECTED: Go2 is not reachable at $ROBOT_IP through $NETWORK_INTERFACE" >&2
  exit 2
}

# Use the same lock as the persistent console launcher. The REST Gateway and
# console are alternative front ends over the same motion path, never peers.
exec 9>/tmp/go2_companion_motion_writer.lock
flock -n 9 || {
  echo "REAL_GATEWAY_REJECTED: another companion motion-writer launcher holds the lock" >&2
  exit 3
}

export PYTHONPATH="$SDK_ROOT:$GATEWAY_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export GO2_MODE=real
export UNITREE_ROBOT_IP="$ROBOT_IP"
export UNITREE_NETWORK_INTERFACE="$NETWORK_INTERFACE"
export UNITREE_DOMAIN_ID=0
export UNITREE_REQUIRE_DDS_STATE=true
export GO2_CONTROL_ENABLED=true
export GO2_READ_ONLY_MODE=false
export FOLLOW_SIMULATION=false
export FOLLOW_EXECUTION_ENABLED=true
export PHASE7_MOTION_EXECUTION_ENABLED=true
if [[ -n "$RISK_EVENTS" ]]; then
  export PHASE7_REQUIRE_EXTERNAL_RISK_FEED=true
  export GO2_COMPANION_RISK_EVENTS_PATH="$RISK_EVENTS"
  RISK_MODE="ACTIVE"
else
  export PHASE7_REQUIRE_EXTERNAL_RISK_FEED=false
  export GO2_COMPANION_RISK_EVENTS_PATH=""
  RISK_MODE="DISABLED"
fi
export GO2_COMPANION_CONFIG="$GATEWAY_ROOT/configs/companion_follow_real.yaml"
export GO2_COMPANION_STATE_PATH="$GATEWAY_ROOT/data/companion_lifecycle_state.json"
export GO2_MAX_VX=0.45
export GO2_MAX_VY=0.0
export GO2_MAX_WZ=0.45
export GO2_CONTROL_WATCHDOG_SECONDS=0.5
export UWB_BEARING_SOURCE=orientation_est
export UWB_BEARING_UNIT=radians
export UWB_BEARING_SIGN=1
export UWB_BEARING_ZERO_OFFSET_RAD=0.55

echo "Go2 real Companion REST Gateway"
echo "  robot:     $ROBOT_IP"
echo "  interface: $NETWORK_INTERFACE"
echo "  risk feed: $RISK_MODE${RISK_EVENTS:+ ($RISK_EVENTS)}"
echo "  status:    http://127.0.0.1:8090/api/v1/robot/companion/status"
echo "  startup:   IDLE (POST START remains required)"
if [[ "$RISK_MODE" == "DISABLED" ]]; then
  echo "  warning:   FALL preemption and WAIT_RESUME are unavailable"
fi

cd "$GATEWAY_ROOT"
python3 -c "import unitree_sdk2py" || {
  echo "REAL_GATEWAY_REJECTED: unitree_sdk2py import failed" >&2
  exit 2
}
exec python3 -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8090 \
  --workers 1 \
  --log-level warning
