# Go2 Navigation Mock Contract

- Contract version: `1.0.0`
- Frozen at: `2026-07-22`
- Status: Step 2 frozen; implementation is deferred to Step 3+
- Service: `go2-gateway`

## 1. Purpose

This contract defines the first-phase Mock-only navigation boundary for mapping, maps, patrol, emergency dispatch, return-home, point-cloud visualization, audio state, safety interlocks, and control ownership.

Every response in this domain must preserve the following invariant:

```json
{
  "provider": "mock",
  "real_motion_enabled": false
}
```

Advancing a Mock state machine must never call the Unitree SDK motion methods, `/cmd_vel`, ROS2, Nav2, SLAM Toolbox, real LiDAR point-cloud subscriptions, the Go2 microphone/speaker, or real ASR/TTS services.

## 2. Existing implementation baseline

The contract reflects the current repository instead of describing reserved functionality as implemented:

- `/api/lidar/status` and `/api/robot/lidar/status` are implemented read-only diagnostics.
- `go2_lidar_mapping_contract.md` explicitly prohibits mapping, localization, goals, and cruise; this contract does not alter that boundary.
- `CapabilityService.navigation` currently reports no SLAM/path planning and exposes scripted/fixed motion capabilities from the older gateway domain.
- `RobotTaskManager.create_patrol_task()` currently returns `TASK_NOT_SUPPORTED`.
- `ControlLock` currently provides only a non-blocking busy lock; it does not yet implement named ownership.
- Existing fall/target task services may call adapter movement. The new `/api/navigation/**` domain must not reuse such a path unless a Mock-only adapter boundary and a no-motion test prove that no motion method is invoked.

Therefore all endpoints below are frozen target contracts, not claims about current routes.

## 3. Relationship to existing APIs

- Existing robot task, movement, fall, status, camera, and voice endpoints remain unchanged in Step 2.
- Existing `/api/tasks/patrol` remains reserved until Step 3 explicitly decides whether it becomes an alias of `/api/navigation/patrol/start`.
- The new navigation service must have its own Mock provider and may reuse DTO conventions, audit utilities, and callback delivery utilities.
- It must not delegate navigation operations to the existing real-capable `robot_service.move()` path.
- `health_new` is the only browser-facing caller. A browser must not call these 8090 endpoints directly.

## 4. Capability contract

```http
GET /api/navigation/capabilities
```

Successful `data`:

```json
{
  "provider": "mock",
  "real_motion_enabled": false,
  "mapping": "mock",
  "maps": "mock",
  "localization": "mock",
  "path_planning": "mock",
  "patrol": "mock",
  "emergency_dispatch": "mock",
  "return_home": "mock",
  "point_cloud": "mock",
  "audio_input": "mock",
  "audio_output": "mock",
  "manual_takeover": "mock",
  "ros2": "unavailable",
  "nav2": "unavailable",
  "slam_toolbox": "unavailable",
  "real_lidar_point_cloud": "not_verified"
}
```

Allowed capability values are `mock | unavailable | not_verified | blocked | ready`. First-phase real navigation capabilities must not return `ready`.

## 5. Response envelope and HTTP behavior

New endpoints return:

```json
{
  "success": true,
  "code": "OK",
  "message": "ok",
  "data": {},
  "timestamp": "2026-07-22T10:00:00+08:00"
}
```

- A valid diagnostic/state request returns HTTP 200 even when the Mock task is blocked.
- A conflicting state transition returns HTTP 409 with a machine-readable code.
- Invalid input returns HTTP 4xx.
- An unrecoverable service failure returns HTTP 503.
- `REAL_MOTION_DISABLED` is not a reason to silently enable motion; it is an explicit invariant or rejection code.

## 6. State snapshot

```http
GET /api/navigation/state
```

Minimum `data`:

```json
{
  "provider": "mock",
  "real_motion_enabled": false,
  "mapping_state": "idle",
  "active_map_id": null,
  "localization_valid": false,
  "control_owner": "NONE",
  "emergency_stop_active": false,
  "active_task": null,
  "safety_interlock": null,
  "mock_scenario": "robot_ready",
  "updated_at": "2026-07-22T10:00:00+08:00"
}
```

`mapping_state`: `idle | mapping | preview_ready | saved | cancelled | failed`.

The Mock pose, target, route, and path may be included in `active_task`, but every such object must carry `source="mock"`.

## 7. Mapping and map APIs

```http
POST /api/navigation/mapping/start
POST /api/navigation/mapping/stop
POST /api/navigation/maps/save
GET  /api/navigation/maps/active
```

### Start mapping

Request:

```json
{
  "session_name": "classroom_demo",
  "request_id": "req_xxx"
}
```

It starts only a deterministic Mock mapping session. It must not start a sensor, subscribe to real point clouds, initialize ROS2, switch robot modes, or move the robot.

### Stop mapping

Request:

```json
{
  "session_id": "mapping_xxx",
  "request_id": "req_xxx"
}
```

It changes the Mock state to `preview_ready` and returns a Mock preview descriptor. It does not save a real map.

### Save map

Request:

```json
{
  "session_id": "mapping_xxx",
  "name": "演示区地图",
  "replace_map_id": null,
  "confirmed": true,
  "request_id": "req_xxx"
}
```

The gateway may retain ephemeral Mock map state and return a Mock map descriptor. `health_new` remains the business source of truth for map metadata, named points, route definitions, versions, and invalidation. No PGM/YAML or robot map is written, loaded, overwritten, or deleted by this contract.

## 8. Patrol and task control APIs

```http
POST /api/navigation/patrol/start
POST /api/navigation/tasks/{task_id}/pause
POST /api/navigation/tasks/{task_id}/resume
POST /api/navigation/tasks/{task_id}/stop
```

Start request uses resolved Mock points supplied by health_new:

```json
{
  "external_task_id": "health_task_xxx",
  "route_id": "route_xxx",
  "map_id": "map_mock_default",
  "point_ids": ["patrol_1", "patrol_2"],
  "return_home_point_id": "robot_home",
  "request_id": "req_xxx"
}
```

The gateway validates identifiers and advances a deterministic Mock task. Coordinates, if included for visualization, are Mock values and must never be forwarded to a motion adapter.

`resume` always reruns the complete safety interlock. `stop` is an administrative Mock task transition, not a call to the existing robot stop/motion endpoint. Emergency stop remains a separate existing safety capability and is not simulated by `stop`.

## 9. Emergency dispatch and return home

```http
POST /api/navigation/emergency/dispatch
POST /api/navigation/return-home
```

Dispatch request:

```json
{
  "incident_id": "incident_20260722_001",
  "external_task_id": "health_task_xxx",
  "map_id": "map_mock_default",
  "target_point_id": "fall_observation_point",
  "request_id": "req_xxx"
}
```

`target_point_id` is resolved by health_new. The gateway must reject `camera_id` or `area_id` as substitutes for a navigation target; it owns execution, not area mapping.

Return-home request:

```json
{
  "external_task_id": "health_task_xxx",
  "home_point_id": "robot_home",
  "reason": "safe_response_confirmed_by_admin",
  "request_id": "req_xxx"
}
```

Return-home is accepted only after a new safety check. It remains a Mock transition and must not call movement APIs.

## 10. Safety interlock

Canonical result:

```json
{
  "passed": false,
  "checks": {
    "robot_online": true,
    "emergency_stop_clear": true,
    "localization_valid": false,
    "map_loaded": true,
    "path_plannable": false,
    "robot_stationary": true,
    "control_available": true
  },
  "blocked_by": ["LOCALIZATION_INVALID", "PATH_NOT_PLANNABLE"],
  "checked_at": "2026-07-22T10:00:00+08:00"
}
```

All values are Mock scenario outputs in phase one. `passed=true` authorizes only a Mock transition. The service must additionally assert `real_motion_enabled=false` immediately before every transition that represents departure, resume, or return.

Required checks apply to patrol start/resume, emergency dispatch/resume, and return-home. A blocked task remains queryable and is never automatically retried after conditions change.

## 11. Control ownership state machine

Canonical owners:

```text
NONE
MANUAL
NAVIGATION
FOLLOW
EMERGENCY_STOP
```

Priority:

```text
EMERGENCY_STOP > MANUAL > NAVIGATION/FOLLOW > NONE
```

Rules:

- Only one owner exists at a time.
- Navigation start acquires `NAVIGATION` after a passed Mock interlock.
- Mock takeover changes `NAVIGATION → MANUAL` and task state to `paused_manual`.
- Mock release changes `MANUAL → NONE`; it does not resume the task.
- Explicit resume reruns the interlock and may acquire `NAVIGATION`.
- Emergency stop changes any owner to `EMERGENCY_STOP`; clearing it does not restore the previous owner or resume work.
- The current low-level `ControlLock.busy` may be used as one check but cannot replace the named owner state.

Mock takeover APIs to freeze:

```http
GET  /api/navigation/control
POST /api/navigation/control/manual-takeover
POST /api/navigation/control/release
```

These endpoints change state only. They accept no `vx`, `vy`, `wz`, direction, gait, pose, or duration fields.

## 12. Task states and compatibility

Fine-grained `execution_state`:

```text
created
safety_checking
blocked
queued
navigating
paused_manual
paused_admin
arrived
voice_prompting
waiting_response
waiting_admin_confirmation
returning_home
completed
failed
cancelled
```

The existing gateway/health task contract uses uppercase statuses and steps. Step 3 must map fine states into the existing values rather than replacing them:

```text
QUEUED | RUNNING | COMPLETED | FAILED | CANCELLED | BLOCKED
```

The result must include both `status` and `execution_state` during migration. Terminal states must agree. Exact mapping is deferred to Step 3 because it depends on the selected persistence model.

## 13. Mock point cloud

Gateway upstream stream reserved by this contract:

```text
WS /ws/navigation/point-cloud
```

Only health_new may consume and proxy it to browser clients. Frame envelope:

```json
{
  "type": "mock_point_cloud_frame",
  "sequence": 42,
  "timestamp": "2026-07-22T10:00:00+08:00",
  "provider": "mock",
  "real_motion_enabled": false,
  "frame_id": "mock_map",
  "points": []
}
```

Point encoding, maximum point count, frame rate and compression are intentionally deferred to Step 3. Mandatory constraints are bounded memory, deterministic data, disconnect cleanup and no real L1 subscription.

## 14. Mock audio

This navigation contract does not expose a real microphone or speaker endpoint. Emergency task events may include:

```json
{
  "audio_input_provider": "mock",
  "audio_output_provider": "mock",
  "asr_status": "mock_waiting",
  "tts_status": "mock_completed"
}
```

Mock response selection is controlled by a centralized scenario provider. No raw audio is accepted, stored, streamed or logged. Existing `/api/voice/status` remains outside this contract and must not be used to claim that real Go2 audio is available.

## 15. Mock scenarios

The centralized provider must support at least:

```text
robot_ready
robot_offline
dds_no_samples
lidar_unavailable
localization_invalid
map_not_loaded
emergency_stop_active
path_not_plannable
manual_takeover
navigation_success
navigation_failure
safe_response
need_help
no_response
uncertain_response
return_home_success
return_home_failure
```

Random Mock behavior scattered through production services is prohibited. Scenario switching must be disabled or protected outside explicit development/test mode. The exact switch mechanism is deferred to Step 3.

## 16. Error codes

```text
MOCK_PROVIDER_REQUIRED
REAL_MOTION_DISABLED
ROBOT_OFFLINE
DDS_NOT_READY
LIDAR_NOT_READY
LOCALIZATION_INVALID
MAP_NOT_LOADED
MAP_POINTS_INVALID
PATH_NOT_PLANNABLE
EMERGENCY_STOP_ACTIVE
CONTROL_NOT_AVAILABLE
MANUAL_CONTROL_ACTIVE
INVALID_CONTROL_TRANSITION
NAVIGATION_NOT_READY
TASK_NOT_FOUND
TASK_STATE_CONFLICT
```

## 17. Prohibited actions

1. Calling `robot_service.move()`, adapter movement methods, Unitree sport clients, `/cmd_vel`, stand/sit/pose commands, or real stop as part of Mock navigation.
2. Starting or stopping real LiDAR, ROS2, Nav2, SLAM Toolbox, localization, mapping, path planning, goals, patrol, or cruise.
3. Reading real L1 point-cloud samples.
4. Recording or playing real robot audio or calling real ASR/TTS.
5. Accepting camera areas as navigation targets without health_new resolution.
6. Returning `ready=true` or equivalent for unverified real capabilities.
7. Treating `mappingPrerequisitesReady=true` as permission to map or move.
8. Automatically resuming a task after manual release or an interlock recovery.

## 18. Step 3 implementation decisions

- Location of the Mock provider, state store, and named owner implementation.
- Whether `/api/tasks/patrol` remains reserved or becomes a compatibility alias.
- Idempotency storage and request ID limits.
- Callback event types and mapping to the current task audit/callback mechanism.
- Exact Mock map representation and health_new persistence handoff.
- Point-cloud wire encoding and resource limits.
- Mock scenario switch authorization.

No Step 3 choice may weaken `real_motion_enabled=false` or route Mock transitions through a real-capable motion adapter.
