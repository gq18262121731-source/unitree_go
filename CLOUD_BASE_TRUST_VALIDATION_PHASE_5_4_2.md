# Phase 5.4.2 — `cloud_base` 与 LiDAR 外参可信性验证报告

日期：2026-07-26  
平台：Ubuntu 22.04 VMware / ROS 2 Humble / CycloneDDS  
设备：Unitree Go2 + L1，设备软件版本 `1.0.0.38`  
结论：**`cloud_base` 作为低速 SLAM 输入候选通过；不得直接进入 Phase 5.5；Phase 5.4 继续 HOLD。**

## 1. 安全边界

本轮仅订阅：

- `/utlidar/cloud`
- `/utlidar/cloud_base`
- `/utlidar/cloud_deskewed`
- `/utlidar/robot_odom`
- Phase 5.3 只读桥接输出 `/odom`

机器人运动由现场人员人工安全移动完成。本轮没有发布 TF，没有运行 SLAM/Nav2，没有发布控制 topic，也没有调用 `move()`、`SportClient`、`LowCmd` 或 `cmd_vel`。

## 2. 采样与运动覆盖

正式采样持续 `60.080 s`：

| Topic | Frame | 样本数 | 频率 |
|---|---|---:|---:|
| `/utlidar/cloud` | `utlidar_lidar` | 922 | 15.346 Hz |
| `/utlidar/cloud_base` | `base_link` | 922 | 15.346 Hz |
| `/utlidar/cloud_deskewed` | `odom` | 871 | 14.497 Hz |
| `/utlidar/robot_odom` | `odom → base_link` | 8974 | 149.367 Hz |
| `/odom` | `odom → base_link` | 8967 | 149.251 Hz |

运动覆盖：

```text
net displacement:       0.805 m
accumulated path:       6.323 m
position span:          [0.426, 1.816, 0.032] m
accumulated rotation:   15.535 rad
reported moving ratio:  50.06%
max linear speed:       1.072 m/s
max angular speed:      2.598 rad/s
```

运动 Gate 全部通过：

```text
[x] accumulated path >= 0.5 m
[x] accumulated rotation >= 30 deg
[x] moving samples >= 10%
```

## 3. 连续性与时间 Gate

所有话题：

- backward jump：`0`
- adjacent duplicate stamp：`0`

`cloud` 与 `cloud_base`：

- `922/922` 时间戳逐帧完全相同；
- 频率与间隔分布完全相同；
- median interval：`65.005 ms`
- P95 interval：`69.851 ms`
- max interval：`76.619 ms`

抽样的 308 帧中，`cloud_base` 对最近 odometry 的时间差：

- median：`-0.137 ms`
- P95：`3.169 ms`
- max：`8.199 ms`

以上统计排除了采集启动边界的第一帧 `60.227 ms` 单点离群。

`cloud_deskewed` 在相同窗口只有 871 帧，并出现一次最长 `325.447 ms` 间隔；因此它在本次采样中的连续性弱于 raw/base。

## 4. 字段、点数与过滤

`cloud` 和 `cloud_base` 都保留：

```text
x, y, z, intensity, ring, time
```

点数统计：

| Topic | Min | Mean | Median | Max |
|---|---:|---:|---:|---:|
| raw cloud | 3969 | 4119.8 | 4124 | 4196 |
| cloud_base | 1016 | 1382.9 | 1386 | 1772 |
| cloud_deskewed | 11016 | 11383.8 | 11388 | 11772 |

308 对 raw/base 扫描中：

- base 点的 metadata 对应率：`100%`
- 对应点 `time` 字段 bit-exact：`100%`
- base 平均保留 raw 点：`33.55%`
- 保留范围：`25.97%–41.81%`
- 每帧 time span 平均：`61.19 ms`
- 每帧 time span P95：`63.06 ms`

因此 `cloud_base` 是 raw cloud 的过滤子集，保留每点时间、ring 和 intensity，并应用固定坐标变换；它不是重建出来的独立扫描。

## 5. 运动状态下的固定外参验证

使用 Phase 5.4.1 的实测外参候选：

```text
utlidar_lidar → base_link

translation [m]:
  [0.2821600275, 0.0000000170, -0.0000000349]

quaternion [x, y, z, w]:
  [-0.8713116353, 0.4730810194, -0.1146184836, 0.0622333233]
```

本轮在 308 对扫描、425,527 个精确点对应上复验：

| 工况 | Mean residual | P95 residual | Max residual |
|---|---:|---:|---:|
| 全部 | 0.090 μm | 0.243 μm | 1.442 μm |
| 运动 | 0.096 μm | 0.270 μm | 1.442 μm |
| 静止 | 0.084 μm | 0.217 μm | 1.232 μm |

运动与静止的误差处于同一数值量级。这证明当前设备软件在实测运动工况下仍稳定使用同一固定刚体变换；Phase 5.4.1 的外参候选不是静止场景偶合结果。

但该数值的 provenance 仍是：

```text
hardware_observed_extrinsic
```

不是：

```text
official_calibration
```

所以仍不得无说明地写入官方 URDF 或声明为 Unitree 出厂标定。

## 6. 点云运动连续性

将连续抽样的 `cloud_base` 按最近 `/utlidar/robot_odom` 位姿放到共同 odom 坐标后比较，抽样间隔约 `195 ms`：

| 指标 | 静止 | 运动 |
|---|---:|---:|
| inter-frame median nearest-neighbor | 0.0828 m | 0.0821 m |
| 10 cm 内点比例 | 54.67% | 53.14% |

运动工况没有出现明显的整体跳变或连续性崩坏。该指标受场景遮挡、过滤点变化和约 5 Hz 的抽样频率影响，只能用于排除 gross jump，不能作为 deskew 精度证明。

## 7. Deskew 判断

`cloud_base` 中每个保留点与 raw cloud 的 metadata、时间字段完全一致，空间坐标仅表现为固定刚体变换。结合另有独立 `/utlidar/cloud_deskewed` 产品，当前最保守的工程判断是：

> 不应假设 `cloud_base` 额外完成了运动 deskew；除非 Unitree 提供接口契约或后续专门标定证明。

本次运动工况中，使用速度乘每帧约 61 ms 的点时间跨度估算潜在未补偿运动暴露：

| 运动样本 | Median | P95 | Max |
|---|---:|---:|---:|
| 平移 | 8.9 mm | 30.9 mm | 34.1 mm |
| 旋转 | 1.34° | 5.38° | 6.33° |

这只是保守暴露估计，不是直接测得的点云畸变量，但已足以说明高速或快速转向时不能忽略 deskew。

## 8. SLAM 输入契约

### SLAM Toolbox

SLAM Toolbox 是 2D SLAM，官方订阅 `/scan`，类型为 `sensor_msgs/LaserScan`，并要求 `odom_frame → base_frame` 的有效 TF。`cloud_base` 是 `sensor_msgs/PointCloud2`，因此**不能直接连接 SLAM Toolbox**。

如选择此路线，需要先增加只读转换层：

```text
/utlidar/cloud_base (PointCloud2, base_link)
          |
          | height/range filtering
          v
/scan (LaserScan, base_link)
```

`pointcloud_to_laserscan` 官方 ROS 2 节点可以完成 `PointCloud2 → LaserScan`，但高度范围、角度分辨率、range、scan_time 以及动态畸变必须单独验收。

### Cartographer

Cartographer ROS 可以按配置订阅 `sensor_msgs/PointCloud2` 的 `points2` 输入，因此 `cloud_base` 在消息类型上是候选。但是完整 TF、时间同步、采样率、deskew/运动畸变以及配置适配仍未验收。

官方参考：

- [SLAM Toolbox 官方仓库与 topic 契约](https://github.com/SteveMacenski/slam_toolbox)
- [ROS 2 Humble SLAM Toolbox API](https://docs.ros.org/en/ros2_packages/humble/api/slam_toolbox/generated/classslam__toolbox_1_1LocalizationSlamToolbox.html)
- [ROS 2 PointCloud-to-LaserScan](https://github.com/ros-perception/pointcloud_to_laserscan)
- [Cartographer ROS 文档](https://google-cartographer-ros.readthedocs.io/en/latest/)

## 9. 是否需要发布 `base_link → utlidar_lidar`

### 使用 `/utlidar/cloud_base`

不需要为了该 topic 人工补外参 TF，因为消息已经声明 `frame_id=base_link`。强行补一个猜测 TF 只会增加双重变换风险。

仍然需要：

- `odom → base_link` 动态 TF；
- 后续 SLAM 产生的 `map → odom`；
- RobotModel 或其他传感器需要的可信静态 TF。

### 使用 `/utlidar/cloud`

仍然需要 `base_link ↔ utlidar_lidar` 的可信外参。若最终采用本报告的反求结果，必须：

- 标注 `source=hardware_observed_extrinsic`
- 绑定设备型号、序列号/硬件版本和软件版本 `1.0.0.38`
- 保留生成数据、算法和误差报告
- 启动时验证 `cloud → cloud_base` 残差，防止固件更新后静默失配

## 10. Gate 结论

```text
[x] cloud_base 点云连续、无时间回拨
[x] raw/base 时间戳 922/922 完全一致
[x] 每点 time/ring/intensity 得到保留
[x] 运动覆盖 Gate 全部通过
[x] 固定外参在运动工况下保持一致
[x] 未发现运动导致的 gross frame jump
[x] 使用 cloud_base 时无需猜测 lidar 静态 TF

[ ] cloud_base 的官方接口契约/外参来源可追溯
[ ] cloud_base 已 deskew 得到证明
[ ] PointCloud2 → LaserScan 的参数与输出质量通过
[ ] SLAM 所需完整 TF Gate 通过
[ ] Phase 5.5 离线 bag 验证通过
```

最终判断：

```text
cloud_base 坐标与时间可信性:       PASS
运动状态固定外参一致性:           PASS
作为低速 SLAM 输入候选:           CONDITIONAL PASS
直接接入 SLAM Toolbox:            NO
直接进入 Phase 5.5 在线 SLAM:     NO
Phase 5.4:                        HOLD
```

建议下一阶段不是在线 SLAM，而是：

```text
Phase 5.4.3
cloud_base → LaserScan 只读转换与 rosbag 离线质量 Gate
```

完成后停止，仍不发布控制，不运行 Nav2。

## 11. 证据文件

- `phase542_motion_capture.json`：60 秒 topic/odom/时间戳捕获
- `phase542_motion_clouds.npz`：308 对 raw/base 与 88 个 deskewed 点云样本
- `phase542_motion_analysis.json`：完整统计和逐帧结果
- `phase54_tools/phase542_capture.py`：只读采样器
- `phase54_tools/phase542_analyze.py`：离线分析器

