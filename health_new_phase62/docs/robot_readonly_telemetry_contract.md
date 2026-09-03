# Phase 6.2 Robot Readonly Telemetry Contract

Phase 6.2 adds one isolated, read-only API:

```text
GET /api/v1/robot/telemetry
```

It does not replace or widen the frozen Mock task, navigation, map, emergency,
or WebSocket contracts. Those interfaces still require:

```text
provider=mock
real_motion_enabled=false
```

## Runtime modes

The default mode is:

```text
robot_telemetry_provider=mock
```

In this mode the endpoint reports `source_status=mock_frozen` and does not read
an external source.

To consume a Phase 6.1 snapshot, set:

```text
robot_telemetry_provider=unitree_readonly
```

and configure exactly one preferred source:

```text
robot_readonly_snapshot_path=<absolute path to atomically updated JSON>
```

or:

```text
robot_readonly_snapshot_url=<read-only HTTP URL returning the JSON object>
```

The HTTP source has priority when both values are present. The configured
timeout is `robot_readonly_snapshot_timeout_seconds` and defaults to one second.

The source payload must satisfy the frozen Phase 6.1 schema from
`go2-readonly-adapter/schema/readonly_status.schema.json`. Any missing field,
unknown field, provider mismatch, or attempt to set
`real_motion_enabled=true` fails closed as `source_status=invalid`; the invalid
payload is not returned to the frontend.

## Safety and semantic boundary

- No command endpoint is added.
- Motion remains unavailable with an empty command list.
- Localization and navigation remain unavailable.
- Sensor availability is not navigation readiness.
- `/utlidar/imu` may be available while `semantic_valid=false`.
- Battery is optional display data read from
  `robot.telemetry.value.battery_percentage`; it is never fabricated.
- An unavailable or invalid source does not fall back to a real capability.

Phase 6.2 deliberately excludes point clouds, maps, TF, SLAM, Nav2, task
dispatch, and all robot motion.
