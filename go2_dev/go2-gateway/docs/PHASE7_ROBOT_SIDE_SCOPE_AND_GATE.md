# Phase 7 Robot-Side Scope and Safety Gate

Date: 2026-08-23

## Frozen scope

The Go2 side owns only:

```text
UWB -> FollowController -> MotionArbiter -> LidarSafetyGuard
    -> RealFollowExecutor -> RobotService -> SportClient.Move

external fall event -> MotionArbiter -> StopMove -> risk state
```

Video transport, few-shot fall classification, and Qwen review are external
black boxes. This repository validates their event contract; it does not
implement those systems. SLAM and Nav2 remain paused.

## External risk event contract

Confirmed fall:

```json
{
  "event_type": "FALL_CONFIRMED",
  "confidence": 0.93,
  "timestamp": "2026-08-21T16:00:00+08:00",
  "incident_id": "fall-001"
}
```

Heartbeat / non-fall observation:

```json
{
  "event_type": "NON_FALL",
  "timestamp": "2026-08-21T16:00:01+08:00"
}
```

Rules:

- `timestamp` must contain a timezone.
- `confidence` must be finite and within `[0, 1]`.
- `FALL_CONFIRMED` requires a non-empty `incident_id`.
- duplicate incident IDs are idempotent.
- `NON_FALL` never clears a confirmed fall by itself.
- clearing requires `PAUSED_BY_FALL -> MONITORING`, followed by a fresh
  `NON_FALL` and an explicit clear operation.

## Motion priority

The implemented priority is:

```text
EMERGENCY > MANUAL > LIDAR_STOP > FOLLOW
```

Additional fail-closed inputs are:

- stale or missing external risk heartbeat when the integration gate is on;
- stale or invalid UWB;
- stale, malformed, sparse, or untrusted-frame LiDAR cloud;
- unsafe or simulated FollowController command.

`LIDAR_SLOW` scales the already bounded follow command. It never creates a
new command and never implements obstacle avoidance.

## Real-motion gates

All are required before a real `Move` call:

1. `PHASE7_MOTION_EXECUTION_ENABLED=true`;
2. an explicit supervised-test arm operation;
3. an explicit resume authorization;
4. `MotionArbiter` authority equals `FOLLOW`;
5. fresh UWB;
6. trusted and fresh LiDAR with CLEAR or SLOW result;
7. no emergency, manual takeover, or active fall incident;
8. external risk heartbeat is fresh when integration is enabled.

Defaults remain:

```text
PHASE7_MOTION_EXECUTION_ENABLED=false
PHASE7_REQUIRE_EXTERNAL_RISK_FEED=true
```

Any preemption clears resume authorization. Recovery therefore cannot cause
an automatic surge; the operator must re-authorize after UWB and LiDAR are
safe again.

## LiDAR v1 policy

The first implementation accepts only base-frame clouds named `base_link` or
`cloud_base`. Unknown frames stop the robot. Default forward ROI:

```text
x: 0.10 .. 2.00 m
y: -0.45 .. 0.45 m
z: -0.35 .. 0.65 m
STOP: <= 0.65 m
SLOW: <= 1.20 m, speed scale 0.35
```

Three consecutive clear samples are required to arm, release a latched SLOW,
or recover from STOP. These values are conservative software defaults, not a
completed physical calibration. A live candidate `roi_min_z=-0.25 m` removed
the observed clear-floor false positives, but the production default remains
`-0.35 m` until low-obstacle coverage and absolute distance are revalidated.

## Current status

```text
Phase 7 scope freeze                     PASS
External fall event contract             PASS (offline)
Motion priority and fall preemption       PASS (offline)
LiDAR ROI CLEAR/SLOW/STOP                 PASS (offline)
UWB timeout -> zero motion                PASS (offline)
Real executor default-disabled gate       PASS (offline)
Live UWB capture                          PASS
Real UWB dry-run replay                   PASS
LiDAR CLEAR/SLOW/STOP transitions         PASS
LiDAR absolute physical calibration       HOLD
Real low-speed motion                     CLOSED
```

No SDK publisher, `SportClient`, `Move`, or `StopMove` was invoked while
creating or testing this Phase 7 implementation.

Formal evidence and the current Gate decision are recorded in:

- `docs/PHASE7_ACCEPTANCE_SUMMARY_20260823.md`;
- `docs/PHASE7_1C_LIDAR_STATIC_CALIBRATION_REPORT_20260823.md`;
- `docs/PHASE7_EVIDENCE_INDEX_20260823.md`.
