# Go2 Companion Persistent Console

## Purpose

`tools/go2_companion_console.py` is a thin CLI over the same
`CompanionLifecycleService` used by the REST API. It does not contain a second
follow controller.

The process owns one `RobotService`, one SDK2 participant, one
`CompanionRuntime`, and one supervised motion writer for its entire lifetime.

Startup is stationary:

```text
process start -> SDK/DDS inputs ready -> IDLE -> wait for START
```

`STOP` issues fail-closed `StopMove`, releases exclusive motion ownership, and
keeps UWB/LiDAR subscriptions plus the runtime worker alive. `EXIT`, Ctrl+C, or
EOF issues another safe stop and then closes the subscribers, runtime, watchdog,
and SDK adapter.

The real launcher supports two explicit risk modes:

| Mode | Risk path | Capability |
| --- | --- | --- |
| Follow-only | omitted | UWB/LiDAR `START` and `STOP`; status reports `Risk=DISABLED`; no fall preemption claim |
| Full | live JSONL | Fresh-heartbeat gate, fall preemption, recovery, and explicit `RESUME` |

No old artifact or synthetic `NON_FALL` stream is selected automatically.

## Commands

| Command | Behavior |
| --- | --- |
| `START` | Recheck robot/UWB/LiDAR/risk/control gates, acquire the sole motion-writer lock, then enter follow |
| `STOP` | Stop immediately and return to `IDLE`; keep DDS inputs alive |
| `RESUME` | Allowed only from the latched `WAIT_RESUME` recovery state and only after another safety check |
| `STATUS` | Show robot, input, risk, state, and latest commanded motion |
| `EXIT` | Stop, close persistent resources, and exit |

`START` cannot replace `RESUME` after a fall preemption.

## Real-mode prerequisites

Set the real gateway values in the shell before starting the process. At a
minimum they must resolve to the approved values for the current machine:

```text
GO2_MODE=real
UNITREE_ROBOT_IP=192.168.123.161
UNITREE_NETWORK_INTERFACE=<actual Go2 Ethernet interface>
UNITREE_DOMAIN_ID=0
GO2_CONTROL_ENABLED=true
GO2_READ_ONLY_MODE=false
FOLLOW_SIMULATION=false
FOLLOW_EXECUTION_ENABLED=true
PHASE7_MOTION_EXECUTION_ENABLED=true
PHASE7_REQUIRE_EXTERNAL_RISK_FEED=<false for follow-only; true for full mode>
GO2_COMPANION_RISK_EVENTS_PATH=<empty or append-only external risk JSONL>
GO2_MAX_VX=0.30
GO2_MAX_VY=0.0
GO2_MAX_WZ=0.30
GO2_CONTROL_WATCHDOG_SECONDS=0.5
```

In full mode, the external risk process must append fresh events after this
console starts. The runtime intentionally tails only newly appended records.
Do not use the Phase 7 test heartbeat as a production fall feed.

Run from the gateway project with the SDK2 source on `PYTHONPATH`:

```text
python3 tools/go2_companion_console.py --execute
```

From Windows PowerShell, use the checked launcher instead of manually exporting
the real-mode environment. Follow-only mode needs no risk argument:

```text
Set-Location -LiteralPath 'E:\笨笨狗\go2_dev\go2-gateway'
.\scripts\Start-Go2CompanionReal.ps1
```

For full mode, provide a JSONL that already exists and is actively appended by
the external risk module:

```text
Set-Location -LiteralPath 'E:\笨笨狗\go2_dev\go2-gateway'
.\scripts\Start-Go2CompanionReal.ps1 `
  -RiskEventsPath 'E:\path\to\external-risk-events.jsonl'
```

The launcher enters `Ubuntu-20.04`, auto-detects exactly one active
`192.168.123.x` interface, pings `192.168.123.161`, checks SDK2 import, and
holds a single-writer lock. It refuses to launch when the Ethernet interface,
risk file, SDK, or Go2 is unavailable. Use `-Interface <name>` only when the
actual interface has already been verified.

Real mode then requires exact typed confirmations:

```text
PHASE7_PERSISTENT_RUNTIME
EXCLUSIVE_MOTION_WRITER
REMOTE_OPERATOR_READY
```

The console still starts in `IDLE`; motion does not begin until the operator
enters `START` and every live preflight gate passes.

## Single-writer rule

Do not run this console at the same time as another gateway, Phase 7 motion
tool, `SportClient` program, or any other autonomous motion writer. The original
remote remains the physical safety takeover device, but normal manual operation
should follow `STOP`.

## Shared REST implementation

The existing endpoints use the same lifecycle class:

```text
GET  /api/v1/robot/companion/status
POST /api/v1/robot/companion/start
POST /api/v1/robot/companion/stop
POST /api/v1/robot/companion/resume
```

CLI and REST therefore share the same state machine, safety preflight,
`MotionArbiter`, executor, and stop/resume policy. They are alternative process
front ends, not two independent motion controllers.
