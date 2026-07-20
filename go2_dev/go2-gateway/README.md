# Go2 Gateway

This is the first-stage Unitree Go2 EDU gateway for the local robot project. It exposes a small HTTP API for status, front-camera snapshots, stand, lie down, stop, emergency stop, and short low-speed motion.

## Scope

Implemented in this stage:

- Mock mode for development without a robot.
- Real adapter for `unitree_sdk2_python`.
- FastAPI endpoints for health, status, motion, emergency stop, and JPEG snapshot.
- Server-side velocity and duration limits.
- Automatic `StopMove()` after every move attempt.
- Control lock, watchdog, shutdown stop, and stale-state protection.
- Pytest coverage for the safety-critical paths.

Not implemented in this stage: fall detection, following, SLAM, navigation, auto charge, voice, LLM agents, special actions, flips, jumps, handstand, low-level motor or joint control.

## Environment

Recommended real-device host:

- Ubuntu 20.04 LTS is the official `unitree_sdk2` baseline; newer Ubuntu versions can be evaluated separately.
- Python >= 3.8 for `unitree_sdk2_python`.
- Dedicated wired NIC for Go2
- Go2 NIC address: `192.168.123.99/24`

Do not run the first-stage DDS gateway inside a default Docker bridge network. Run this gateway on the host and let the existing system call it over HTTP.

## Install

```bash
cd go2-gateway
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Install `unitree_sdk2_python` from source for real mode:

```bash
cd ../unitree_sdk2_python
pip install -e .
```

If CycloneDDS is missing, install CycloneDDS 0.10.x and set `CYCLONEDDS_HOME` before installing the SDK.

## Configuration

Copy `.env.example` to `.env` or export variables directly.

Important values:

- `GO2_MODE=mock` or `GO2_MODE=real`
- `GO2_NETWORK_INTERFACE=enp3s0`
- `GO2_MAX_VX=0.20`
- `GO2_MAX_VY=0.15`
- `GO2_MAX_WZ=0.35`
- `GO2_MAX_MOVE_DURATION=1.0`

## Run

Mock mode:

```bash
GO2_MODE=mock uvicorn app.main:app --host 0.0.0.0 --port 8090 --workers 1
```

Real mode:

```bash
GO2_MODE=real GO2_NETWORK_INTERFACE=enp3s0 uvicorn app.main:app --host 0.0.0.0 --port 8090 --workers 1
```

Use exactly one worker. Do not start multiple gateway instances against the same robot.

## API

```bash
curl http://127.0.0.1:8090/health
curl http://127.0.0.1:8090/api/robot/status
curl -X POST http://127.0.0.1:8090/api/robot/stand
curl -X POST http://127.0.0.1:8090/api/robot/stop
curl -X POST http://127.0.0.1:8090/api/robot/emergency-stop
curl -X POST http://127.0.0.1:8090/api/robot/lie-down
```

Short move:

```bash
curl -X POST http://127.0.0.1:8090/api/robot/move \
  -H "Content-Type: application/json" \
  -d '{"vx":0.1,"vy":0.0,"wz":0.0,"duration":0.3}'
```

Snapshot:

```bash
curl http://127.0.0.1:8090/api/robot/camera/snapshot --output go2_snapshot.jpg
```

## Verification Scripts

```bash
python scripts/check_environment.py
python scripts/verify_state.py
python scripts/verify_camera.py
python scripts/verify_motion.py
```

`verify_motion.py` asks for operator confirmation and defaults to stand, stop, and lie-down only.

## Tests

```bash
pytest -q
```

Tests run in Mock mode and do not require a real robot.

## Real Go2 EDU Validation Order

1. Record model as Unitree Go2 EDU, then record serial number, firmware, app version, SDK commit, remote controller model, NIC name, and Ubuntu version.
2. Configure Go2 NIC to `192.168.123.99/24`.
3. Run Unitree SDK hello-world publisher/subscriber.
4. Verify DDS state topics, especially `rt/lf/sportmodestate` and `rt/lf/lowstate`.
5. Run official front-camera capture example.
6. Run official high-level example only for `StandUp`, `StopMove`, and `StandDown`.
7. Start this gateway in real mode.
8. Verify `/health`, `/api/robot/status`, snapshot, stand, stop, lie-down.
9. Test tiny short moves only after safety checks.

For WSL2-specific DDS troubleshooting, see `WSL_DDS_FINAL_CHECK.md`.

## Troubleshooting

- `SDK_NOT_INITIALIZED`: check SDK install, CycloneDDS, NIC name, and whether the robot is powered on.
- `ROBOT_OFFLINE`: state has not been received or is stale.
- `INVALID_MOTION_PARAMETER`: speed or duration exceeds the first-stage safety limits.
- `CAMERA_DECODE_FAILED`: camera returned bytes that are not a valid JPEG.

Freeze versions before acceptance:

```bash
cd ../unitree_sdk2_python
git rev-parse HEAD > ../go2-gateway/SDK_COMMIT.txt
pip freeze > ../go2-gateway/PIP_FREEZE.txt
```
