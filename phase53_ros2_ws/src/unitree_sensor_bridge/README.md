# Unitree Sensor Bridge

Phase 5.3 read-only bridge for ROS 2 Humble.

## Data flow

| Unitree DDS / ROS2-compatible source | Published target |
| --- | --- |
| `/utlidar/cloud` | `/sensor/lidar` (`sensor_msgs/msg/PointCloud2`) |
| `/utlidar/imu` | `/sensor/imu` (`sensor_msgs/msg/Imu`) |
| `/utlidar/robot_odom` | `/odom` (`nav_msgs/msg/Odometry`) |

The bridge republishes complete messages without modifying their header stamps,
frame IDs, covariance values, point fields, or payloads. It does not publish
TF or any control topic.

## Safety boundary

This package contains no Unitree motion client, command message, `cmd_vel`,
SLAM, Nav2, or autonomous-control dependency. Its only publishers are the
three target sensor topics listed above.

## Build and run

```bash
cd ~/phase53_ros2_ws
source /opt/ros/humble/setup.bash
colcon build --symlink-install
source install/setup.bash
ros2 launch unitree_sensor_bridge unitree_sensor_bridge.launch.py
```

## RViz validation

Phase 5.3 does not publish TF. Validate each stream in its native fixed frame:

```bash
rviz2 -d src/unitree_sensor_bridge/config/phase53_lidar.rviz
rviz2 -d src/unitree_sensor_bridge/config/phase53_imu.rviz
rviz2 -d src/unitree_sensor_bridge/config/phase53_odom.rviz
```

The configurations use `utlidar_lidar`, `utlidar_imu`, and `odom`
respectively. A combined multi-frame view is deliberately deferred until the
separate TF validation phase.
