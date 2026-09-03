# Phase 5.4.2 — Go2 SDK/API 标定信息只读探测报告

日期：2026-07-26  
设备锚点：Go2 X V2.0 / Firmware V1.1.15（App 信息）  
L1 服务软件版本：`1.0.0.38`  
环境：Ubuntu 22.04.5 VM / Go2 Ethernet `enp0s8` / DDS Domain 0  
结论：**未找到官方或设备 API 暴露的 `base_link → utlidar_lidar` 外参；Phase 5.4 继续 HOLD。**

## 1. 范围与安全边界

本轮只执行：

- Unitree 官方 SDK2、SDK2 Python、ROS2 与 UniLiDAR 源码静态审查；
- `/api/robot_state` 的只读 `ServiceList` RPC；
- `/api/config` 的只读版本查询和 `Meta` RPC；
- `rt/utlidar/lidar_state` Subscriber；
- 与 Phase 5.4.1 `cloud → cloud_base` 反推结果的离线对比。

本轮没有：

- 调用 `Config.Set` 或 `Config.Del`；
- 调用 `ServiceSwitch` 或 `SetReportFreq`；
- 修改或保存设备配置；
- 创建控制客户端或调用 `SportClient`、`move()`、`LowCmd`、`cmd_vel`；
- 发布 TF；
- 启动 SLAM、Nav2；
- 修改 Mock、`health_new` 或 `go2-gateway` 业务代码。

说明：Unitree 的 `/api/config` 与 `/api/robot_state` 是 DDS RPC
request/response Topic，不是 HTTP GET。本轮只发送语义为查询的 RPC request；
写 API 和服务切换 API 的调用次数为 0。

## 2. SDK/API 定义审查

审查的官方仓库版本：

| 仓库 | Commit |
|---|---|
| `unitreerobotics/unitree_sdk2` | `7740f8b67e386ab09c3b333187fd5f8582a75ddc` |
| `unitreerobotics/unitree_sdk2_python` | `37116c521f1588482e238d8450e471ba78ab9863` |
| `unitreerobotics/unitree_ros2` | `668d1ec5a05d1c38d3306bdca7d59f2ba3581a88` |
| `unitreerobotics/unilidar_sdk` | `1bd7d95d8ab7ce7a22058d2bb07e39fd62612aa6` |

### 2.1 Config API

来源：

```text
unitree_sdk2/include/unitree/robot/go2/config/config_api.hpp
unitree_sdk2/include/unitree/robot/go2/config/config_client.hpp
```

公开接口：

| API | ID | 语义 | 本轮 |
|---|---:|---|---|
| `Set(name, content)` | 1001 | 写配置 | 禁止、未调用 |
| `Get(name, content)` | 1002 | 读配置内容 | 仅在 Meta 成功后允许；本轮 0 次 |
| `Del(name)` | 1003 | 删除配置 | 禁止、未调用 |
| `Meta(name, meta)` | 1004 | 读配置元数据 | 已调用 |

`ConfigMeta` 格式：

```text
name: string
lastModified: string
size: int32
epoch: int32
```

`Config.Get` 的返回格式仅定义为：

```text
content: string
```

SDK 没有 `List`/枚举配置名称的 API。因此，在不知道设备内部精确 config
name 的情况下，只能对有依据的候选名称做 `Meta`，不能完整枚举设备配置库。

### 2.2 Robot State API

来源：

```text
unitree_sdk2/include/unitree/robot/go2/robot_state/robot_state_api.hpp
unitree_sdk2/include/unitree/robot/go2/robot_state/robot_state_client.hpp
```

| API | ID | 语义 | 本轮 |
|---|---:|---|---|
| `ServiceSwitch` | 1001 | 改变服务状态 | 禁止、未调用 |
| `SetReportFreq` | 1002 | 改变报告频率 | 禁止、未调用 |
| `ServiceList` | 1003 | 读取服务列表 | 已调用 1 次 |

### 2.3 LidarState IDL

来源：

```text
unitree_sdk2/include/unitree/idl/go2/LidarState_.hpp
```

字段只有：

```text
stamp
firmware_version
software_version
sdk_version
sys_rotation_speed
com_rotation_speed
error_state
dirty_percentage
cloud_frequency
cloud_packet_loss_rate
cloud_size
cloud_scan_num
imu_frequency
imu_packet_loss_rate
imu_rpy
serial_recv_stamp
serial_buffer_size
serial_buffer_read
```

没有：

```text
extrinsic
calibration pose
transform
base_link
utlidar_lidar mounting pose
```

### 2.4 UniLiDAR 配置与内部标定字段

公开 UniLiDAR 配置包含：

```text
rotate_yaw_bias
range_scale
range_bias
range_min / range_max
cloud_frame / imu_frame
```

底层 MAVLink auxiliary data 还定义：

```text
b_axis_dist
theta_angle
ksi_angle
```

其注释是激光测距内部补偿参数。它们不是 LiDAR 安装到 Go2
`base_link` 的六自由度外参，不能用于补齐 TF。

## 3. `/api/robot_state` 实机结果

客户端 API 版本：

```text
1.0.0.1
```

设备端 API 版本：

```text
1.0.0.2
```

`ServiceList`：

```text
code: 0
service count: 31
```

与本任务直接相关的服务：

| 服务 | status | protect |
|---|---:|---:|
| `unitree_lidar` | 0 | 0 |
| `unitree_lidar_slam` | 0 | 0 |
| `voxel_height_mapping` | 0 | 0 |
| `robot_state` | 0 | 1 |

结果只证明这些服务已注册。ServiceState 仅包含：

```text
name
status
protect
```

没有配置路径、外参、frame 或传感器 pose。

## 4. `/api/config` 实机结果

客户端与服务端 API 版本均为：

```text
1.0.0.1
```

对以下 17 个有依据的候选名称执行了只读 `Meta`：

```text
calibration
extrinsic
lidar
lidar_config
mapping
pose
radar
robot
robot_config
sensor
sensor_config
slam
unitree_lidar
unitree_lidar_slam
uslam
utlidar
utlidar_config
```

全部返回：

```text
8202: config name is not found
```

由于 Meta 全部失败，探针没有调用任何 `Config.Get`，没有读取或保存配置内容。

这证明：

1. 当前公开 SDK 能访问 config 服务；
2. 上述直观名称不是设备 Config API 的有效配置名；
3. SDK 没有名称枚举接口；
4. 不能据此证明设备内部没有外参，只能确认**当前公开、可追溯的 API 路径未暴露外参**。

## 5. `rt/utlidar/lidar_state` 实机结果

```text
software_version=1.0.0.38
firmware_version=
sdk_version=
error_state=0
dirty_percentage=83
cloud_frequency=15.393104 Hz
cloud_packet_loss_rate=0
imu_frequency=248.134720 Hz
imu_packet_loss_rate=0
publisher_count=0
```

设备状态正常，但消息中没有安装外参字段。`firmware_version` 和
`sdk_version` 仍为空，不能用它们建立更精细的配置版本映射。
`dirty_percentage=83` 是本轮原始状态值，较 Phase 5.4.1 的样本值 5 明显
升高；它不影响本轮“是否暴露外参”的结构判断，但应在后续传感器质量检查中
单独复核，本轮不清洁、不校准、不修改设备。

## 6. 官方公开几何来源

官方公开材料能够确认：

```text
utlidar_lidar → utlidar_imu

translation:
[-0.007698, -0.014655, 0.00667] m

rotation:
identity
```

它是 LiDAR 内部点云坐标系到内部 IMU 坐标系的几何，不包含 Go2
`base_link`。

Unitree ROS2 的公开说明只要求把 RViz Fixed Frame 设置为
`utlidar_lidar` 来显示原始点云，也没有声明
`base_link → utlidar_lidar`。

官方 Go2 URDF 的 `radar_joint`：

```text
base → radar
xyz = [0.28945, 0, -0.046825] m
rpy = [0, 2.8782, 0] rad
```

没有把 `radar` 明确绑定为当前实机 DDS frame `utlidar_lidar`，并且已经被
实机点云数值验证否决。

## 7. 与 `cloud_base` 反推结果对比

Phase 5.4.1 从同时间戳、同点标识的 raw cloud 与 cloud_base 反推：

```text
source: utlidar_lidar
target: base_link

translation [m]:
  [0.2821600275, 0.0000000170, -0.0000000349]

rotation matrix:
   0.526113905  -0.810135815   0.258619645
  -0.838668172  -0.544642725   0.000001579
   0.140854029  -0.216896895  -0.965979233

quaternion [x, y, z, w]:
  [-0.8713116353, 0.4730810194, -0.1146184836, 0.0622333233]

RPY [rad]:
  [-2.920720112, -0.141323991, -1.010529934]
```

拟合残差：

```text
mean: 0.0000000768 m
P95:  0.0000002110 m
max:  0.0000009849 m
```

与 URDF `radar_joint` 的差异：

```text
translation norm: 0.047389 m
rotation:         2.146760 rad（约 123°）
```

结论：

- 反推结果高置信证明固件内部存在稳定固定变换；
- 但它仍是输出数据的逆向测量，不是官方标定文件或设备配置导出；
- URDF `radar_joint` 不能作为替代；
- 本轮 SDK/API 结果没有提供可用于交叉确认反推矩阵的官方参数。

## 8. 是否找到官方外参

```text
base_link → utlidar_lidar:
NOT FOUND
```

相关但不满足要求的来源：

| 来源 | 找到内容 | 是否满足 |
|---|---|---:|
| Unitree L1/UniLiDAR 官方几何 | `utlidar_lidar → utlidar_imu` | 否 |
| Go2 公共 URDF | `base → radar` | 否，实机不匹配 |
| `/api/robot_state` | LiDAR 服务名和状态 | 否 |
| `/api/config` | 服务可达；候选名称均不存在 | 否 |
| `LidarState` | 运行质量与版本字段 | 否 |
| `cloud → cloud_base` 反推 | 极稳定六自由度候选 | 否，来源不可追溯 |

## 9. Phase 5.4 Gate

```text
[x] SDK2 Config/RobotState API 定义已审查
[x] RobotState ServiceList 只读实测
[x] Config Meta 只读实测
[x] LidarState 只读复测
[x] UniLiDAR 内部标定字段语义已区分
[x] 与 cloud_base 反推结果完成对比
[x] 未修改或保存设备配置
[x] 未发布 TF
[x] 未启动 SLAM/Nav2
[x] 未调用运动控制

[ ] 找到可追溯的 base_link → utlidar_lidar 官方/设备外参
[ ] 官方确认 cloud_base 的外参和接口契约
```

最终判断：

```text
Phase 5.4: HOLD
Phase 5.5 SLAM: BLOCKED
TF guess publication: PROHIBITED
```

不能解除 Phase 5.4 HOLD。

## 10. 下一步允许项

只允许继续获取以下任一证据：

1. Unitree 技术支持针对本机型号、序列号和 Firmware V1.1.15 提供的工厂
   `base_link → utlidar_lidar` 标定；
2. 官方说明精确的 Config API `name`，再使用 `Meta/Get` 只读读取；
3. 官方说明 `cloud_base` 的 frame 契约、外参来源、过滤与 deskew 行为；
4. 受控标定流程和独立残差报告。

在此之前，不发布反推 TF，不进入 SLAM/Nav2。

## 11. 原始证据

- `phase542_sdk_api_probe_result.txt`
- `phase542_lidar_state.txt`
- `phase54_tools/phase542_sdk_api_probe.cpp`
- `phase54_tools/CMakeLists_phase542_api_probe.txt`
- `LIDAR_COORDINATE_CHAIN_ANALYSIS_PHASE_5_4_1.md`
- `phase541_analysis.json`

官方参考：

- [Unitree SDK2](https://github.com/unitreerobotics/unitree_sdk2)
- [Unitree ROS2 LiDAR/RViz 说明](https://github.com/unitreerobotics/unitree_ros2#rviz)
- [Unitree UniLiDAR SDK 坐标定义](https://github.com/unitreerobotics/unilidar_sdk)

到此停止。
