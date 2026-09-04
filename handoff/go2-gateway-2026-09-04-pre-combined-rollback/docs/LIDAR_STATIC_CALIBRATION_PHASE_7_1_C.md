# Phase 7.1-C LiDAR Static Distance Calibration

Status: `FUNCTIONAL_TRANSITIONS_PASS_ABSOLUTE_DISTANCE_HOLD`

This is a stationary-robot gate. It must not publish motion or arm the Phase 7
executor.

## Preconditions

- Go2 is powered and stationary on level ground.
- the accepted cloud frame is verified as `base_link` or `cloud_base`;
- the point cloud is fresh and dense enough for the configured ROI;
- obstacle distance is measured independently with a tape or laser measure;
- `PHASE7_MOTION_EXECUTION_ENABLED=false`.

Use the read-only probe:

```bash
python3 tools/probe_lidar_safety_phase7_1c.py \
  --peer 192.168.123.161 \
  --local-address <go2-facing-host-ip> \
  --topic rt/utlidar/cloud_base \
  --expected-distance 2.00 \
  --position-label 2.00m \
  --seconds 5 \
  --minimum-samples 30
```

The probe creates DDS readers only and reports `motion_calls=0`. Measure the
physical obstacle distance from the projected `base_link` origin (the robot
body center reference), not from the front shell or LiDAR face.

## Positions

Place a rigid, easily detected object centered in front of Go2 at:

```text
2.00 m
1.50 m
1.20 m
0.80 m
0.65 m
0.50 m
```

Record for each position:

- measured physical distance;
- LiDAR nearest ROI distance;
- ROI point count;
- cloud frame and age;
- CLEAR/SLOW/STOP result;
- result stability over at least 30 consecutive clouds.

Expected boundary behavior with the current provisional defaults:

```text
distance > 1.20 m       CLEAR
0.65 m < distance <= 1.20 m  SLOW
distance <= 0.65 m      STOP
```

Boundary measurements at exactly 1.20 m and 0.65 m may fluctuate because of
sensor noise. Acceptance therefore requires documenting the observed margin;
do not tune thresholds from a single cloud.

## Recovery test

After a STOP result, remove the obstacle. Confirm that the guard remains STOP
until three consecutive clear clouds have arrived. The real executor must
still require explicit resume authorization after that software recovery.

## Stop conditions

- unknown or unverified cloud frame;
- nearest distance disagrees materially with the independent measurement;
- sparse/intermittent cloud causes unsafe CLEAR decisions;
- STOP fails at 0.50 m;
- recovery occurs from fewer than three clear samples;
- any real motion call occurs.

Phase 7.2 remains closed until this report has real measurements and a PASS
decision.

## Formal result (2026-08-23)

The live read-only campaign is complete. Point-cloud transport and decoding,
CLEAR/SLOW/STOP transitions, immediate STOP, STOP latching, three-clear SLOW
release, and recovery after actual obstacle removal all passed. The continuous
test used a runtime candidate `roi_min_z=-0.25 m`; this value has not replaced
the production default `-0.35 m`.

Absolute physical-distance calibration remains on HOLD:

- a physically placed 1.20 m target produced stable CLEAR rather than SLOW;
- observed positive distance errors were approximately 0.11 to 0.17 m;
- the 0.50 m target reported a median 0.6718 m, outside the 0.15 m tolerance;
- the physical measurement origin relative to `base_link` is not yet
  traceable.

The final decision is therefore:

```text
FUNCTIONAL_TRANSITIONS_PASS
ABSOLUTE_DISTANCE_HOLD
PHASE_7_2_REAL_MOTION_CLOSED
```

See `docs/PHASE7_1C_LIDAR_STATIC_CALIBRATION_REPORT_20260823.md` for the full
measurements and `artifacts/phase7_1c_hysteresis_roi_n0p25_20260823.json` for
the continuous session. All formal artifacts report `motion_calls=0`.
