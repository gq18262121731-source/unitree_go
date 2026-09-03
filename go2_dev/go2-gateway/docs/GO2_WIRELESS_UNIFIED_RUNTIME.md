# Go2 Unified Wireless Runtime

## Architecture

`Go2WirelessRuntime` is the sole owner of the Go2 WebRTC PeerConnection:

```text
Go2WirelessRuntime (one process / one PeerConnection)
├── DataChannel
│   ├── Move / StopMove / SportModeState
│   ├── BalanceStand / Euler / BodyHeight
│   └── AudioHub WAV upload + playback
├── Video Track
│   └── size-one newest-frame queue
├── dedicated JPEG encoder worker
└── shared latest JPEG frame
    └── local MJPEG bridge http://127.0.0.1:8093
```

`WebRTCMotionBackend` receives the runtime by dependency injection. It never
constructs or disconnects a PeerConnection unless its caller explicitly gives
it ownership of the runtime lifecycle. The HTTP bridge only reads runtime
status and latest-frame data.

The video recv task does not run OpenCV or inference. It replaces the item in a
size-one queue when a newer frame arrives. A separate encoder thread performs
frame conversion and JPEG encoding, so a slow viewer or encoder cannot block
DataChannel state callbacks or the scripted-motion refresh loop.

The following remain unchanged:

- distance/yaw closed-loop algorithms
- tuned motion speeds, tolerances, control rate, stalls, and timeouts
- `RobotService` watchdog and exclusive writer

The same action controller now also sequences high-level pose and speaker
capabilities. No joint-position, joint-torque, or second WebRTC client is used.

Raw high-frequency WebRTC protocol payloads are hidden from the normal console.
This includes SportState, LowState, MultiState, UWB payloads, heartbeat and
`rtc_inner_req`. Normal mode also hides AudioHub WAV split/chunk progress,
request/response payloads, Base64 `block_content` and full `audio_list` JSON;
AudioHub upload and playback remain active. Use
`-UwbVerbose` for UWB-only payload debugging or `-VerboseProtocolLog` to restore
all raw protocol logs. Warnings, errors, connection transitions, Lifecycle,
voice/ASR/intent, motion and latency logs are never hidden.

Fixed voice presets are preloaded as one batch. Startup reads the robot's
`audio_list` once, reuses that snapshot, uploads only missing content-addressed
presets, and refreshes the list once after uploads. Existing Go2 audio is not
deleted. A failed upload is checked against the robot list before retrying;
`VOICE_CONTROL_PRELOAD_FAILED` includes the attempt count and concrete reason.
Large presets such as `NO_RESPONSE_ESCALATED.wav` use a size-aware upload
timeout rather than the old fixed 15-second limit.

## Start

Close the Unitree App and ensure Companion is `IDLE`. Do not start the legacy
video process separately.

```powershell
cd "E:\笨笨狗\go2_dev\go2-gateway"
.\scripts\Start-Go2WirelessRuntime.ps1 -RobotIp 192.168.8.252
```

Confirm:

```text
EXCLUSIVE_MOTION_WRITER
UNITREE_APP_CLOSED
OPEN_AREA_REMOTE_READY
```

The browser opens `http://127.0.0.1:8093`. Its status panel must show:

```text
唯一连接       1（应为1）
DataChannel   READY
SportState    READY
无线视频       在线
```

The runtime console supports:

```text
START         long-lived UWB-only companion session; runs until STOP
RESUME        resume only from WAIT_RESUME after safety checks
UWB_GATE      subscriber-only 15 s WebRTC UWB transport/input Gate
MIC_GATE      capture 5 s Go2 microphone WAV; sends no Sport command
VOICE_INTENT_GATE  one-utterance read-only ASR/intent Gate; never executes intent
VOICE_CONTROL one-utterance ASR -> local whitelist -> Lifecycle execution
FOLLOW_3MIN   supervised 180 s WebRTC UWB-only follow field test
GATE          one joint video + 0.20 m forward + Stop test
POSE_GATE     first pose only, then neutral pose and StopMove
AUDIO_GATE    speak "演示完成" while stationary
START_DEMO    full phone_demo movement + pose + speech sequence
STOP          emergency StopMove; remains responsive during motion
MANUAL        enter manual takeover; Companion cannot resume automatically
NO_RESPONSE  record one emergency no-response attempt
RESET_DEMO    explicit post-emergency reset for the next demonstration
STATUS        runtime connection/state/video counters
EXIT          StopMove, HTTP shutdown, WebRTC disconnect
```

## Single-utterance voice control

The microphone callback is registered on the same PeerConnection already used
by video, SportModeState, UWB, movement and AudioHub playback. No second WebRTC
client is constructed. Start `D:\health_new` on `http://127.0.0.1:8000`, then
start the wireless Runtime with the elder identity used for grounded dialogue:

```powershell
.\scripts\Start-Go2WirelessRuntime.ps1 `
  -RobotIp 192.168.8.252 `
  -HealthNewUrl http://127.0.0.1:8000 `
  -ElderId elder01_02
```

First verify transport only:

```text
MIC_GATE
WEBRTC_MIC_READONLY_GATE
```

The Gate records five seconds to `data/voice/mic_gate_latest.wav` and prints
sample rate, channels, duration, peak, RMS and byte count. Accept it only when
`WEBRTC_MIC_READONLY_PASS` is printed and `sportCommandsSentDuringGate` is `{}`.

Then verify one intent without movement:

```text
VOICE_INTENT_GATE
```

After `Audio channel: on`, say the wake word and complete command in one
utterance, for example `小康，陪我出去走走`. Speech capture ends after 300 ms
of trailing silence (configurable with
`GO2_VOICE_VAD_TRAILING_SILENCE_SECONDS`; the frozen competition value is
`0.3`). There is no separate wake-word recording, wake acknowledgement, or
second microphone capture.

The existing `POST /api/v1/voice/asr` supplies the transcript. The finite
control phrases are routed locally to `START_COMPANION`, `STOP_COMPANION`,
`RESUME_COMPANION`, `REQUEST_HELP`, `CALL_FAMILY`, or `I_AM_OK`; they do not
wait for the Xiaokang agent. Ordinary conversation can still use
`POST /api/v1/go2-companion/text-turn`. `VOICE_INTENT_GATE` evaluates the same
Lifecycle preconditions but always prints `EXECUTED: false`, sends no control
feedback audio, and cannot emit `vx`, `vy`, or `wz`.

Only after the read-only result is correct, use `VOICE_CONTROL` for physical
control. The order is fixed:

```text
ASR -> local intent whitelist -> Lifecycle -> control accepted/executed
    -> fixed local WAV feedback
```

The LLM never generates velocity. Fixed control responses are preloaded from
`data/voice/presets/current`. Runtime logs expose `VOICE_T0_VAD_END` through
`VOICE_T5_AUDIO_ACCEPTED`, plus ASR, intent, control, and speech-feedback
latencies. Action acceptance/execution occurs before acknowledgement playback.

`START_COMPANION` is authorizable only from `IDLE`. `RESUME_COMPANION` is
authorizable only from `WAIT_RESUME` after WebRTC/UWB/writer checks and with no
active fall or manual takeover. “我没事” maps only to `I_AM_OK`: it clears the
help escalation path when allowed but never resumes movement. A separate and
explicit “继续走吧” is required for `RESUME_COMPANION`.

When a confirmed fall enters the emergency voice check, the Runtime uses fixed
local prompts, records at most two responses, and accepts only `I_AM_OK`,
`REQUEST_HELP`, and `CALL_FAMILY`. Two absent/invalid responses keep motion
stopped and enter `ESCALATED_EMERGENCY`. Automatic recovery after a risk stop
is forbidden; `RESET_DEMO` is required before the next demonstration.

## Read-only WebRTC UWB Gate

The installed WebRTC library defines all three read-only topics used by this
Gate:

```text
rt/uwbstate          distance/orientation/enabled/error fields
rt/multiplestate     uwbSwitch
rt/lf/lowstate       connection-health reference
```

They are subscribed on the runtime's existing PeerConnection. The Gate does
not construct a SportClient or publisher and sends no Move, StopMove, or other
Sport request during its observation window.

Restart the runtime after updating the code, then enter:

```text
UWB_GATE
WEBRTC_UWB_READONLY_GATE
```

It observes for 15 seconds and prints the latest `distance_est`,
`orientation_est`, `yaw_est`, `enabled_from_app`, and `error_state`. Accept the
wireless transport only when the final JSON contains:

```text
transportPassed: true
newSampleCount: 2 or more
schemaValid: true
sportCommandsSentDuringGate: {}
moveCommandsSentDuringGate: 0
connectionCount: 1
```

`followInputReady: true` additionally requires `enabled_from_app: 1` and an
explicit `error_state: 0`. Some WebRTC firmware payloads omit `error_state`
when its DDS value is zero. The runtime deliberately does not infer that a
missing safety field means zero. A `WEBRTC_UWB_READONLY_PASS_INPUT_NOT_READY`
result therefore means the wireless WebRTC transport works, but the Gate alone
does not authorize motion. The bounded field session below may use the
separately approved omission policy only after tag-off stale-stop and tag-on
recovery behavior have been physically verified. A no-samples result must not
proceed to real follow control.

## Three-minute wireless UWB follow field test

`FOLLOW_3MIN` reuses the production follow profile and the same WebRTC
PeerConnection used by video and SportModeState. Its transport/test limits are
kept separately in `configs/webrtc_uwb_follow_3min.yaml`:

```text
duration                 180 s
control rate             4 Hz
UWB stale stop           1.0 s
maximum forward speed    0.30 m/s
maximum yaw speed        0.30 rad/s
ALIGN turn speed         0.50 rad/s
ALIGN enter / exit       60° / 15°
UWB stale auto-recovery  enabled (UWB stale only)
recovery freshness       age < 0.50 s
recovery debounce        3 new samples spanning >= 0.50 s
```

The wireless test uses an explicit align-before-follow state. At 60 degrees of
bearing error it latches `ALIGN`, forces forward velocity to zero, and turns
toward the configured target bearing at `0.50 rad/s`. It remains in ALIGN
through the 15--60 degree band and exits only on a fresh sample at or below 15
degrees. Normal forward following is reconsidered only after that exit.

This first wireless test is intentionally UWB-only: it does not claim LiDAR
obstacle protection. Use it only in a cleared open area with the original
remote ready. At `wireless>` enter the command and exact confirmations:

```text
FOLLOW_3MIN
WIRELESS_UWB_FOLLOW_3MIN_APPROVED
UWB_ONLY_NO_LIDAR_OPEN_AREA
REMOTE_STOP_READY
```

Type `STOP` at any time for an immediate StopMove. A UWB stale age of 1.0 s
sends StopMove and changes the active session to `UWB_WAITING`; it does not
close WebRTC or disable the companion session. Motion resumes only after three
distinct valid UWB samples remain younger than 0.50 s for at least 0.50 s.
Recovery discards controller/planner history and computes a new command from
the latest target position; it never replays the pre-dropout velocity.

Only transient UWB stale/not-ready participates in that automatic recovery.
Invalid UWB, WebRTC/Sport/LowState/video loss, a disabled UWB switch, an
explicit non-zero UWB error, command/control errors, manual `STOP`, and process
exit end the session and require a new explicit `START` or `FOLLOW_3MIN`.

## Long-lived companion session

After the staged follow test is accepted, enter the following once at the
existing `wireless>` prompt:

```text
START
```

`START` has no 180-second deadline. It keeps the single WebRTC connection and
video bridge alive while following, waiting for UWB, and recovering. `STATUS`
includes `wirelessCompanion.state`, the latest recovery event, and dropout
metrics. A normal temporary dropout produces:

```text
FOLLOWING -> UWB_WAITING -> RECOVERING -> FOLLOWING
```

Use `STOP` to end the companion session. A later restart requires only a new
`START`; it does not require a fixed confirmation phrase. Both console `START`
and voice `START_COMPANION` execute only after the existing Lifecycle safety
gates pass: IDLE state, WebRTC connected, fresh/valid UWB, no active risk or
manual takeover, and an available exclusive motion writer. Success prints
`START accepted -> FOLLOWING`; rejection prints `START_REJECTED:<code>:<reason>`.

For an isolated debug session only, the legacy single phrase can be enabled
explicitly:

```powershell
.\scripts\Start-Go2WirelessRuntimeWithFollowTarget.ps1 -ManualConfirmStart
```

The equivalent direct Python flag is `--manual-confirm-start`, and the
equivalent environment variable is `GO2_MANUAL_CONFIRM_START=1`. These are off
by default. This mode remains UWB-only and does not claim LiDAR obstacle input.

Always type `EXIT` to stop the unified runtime. The legacy
`stop_wireless_video.cmd` deliberately refuses to force-kill it, because a
forced process termination could bypass StopMove and WebRTC cleanup.

## One-time joint Gate

With the video visible, type:

```text
GATE
JOINT_VIDEO_MOTION_GATE_APPROVED
```

The Gate performs:

1. require fresh video and SportModeState;
2. observe video for 10 seconds;
3. execute the existing closed-loop `forward(0.20)` action and StopMove;
4. observe video for another 10 seconds;
5. verify video/state remain fresh, frame count advances, and
   `connectionCount` stays exactly `1`.

Operator acceptance is required for normal gait, prompt stopping, continuous
video, no WebRTC reconnect, and immediate remote takeover.

Before the full sequence, validate the two new capabilities independently:

```text
POSE_GATE
POSE_GATE_APPROVED

AUDIO_GATE
AUDIO_GATE_APPROVED
```

For `POSE_GATE`, accept only if the body reaches the requested pose with four
feet stable, returns to neutral, stops promptly, and the remote immediately
retakes control. For `AUDIO_GATE`, accept only if the Go2 speaker says the
expected phrase once, motion remains stopped, video remains active, and no
WebRTC reconnect occurs.

After both Gates pass, type `START_DEMO`, `PHONE_DEMO_APPROVED`, and
`POSE_AUDIO_REAL_APPROVED` to run the YAML sequence while video remains active.

Pose uses the installed Unitree high-level Sport APIs in this order:

```text
StopMove -> settle 0.25 s -> BalanceStand -> Euler + BodyHeight
         -> hold -> Euler 0 + BodyHeight 0 -> StopMove
```

YAML angles are degrees and are converted to radians at the backend boundary.
Relative body height is hard-limited to the official `[-0.18, +0.03] m` range.
`speak` renders a local Windows Chinese TTS WAV and uses the same connection's
AudioHub to cache/upload/play it.

## Legacy launchers

The old STA video PowerShell script now delegates to this unified launcher. The
standalone scripted-motion launcher is retained for diagnostics without video;
if port 8093 is active it tells the operator to use the unified runtime instead
of attempting a second WebRTC connection.

## Competition auto-demo

The one-command `0.0.0.0:8093` relay plus automatic `phone_demo` mode is
documented in `docs/GO2_COMPETITION_MODE.md` and launched with
`scripts/Start-Go2CompetitionDemo.ps1`.
