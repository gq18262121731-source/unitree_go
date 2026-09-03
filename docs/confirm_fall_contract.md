# Confirm Fall Contract

This document freezes the go2-gateway confirm_fall workflow contract used by
health_new and camera-service integrations. Existing legacy fields and routes
remain supported.

## Task Status

Unified task status values:

- `QUEUED`: task accepted and waiting for the robot execution slot.
- `RUNNING`: task is actively executing a workflow step.
- `COMPLETED`: task reached a final outcome.
- `FAILED`: task failed because of an internal or execution error.
- `CANCELLED`: task was cancelled and must not continue later steps.
- `BLOCKED`: task was accepted but cannot move because a safety or readiness
  gate failed.

Legacy statuses such as `waiting`, `running`, `moving`, `arrived`, `checking`,
`finished`, `failed`, `cancelled`, and `BLOCKED_ROBOT_OFFLINE` may still appear
for backward compatibility. Responses include unified status fields when
available.

## Task Steps

Unified confirm_fall steps:

- `RECEIVED`: fall event accepted and task created.
- `PREFLIGHT`: safety, readiness, and location checks are performed.
- `MOVING`: robot is moving according to a validated fixed-point motion plan.
- `ARRIVED`: robot reached the planned first-stage target point.
- `CAMERA_CHECK`: arrival snapshot evidence is attempted.
- `VOICE_PROMPT`: elder status prompt is sent.
- `WAITING_RESPONSE`: gateway waits for manual or mock elder response.
- `REPORTING`: final callback/reporting state is emitted.

Legacy step names remain supported in existing responses and audit entries.

## Task Results

Final confirm_fall outcomes:

- `SAFE`: elder indicated they are safe.
- `NEED_HELP`: elder indicated they need help.
- `NO_RESPONSE`: no final elder response arrived before timeout.
- `UNKNOWN`: elder response is inconclusive or manually marked unknown.

No keyword-based ASR inference is performed by go2-gateway. Current responses
come from mock configuration or explicit writeback APIs.

## IDs

- `source_event_id`: camera-service fall event ID.
- `external_task_id`: health_new task ID.
- `task_id`: go2-gateway internal task ID.
- `trace_id`: end-to-end trace ID shared by callbacks for one task.
- `callback_id`: unique ID for one callback delivery envelope.
- `sequence`: monotonic per-task sequence number.

## Callback Minimum Payload

Each phase callback contains at least:

```json
{
  "callback_id": "cb_xxx",
  "sequence": 4,
  "task_id": "go2_task_xxx",
  "external_task_id": "health_task_xxx",
  "source_event_id": "fall_event_xxx",
  "trace_id": "trace_xxx",
  "status": "RUNNING",
  "step": "MOVING",
  "message": "Robot is moving toward living_room.",
  "occurred_at": "2026-07-21T12:00:00+08:00"
}
```

Final callbacks include:

```json
{
  "outcome": "NEED_HELP",
  "observation": {
    "snapshot_url": "/api/robot/tasks/task_xxx/evidence/arrival.jpg",
    "camera_available": true,
    "voice_available": true,
    "response_type": "NEED_HELP",
    "transcript": "I fell and cannot get up."
  }
}
```

Callback delivery failure must not fail the robot task. Delivery attempts are
tracked and can be replayed.
