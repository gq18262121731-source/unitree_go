# Go2 Companion Lifecycle P0-1

## Scope

P0-1 turns the validated Phase 7 UWB/LiDAR control chain into one process-owned
background runtime. It does not add navigation, patrol, return-home, avoidance,
fall-view rotation, audio, or client UI behavior.

The only public operations are:

```text
GET  /api/v1/robot/companion/status
POST /api/v1/robot/companion/start
POST /api/v1/robot/companion/stop
POST /api/v1/robot/companion/resume
```

Responses use the existing gateway envelope. Lifecycle state is returned in
`data`.

## Runtime ownership

`CompanionLifecycleService` owns at most one `CompanionRuntime`.
`CompanionRuntime` owns the read-only UWB/LiDAR input stream, validated field
profile, supervisor, controller, LiDAR guard, risk arbiter, real executor, and
the fail-closed control worker.

Raw `Move()` is not exposed through the lifecycle API.

## START

`START` is idempotent when the runtime is already `FOLLOWING`. Concurrent calls
can observe `STARTING`, but only one runtime is constructed.

Before activation the service requires:

```text
robot online and state fresh
no active legacy robot task
UWB fresh, enabled_from_app=1, error_state=0
LiDAR fresh and clearance confirmation complete
no active fall incident or emergency
no manual takeover
fresh external risk heartbeat
```

Real mode additionally requires all existing motion gates plus a configured
append-only risk file:

```text
GO2_CONTROL_ENABLED=true
GO2_READ_ONLY_MODE=false
FOLLOW_SIMULATION=false
FOLLOW_EXECUTION_ENABLED=true
PHASE7_MOTION_EXECUTION_ENABLED=true
PHASE7_REQUIRE_EXTERNAL_RISK_FEED=true
GO2_COMPANION_RISK_EVENTS_PATH=<append-only JSONL>
GO2_COMPANION_STATE_PATH=data/companion_lifecycle_state.json
GO2_MAX_VX=0.30
GO2_MAX_VY=0.0
GO2_MAX_WZ=0.30
```

The configured profile speed caps must not exceed the gateway's global
`GO2_MAX_VX` and `GO2_MAX_WZ` limits.

The frozen field profile and gateway ceiling are aligned at `vx=0.30 m/s`,
`vy=0`, and `|wz|=0.30 rad/s`. Companion execution independently hard-forces
`vy=0`, and gait startup uses `walk_min_mps=0.20`.

## STOP

`STOP` first issues the safe stop path, then closes input subscriptions, stops
the worker, disarms the executor, releases the runtime, and returns `IDLE`.
It is valid from every state and remains successful when already `IDLE`.

A `STOP` request can cancel a concurrent `START` that is waiting for sensor
readiness.

While the runtime is active it exclusively owns all non-stop robot commands.
Direct HTTP motion, posture, reconnect, and legacy task dispatch return
`CONTROL_BUSY`; `StopMove` and emergency stop remain globally available. The
client-provided `controlSource` field is audit metadata and cannot impersonate
the runtime owner.

## RESUME

`RESUME` is accepted only from `WAIT_RESUME`. Robot readiness, task ownership,
UWB, LiDAR, risk heartbeat, fall lock, emergency state, and manual takeover are
checked again. Controller dynamic state is cleared before executor resume is
authorized, so an old velocity is never reused.

## Restart and worker failure

Real-mode gateway startup issues `StopMove()` and initializes lifecycle state as
`IDLE`. A small atomic marker records whether the previous runtime reached a
clean `STOP`. If startup finds an unclean `ACTIVE` marker, it reports
`resume_required=true`, rejects `START` with `SERVICE_RESTART_INTERRUPTED`, and
requires an explicit idempotent `STOP` acknowledgement before a new start. A
previous runtime is never restored. If the runtime worker raises, it latches an
emergency reason, invokes the safe stop path, disarms the executor, and reports
`SAFE_STOP` with `resume_required=true`.

## Status fields

The response includes:

```text
state, reason, incident_id, resume_required, runtime_active
robot_online
uwb.valid / age_ms / enabled_from_app / error_state / distance / bearing
lidar.valid / state / age_ms / reason / nearest_distance
risk.state / heartbeat_fresh / age_ms / incident / manual_takeover
motion.vx / vy / wz / authority
runtime input and risk-feed diagnostics
```

## Validation boundary

Automated tests use injected runtimes or the explicit Mock adapter. They cover
idempotent start/stop, global stop, invalid resume, UWB/LiDAR startup failures,
restart-to-IDLE, active-task conflict, worker failure stop, and concurrent
double-start. They do not authorize or execute a real Go2 field session.
