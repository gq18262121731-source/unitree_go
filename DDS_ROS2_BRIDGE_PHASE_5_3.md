# Phase 5.3 DDS to ROS2 Bridge Acceptance Report

Updated: 2026-07-26 17:30 +08:00

Overall status: **PASS with one RViz plugin shutdown observation**  
Phase 5.4 TF validation: **NOT ENTERED**

## Scope and safety boundary

Phase 5.3 implemented and validated a read-only bridge from the Go2 L1
ROS2-compatible DDS sensor publications to stable ROS2 topic names.

The implementation does not contain or use:

- `move()` or `SportClient`;
- `LowCmd` or `cmd_vel`;
- a Unitree command publisher;
- TF publication;
- SLAM, Nav2, autonomous control, return, or patrol;
- changes to `health_new`, Mock Provider, or `go2-gateway` business logic.

## Environment

| Item | Verified value | Status |
| --- | --- | --- |
| Ubuntu | 22.04.5 LTS | PASS |
| Kernel | `6.8.0-136-generic` | PASS |
| ROS2 | Humble Desktop | PASS |
| RMW | `rmw_cyclonedds_cpp` | PASS |
| Go2 interface | `enp0s8`, `192.168.123.223/24` | PASS |
| Go2 | `192.168.123.161` | PASS |
| CycloneDDS interface selection | file configuration selecting `enp0s8` | PASS |

## Phase 5.3.1 DDS Reader validation

The reader used Unitree SDK2 commit `7740f8b`, five
`ChannelSubscriber` instances, and no publisher.

Ten-second VM result:

| DDS topic | Samples | Frequency | Status |
| --- | ---: | ---: | --- |
| `rt/lowstate` | 4930 | 492.981 Hz | PASS |
| `rt/sportmodestate` | 2949 | 294.889 Hz | PASS |
| `rt/utlidar/cloud` | 154 | 15.399 Hz | PASS |
| `rt/utlidar/imu` | 2476 | 247.591 Hz | PASS |
| `rt/utlidar/robot_odom` | 1500 | 149.994 Hz | PASS |

Reader exit code: `0`  
Publisher count: `0`

## Phase 5.3.2 bridge implementation

Host source:

```text
E:\笨笨狗\phase53_ros2_ws\src\unitree_sensor_bridge
```

VM workspace:

```text
~/phase53_ros2_ws
```

The bridge is an independent `ament_cmake` C++ package. It does not link the
SDK2 bundled CycloneDDS library into the ROS2 process. ROS2 CycloneDDS reads
the Unitree ROS2-compatible DDS types directly on `enp0s8`, then the bridge
republishes the complete standard messages.

| Unitree source | Source type | ROS2 target | Target type |
| --- | --- | --- | --- |
| `/utlidar/cloud` | `sensor_msgs/msg/PointCloud2` | `/sensor/lidar` | `sensor_msgs/msg/PointCloud2` |
| `/utlidar/imu` | `sensor_msgs/msg/Imu` | `/sensor/imu` | `sensor_msgs/msg/Imu` |
| `/utlidar/robot_odom` | `nav_msgs/msg/Odometry` | `/odom` | `nav_msgs/msg/Odometry` |

`colcon build --symlink-install` completed with one package built.

ROS graph inspection showed exactly the three source subscriptions and three
target sensor publishers above. No `/cmd_vel`, command topic, or `/tf`
publisher was present.

## Topic frequency acceptance

Independent `ros2 topic hz` observations:

| Target topic | Observed rate | Expected | Status |
| --- | ---: | ---: | --- |
| `/sensor/lidar` | approximately 15.4 Hz | approximately 15 Hz | PASS |
| `/sensor/imu` | approximately 250.7 Hz | approximately 248 Hz | PASS |
| `/odom` | approximately 148.7 Hz | approximately 150 Hz | PASS |

An independent ten-second `rclpy` validation produced:

| Target topic | Samples | Rate |
| --- | ---: | ---: |
| `/sensor/lidar` | 155 | 15.497 Hz |
| `/sensor/imu` | 2505 | 250.450 Hz |
| `/odom` | 1501 | 150.070 Hz |

## Timestamp and frame integrity

The C++ bridge republishes each complete input message and does not write to
`header.stamp`.

Simultaneous source/target validation:

| Stream | Source-target stamp match ratio | Zero stamps | Backward jumps | Frame |
| --- | ---: | ---: | ---: | --- |
| Lidar | 1.0 | 0 | 0 | `utlidar_lidar` |
| IMU | 1.0 | 0 | 0 | `utlidar_imu` |
| Odometry | 1.0 | 0 | 0 | `odom` |

No fixed offset, `time.time()` replacement, or fabricated timestamp is used.
PointCloud2 fields and payload, IMU covariance, and Odometry pose/twist and
child frame are forwarded unchanged.

## RViz validation

The three streams were validated separately in their native fixed frames so
that Phase 5.3 would not fabricate or prematurely publish TF:

| Display | Fixed frame | Result |
| --- | --- | --- |
| PointCloud2 `/sensor/lidar` | `utlidar_lidar` | live colored point cloud visible; PASS |
| IMU `/sensor/imu` | `utlidar_imu` | live orientation axes visible; PASS |
| Odometry `/odom` | `odom` | live pose direction arrow visible; PASS |

Package `ros-humble-rviz-imu-plugin`
`2.1.5-1jammy.20260612.200618` was installed for IMU visualization.

Observation: the IMU plugin displayed data correctly, but RViz reported a
segmentation fault while closing the IMU configuration, including after a
normal window-close sequence. The bridge and DDS streams continued normally.
This is recorded as an RViz plugin shutdown issue, not hidden as a clean exit.
It does not alter the bridge topic, frequency, timestamp, or frame acceptance
results.

## VM artifacts

```text
~/go2_validation/phase53_dds_reader_result.txt
~/go2_validation/phase53_bridge.log
~/go2_validation/phase53_bridge_validation.json
~/go2_validation/phase53_bridge_node_info.txt
~/go2_validation/hz_lidar.txt
~/go2_validation/hz_imu.txt
~/go2_validation/hz_odom.txt
~/go2_validation/rviz_lidar.log
~/go2_validation/rviz_imu.log
~/go2_validation/rviz_imu_clean.log
~/go2_validation/rviz_odom.log
```

## Final Gate

- [x] SDK2 read-only DDS Reader receives LowState and SportModeState
- [x] SDK2 read-only DDS Reader receives L1, IMU, and Odometry
- [x] `/sensor/lidar` exists with `sensor_msgs/msg/PointCloud2`
- [x] `/sensor/imu` exists with `sensor_msgs/msg/Imu`
- [x] `/odom` exists with `nav_msgs/msg/Odometry`
- [x] Lidar, IMU, and Odometry rates meet the expected baseline
- [x] Sensor timestamps are preserved with no offset or rollback
- [x] No control publisher or motion API exists in the bridge
- [x] PointCloud2, IMU, and Odometry can be displayed in RViz
- [x] Bridge, RViz, and temporary file-transfer services stopped after validation
- [x] Phase 5.4 TF validation was not started

Phase 5.3 is accepted. Work stops here before Phase 5.4.
