# Phase 6.2 Acceptance

## Scope

Phase 6.2 integrates the frozen Phase 6.1 Unitree read-only status contract into
`health_new` without changing the frozen Mock task, navigation, emergency, map,
or WebSocket contracts.

Branch:

```text
feature/health-real-readonly-integration-v1
```

Base:

```text
robot-mock-demo-v1.1
5438a22
```

## Implemented

- Added `GET /api/v1/robot/telemetry`.
- Defaulted the integration to `provider=mock`.
- Added strict validation of the Phase 6.1 v1 JSON snapshot.
- Added file and read-only HTTP snapshot sources.
- Rejected any snapshot that enables motion, navigation, or localization.
- Preserved `imu.available=true` independently from
  `imu.semantic_valid=false`.
- Displayed robot online state, optional battery, transport health, LiDAR, IMU,
  odometry, localization HOLD, navigation HOLD, and motion disabled.
- Left existing Mock-only validators and APIs unchanged.

## Verification

Backend:

```text
30 passed
```

This includes the new telemetry tests plus the frozen robot gateway and
navigation API tests.

Frontend:

```text
test:robot-status       PASS
lint:robot-status       PASS
vite production build  PASS
```

Full Mock browser acceptance:

```text
provider                    mock
real_motion_enabled         false
checks                      11
failed checks               0
REST requests               65
WebSocket events            187
unexpected console errors   0
```

The QA process used isolated ports and temporary process environment overrides:

```text
gateway   18090
backend   18000
frontend  15173
DATA_MODE=mock
SERIAL_ENABLED=false
CAMERA_SOURCE_MODE=rtsp
```

Those overrides were not written to project or system configuration. All
processes owned by the acceptance manifest were stopped afterward.

Phase 6.2 changed-file safety scan:

```text
Move()       0
cmd_vel      0
SportClient  0
LowCmd       0
```

## Existing baseline observation

The repository-wide `vue-tsc --noEmit` command still reports pre-existing
frozen-baseline errors in `src/utils/markdown.ts` and `VideoBridgePage.vue`.
No Phase 6.2 source appears in that error list. The focused robot status lint,
contract test, and production build all pass.

## Result

```text
Phase 6.2-A contract                    PASS
Phase 6.2-B backend readonly telemetry PASS
Phase 6.2-C minimal status display     PASS
Phase 6.2-D Mock/Readonly regression   PASS

Final state:
READONLY_WITH_SEMANTIC_HOLD
```

Phase 6.2 does not authorize Phase 6.3, SLAM, Nav2, task dispatch, or motion.
