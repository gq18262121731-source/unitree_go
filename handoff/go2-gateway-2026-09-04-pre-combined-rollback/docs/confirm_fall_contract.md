# Confirm Fall Task Contract

This contract describes the Go2 gateway `confirm_fall` task consumed by `health_new`.

## Scope

`health_new` dispatches a fall event or a `confirm_fall` robot task. The gateway performs only safety-gated, fixed-plan robot execution. It does not expose DDS, low-level motors, SLAM, Nav2, autonomous navigation, or real ASR as public control surfaces.

## IDs

- `task_id`: gateway-generated robot task ID. Use it for direct task, evidence, callback delivery, cancel, and elder-response APIs.
- `external_task_id`: optional `health_new` task or correlation ID, accepted as `external_task_id`, `externalTaskId`, or `taskId`.
- `source_event_id`: camera/event idempotency key, accepted as `source_event_id`, `sourceEventId`, `event_id`, `eventId`, `camera_event_id`, or `cameraEventId`.
- `trace_id`: gateway trace ID stored in task `source.traceId` and echoed in callbacks.
- `callback_id`: unique ID for one callback delivery.
- `sequence`: monotonic per-task callback delivery sequence.

## Unified State Machine

Statuses:

- `QUEUED`: task accepted but waiting for the robot task queue.
- `RUNNING`: task is actively executing.
- `COMPLETED`: task reached a terminal success state.
- `FAILED`: task failed because execution raised a non-recoverable error.
- `CANCELLED`: task was cancelled; later motion, camera, voice, and reporting steps stop.
- `BLOCKED`: task was recorded but deliberately not dispatched.

Steps:

- `RECEIVED`
- `PREFLIGHT`
- `MOVING`
- `ARRIVED`
- `CAMERA_CHECK`
- `VOICE_PROMPT`
- `WAITING_RESPONSE`
- `REPORTING`

Legacy fields such as `status`, `step`, `currentStep`, and old lowercase step names remain in responses for existing `health_new` pages. New consumers should prefer `status_v2` and `current_step`.

## Outcomes

Final `result.outcome` values:

- `SAFE`: elder responded that no help is needed.
- `NEED_HELP`: elder requested help.
- `NO_RESPONSE`: no elder response arrived before timeout.
- `UNKNOWN`: response was present but not classifiable.

The old `result.confirm` and `result.voiceResult` fields remain for compatibility. New consumers should use `result.outcome`, `result.elderResponse`, and `result.observation`.

## Workflow

1. Fall event is received and normalized.
2. Preflight checks robot readiness, control gates, read-only mode, DDS state in real mode, and location plan validity.
3. Robot moves using the configured fixed motion plan.
4. Arrival is recorded.
5. Camera evidence is captured to `data/task_evidence/{task_id}/arrival.jpg`.
6. Voice prompt is delivered through the configured mock or HTTP voice bridge.
7. Gateway waits for elder response or timeout.
8. Result is reported and callbacks are delivered.

Camera failure is non-fatal for `confirm_fall`: the task continues with `camera=failed`, `result.robotCamera.cameraAvailable=false`, and `result.observation.camera_available=false`.

Motion exceptions are fatal and trigger `StopMove` before the task becomes `FAILED`.

Cancel stops further workflow steps and prevents a later elder response from overriding the terminal task.

## Blocked Tasks

In real or safety-gated modes, these conditions create a task with `status_v2=BLOCKED`, publish a callback, and do not move the robot:

- `GO2_CONTROL_ENABLED=false`
- `GO2_READ_ONLY_MODE=true`
- DDS offline or robot state unavailable/stale in real mode
- motion service not ready
- preflight failure
- unknown/fallback location in real mode

Real mode never falls back to an unknown location plan.

## Elder Response API

```http
POST /api/robot/tasks/{task_id}/elder-response
Content-Type: application/json
```

```json
{
  "response_type": "NEED_HELP",
  "transcript": "need help"
}
```

Rules:

- Accepted only while `current_step=WAITING_RESPONSE`.
- Valid `response_type` values are `SAFE`, `NEED_HELP`, and `UNKNOWN`.
- The same response can be replayed idempotently.
- A different second response is rejected.
- Terminal, failed, cancelled, or blocked tasks cannot be overridden.
- No real ASR is performed by this endpoint.

Timeout is configured by `GO2_ELDER_RESPONSE_TIMEOUT_SECONDS`. In mock mode, `GO2_MOCK_CONFIRM_FALL_OUTCOME` can inject `SAFE`, `NEED_HELP`, `UNKNOWN`, or `NO_RESPONSE`.

## Evidence API

```http
GET /api/robot/tasks/{task_id}/evidence/arrival.jpg
```

The endpoint returns the arrival JPEG if it exists. Task results expose the HTTP evidence URL, not a local absolute filesystem path. Mock evidence is marked with `source=mock`.

## Callback Payload

Callbacks include legacy task fields plus delivery metadata:

```json
{
  "callback_id": "cb_123",
  "sequence": 7,
  "trace_id": "trace_123",
  "task_id": "task_123",
  "external_task_id": "health-task-001",
  "status": "finished",
  "status_v2": "COMPLETED",
  "step": "finished",
  "current_step": "REPORTING",
  "finished": true,
  "outcome": "NEED_HELP",
  "result": {
    "outcome": "NEED_HELP",
    "observation": {
      "camera_available": true,
      "snapshot_url": "/api/robot/tasks/task_123/evidence/arrival.jpg",
      "response_type": "NEED_HELP",
      "transcript": "need help"
    }
  }
}
```

`health_new` should deduplicate by `task_id + revision` and may use `callback_id` or `sequence` for delivery diagnostics.

## Callback Delivery APIs

```http
GET /api/robot/tasks/{task_id}/callback-deliveries
POST /api/robot/tasks/{task_id}/callbacks/replay
POST /api/tasks/{task_id}/feedback/replay
```

Delivery records expose `callback_id`, `sequence`, `status`, `http_status`, `error`, `retry_count`, and timestamps. Replay endpoints send the latest task snapshot again.

## Compatibility Endpoints

The gateway keeps both canonical and `/api/robot` compatibility paths:

```http
GET /api/readiness
GET /api/robot/readiness
GET /api/preflight
GET /api/robot/preflight
GET /api/tasks/current
GET /api/robot/tasks/current
POST /api/robot/tasks/{task_id}/elder-response
GET /api/robot/tasks/{task_id}/evidence/arrival.jpg
GET /api/robot/tasks/{task_id}/callback-deliveries
POST /api/robot/tasks/{task_id}/callbacks/replay
```

## Restart Restore

Terminal tasks and blocked tasks are restored from the audit log. Running or waiting tasks are not resumed after service restart; they are restored as failed/interrupted with `SERVICE_RESTART_INTERRUPTED` so the robot never resumes motion implicitly.
