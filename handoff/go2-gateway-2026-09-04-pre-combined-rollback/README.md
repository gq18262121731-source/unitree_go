# Go2 Gateway

This is the first-stage Unitree Go2 EDU gateway for the local robot project. It exposes a small HTTP API for status, front-camera snapshots, stand, lie down, stop, emergency stop, short low-speed motion, and event-driven robot tasks for the elder-care demo loop.

## Scope

Implemented in this stage:

- Mock mode for development without a robot.
- Real adapter for `unitree_sdk2_python`.
- Stable gateway facade for `connect`, `get_status`, `stand`, `sit`, `stop`, `move`, and `get_camera`.
- FastAPI endpoints for health, status, motion, emergency stop, and JPEG snapshot.
- Event-driven task endpoints for fall confirmation and first-stage target movement.
- Event intake and task-manager boundaries for health-system robot events.
- Local JSONL task audit log for task lifecycle and result persistence.
- Optional `health_new` task status callback.
- Server-side velocity and duration limits.
- Automatic `StopMove()` after every move attempt.
- Control lock, watchdog, shutdown stop, and stale-state protection.
- Pytest coverage for the safety-critical paths.
- Single-instance Companion Lifecycle API for supervised UWB/LiDAR following.

Not implemented in this stage: autonomous SLAM navigation, generic follow tasks,
auto charge, real speech recognition, LLM agents, special actions, flips, jumps,
handstand, low-level motor or joint control. V1 companion following is available
only through the supervised lifecycle endpoints described below.

## Architecture Boundaries

```text
app/gateway/       stable Go2 capability facade over SDK adapters
app/services/      status, motion, camera, voice, feedback, and task execution services
app/task_manager/  task creation/query boundary
app/event/         health_new event intake
app/api/           HTTP route registration
demo/              scenario demo scripts
```

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
- `UNITREE_ROBOT_IP=192.168.123.161` or legacy `GO2_ROBOT_IP`
- `UNITREE_NETWORK_INTERFACE=enp3s0` or legacy `GO2_NETWORK_INTERFACE`
- `UNITREE_DOMAIN_ID=0`
- `UNITREE_DDS_TIMEOUT_SECONDS=10`
- `UNITREE_REQUIRE_DDS_STATE=true`
- `GO2_MAX_VX=0.30`
- `GO2_MAX_VY=0.0`
- `GO2_MAX_WZ=0.30`
- `GO2_MAX_MOVE_DURATION=1.0`
- `GO2_CAMERA_SNAPSHOT_URL=/api/camera/snapshot`
- `GO2_CAMERA_STREAM_URL=/api/camera/stream` or a WebRTC/RTSP bridge URL when available
- `GO2_CAMERA_STREAM_INTERVAL_SECONDS=0.5`
- `GO2_VOICE_MODE=mock`
- `GO2_FALL_PROMPT=您好，请问您现在是否需要帮助？`
- `GO2_VOICE_PROMPT_URL=` optional HTTP speaker bridge. When set, fall-confirmation tasks POST the prompt to this URL.
- `GO2_VOICE_PROMPT_TIMEOUT_SECONDS=2`
- `GO2_VOICE_PROMPT_RETRIES=1`
- `GO2_VOICE_PROMPT_RETRY_DELAY_SECONDS=0.2`
- `GO2_TASK_AUDIT_ENABLED=true`
- `GO2_TASK_AUDIT_LOG_PATH=logs/task-events.jsonl`
- `GO2_LOCATION_MOTION_PLANS_JSON=` optional fixed-point motion plans, for example `{"bedroom":[[0.08,0,0,0.2]]}`
- `FOLLOW_SIMULATION=true` keeps follow output software-only.
- `FOLLOW_EXECUTION_ENABLED=false` remains the independent real-motion gate.
- `PHASE7_MOTION_EXECUTION_ENABLED=false` locks the Phase 7 arbiter-to-robot path.
- `PHASE7_REQUIRE_EXTERNAL_RISK_FEED=true` makes a missing/stale risk feed stop motion after integration.
- `FOLLOW_VELOCITY_FEEDFORWARD_ENABLED=false` enables the optional UWB-derived
  target-velocity feedforward only when explicitly set to `true`.
- `FOLLOW_VELOCITY_FEEDFORWARD_GAIN=1.0`
- `FOLLOW_VELOCITY_FILTER_ALPHA=0.4`
- `FOLLOW_MAX_ESTIMATED_TARGET_SPEED=0.3`
- `FOLLOW_MAX_PLAUSIBLE_TARGET_SPEED=0.8`
- `HEALTH_NEW_CALLBACK_URL=` optional task status callback target
- `HEALTH_NEW_CALLBACK_TOKEN=` optional bearer token for callbacks
- `HEALTH_NEW_CALLBACK_RETRIES=2`
- `HEALTH_NEW_CALLBACK_RETRY_DELAY_SECONDS=0.2`

## Run

Mock mode:

```bash
GO2_MODE=mock uvicorn app.main:app --host 0.0.0.0 --port 8090 --workers 1
```

Real mode:

```bash
GO2_MODE=real GO2_NETWORK_INTERFACE=enp3s0 uvicorn app.main:app --host 0.0.0.0 --port 8090 --workers 1
```

When `UNITREE_ROBOT_IP` or legacy `GO2_ROBOT_IP` is changed for a Wi-Fi or hotspot network, the real adapter rewrites the Unitree SDK CycloneDDS peer from the SDK default `192.168.123.161` to the configured robot IP.

Use exactly one worker. Do not start multiple gateway instances against the same robot.
Real mode distinguishes network reachability from DDS readiness. A successful ping is not enough for `robotOnline=true`; at least one real `SportModeState` or `LowState` DDS sample must be received and fresh before motion is allowed.

For safe follow-algorithm validation, keep `FOLLOW_SIMULATION=true` and
`FOLLOW_EXECUTION_ENABLED=false`. The velocity feedforward is disabled by
default, requires an ordered monotonic timestamp for each new UWB sample, and
falls back to the original proportional controller when timing is unavailable.
It rejects implausible target-speed spikes, suppresses feedforward for abnormal
yaw, and clears all estimator state on UWB timeout, distance stop, or control
ownership change.

The Phase 7.1 physical UWB calibration uses `orientation_est` as the target
bearing. `yaw_est` is not a target-bearing input. The current device/tag/test
pose calibration is explicit and replaceable:

```text
UWB_BEARING_SOURCE=orientation_est
UWB_BEARING_UNIT=radians
UWB_BEARING_SIGN=1
UWB_BEARING_ZERO_OFFSET_RAD=0.55
```

The zero offset is observed calibration for this hardware setup, not a
Unitree-published universal constant.

## Phase 7 robot-side motion boundary

Phase 7 is limited to UWB following, LiDAR forward safety, external fall-event
preemption, and a single motion arbiter. Video, fall-model inference, Qwen,
SLAM, and Nav2 are outside this phase.

The Phase 7 executor accepts only a `MotionArbiter` decision, never a raw
`FollowController` command. Even with its environment gate enabled, it still
requires an explicit supervised-test arm and resume authorization. Any
emergency, manual takeover, LiDAR STOP, stale UWB, or active fall incident
clears resume authorization and calls the stop path.

See `docs/PHASE7_ROBOT_SIDE_SCOPE_AND_GATE.md` and
`docs/UWB_FOLLOW_INPUT_ACCEPTANCE_PHASE_7_1.md`.

### Go2 Companion Follow V1

The live UWB-follow gate now inserts a side-effect-free `CompanionSupervisor`
between UWB planning and motion arbitration. The supervisor owns the complete
behavior state machine (`FOLLOWING`, stationary-person observation, target
loss, obstacle stop, fall monitoring, recovery, and `WAIT_RESUME`) while the
existing `MotionArbiter`, LiDAR guard, and executor remain independent
fail-closed gates.

The first 3 m x 3 m profile uses a 1.5 m rear / 0.5 m right target, 1.80/1.70 m
distance hysteresis, a 12 degree bearing dead band, 0.20 m/s minimum effective
walking speed, 0.30 m/s forward cap, and 0.30 rad/s yaw cap. A confirmed fall
cannot return directly to following: it must pass through monitoring, stable
recovery, `WAIT_RESUME`, and an explicit operator resume.

See `docs/GO2_COMPANION_FOLLOW_V1_IMPLEMENTATION_20260824.md` for the state and
integration contract. Real motion remains subject to the existing typed live
gate and is never enabled by importing or constructing the supervisor.

### P0-1 Companion Lifecycle Service

The Phase 7 loop is available as one process-owned runtime. HTTP handlers may
only call `status`, `start`, `stop`, or `resume`; they cannot submit raw velocity
commands. Startup waits for fresh UWB, confirmed LiDAR clearance, a fresh risk
heartbeat, an online robot, and free control ownership. `STOP` is global and
idempotent. `RESUME` is accepted only from `WAIT_RESUME` and resets controller
dynamic state before motion can be authorized again.

The gateway always starts in `IDLE`; real-mode startup issues `StopMove()` and
never restores a previous following session. The temporary P0-1 real risk source
is an append-only JSONL file configured by `GO2_COMPANION_RISK_EVENTS_PATH`.
P0-4 will replace that bridge with the unified risk-event integration.

See `docs/GO2_COMPANION_LIFECYCLE_P0_1.md` for the formal API and gates.

## API

For the `health_new` integration contract, see `HEALTH_NEW_INTEGRATION.md`.
Use `/api/capabilities` for runtime discovery of supported event URLs, task URLs, source fields, voice status URL, feedback status, and navigation boundaries.
Use `/health` for a compact operations summary: dispatch readiness, robot online/stale/busy flags, current active task, and feedback queue counters.
Use `/api/preflight` for a non-motion integration checklist before a live demo. It aggregates connection, immediate readiness, queued-task acceptance, camera status without sampling a new frame, voice bridge readiness, feedback queue health, and capability URLs. It always returns HTTP 200; use `/api/readiness` as the strict immediate-dispatch gate.
Use `/api/robot/diagnostics/dds` for read-only Go2 network/DDS diagnostics. It reports `networkReachable`, `ddsInitialized`, `ddsStateAvailable`, `robotOnline`, `motionReady`, route/interface details, DDS topic sample counts, warnings, and recommendations.

```bash
curl http://127.0.0.1:8090/health
curl http://127.0.0.1:8090/api/preflight
curl http://127.0.0.1:8090/api/capabilities
curl http://127.0.0.1:8090/api/locations
curl "http://127.0.0.1:8090/api/locations/resolve?location=%E5%8D%A7%E5%AE%A4"
curl http://127.0.0.1:8090/api/connection
curl -X POST http://127.0.0.1:8090/api/connection/reconnect
curl http://127.0.0.1:8090/api/readiness
curl http://127.0.0.1:8090/api/status
curl http://127.0.0.1:8090/api/robot/status
curl http://127.0.0.1:8090/api/voice/status
curl http://127.0.0.1:8090/api/v1/robot/companion/status
curl -X POST http://127.0.0.1:8090/api/v1/robot/companion/start
curl -X POST http://127.0.0.1:8090/api/v1/robot/companion/stop
curl -X POST http://127.0.0.1:8090/api/v1/robot/companion/resume
curl -X POST http://127.0.0.1:8090/api/robot/stand
curl -X POST http://127.0.0.1:8090/api/robot/sit
curl -X POST http://127.0.0.1:8090/api/robot/stop
curl -X POST http://127.0.0.1:8090/api/robot/emergency-stop
curl -X POST http://127.0.0.1:8090/api/robot/lie-down
```

Use `/api/readiness` before dispatching a robot task that must start immediately. It returns 200 when the gateway is initialized, the robot is online, motion control is enabled, state is fresh, and no task is active. It returns 403 for `CONTROL_DISABLED`, 503 for `SDK_NOT_INITIALIZED`, `ROBOT_OFFLINE`, or `ROBOT_STATE_STALE`, and 409 for `CONTROL_BUSY`. The task endpoints use the looser acceptance guard: while another task is active they can still accept a new task as `waiting` if `accepting_tasks=true`. Both `/api/connection` and `/api/readiness` include `last_error` so the main system can display the latest SDK/network initialization failure.

`/api/status` is the compact robot card contract for `health_new`: it exposes `online`, `battery`, `battery_detail`, `mode`, `action`, `action_updated_at`, `busy`, `control_enabled`, `state_stale`, `last_seen`, current task fields, `error`, and `last_error` at the top level.

Short move:

```bash
curl -X POST http://127.0.0.1:8090/api/robot/move \
  -H "Content-Type: application/json" \
  -d '{"vx":0.1,"vy":0.0,"wz":0.0,"duration":0.3}'
```

Snapshot:

```bash
curl http://127.0.0.1:8090/api/camera/status
curl http://127.0.0.1:8090/api/camera/stream
curl http://127.0.0.1:8090/api/camera/snapshot --output go2_snapshot.jpg
curl http://127.0.0.1:8090/api/robot/camera/stream
curl http://127.0.0.1:8090/api/robot/camera/snapshot --output go2_snapshot.jpg
```

Fall event to robot task:

```bash
curl -X POST http://127.0.0.1:8090/api/events/fall \
  -H "Content-Type: application/json" \
  -d '{"event":"fall_detected","elder_id":"001","location":"bedroom","confidence":0.94,"source_event_id":"camera-fall-001","callback_url":"http://127.0.0.1:8080/api/robot/callback"}'

curl -X POST http://127.0.0.1:8090/api/tasks/confirm-fall \
  -H "Content-Type: application/json" \
  -d '{"task":"confirm_fall","elder_id":"001","location":"卧室","confidence":0.94,"source_event_id":"camera-fall-001","taskId":"health-task-001"}'

curl -X POST http://127.0.0.1:8090/api/robot/events/fall \
  -H "Content-Type: application/json" \
  -d '{"event":"fall_detected","elder_id":"001","location":"bedroom","confidence":0.94}'
```

`/api/tasks/confirm-fall` is the preferred semantic endpoint when `health_new` has already converted a camera event into a robot task. It accepts `taskId` as the main-system task/correlation ID and internally reuses the same `confirm_fall` task manager path as `/api/events/fall`.

`source_event_id` is optional, but when supplied it is treated as an idempotency key. The gateway also accepts camera-service aliases `sourceEventId`, `event_id`, `eventId`, `camera_event_id`, and `cameraEventId`. Re-sending the same fall event returns the existing task instead of creating a second robot motion task. If the replay includes a `callback_url` and the original task did not have one, the gateway attaches it and publishes the current task state to that callback.
`external_task_id` is also treated as a health_new idempotency/correlation key. Re-sending the same external task ID returns the existing robot task, and a replay of the same `source_event_id` can attach a missing external task ID for later lookup.
Use `GET /api/events/fall/<sourceEventId>` or `GET /api/robot/events/fall/<sourceEventId>` to check whether a camera fall event has already been accepted and which robot task it maps to.

`GET /api/locations` lists the first-stage fixed motion plans and Chinese aliases such as `卧室`, `卫生间`, `客厅`, and `厨房`. `GET /api/locations/resolve?location=卧室` lets `health_new` check the canonical fixed point and fallback plan before dispatch. This remains a fixed-point motion contract, not SLAM or autonomous navigation.
Use `GET /api/tasks/external/<externalTaskId>` or `GET /api/robot/tasks/external/<externalTaskId>` when `health_new` needs to recover the internal robot task ID from its own task ID.

Task query responses and callback payloads include a monotonic `revision`. The gateway sends callbacks through one FIFO worker, and `health_new` should still keep the highest processed revision per `task_id` and ignore older callback payloads that arrive late during network retries or integration edge cases. Task status, summaries, timeline, `/api/status`, and callback payloads also expose `steps` plus `progress` for frontend stepper displays, and expose `finished` so polling clients know when a task reached `finished`, `failed`, or `cancelled`.

When the robot is already executing a task, new accepted robot tasks are stored as `waiting` and executed FIFO after earlier non-terminal tasks finish. The gateway does not send concurrent motion commands. `health_new` can keep submitting fall events during a current response and use the returned `task_id` or `external_task_id` to poll status or receive callbacks. Status, summary, result, timeline, `/api/status`, and callback payloads expose `queue_position`, `queue_size`, `queue_head`, `blocked_by_task_id`, and `queue` so the robot page can show whether a task is executing or waiting behind another task. `/api/tasks/queue` returns the current non-terminal FIFO queue with `active`, `waiting`, and ordered `tasks` lists for the robot operations page.

Task status:

```bash
curl http://127.0.0.1:8090/api/tasks
curl http://127.0.0.1:8090/api/tasks/summary?limit=50
curl http://127.0.0.1:8090/api/tasks/active
curl http://127.0.0.1:8090/api/tasks/queue
curl http://127.0.0.1:8090/api/tasks/<taskId>
curl http://127.0.0.1:8090/api/tasks/<taskId>/status
curl http://127.0.0.1:8090/api/tasks/<taskId>/result
curl http://127.0.0.1:8090/api/tasks/<taskId>/timeline
curl http://127.0.0.1:8090/api/tasks/<taskId>/audit-log
curl http://127.0.0.1:8090/api/tasks/external/<externalTaskId>
curl http://127.0.0.1:8090/api/tasks/external/<externalTaskId>/status
curl http://127.0.0.1:8090/api/tasks/external/<externalTaskId>/result
curl http://127.0.0.1:8090/api/tasks/external/<externalTaskId>/timeline
curl http://127.0.0.1:8090/api/tasks/external/<externalTaskId>/audit-log
curl http://127.0.0.1:8090/api/tasks/audit-log?limit=50
curl http://127.0.0.1:8090/api/feedback/status
curl -X POST http://127.0.0.1:8090/api/tasks/<taskId>/feedback/replay \
  -H "Content-Type: application/json" \
  -d '{"callback_url":"http://127.0.0.1:8080/api/robot/callback"}'
curl -X POST http://127.0.0.1:8090/api/tasks/external/<externalTaskId>/feedback/replay \
  -H "Content-Type: application/json" \
  -d '{"callback_url":"http://127.0.0.1:8080/api/robot/callback"}'
curl http://127.0.0.1:8090/api/robot/tasks
curl http://127.0.0.1:8090/api/robot/tasks/summary?limit=50
curl http://127.0.0.1:8090/api/robot/tasks/active
curl http://127.0.0.1:8090/api/robot/tasks/queue
curl http://127.0.0.1:8090/api/robot/tasks/<taskId>
curl http://127.0.0.1:8090/api/robot/tasks/<taskId>/status
curl http://127.0.0.1:8090/api/robot/tasks/<taskId>/result
curl http://127.0.0.1:8090/api/robot/tasks/<taskId>/timeline
curl http://127.0.0.1:8090/api/robot/tasks/<taskId>/audit-log
curl http://127.0.0.1:8090/api/robot/tasks/external/<externalTaskId>
curl http://127.0.0.1:8090/api/robot/tasks/external/<externalTaskId>/status
curl http://127.0.0.1:8090/api/robot/tasks/external/<externalTaskId>/result
curl http://127.0.0.1:8090/api/robot/tasks/external/<externalTaskId>/timeline
curl http://127.0.0.1:8090/api/robot/tasks/external/<externalTaskId>/audit-log
curl http://127.0.0.1:8090/api/robot/tasks/audit-log?limit=50
curl http://127.0.0.1:8090/api/robot/feedback/status
curl -X POST http://127.0.0.1:8090/api/robot/tasks/<taskId>/feedback/replay \
  -H "Content-Type: application/json" \
  -d '{"callback_url":"http://127.0.0.1:8080/api/robot/callback"}'
curl -X POST http://127.0.0.1:8090/api/robot/tasks/external/<externalTaskId>/feedback/replay \
  -H "Content-Type: application/json" \
  -d '{"callback_url":"http://127.0.0.1:8080/api/robot/callback"}'
```

`/api/tasks/<taskId>/result` returns the compact result contract for `health_new`: task status, confirmation result, robot camera URLs, voice result, voice delivery status, source event, structured failure fields (`error_code`, `failure_step`), error, and `finished`. If Go2 camera confirmation fails, `camera` becomes `failed`, `confirm` becomes `unknown`, and `robot_camera.snapshot` becomes `failed`.
`/api/tasks/summary?limit=50` returns recent task summaries using the same top-level context fields as task status, so the `health_new` robot page can render a task list without unpacking raw task internals.
`/api/status`, task status, summaries, timeline, and callback payloads include `steps`, `progress`, and `finished` so the frontend can render the fall-response flow directly and stop polling terminal tasks: receive event, moving, arrived, robot camera, voice check, finished.
`/api/status`, task status, result, timeline, and callback payloads include top-level `elder_id`, `location`, `location_resolution`, `confidence`, `source_event_id`, `camera_id`, and `external_task_id` in addition to the original `source` object, so `health_new` can route alerts without unpacking nested fields. `location_resolution` contains the canonical fixed point, whether fallback was used, and the first-stage motion plan snapshot for audit/debug displays.
`/api/tasks/audit-log` returns recent JSONL audit entries for the robot event center or operations view. `/api/tasks/<taskId>/audit-log` and `/api/tasks/external/<externalTaskId>/audit-log` return the persisted lifecycle for one robot task, including terminal `result`, so `health_new` can trace a single fall response after callbacks or polling have finished. On gateway startup, terminal tasks (`finished`, `failed`, `cancelled`) are restored from the audit log so `health_new` can still query result/timeline by `taskId`, `source_event_id`, or `external_task_id` after a restart; non-terminal tasks are not resumed to avoid unintended robot motion.
`/api/feedback/status` returns callback configuration, queue backlog, sent/failed/dropped counters, and the latest health_new callback delivery error.
`POST /api/tasks/<taskId>/feedback/replay` queues the current task snapshot to the task callback URL, the global `HEALTH_NEW_CALLBACK_URL`, or the request body `callback_url`. `POST /api/tasks/external/<externalTaskId>/feedback/replay` does the same lookup by the `taskId`/`external_task_id` that `health_new` supplied at dispatch time. Use these when `health_new` recovers from a temporary callback outage and wants the latest task state pushed again.
`GET /api/tasks/latest` returns the most recently updated task summary with `exists=true`; before any task exists it returns `{"exists":false,"task_id":null,"task":null,"status":"none"}`. This is the simplest endpoint for a dashboard card that wants the last robot response after `/api/status` has returned to idle.

Cancel a running task and stop the robot:

```bash
curl -X POST http://127.0.0.1:8090/api/tasks/<taskId>/cancel \
  -H "Content-Type: application/json" \
  -d '{"reason":"operator_cancel"}'

curl -X POST http://127.0.0.1:8090/api/tasks/external/<externalTaskId>/cancel \
  -H "Content-Type: application/json" \
  -d '{"reason":"health_new_cancel"}'
```

`health_new` can cancel by the internal robot task ID or by its own `externalTaskId`. When the gateway shuts down with an active task, it marks that task as `cancelled` with `error=gateway_shutdown`, publishes the final callback, waits for the task worker to exit, then closes the robot connection.

Record elder voice feedback:

```bash
curl -X POST http://127.0.0.1:8090/api/tasks/<taskId>/voice-result \
  -H "Content-Type: application/json" \
  -d '{"voice_result":"need_help","need_help":true}'

curl -X POST http://127.0.0.1:8090/api/tasks/external/<externalTaskId>/voice-result \
  -H "Content-Type: application/json" \
  -d '{"voice_result":"need_help","need_help":true}'
```

Voice feedback is only valid for `confirm_fall` tasks. It can be recorded by internal `taskId` or by the `externalTaskId` supplied by `health_new`, while a fall-confirmation task is running or after it reaches normal `finished`. For non-fall, `cancelled`, or `failed` tasks, the API returns HTTP 409 with `TASK_STATE_CONFLICT` so `health_new` does not attach elder feedback to an invalid robot run.

Optional voice speaker bridge:

```bash
GO2_VOICE_MODE=http
GO2_VOICE_PROMPT_URL=http://127.0.0.1:8091/api/speak
```

When configured, the gateway POSTs `task_id`, `elder_id`, `prompt`, `voice_mode`, and `prompted_at` to the speaker bridge during `voice_check`. If delivery fails, the task result exposes `voice=failed`, `voice_delivery=failed`, and `voice_error` while the fall-confirmation task remains queryable.

`GET /api/voice/status` and `/api/preflight` expose `ready`, `delivery_mode`, `prompt_url_configured`, and `next_action`. If `GO2_VOICE_MODE=http` is set without `GO2_VOICE_PROMPT_URL`, voice readiness is reported as `not_configured` instead of being silently treated as mock playback.

Reserved task contracts:

```bash
curl -X POST http://127.0.0.1:8090/api/tasks/follow \
  -H "Content-Type: application/json" \
  -d '{"target":"elder001"}'

curl -X POST http://127.0.0.1:8090/api/tasks/patrol \
  -H "Content-Type: application/json" \
  -d '{"route":"night"}'
```

The generic `/api/tasks/follow` and patrol routes still return
`TASK_NOT_SUPPORTED`. V1 UWB companion following is intentionally a separate,
single-instance safety lifecycle and is not dispatched through those task
routes.

First-stage target movement:

```bash
curl http://127.0.0.1:8090/api/locations

curl -X POST http://127.0.0.1:8090/api/tasks/target-move \
  -H "Content-Type: application/json" \
  -d '{"location":"bedroom"}'
```

`/api/robot/tasks/target-move` remains available as a compatibility alias.

## Verification Scripts

```bash
python scripts/verify_release.py
python scripts/check_environment.py
GO2_MODE=real python scripts/check_environment.py --strict-real
python scripts/verify_preflight.py --base-url http://127.0.0.1:8090 --require-ready
python scripts/verify_state.py
python scripts/verify_camera.py
python scripts/verify_motion.py
python scripts/verify_health_new_contract.py
python scripts/verify_real_acceptance.py --base-url http://127.0.0.1:8090 --exercise-camera --require-dispatch-ready
python scripts/verify_running_gateway_fall_loop.py --base-url http://127.0.0.1:8090 --need-help --external-task-id health-task-demo-001
./scripts/verify_gateway_readonly.sh
python demo/simulate_fall_event.py --base-url http://127.0.0.1:8090 --need-help --external-task-id health-task-demo-001
```

Read-only DDS diagnostics against a running network path:

```bash
python tools/dds_diagnostics.py --interface WLAN --domain-id 0 --peer 192.168.43.147 --timeout 10
```

This script only subscribes to Go2 `SportModeState` and `LowState`; it does not publish `rt/lowcmd`, call motion RPCs, stand, sit, or move.

`verify_health_new_contract.py` runs the main dispatch contract in Mock mode without a real robot or a running uvicorn server. It also verifies `/health`, `/api/preflight`, `/api/capabilities`, voice readiness/status discovery, Chinese location alias resolution, the recommended target-move path, external-task direct status/result/timeline queries, successful terminal callbacks, feedback delivery status, feedback replay, snake_case/camelCase payload compatibility, voice-result callbacks, recent audit-log queries, and the structured Go2 camera failure contract.

`/api/capabilities` exposes the confirm-fall task status vocabulary for `health_new`: `waiting`, `running`, `moving`, `arrived`, `checking`, `finished`, `failed`, and `cancelled`; terminal statuses are `finished`, `failed`, and `cancelled`.

`verify_release.py` is the safe default before handoff: Python compile, `verify_health_new_contract.py`, and `pytest -q`. Add `--running-base-url http://127.0.0.1:8090` to include non-motion preflight against an already running gateway.

`check_environment.py` prints local SDK, network interface, route, and ping diagnostics. On a real Go2 host, run `GO2_MODE=real python scripts/check_environment.py --strict-real` before starting the gateway; add `--require-ping` when the robot network is expected to respond to ICMP.

`verify_preflight.py` targets an already running gateway and performs only non-motion HTTP checks, including voice readiness visibility. Use `--require-ready` before dispatch testing; use `--allow-readonly` when checking a real robot with `GO2_CONTROL_ENABLED=false`.

`verify_real_acceptance.py` targets an already running gateway and is the staged field acceptance entry point. By default it performs non-motion checks across `/health`, `/api/status`, `/api/preflight`, `/api/capabilities`, location resolution, camera status, voice status, and feedback status. Add `--exercise-camera` to fetch one JPEG snapshot. Add both `--allow-motion --dispatch-fall` only when the operator is ready to run the confirm-fall task loop on a safe test floor. Add `--expect-callback` with `--dispatch-fall` to start a local health_new-style callback receiver and require the terminal callback before the field acceptance passes.

`verify_running_gateway_fall_loop.py` targets an already running gateway over HTTP. Use it after starting the gateway in Mock or real mode to verify `/health`, `/api/preflight`, `/api/capabilities`, readiness, confirm-fall task dispatch, task result query, camera status, feedback status, callback delivery, optional `external_task_id` passthrough with direct external status/result/timeline queries, and optional voice-result write-back by external task ID. It defaults to `/api/tasks/confirm-fall`; pass `--dispatch-mode event` to exercise `/api/events/fall`.

`verify_gateway_readonly.sh` starts a real-mode gateway with `GO2_CONTROL_ENABLED=false`, checks health/status/snapshot, and verifies readiness, direct move, confirm-fall task dispatch, fall-event dispatch, and target-move dispatch all return `CONTROL_DISABLED` without creating a task.

`verify_motion.py` asks for operator confirmation and defaults to stand, stop, and lie-down only.

Live callback demo:

```bash
# terminal 1
python demo/health_new_callback_receiver.py --port 8088
# Optional full payload view:
# python demo/health_new_callback_receiver.py --port 8088 --dump-json

# terminal 2
GO2_MODE=mock uvicorn app.main:app --host 0.0.0.0 --port 8090 --workers 1

# terminal 3
python demo/simulate_fall_event.py \
  --base-url http://127.0.0.1:8090 \
  --callback-url http://127.0.0.1:8088/api/robot/callback \
  --need-help \
  --external-task-id health-task-demo-001
```

The callback receiver prints `rev=<revision>`, `external_task_id`, `progress`, `error_code`, and `failure_step` so operators can spot callback delivery order, health_new task correlation, and failure causes during integration.
The fall-event simulator runs `/api/preflight` before dispatch by default, then prints task revision/progress changes, the compact `/api/tasks/<taskId>/result` payload, `/api/tasks/latest`, and both again after optional `voice-result` write-back. When `--external-task-id` is supplied, the simulator writes voice feedback through `/api/tasks/external/<externalTaskId>/voice-result`, matching the recommended `health_new` integration path. It defaults to the semantic `/api/tasks/confirm-fall` path; pass `--dispatch-mode event` when testing the raw camera-event path. Use `--skip-preflight` only when testing against an older gateway.

## Robot Video Gateway（8093）

统一无线 Runtime 同时提供独立的视频边缘网关契约：`GET /healthz`、`GET /api/v1/video/status`、`GET /api/v1/robot/video` 和 `GET /stream.mjpg`。使用专用启动脚本后，其他电脑和 Flutter 移动端可通过 mDNS/DNS-SD 搜索 `_robot-video._tcp.local`，或通过 `http://robot-gateway.local:8093` 访问机器狗电脑，不保存其 DHCP 地址、不连接 Go2，也不引入 Unitree SDK 或 WebRTC 依赖。

局域网启动（首次配置防火墙时使用管理员 PowerShell）：

```powershell
.\scripts\Start-RobotVideoGateway.ps1 -ConfigureFirewall
```

完整接口、健康判定和验收步骤见 [`docs/ROBOT_VIDEO_GATEWAY.md`](docs/ROBOT_VIDEO_GATEWAY.md)。旧 `/status` 与 `/snapshot` 保留兼容。

## Tests

```bash
pytest -q
```

Current Mock-mode baseline: `151 passed`. Tests run in Mock mode and do not require a real robot.

## Real Go2 EDU Validation Order

1. Record model as Unitree Go2 EDU, then record serial number, firmware, app version, SDK commit, remote controller model, NIC name, and Ubuntu version.
2. Configure Go2 NIC to `192.168.123.99/24`.
3. Run Unitree SDK hello-world publisher/subscriber.
4. Verify DDS state topics, especially `rt/lf/sportmodestate` and `rt/lf/lowstate`.
5. Run official front-camera capture example.
6. Run official high-level example only for `StandUp`, `StopMove`, and `StandDown`.
7. Run `GO2_MODE=real python scripts/check_environment.py --strict-real` from this gateway directory.
8. Start this gateway in real mode.
9. Run `python scripts/verify_real_acceptance.py --base-url http://<gateway-host>:8090 --exercise-camera --require-dispatch-ready`.
10. Verify stand, stop, and lie-down before any translation movement.
11. Test tiny short moves only after safety checks.
12. Run `python scripts/verify_real_acceptance.py --base-url http://<gateway-host>:8090 --allow-motion --dispatch-fall --expect-callback --need-help --external-task-id health-task-demo-001` to verify the full health_new dispatch loop, including terminal callback delivery.

For WSL2-specific DDS troubleshooting, see `WSL_DDS_FINAL_CHECK.md`.

## Troubleshooting

- `SDK_NOT_INITIALIZED`: check SDK install, CycloneDDS, NIC name, and whether the robot is powered on.
- `CONTROL_DISABLED`: `GO2_CONTROL_ENABLED=false`; the gateway is in read-only mode and rejects motion/task dispatch.
- `ROBOT_OFFLINE`: state has not been received or is stale.
- `INVALID_REQUEST`: request JSON or query parameters failed validation; response `data.errors` contains field-level details.
- `INVALID_MOTION_PARAMETER`: speed or duration exceeds the first-stage safety limits.
- `CAMERA_DECODE_FAILED`: camera returned bytes that are not a valid JPEG.
- `TASK_STATE_CONFLICT`: a task cannot accept the requested state update, for example `voice-result` was posted to a non-`confirm_fall`, `cancelled`, or `failed` task.

Freeze versions before acceptance:

```bash
cd ../unitree_sdk2_python
git rev-parse HEAD > ../go2-gateway/SDK_COMMIT.txt
pip freeze > ../go2-gateway/PIP_FREEZE.txt
```
