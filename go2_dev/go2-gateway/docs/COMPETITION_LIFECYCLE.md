# Go2 competition lifecycle

The wireless runtime owns the only Go2 WebRTC connection and the only motion
writer. Video, microphone capture and speaker playback remain available while
motion is stopped or emergency-latched.

## Authority

```text
EMERGENCY > MANUAL > COMPANION > IDLE
```

- A suspected or confirmed fall immediately returns `STOP_MOVE` and latches the
  incident before any voice or model work begins.
- Manual input first stops the companion worker and then acquires the same
  `RobotService` writer as `wireless_manual`.
- Every manual key is a short dead-man pulse followed by unconditional
  `StopMove`; releasing manual control returns to `IDLE` and never resumes
  companion motion automatically.

## Emergency flow

```text
FOLLOWING
  -> FALL_SUSPECTED/FALL_CONFIRMED
  -> VOICE_CHECK
     -> I_AM_OK -> WAIT_RESUME
     -> REQUEST_HELP/CALL_FAMILY -> HELP_REQUESTED
     -> NO_RESPONSE -> RECHECK
        -> NO_RESPONSE -> ESCALATED_EMERGENCY
```

`I_AM_OK` only sets `help_required=false`. It does not clear the visual risk
lock and it does not resume motion. A matching `RECOVERY_CONFIRMED` (or another
approved external risk-clear event) must clear the incident, followed by an
explicit `RESUME_COMPANION`. Resume rechecks WebRTC, UWB freshness/validity,
manual takeover and writer availability.

When ASR is configured, the runtime performs two emergency response captures
without requiring the wake word. Two missing or invalid responses escalate the
incident. Emergency states keep video and audio transports alive and keep all
motion at zero.

## HTTP control surface

The endpoints are served by the existing port 8093 bridge and reuse its attached
runtime control object:

```text
GET  /api/v1/robot/companion/status
POST /api/v1/robot/companion/start
POST /api/v1/robot/companion/stop
POST /api/v1/robot/companion/resume
POST /api/v1/robot/companion/intent
POST /api/v1/robot/companion/risk-event
POST /api/v1/robot/companion/no-response
POST /api/v1/robot/companion/manual
POST /api/v1/robot/companion/manual/release
POST /api/v1/robot/companion/reset-demo
```

Example risk event:

```json
{
  "event_type": "FALL_SUSPECTED",
  "incident_id": "FALL-001",
  "timestamp": "2026-08-31T10:00:00+08:00",
  "confidence": 0.92
}
```

Example voice intent:

```json
{"intent": "I_AM_OK"}
```

Example manual pulse:

```json
{"key": "W"}
```

Supported manual keys are `W/S/A/D/Q/E`, `SPACE`, `M` and `ESC`. The Windows
console command `MANUAL` provides the same controls inside the unified runtime.

## Reset

`reset-demo` always sends `StopMove`, cancels the emergency voice workflow,
releases manual control, clears incident/help/response/escalation state and
returns to `IDLE`. It deliberately does not close or recreate WebRTC, video,
microphone, speaker, UWB or HTTP services.

## External notification boundary

`NOTIFY_FAMILY` and `NOTIFY_COMMUNITY` are recorded in status as
`PENDING_EXTERNAL_ADAPTER`. The gateway does not fabricate successful delivery.
Production deployment must attach the real Health New/contact adapter and turn
that status into an acknowledged delivery result.

`RESET_DEMO` clears the robot-side lifecycle. If the external vision service
maintains its own incident/test state, it must consume the reset action or call
its own reset endpoint; the gateway does not silently claim that remote state
was cleared.
