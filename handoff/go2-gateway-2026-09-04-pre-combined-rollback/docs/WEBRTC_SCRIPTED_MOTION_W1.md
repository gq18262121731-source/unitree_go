# Phase W1 — Wireless Scripted Motion

## Status

Implementation and offline regression tests are complete. Real-robot W3, W4,
and full-sequence trials are intentionally not run by this change.

The movement path is now:

```text
phone_demo.yaml / single action
  -> MotionActionDispatcher
  -> ScriptedMotionController
  -> RobotService + watchdog + exclusive writer
  -> WebRTCMotionBackend
  -> Wi-Fi WebRTC DataChannel
  -> Go2 Sport Move / StopMove
```

The original SDK2 adapter remains the default `--transport sdk2` path. No UWB,
LiDAR, Companion, fall detection, navigation, pose, audio, or video capability
was added to the WebRTC backend.

## Frozen behavior

- One persistent WebRTC connection is used for an entire scripted-motion run.
- Only `rt/lf/sportmodestate` and `rt/sportmodestate` are subscribed.
- Only Sport `Move` and `StopMove` requests are published.
- Every API response must acknowledge status code `0`; otherwise the command
  fails closed.
- Existing position/yaw closed-loop logic, stale-state rejection, stall
  detection, speed limits, timeout, watchdog, exclusive writer, and cleanup
  StopMove are reused unchanged.
- `turn_clockwise` always uses negative `wz`.
- The two phone-demo rotations are clockwise 90 and 105 degrees.
- Historical W1 scope ended at Move/Stop. The unified competition runtime now
  adds high-level pose and AudioHub speech on the same PeerConnection; see
  `GO2_WIRELESS_UNIFIED_RUNTIME.md`.

## Offline trajectory geometry

Frame: initial robot pose is `(0, 0)`, +x is forward, +y is left.

Key ideal center-path waypoints:

| After step | x (m) | y (m) | heading |
|---|---:|---:|---:|
| forward 0.8 | 0.800 | 0.000 | 0° |
| clockwise 90 + forward 1.6 | 0.800 | -1.600 | -90° |
| clockwise 105 + forward 1.3 | -0.456 | -1.264 | 165° |
| backward 0.1 + right 1.5 | 0.029 | 0.159 | 165° |
| backward 0.1 + left 0.9 + right 0.7 | 0.074 | -0.060 | 165° |

Ideal path bounds are about `1.256 m × 1.759 m`. Adding 1.0 m on every side
gives `3.256 m × 3.759 m`; use at least a clear `4 m × 4 m` area for the first
real full-sequence trial. This allowance must cover the chassis footprint,
gait sway, odometry error, and stopping drift.

Recalculate after any YAML distance/angle change:

```powershell
python tools\plan_motion_sequence.py configs\phone_demo.yaml --margin-m 1.0
```

## Staged Windows commands

Before every trial: close the Unitree App, stop the wireless video bridge, keep
the original remote ready, verify Companion is `IDLE`, and clear the area.

W3 — one clockwise 30-degree direction test:

```powershell
cd "E:\笨笨狗\go2_dev\go2-gateway"
.\scripts\Start-Go2WebRTCScriptedMotion.ps1 -Stage W3Clockwise30 -RobotIp 192.168.8.252
```

Accept only if it turns clockwise with a normal gait, stops promptly, and the
remote can immediately take over.

W4 — one continuous closed-loop forward 0.8 m then clockwise 90 degrees:

```powershell
.\scripts\Start-Go2WebRTCScriptedMotion.ps1 -Stage W4ForwardClockwise90 -RobotIp 192.168.8.252
```

After W3 and W4 pass once, run the full movement portion directly:

```powershell
.\scripts\Start-Go2WebRTCScriptedMotion.ps1 -Stage PhoneDemo -RobotIp 192.168.8.252
```

The launcher decrypts the existing DPAPI key only into the child process
environment, restores prior environment values afterward, refuses to run while
port 8093 is occupied, checks WebRTC signaling port 9991, and uses the existing
Windows Python 3.12 WebRTC environment.

## Acceptance record still required

- W3 clockwise direction / gait / stop / remote takeover.
- W4 forward direction and distance / clockwise direction and angle / no pause
  or reversal / stop / remote takeover.
- Full sequence order and distances, two clockwise turns, five-second wait,
  three explicit skipped steps, final StopMove, and remote takeover.

## Unified video + motion mode

For simultaneous wireless video and scripted motion, do not run the independent
video bridge and this standalone motion process together. Use the single-owner
runtime documented in `docs/GO2_WIRELESS_UNIFIED_RUNTIME.md` and launched by
`scripts/Start-Go2WirelessRuntime.ps1`.
