# Go2 PC Scripted Motion

## Scope and state-source audit

`ScriptedMotionController` adds PC-side motion-block semantics without changing
the UWB follow controller, Companion lifecycle, P0-1/P0-4, or LiDAR safety code.
It uses SDK2 DDS only; ROS2, Nav2, SLAM, joint commands, and automatic obstacle
avoidance are not involved.

The implementation is split into three independent layers:

```text
configs/phone_demo.yaml             action targets and ordering
        -> MotionActionDispatcher   validated sequence dispatch
        -> ScriptedMotionController closed-loop action execution
        -> RobotService / adapter / SportClient
```

`go2_motion_demo.py` contains no phone-demo distances, angles, or waits.

The existing `UnitreeGo2Adapter` already creates `ChannelSubscriber` readers for:

- `rt/lf/sportmodestate`
- `rt/sportmodestate`

Both use `unitree_go.msg.dds_.SportModeState_`. Existing Phase 1 collection and
the SDK message definition expose `position[0:2]`, `imu_state.rpy[2]`, the IMU
quaternion, and a source timestamp. The scripted controller therefore reuses the
adapter's latest sample through a read-only `get_motion_state()` snapshot. It
does not create a new DDS participant, reader, or writer. Local monotonic receive
time is captured in the existing callback and is the stale-state authority.

The repository also contains `rt/utlidar/robot_odom` observations, but that topic
is not part of the current gateway adapter. Adding a second odometry DDS stack is
unnecessary because verified `SportModeState.position + imu_state.rpy` provides
the required planar pose.

## Closed-loop behavior

For translations, the start pose defines a local frame:

```text
forward = dx*cos(yaw0) + dy*sin(yaw0)
lateral = -dx*sin(yaw0) + dy*cos(yaw0)
```

Backward and right movement reverse the corresponding signed projection. Side
slip on the orthogonal axis does not falsely complete an action.

For turns, every new sample contributes:

```text
delta = wrap_to_pi(current_yaw - last_yaw)
accumulated_yaw += delta
```

This handles `+179 deg -> -179 deg`, the reverse crossing, and targets larger
than 180 degrees. Time is used only for timeout enforcement. The default config
does not permit time-only motion. If `allow_time_fallback` is explicitly enabled,
the result is labelled `estimated_time_fallback`; `actual_value` and `error`
remain null so an estimate cannot be mistaken for odometry.

Default commands are 0.30 m/s forward, 0.18 m/s backward, 0.30 m/s lateral, and
0.60 rad/s yaw. Forward translation reduces to the field-proven 0.23 m/s near
the target; backward remains limited by its 0.18 m/s nominal speed. The first
0.20 m/s forward deceleration trial paused and twisted before recovering.
The first real 0.18 m/s lateral trial produced body shift without a lateral gait,
so lateral motion retains the official Go2 example's 0.30 m/s through completion.
The first real 0.28 rad/s
turn trial produced body twist without a turning gait. Scripted turns use the
explicitly approved 0.60 rad/s field-tuning speed, then return to the already
validated official-example speed of 0.50 rad/s for the final 20 degrees. Hard
caps are 0.30 m/s forward/backward/lateral and 0.60 rad/s scripted yaw. The
Companion/Follow path remains `vy=0`, and its yaw cap remains unchanged at 0.30
rad/s. Commands
refresh at 5 Hz through `RobotService.refresh_velocity()`, retaining its
readiness checks, exclusive owner check, gateway limits, and watchdog.

Every action has a timeout and unconditional `StopMove` cleanup. Invalid or stale
pose, offline state detected by `RobotService`, non-finite data, SDK errors,
timeout, emergency abort, `KeyboardInterrupt`, and process-scope cleanup all stop
motion. `emergency_stop()` latches abort until `clear_emergency_stop()` is called.

## Exclusive-writer gate

Before real initialization, the CLI requires all of the following:

1. `data/companion_lifecycle_state.json` exists and says `IDLE`.
2. The operator types `EXCLUSIVE_MOTION_WRITER` exactly.
3. The operator types `OPEN_AREA_REMOTE_READY` exactly.

The state-file check protects the persistent Companion process. It cannot prove
that every arbitrary standalone SDK tool on the host is stopped, so the operator
confirmation remains mandatory. Never run this tool with
`go2_supervised_follow_phase7_2c.py`, Companion `FOLLOWING`, or another
`SportClient` motion tool.

The current `LidarSafetyGuard` consumes already-decoded, correctly framed point
clouds supplied by the Phase 7 input path. Reusing it here would require coupling
scripted motion to that follow-specific input runtime or duplicating point-cloud
infrastructure. This phase deliberately does neither. Real trials require an
open area, a human spotter, and the factory remote ready for takeover.

## Configuration and Python API

Motion-performance configuration is in `configs/scripted_motion.yaml`, with
units in every field name. It controls speed, tolerance, deceleration, control
rate, stale timeout, and action timeout without changing controller code.

Action targets and ordering are independently stored in
`configs/phone_demo.yaml`:

```yaml
scripted_sequence:
  name: phone_demo
  steps:
    - action: forward
      distance_m: 0.8
    - action: turn_left
      angle_deg: 90
    - action: wait
      seconds: 5
```

The sequence loader rejects unknown actions, missing or extra parameters,
incorrect unit fields, non-finite values, and non-positive distance/angle/time.
The dispatcher stops the sequence immediately when an implemented action fails.
Programmatic usage after constructing the existing `RobotService` is:

```python
config = load_scripted_motion_config("configs/scripted_motion.yaml")
with ScriptedMotionController(robot_service, config) as go2:
    go2.forward(0.8)
    go2.turn_left(90)
    go2.wait(5)
    go2.move_right(0.7)
```

Generic YAML execution uses the same dispatcher as the built-in demo:

```python
sequence = load_motion_sequence("configs/phone_demo.yaml")
result = MotionActionDispatcher(go2).execute(sequence)
```

`pose(...)` and `play_audio(...)` deliberately raise `NotImplementedError`. The
SDK audit found Go2 `SportClient.Euler`, `BalanceStand`, and `Pose(bool)`, but no
verified Go2 body-height/audio block contract matching the phone sequence.
No low-level joint behavior or guessed API composition is used.

## Real-trial gates

Run inside the verified WSL Ubuntu 20.04 environment. First stop Companion and
confirm its status is `IDLE`. Keep the factory remote in hand.

```bash
cd /mnt/e/笨笨狗/go2_dev/go2-gateway
export GO2_MODE=real
export GO2_NETWORK_INTERFACE=eth0
export GO2_CONTROL_ENABLED=true
export GO2_READ_ONLY_MODE=false
export GO2_MAX_VX=0.30
export GO2_MAX_VY=0.30
export GO2_MAX_WZ=0.60
```

Run only one trial at a time, inspect direction/gait/stop, and verify remote
takeover after each:

```bash
# T1
python3 tools/go2_motion_demo.py --action forward --value 0.20 --execute

# T2, only after T1 passes
python3 tools/go2_motion_demo.py --action turn_left --value 30 --execute

# T3, only after T2 passes
python3 tools/go2_motion_demo.py --action right --value 0.20 --execute
```

Only after T1/T2/T3 pass should 0.8 m and 90 degrees be tried. The complete
sequence has an additional flag and typed approval:

```bash
python3 tools/go2_motion_demo.py \
  --demo phone_demo --allow-phone-demo --execute
```

Without `--demo` or `--action`, the CLI accepts `F`, `B`, `L`, `R`, `TL`, `TR`,
`WAIT`, `STOP`, `STATUS`, and `EXIT` interactively.

A separate sequence file can be selected without editing Python:

```bash
python3 tools/go2_motion_demo.py \
  --sequence configs/my_demo.yaml --allow-sequence --execute
```

Real custom sequences additionally require the exact typed confirmation
`SCRIPTED_SEQUENCE_APPROVED`.

## Phone block mapping

| # | Phone block | PC scripted action |
|---:|---|---|
| 1 | Forward 0.8 m | `action: forward`, `distance_m: 0.8` |
| 2 | Rotate clockwise 90 deg | `action: turn_clockwise`, `angle_deg: 90` |
| 3 | Forward 1.6 m | `action: forward`, `distance_m: 1.6` |
| 4 | Rotate clockwise 105 deg | `action: turn_clockwise`, `angle_deg: 105` |
| 5 | Forward 1.3 m | `action: forward`, `distance_m: 1.3` |
| 6 | Wait 5 s | `action: wait`, `seconds: 5` |
| 7 | Backward 0.1 m | `action: backward`, `distance_m: 0.1` |
| 8 | Move right 1.5 m | `action: move_right`, `distance_m: 1.5` |
| 9 | Pose -6/+14/0 deg, -0.08 m, 1.5 s | `action: pose`; WebRTC unified runtime only |
| 10 | Backward 0.1 m | `action: backward`, `distance_m: 0.1` |
| 11 | Move left 0.9 m | `action: move_left`, `distance_m: 0.9` |
| 12 | Pose +6/-14/0 deg, +0.03 m, 1.5 s | `action: pose`; WebRTC unified runtime only |
| 13 | Move right 0.7 m | `action: move_right`, `distance_m: 0.7` |
| 14 | Say "演示完成" | `action: speak`; WebRTC AudioHub on the shared connection |

No real robot action is performed by installation, tests, or compile checks.
