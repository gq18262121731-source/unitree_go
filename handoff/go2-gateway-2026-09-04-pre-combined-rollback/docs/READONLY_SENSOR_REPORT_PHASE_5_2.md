# Phase 5.2：Go2 真实传感器只读验证报告

测试日期：2026-07-25  
最终状态：**PASS（带时间同步与静态 IDL 兼容性备注）**  
停止点：Phase 5.2；未进入 Phase 5.3 ROS2 Bridge

## 1. 范围与安全边界

本轮仅在官方 Ethernet SDK2 DDS 链路上创建 Subscriber，并统计传感器消息的元数据与少量标量字段。

本轮没有：

- 创建业务 DDS Publisher 或发布任何 Topic；
- 调用 `move()`、`SportClient`、Motion API、`cmd_vel` 或其他运动控制；
- 启动 ROS2、Nav2、SLAM、建图、自动导航；
- 保存点云文件或持久化传感器载荷；
- 修改 Mock Provider、Mock API、Mock WebSocket、`health_new`；
- 修改 `unitree_sdk2py`、CycloneDDS、Domain ID 或网络配置；
- 新增或修改 go2-gateway 业务 Provider。

## 2. Phase 5.1 冻结基线

| 阶段 | 结果 |
| --- | --- |
| 5.1.0 环境确认 | PASS |
| 5.1.1 WLAN 单播网络 | PASS |
| 5.1.1 WLAN SDK2 DDS | FAIL |
| 5.1.2 WLAN / Go2 AP DDS 诊断 | 完成 |
| 5.1.3 SDK2 DDS 服务诊断 | 完成 |
| 5.1.4 官方 Ethernet DDS A/B | PASS |

冻结结论：

> 当前 Go2 固件环境中，WLAN/Go2 AP 可用于 Ping、App 和视频，但 SDK2 DDS 状态与传感器数据必须通过官方 Ethernet 开发链路访问。

## 3. 网络与 DDS

| 项目 | 实测值 |
| --- | --- |
| Go2 Ethernet IP | `192.168.123.161` |
| PC / WSL IP | `192.168.123.222/24` |
| WSL 接口 | `eth0` |
| 路由 | `192.168.123.161 dev eth0 src 192.168.123.222` |
| DDS Domain | `0` |
| SDK2 DDS | PASS |
| 远端 Participant | 25 个 |
| Publication endpoint | 130 个 |
| 唯一 `rt/*` Topic | 95 个 |

## 4. 实际发现的传感器 Topic

以下名称和类型均来自 CycloneDDS 内置 discovery 表，不是预设或猜测。除特别注明外，每个 Topic 均发现 1 个 Publisher。

| Topic | 消息类型 |
| --- | --- |
| `rt/utlidar/cloud` | `sensor_msgs::msg::dds_::PointCloud2_` |
| `rt/utlidar/cloud_base` | `sensor_msgs::msg::dds_::PointCloud2_` |
| `rt/utlidar/cloud_deskewed` | `sensor_msgs::msg::dds_::PointCloud2_` |
| `rt/utlidar/grid_map` | `sensor_msgs::msg::dds_::PointCloud2_` |
| `rt/utlidar/height_map` | `sensor_msgs::msg::dds_::PointCloud2_` |
| `rt/utlidar/height_map_array` | `unitree_go::msg::dds_::HeightMap_` |
| `rt/utlidar/imu` | `sensor_msgs::msg::dds_::Imu_` |
| `rt/utlidar/lidar_state` | `unitree_go::msg::dds_::LidarState_` |
| `rt/utlidar/range_info` | `geometry_msgs::msg::dds_::PointStamped_` |
| `rt/utlidar/range_map` | `sensor_msgs::msg::dds_::PointCloud2_` |
| `rt/utlidar/robot_odom` | `nav_msgs::msg::dds_::Odometry_` |
| `rt/utlidar/robot_pose` | `geometry_msgs::msg::dds_::PoseStamped_` |
| `rt/utlidar/voxel_map` | `sensor_msgs::msg::dds_::PointCloud2_` |
| `rt/utlidar/voxel_map_compressed` | `unitree_go::msg::dds_::VoxelMapCompressed_` |
| `rt/uslam/cloud_map` | `sensor_msgs::msg::dds_::PointCloud2_` |
| `rt/uslam/map_file_pub` | `sensor_msgs::msg::dds_::PointCloud2_` |

还发现 `mapping_cmd`、`switch`、`client_command` 等命令 Topic。本轮没有订阅或发布这些 Topic。

## 5. L1 LiDAR 点云验证

只读订阅：

```text
topic: rt/utlidar/cloud
type: sensor_msgs::msg::dds_::PointCloud2_
duration: 10.04 s
samples: 152
frequency: 15.13 Hz
frame_id: utlidar_lidar
```

点云统计：

| 项目 | 实测值 |
| --- | --- |
| 点数/帧（最小/平均/最大） | `3949 / 4070.05 / 4146` |
| 高度 | `1` |
| `point_step` | `32` bytes |
| 单帧示例数据长度 | `131104` bytes（4097 点） |
| 字段 | `x, y, z, intensity, ring, time` |
| `is_dense` | `true` |
| 时间戳 | 连续、单调递增 |

结论：L1 主点云真实数据持续可用，点数和频率稳定。

## 6. L1 状态验证

`rt/utlidar/lidar_state` 的设备端动态类型比当前本地 `unitree_sdk2py 1.0.1` 静态 IDL 多出
`dirty_percentage` 字段。使用本地静态 `LidarState_` 时无法收到可解码样本；保持 SDK 文件不变，
改用 CycloneDDS Type Discovery 获取设备端类型后成功只读：

```text
duration: 8.01 s
samples: 39
frequency: 4.87 Hz
software_version: 1.0.0.38
error_state: 0
dirty_percentage: 1
cloud_frequency: 15.31–15.39 Hz
cloud_packet_loss_rate: 0.552486（设备原始字段值，单位未确认）
cloud_size: 61020–61111
cloud_scan_num: 210
imu_frequency: 248.19–250.61 Hz
imu_packet_loss_rate: 0.0
```

结论：Topic 与数据均正常；这是本地静态 IDL 和设备端类型版本差异，不是 L1 服务离线。

## 7. L1 IMU 验证

当前 SDK 包未内置 `sensor_msgs::msg::dds_::Imu_` Python 类。本轮没有修改 SDK，而是从
`rt/utlidar/imu` Publication 的 TypeIdentifier 动态取得完整类型并只读订阅。

```text
topic: rt/utlidar/imu
type: sensor_msgs::msg::dds_::Imu_
duration: 10.02 s
samples: 2361
callback frequency: 235.57 Hz
message timestamp frequency: 248.45 Hz
frame_id: utlidar_imu
timestamp: 连续、单调递增
```

首样本标量：

```json
{
  "orientation_xyzw": [-0.063931, 0.014670, -0.000426, -0.996155],
  "angular_velocity": [0.006601, -0.005074, 0.004282],
  "linear_acceleration": [-0.078335, 0.094642, 9.750657]
}
```

验收字段中的 quaternion、gyro、acceleration 均已收到真实值。

## 8. Odometry 与 Pose 验证

### Odometry

```text
topic: rt/utlidar/robot_odom
type: nav_msgs::msg::dds_::Odometry_
duration: 10.03 s
samples: 1454
callback frequency: 144.92 Hz
message timestamp frequency: 150.27 Hz
frame_id: odom
child_frame_id: base_link
timestamp: 连续、单调递增
```

首样本：

```json
{
  "position": [1.804946, 1.130702, 0.304804],
  "orientation_xyzw": [-0.014413, -0.004323, 0.892350, -0.451094],
  "linear_velocity": [0.000606, -0.000082, -0.020609],
  "angular_velocity": [-0.001065, -0.005326, 0.002131]
}
```

### Pose

```text
topic: rt/utlidar/robot_pose
type: geometry_msgs::msg::dds_::PoseStamped_
duration: 10.03 s
samples: 182
callback frequency: 18.14 Hz
message timestamp frequency: 18.77 Hz
frame_id: odom
timestamp: 连续、单调递增
```

首样本：

```json
{
  "position": [1.804946, 1.130702, 0.304804],
  "orientation_xyzw": [-0.014413, -0.004323, 0.892350, -0.451094]
}
```

## 9. 时间戳与延迟备注

6 秒同步观测的 `PC time - message timestamp`：

| Topic | 最小/平均/最大 |
| --- | --- |
| `rt/utlidar/cloud` | `707.16 / 852.74 / 951.63 ms` |
| `rt/utlidar/imu` | `636.55 / 785.43 / 885.20 ms` |
| `rt/utlidar/robot_odom` | `637.14 / 785.99 / 885.09 ms` |
| `rt/utlidar/robot_pose` | `641.25 / 786.08 / 883.54 ms` |

这些数值随测试窗口持续变化，而且 IMU、Odometry、Pose 的偏差高度一致，因此包含 Go2/L1 与
PC 之间尚未校准的时钟偏移，不能直接当作 DDS 网络传输延迟。主点云相对其他消息平均多约
67 ms，可能包含点云扫描与组帧时间。进入需要跨设备时间对齐的后续阶段前，应先建立时钟同步
基线，再测端到端延迟。

本阶段的验收重点是消息持续到达、时间戳单调和频率稳定；三项均通过。

## 10. Readonly Sensor Report

```json
{
  "network": "PASS - Ethernet eth0, PC 192.168.123.222, Go2 192.168.123.161",
  "dds": "PASS - Domain 0, remote participants and RTPS sensor data visible",
  "lidar": "PASS - rt/utlidar/cloud, 15.13 Hz, avg 4070 points/frame",
  "lidar_state": "PASS via dynamic type discovery - 4.87 Hz, error_state=0",
  "imu": "PASS - rt/utlidar/imu, message timestamp rate 248.45 Hz",
  "odometry": "PASS - rt/utlidar/robot_odom, message timestamp rate 150.27 Hz",
  "pose": "PASS - rt/utlidar/robot_pose, message timestamp rate 18.77 Hz",
  "motion_control": "NOT USED",
  "dds_publish": "NOT USED",
  "ros2_nav2_slam": "NOT STARTED"
}
```

## 11. 最终判断与停止点

Phase 5.2 真实传感器只读链路通过：

- L1 点云已收到，频率、点数、字段、坐标系和时间戳已确认；
- L1 IMU 已收到，四元数、角速度、线加速度均可读取；
- Odometry 和 Pose 已收到，坐标系及速度/姿态字段已确认；
- L1 状态服务正常，发现一个需要后续处理的 SDK 静态 IDL 版本差异；
- 跨设备时间戳可用且单调，但端到端延迟测量前需要先解决时钟同步。

按阶段边界在此停止。没有进入 Phase 5.3 ROS2 Bridge。
