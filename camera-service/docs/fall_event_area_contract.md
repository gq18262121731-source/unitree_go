# Fall Event Area Extension Contract

- Contract version: `1.0.0`
- Frozen at: `2026-07-22`
- Status: Step 2 frozen; code implementation is deferred
- Producer: `camera-service`
- Consumer: `health_new`

## 1. Purpose

This contract adds optional area identity to the existing confirmed-fall event without adding robot navigation knowledge to camera-service.

Only two fields are added:

```text
area_id
area_name
```

No existing field is removed, renamed, or assigned a new meaning.

The cross-system phase-one invariant remains `provider=mock` and
`real_motion_enabled=false`, but neither value is added to the camera event.
They are execution-layer state owned by health_new/go2-gateway; camera-service
still adds only `area_id` and `area_name`.

## 2. Reviewed implementation baseline

The local `E:\笨笨狗\camera-service` workspace currently contains only the `docs` directory. The producer baseline used for Step 1 was the reviewed upstream implementation of `app/services/fall_event_reporter_service.py`, where `_build_payload()` already emits fields including:

```text
camera_id
stream_name
source
event_type
state
status
severity
risk
risk_level
fall_detected
fall_prob
fall_score
track_id
incident_id
bbox
snapshot_url
snapshot_path
timestamp
scores
injury
metadata
```

At the reviewed revision, `area_id` and `area_name` were not present. This document freezes their additive behavior; it does not claim the producer code has already been changed locally.

## 3. Delivery endpoint

The health_new receiver is:

```http
POST /api/v1/video-bridge/fall-events
Content-Type: application/json
```

If camera-service composes the endpoint from a base URL that already ends in `/api/v1`, its configured relative path may remain `/video-bridge/fall-events`. The final HTTP target must be the complete path above.

## 4. Field definitions

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `area_id` | string or null | No | Stable health_new business-area identifier, such as `elderly_activity_area` |
| `area_name` | string or null | No | Human-readable display label, such as `养老活动区` |

Rules:

- Both fields are canonical top-level event fields.
- An unknown value is represented by omission or `null`, not an empty invented identifier.
- `area_id` should be stable across camera restarts and must not contain map coordinates.
- `area_name` is display metadata and must not be used as a navigation key.
- Producer-side maximum lengths and normalization are deferred to Step 3 and must align with health_new validation.
- Duplicating these fields into `metadata.event` is not required and is discouraged because it creates two sources of truth.

## 5. Compatible payload

```json
{
  "camera_id": "camera_01",
  "stream_name": "primary",
  "source": "vision_service",
  "event_type": "fall_confirmed",
  "state": "confirmed_fall",
  "risk_level": "high",
  "fall_detected": true,
  "fall_prob": 0.96,
  "track_id": "track_12",
  "incident_id": "incident_20260722_001",
  "bbox": [100, 120, 320, 520],
  "timestamp": "2026-07-22T10:00:00+08:00",
  "area_id": "elderly_activity_area",
  "area_name": "养老活动区",
  "metadata": {}
}
```

Legacy payloads without the new fields remain valid:

```json
{
  "camera_id": "camera_01",
  "event_type": "fall_confirmed",
  "incident_id": "incident_20260722_001",
  "timestamp": "2026-07-22T10:00:00+08:00"
}
```

## 6. Ownership and resolution

camera-service reports what area the configured camera observes. It does not know which robot point should be used.

health_new owns:

```text
area_id/camera_id
→ observation_point_id
→ home_point_id
→ alarm linkage
→ robot task creation
```

go2-gateway receives an already resolved Mock point ID from health_new and owns Mock execution state, safety interlock results, and control ownership.

## 7. Forbidden producer fields and actions

camera-service must not add or derive:

```text
target_point_id
x
y
yaw
robot_command
```

It must also not:

1. Call health_new robot task endpoints or go2-gateway.
2. Select a home, observation, patrol, or emergency target point.
3. Convert image coordinates, bounding boxes, camera calibration, or area labels into robot coordinates.
4. Trigger mapping, localization, patrol, return-home, or robot movement.
5. Treat missing area configuration as a reason to suppress a valid confirmed-fall event.

## 8. Error and retry behavior

- A missing `area_id` is not a camera-service delivery failure.
- Existing retry and idempotency behavior continues to use the event/incident identity already present in the producer.
- The producer must not rewrite `incident_id` during retries.
- A health_new response indicating unresolved area mapping is a downstream business state; camera-service must not invent a robot target and retry with coordinates.
- Logs may include `area_id` and `area_name`, but existing privacy rules for snapshots and person data remain unchanged.

## 9. Backward compatibility

- Old camera-service → new health_new: accepted; health_new falls back to existing camera/location resolution.
- New camera-service → old health_new: accepted by the reviewed health_new request model because it currently allows extra fields.
- New camera-service → new health_new: fields are explicitly parsed and used only for health_new-owned mapping.

This is an additive, optional schema extension.

## 10. Step 3 acceptance tests

1. Payload with both fields preserves all existing fields.
2. Payload without both fields is unchanged and still delivered.
3. `area_id` without `area_name` is accepted.
4. `area_name` without `area_id` is delivered as display-only metadata and does not become a key.
5. Retry keeps the same `incident_id`, `area_id`, and `area_name`.
6. No payload contains `target_point_id`, `x`, `y`, `yaw`, or `robot_command`.
7. No camera-service code imports or calls robot/gateway clients.
8. A missing area configuration does not suppress `fall_confirmed`.

## 11. Step 3 decisions

- Exact configuration source for camera-to-area assignment.
- String normalization and maximum lengths.
- Whether area values are attached at reporter construction time or event build time.
- Unit test fixture names and how the upstream repository is made available locally.

These decisions may not expand camera-service into robot scheduling or control.
