# Phase 7.1 UWB Real Input Acceptance

Status: `PHASE_7_1B_PASS_PHASE_7_1C_READY`

This gate intentionally sends no robot motion command.

## Existing read-only probe

Use:

```text
E:\笨笨狗\go2_dev\tools\go2_uwb_readonly_probe.py
```

The probe creates DDS readers for:

- `rt/lowstate`;
- `rt/uwbstate`;
- `rt/uwbswitch`;
- `rt/multiplestate`.

It contains no DDS publisher, `SportClient`, Follow-mode switch, or motion
call.

Example on the physical Ubuntu host, after checking the actual Go2-facing
Ethernet interface:

```bash
export PYTHONPATH=/path/to/go2_dev/unitree_sdk2_python
python3 /path/to/go2_dev/tools/go2_uwb_readonly_probe.py \
  --peer 192.168.123.161 \
  --interface <go2_ethernet_interface> \
  --seconds 60 | tee phase7_1_uwb_capture.jsonl
```

During the capture, move only the UWB tag: hold still, walk away slowly, turn
left/right, return, then remove or power down the tag long enough to exercise
the timeout. Keep Go2 motion disabled.

## Acceptance evidence

Record the final `probe_result` plus representative `uwb_sample` rows.

Required:

- `dds_baseline_ok=true`;
- `uwb_writer_discovered=true`;
- `uwb_samples_received=true`;
- `distance_est` and `orientation_est` are finite and update continuously;
- distance changes in the expected direction when the tag moves;
- bearing sign, unit, and zero offset are calibrated against known
  front/left/right movement;
- `error_state` and `enabled_from_app` are recorded;
- receive gaps and observed UWB rate are recorded;
- tag loss is detected by the two-second planner timeout;
- timeout produces a zero command in the offline controller/arbiter path;
- adapter `move_count` remains zero for the entire gate.

## Stop conditions

Stop Phase 7.1 and do not arm real motion if any of these occurs:

- LowState baseline is absent;
- the UWB writer is absent or samples remain zero;
- distance/bearing are non-finite, frozen, or physically inconsistent;
- timestamps or receive order move backward;
- tag loss is not detected;
- any component attempts to publish DDS or call motion.

Each `uwb_sample` now contains `receive_monotonic` and `elapsed_seconds`.
The final `probe_result` contains `maximum_uwb_receive_gap_seconds`. Use the
monotonic field for replay ordering; do not derive control timing from the
wall-clock `timestamp` field.

## Phase 7.1-B dry-run replay

After physically confirming the distance and bearing calibration, replay the
same capture without enabling motion:

```bash
cd /path/to/go2_dev/go2-gateway
python3 tools/replay_uwb_phase7_1.py \
  /path/to/phase7_1_uwb_capture.jsonl \
  --bearing-source orientation_est \
  --bearing-unit radians \
  --bearing-sign 1 \
  --bearing-zero-offset-rad 0.55 \
  --confirm-calibration \
  --output artifacts/phase7_1b_uwb_replay.json
```

These values are the current device/tag/test-pose calibration, not universal
Unitree constants. Without `--confirm-calibration`, the replay tool can only
return `HOLD_CALIBRATION_NOT_CONFIRMED`.

The replay path is:

```text
probe JSONL -> UwbInputValidator -> FollowTargetPlanner
            -> FollowController -> MotionArbiter
            -> RealFollowExecutor (disabled)
```

The tool checks every captured receive gap of at least two seconds, also
advances the planner by two seconds after the final sample, and requires a
zero-motion timeout decision. It rejects non-finite fields,
non-increasing receive times, `enabled_from_app != 1`, and nonzero
`error_state`. A synthetic CLEAR LiDAR cloud is used only to isolate the UWB
path; it does not satisfy Phase 7.1-C.

## Gate result

Live evidence collected on 2026-08-22 established:

- target bearing source: `orientation_est`;
- bearing unit: radians;
- left positive, right negative;
- current zero-offset correction: `+0.55 rad`;
- `yaw_est` must not drive target bearing;
- tag loss produces a captured receive gap and a zero-motion timeout decision;
- dry-run `move_count` remains zero.

The confirmed `FollowOffset(back_distance=1.5, right_offset=0.5)` deliberately
targets a right-rear relationship. Consequently, a person directly ahead must
produce a right-turn correction; `wz ~= 0` is not the zero-error expectation
for that pose. The dry-run now checks the actual desired relative pose:

```text
distance = hypot(1.5, 0.5) = 1.5811 m
bearing = atan2(0.5, 1.5) = 0.3218 rad
```

After the calibrated `orientation_est` mapping, that pose produces
`target_x ~= 0`, `target_y ~= 0`, `vx ~= 0`, and `wz ~= 0`. Front, left, and
right samples also produce corrections consistent with the right-rear
geometry. Phase 7.1-B is PASS and Phase 7.1-C LiDAR static calibration is
READY. Phase 7.2 real motion remains closed.

## Archived evidence

- power-cycle capture: `artifacts/phase7_1_uwb_powercycle_synced_20260822_141408.jsonl`;
- yaw calibration capture: `artifacts/phase7_1_uwb_yaw_calibration_20260822_141937.jsonl`;
- dropout dry-run: `artifacts/phase7_1b_powercycle_replay_20260822.json`;
- direction/right-rear dry-run: `artifacts/phase7_1b_yaw_calibration_replay_20260822.json`;
- consolidated report: `docs/PHASE7_ACCEPTANCE_SUMMARY_20260823.md`.

The evidence checksum manifest is
`artifacts/phase7_evidence_sha256_20260823.txt`.
