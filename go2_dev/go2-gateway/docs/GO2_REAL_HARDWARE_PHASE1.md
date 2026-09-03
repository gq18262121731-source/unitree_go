# Go2 Real Hardware Phase 5.1

This phase is a read-only validation path for Unitree Go2 EDU and the L1 LiDAR.
It is separate from the existing Mock navigation contract.

## Safety boundary

The code under `app/providers/unitree/` creates only:

- a CycloneDDS domain and participant;
- DDS built-in discovery readers;
- typed DDS data readers for discovered state and point-cloud topics.

It does not create a Unitree sport client, DDS publisher, data writer, motion
command, Nav2 node, or SLAM node. `RealGo2Provider` deliberately does not
implement the navigation provider protocol.

The probe refuses to start unless:

```text
ROBOT_PROVIDER=unitree_real
REAL_MOTION_ENABLED=false
```

The repository default remains:

```text
ROBOT_PROVIDER=mock
REAL_MOTION_ENABLED=false
```

## Run in the robot-connected Linux environment

```bash
cd /path/to/go2-gateway
source ~/.venvs/go2-gateway/bin/activate

export ROBOT_PROVIDER=unitree_real
export REAL_MOTION_ENABLED=false
export UNITREE_ROBOT_IP=192.168.123.161
export UNITREE_NETWORK_INTERFACE=eth0
export UNITREE_DOMAIN_ID=0

python scripts/verify_go2_phase1.py \
  --discovery-seconds 5 \
  --sample-seconds 15 \
  --output data/go2-phase1-report.json
```

The DDS topic list in the report comes from live DDS built-in discovery. State
readers are created only for discovered LowState and SportModeState publishers.
The point-cloud reader is created only for a discovered topic whose name
contains `utlidar` and whose discovered DDS type is `PointCloud2`.

## Report fields

The JSON report contains:

- selected interface, source IP, route, reachability, latency, and packet loss;
- DDS initialization state and the complete discovered topic/type list;
- LowState battery and IMU observations;
- SportModeState mode, position, IMU, and source timestamp;
- LiDAR topic, receive frequency, point count, source timestamp, frame, and
  receive latency.
- an overall `status`, boolean `passed`, and per-layer checks.

If network probing fails, DDS is not initialized. Missing state or LiDAR
samples remain explicit as zero counts and null measurements. The command
returns a non-zero exit code unless network, DDS, both state topics, and the
LiDAR point cloud all pass.

## Git baseline requirement

The intended application branch must be created from the frozen Mock baseline:

```bash
git checkout robot-mock-demo-v1.1
git checkout -b feature/go2-real-hardware-phase1
```

Do not initialize a replacement repository or manufacture this tag in a
directory that has no application Git history.
