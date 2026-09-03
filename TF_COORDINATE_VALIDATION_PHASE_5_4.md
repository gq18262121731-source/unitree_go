# Phase 5.4 — Go2 TF 与坐标系验证报告

日期：2026-07-26  
环境：Ubuntu 22.04.5 LTS VM / ROS 2 Humble / CycloneDDS  
结论：**HOLD — 安全子集通过，完整 Phase 5.4 不通过**

## 1. Gate 结论

| 验收项 | 结果 | 证据 |
|---|---:|---|
| 枚举真实 `frame_id` | PASS | 实机 10 秒只读探针 |
| `/odom` 语义为 `odom -> base_link` | PASS | 1494 个唯一时间戳 |
| `odom -> base_link` 动态 TF | PASS | 约 150 Hz |
| 动态 TF 保留 `/odom` 时间戳 | PASS | 1494/1494 匹配，ratio 1.0 |
| 动态 TF 时间回拨 | PASS | 0 |
| `utlidar_lidar -> utlidar_imu` 静态 TF | PASS | Unitree L1 官方几何 |
| `base_link -> utlidar_lidar` 可信外参 | **FAIL / 缺失** | 官方 Go2 URDF 的 `radar_joint` 未通过实机校验 |
| TF 树完整连通 | **FAIL** | 当前有两个连通分量 |
| `/sensor/lidar` 在 RViz `Fixed Frame=odom` | **BLOCKED** | 缺少可信 `base_link -> utlidar_lidar` |
| SLAM / Nav2 / 控制接口 | 未进入 | 符合安全边界 |

**不得进入 Phase 5.5。**

## 2. 实机 frame 枚举

| Topic | 类型 | 原生 frame | 10 秒观测 |
|---|---|---|---:|
| `/sensor/lidar` | `sensor_msgs/PointCloud2` | `utlidar_lidar` | 约 15.3 Hz |
| `/sensor/imu` | `sensor_msgs/Imu` | `utlidar_imu` | 约 246 Hz |
| `/odom` | `nav_msgs/Odometry` | parent `odom`, child `base_link` | 约 148–150 Hz |
| `/utlidar/cloud_base` | `sensor_msgs/PointCloud2` | `base_link` | 约 15 Hz |
| `/utlidar/cloud_deskewed` | `sensor_msgs/PointCloud2` | `odom` | 约 15 Hz |
| `/utlidar/robot_pose` | `geometry_msgs/PoseStamped` | `odom` | 有样本 |

初始状态下 `/tf` 与 `/tf_static` 均无任何变换样本。Topic 名称存在是因为探针订阅了它们，不代表机器人发布了 TF。

原始证据：

- `phase54_source_frames.json`
- `phase54_probe.json`

## 3. 官方资料审查

### 3.1 Go2 官方模型

来源：

- `unitreerobotics/unitree_ros`
- commit `aa0f5c68b5aba347bad409e71b6430407da758d7`
- `robots/go2_description/urdf/go2_description.urdf`

模型定义：

```text
base -> radar
xyz = [0.28945, 0, -0.046825] m
rpy = [0, 2.8782, 0] rad
```

该模型把传感器命名为 `radar`，没有证明 `radar` 就是实机 DDS 消息中的 `utlidar_lidar`。

### 3.2 L1 LiDAR 官方几何

Unitree L1 SDK 明确给出 LiDAR 到内部 IMU 的平移，且两坐标轴方向平行：

```text
utlidar_lidar -> utlidar_imu
xyz = [-0.007698, -0.014655, 0.00667] m
rotation = identity
```

这条静态边有明确官方依据，因此允许发布。

## 4. `radar_joint` 实机否决测试

测试使用同一传感器时间戳的：

- `/utlidar/cloud`，frame `utlidar_lidar`
- `/utlidar/cloud_base`，frame `base_link`

把原始点云按官方 URDF 的 `base -> radar` 候选变换到 `base_link`，再对每个 `cloud_base` 点查找最近的候选点。

结果：

| 指标 | 数值 |
|---|---:|
| 匹配时间戳 | `1785059992351552486 ns` |
| 原始点数 | 4075 |
| `cloud_base` 点数 | 1284 |
| 平均误差 | 0.293 m |
| RMS 误差 | 0.517 m |
| P95 误差 | 0.919 m |
| 最大误差 | 3.081 m |
| Gate | 0.002 m RMS |
| 结论 | **FAIL** |

`cloud_base` 存在过滤/降采样，所以点数不同是正常现象；但该候选外参不能复现实机的机体坐标点云，误差远大于 Gate。因此：

> 不得把官方 URDF 的 `radar` 手动重命名为 `utlidar_lidar`，也不得发布该候选 TF。

原始证据：`phase54_extrinsic_check.json`

## 5. 已实现的安全 TF 子集

独立包：

```text
phase54_ros2_ws/src/unitree_tf_bridge
```

发布：

```text
odom -> base_link
```

- 数据只来自 `/odom.pose.pose`；
- parent/child 必须严格等于 `odom` / `base_link`；
- `TransformStamped.header` 直接复制 `/odom.header`；
- 不使用 `now()` 重打动态 TF 时间；
- 不使用固定时间 offset；
- frame 不匹配时拒绝发布。

发布：

```text
utlidar_lidar -> utlidar_imu
```

- 使用 Unitree L1 官方内部几何；
- 静态 TF；
- 不连接到 `base_link`，避免掩盖外参缺口。

刻意不发布：

```text
base_link -> utlidar_lidar
```

## 6. 运行验收

10 秒探针：

```text
odom -> base_link:
samples                 1494
unique odom stamps      1494
unique TF stamps        1494
matched stamps          1494
match ratio             1.0
TF timestamp rollback   0

utlidar_lidar -> utlidar_imu:
static samples           1
```

`tf2_tools view_frames`：

```text
component A:
odom
└── base_link
    average rate: 150.013 Hz

component B:
utlidar_lidar
└── utlidar_imu
```

证据：

- `phase54_partial_tf_probe.json`
- `phase54_frames.gv`
- `phase54_frames.pdf`
- `phase54_tf.log`
- `phase54_view_frames.log`

## 7. 安全边界

本阶段代码没有：

- `move()`
- `SportClient`
- `LowCmd`
- `cmd_vel`
- `/api/sport/request`
- SLAM
- Nav2
- 自动控制

节点只订阅 `/odom`，只输出 ROS TF。Phase 5.3 桥接和 Phase 5.4 TF 验证进程在取证完成后均已停止。

## 8. 解除 HOLD 所需证据

以下任一项可解除缺口，但仍须经过实机数值复核：

1. Unitree 针对当前 Go2 序列/固件的 `base_link -> utlidar_lidar` 工厂标定；
2. Go2 的只读配置导出中提供的 LiDAR 外参；
3. Unitree 官方文档明确把 `go2_description` 的 `radar` 绑定到 DDS `utlidar_lidar`，并说明固件/硬件版本适用范围；
4. 受控标定流程生成的外参及残差报告。

在获得可信外参前：

```text
Phase 5.4: HOLD
Phase 5.5 SLAM: 禁止进入
```
