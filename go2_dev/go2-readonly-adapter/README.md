# Go2 Readonly Adapter

Phase 6.1 standalone adapter for the integrated Go2 X EDU platform.

This project converts read-only DDS, ROS2, or replay observations into a stable
JSON capability contract. It is deliberately isolated from `health_new` and the
competition Mock implementation.

The frozen v1 contract is defined by:

```text
schema/readonly_status.schema.json
```

## Safety boundary

Included:

- SDK2 DDS subscription for public, verified Python IDL types;
- ROS2 subscription for the Phase 5.3 bridge outputs
  `/sensor/lidar`, `/sensor/imu`, and `/odom`;
- deterministic JSONL replay;
- timestamp, frequency, freshness, and semantic status;
- capability normalization and health reporting.

Excluded:

- DDS or ROS2 publishers;
- `/api/slam_operate/*` requests;
- `/uslam/client_command`;
- TF publication;
- SLAM, mapping, or Nav2;
- motion commands or task dispatch;
- any import from or write to `health_new`.

Sensor availability is never treated as localization or navigation readiness.
The `/utlidar/imu` semantic status remains `false` until a separate validation
explicitly supersedes the Phase 5.4.8/5.4.9 result.

## Offline use

From this directory:

```powershell
$env:PYTHONPATH = "src"
python -m go2_readonly_adapter.cli `
  --source replay `
  --input examples\offline_replay.jsonl `
  --pretty
```

Run tests:

```powershell
$env:PYTHONPATH = "src"
python -m pytest
```

## Ubuntu ROS2 observation

Run only in the already validated ROS2 Humble environment:

```bash
export PYTHONPATH="$PWD/src"
python3 -m go2_readonly_adapter.cli \
  --source ros2 \
  --duration 30 \
  --pretty \
  --output reports/ros2_readonly.json
```

This path creates three subscriptions and zero publishers.

## 30-minute real read-only soak

After Phase 5.4.11 has completed:

```bash
export PYTHONPATH="$PWD/src"
python3 -m go2_readonly_adapter.soak \
  --duration 1800 \
  --output ~/go2_validation/phase61b_readonly_soak.json
```

The soak writes an atomic checkpoint every five seconds and records topic
freshness, timestamp rollback, interval jitter, process CPU time, and RSS.

## Ubuntu SDK2 DDS observation

Run only after sourcing the environment containing the official
`unitree_sdk2py` and `cyclonedds` dependencies:

```bash
export PYTHONPATH="$PWD/src"
python3 -m go2_readonly_adapter.cli \
  --source dds \
  --interface enp0s8 \
  --robot-ip 192.168.123.161 \
  --duration 30 \
  --pretty \
  --output reports/dds_readonly.json
```

The public SDK2 Python checkout in this workspace does not contain the
`sensor_msgs/Imu` DDS IDL used by `/utlidar/imu`. The DDS reader therefore does
not fabricate that type; the validated ROS2 bridge is the observation route for
that topic.

## Git provenance

The requested parent branch `feature/go2-real-hardware-phase1` is not present in
the configured `unitree_go` remote, and the local `go2-gateway` directory is not
a Git repository. This standalone directory is therefore initialized as an
isolated experimental branch named `feature/go2-real-readonly-v1`, without
claiming a parent commit or pushing to a remote.
