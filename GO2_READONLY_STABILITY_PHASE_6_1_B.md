# Phase 6.1-B Go2 Read-Only Stability Gate

## Result

**PASS**

The real Go2 read-only adapter completed an uninterrupted 1,800-second
stability run. All automated acceptance checks passed.

## Environment

- Robot: Go2 X EDU, hardware V2.0, firmware V1.1.15
- Ubuntu VM: 192.168.123.223
- Go2 Ethernet: 192.168.123.161
- Source: Phase 5.3 ROS2 bridge
- Provider transport: `ros2`
- Provider health: `READONLY_WITH_SEMANTIC_HOLD`

The semantic hold is intentional: `/utlidar/imu` remains unsuitable as a
Point-LIO raw-specific-force input. It does not invalidate read-only telemetry.

## Formal run

- Started: 2026-07-30 04:30:18 UTC
- Finished: 2026-07-30 05:00:18 UTC
- Duration: 1,800.794 seconds
- Completed: true
- Passed: true
- Unexpected errors: 0

## Sensor results

| Stream | Samples | Mean observed frequency | Timestamp rollback | Stale checkpoints |
|---|---:|---:|---:|---:|
| LiDAR | 27,720 | 15.401 Hz | 0 | 0 |
| IMU | 446,155 | 247.864 Hz | 0 | 0 |
| Odometry | 267,745 | 148.749 Hz | 0 | 0 |

All streams were fresh at completion.

## Resource results

- Final RSS: 53.434 MB
- RSS growth: 0.602 MB
- RSS slope: 0.020 MB/minute
- Process CPU time: 553.483 seconds
- RSS growth Gate: less than 50 MB — PASS

## Automated checks

- Duration reached: PASS
- LiDAR samples present: PASS
- IMU samples present: PASS
- Odometry samples present: PASS
- LiDAR frequency 10–20 Hz: PASS
- IMU frequency 200–300 Hz: PASS
- Odometry frequency 100–200 Hz: PASS
- Topics fresh: PASS
- Stale checkpoints zero: PASS
- Timestamp rollback zero: PASS
- Motion disabled: PASS
- Localization disabled: PASS
- Navigation disabled: PASS
- Unexpected error absent: PASS

## Safety boundary

- Publishers created: 0
- Motion control: NOT USED
- SLAM started: false
- TF published: false
- Nav2: NOT USED
- `health_new`: unchanged

## Prior interrupted attempt

The first attempt on 2026-07-29 was invalidated after the Go2 Ethernet path
became unreachable at approximately 9 minutes 15 seconds. Its partial duration
was not accumulated. After restoring connectivity, the full Gate was restarted
from zero and completed successfully.

## Evidence

- Result JSON: `PHASE_6_1_B_READONLY_SOAK_RESULT.json`
- SHA-256:
  `45585BCDDB67B63506B44EDF99B8909A5A38A69D58001F6FA0AA478BE32AC957`

## Phase decision

Phase 6.1-B is accepted. The standalone `UnitreeReadonlyProvider` is eligible
for a separately approved Phase 6.2 read-only integration. This result does not
authorize SLAM, Nav2, TF changes, or any motion-control capability.
