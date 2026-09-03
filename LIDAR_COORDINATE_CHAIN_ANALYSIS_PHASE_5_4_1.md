# Phase 5.4.1 — Unitree LiDAR 坐标链路只读分析报告

日期：2026-07-26  
平台：Ubuntu 22.04 VMware / ROS 2 Humble / CycloneDDS  
机器人：Unitree Go2，Ethernet `192.168.123.161`  
结论：**分析完成；Phase 5.4 继续 HOLD；不得进入 SLAM/Nav2。**

## 1. 安全边界

本轮仅订阅和离线分析以下话题：

- `/utlidar/cloud`
- `/utlidar/cloud_base`
- `/utlidar/cloud_deskewed`
- `/utlidar/robot_pose`
- `/utlidar/robot_odom`
- Phase 5.3 只读桥接输出 `/odom`

本轮没有发布猜测 TF，没有启动 SLAM/Nav2，没有发布控制话题，也没有调用 `move()`、`SportClient`、`LowCmd` 或 `cmd_vel`。

## 2. 采样概况

连续采样时长为 `20.046 s`，各话题均未发现时间回拨：

| Topic | Frame | 样本数 | 估算频率 | Backward jump |
|---|---|---:|---:|---:|
| `/utlidar/cloud` | `utlidar_lidar` | 307 | 15.315 Hz | 0 |
| `/utlidar/cloud_base` | `base_link` | 307 | 15.315 Hz | 0 |
| `/utlidar/cloud_deskewed` | `odom` | 305 | 15.215 Hz | 0 |
| `/utlidar/robot_pose` | `odom` | 375 | 18.707 Hz | 0 |
| `/utlidar/robot_odom` | `odom` → `base_link` | 3001 | 149.703 Hz | 0 |
| `/odom` | `odom` → `base_link` | 2999 | 149.603 Hz | 0 |

采样期间机器人基本静止：位置轴向跨度约为 `0.49 / 0.34 / 0.94 mm`，首尾位移约 `0.15 mm`。因此，本报告能高置信验证静态几何一致性，但不能替代运动状态下的 deskew/外参验证。

## 3. Topic 坐标含义

### 3.1 `/utlidar/cloud`

- `frame_id = utlidar_lidar`
- 每帧约 `3750–4152` 点
- 字段为 `x/y/z/intensity/ring/time`
- 是 LiDAR 原生坐标系中的原始扫描候选。

### 3.2 `/utlidar/cloud_base`

- `frame_id = base_link`
- 每帧约 `811–1569` 点
- 字段与 `/utlidar/cloud` 完全一致
- 与对应 raw cloud 的时间戳逐帧完全相同：`307/307`
- 所有 base 点都可通过 `(ring, time bits, intensity bits)` 在同时间戳 raw cloud 中找到唯一对应点，匹配率 `100%`
- 平均保留 raw 点的 `28.998%`，范围 `21.166%–38.358%`

因此可以确认：`cloud_base` 不是独立扫描，而是 raw cloud 的**过滤后子集**，并已经应用固定刚体变换到 `base_link`。

### 3.3 `/utlidar/cloud_deskewed`

- `frame_id = odom`
- 每帧约 `10811–11569` 点，显著多于单帧 `cloud_base`
- 只有 `x/y/z/intensity`，不再保留 `ring/time`
- 与 raw/base 没有逐帧相同时间戳
- 相对最近 `robot_odom` 的时间差中位数为 `-0.066 ms`

将最近 `cloud_base` 仅按最近 odometry pose 变换至 `odom` 后，与 deskewed cloud 比较，去掉一个采样边界帧后：

- 平均最近邻误差约 `0.151 m`
- `1 mm` 内匹配约 `0.082%`
- `5 mm` 内匹配约 `1.83%`
- `20 mm` 内匹配约 `18.18%`

所以 `cloud_deskewed` 不是“当前单帧 `cloud_base` 乘一次 `odom→base_link`”的简单结果。结合点数明显增多，可以合理推断它是固件内部经过 deskew、过滤及/或短时累积的 odom 坐标产品；公开接口不足以确认具体算法。

### 3.4 `/utlidar/robot_pose`

375 个 pose 样本均能在 `/utlidar/robot_odom` 中找到：

- 时间戳误差：`0`
- 位置误差：`0`
- 姿态误差：数值精度范围内为 `0`

它是 `robot_odom` pose 的约 `18.7 Hz` 降采样视图，不是独立定位源。

### 3.5 `/utlidar/robot_odom` 与 `/odom`

Phase 5.3 `/odom` 的 2999 个样本均与 `/utlidar/robot_odom` 精确匹配：

- exact stamp：`2999/2999`
- position error：`0`
- orientation error：数值精度范围内为 `0`

这再次确认现有 `odom → base_link` 数据链没有重打时间戳或修改位姿。

## 4. `cloud → cloud_base` 固定变换

在跨越约 20 秒的 30 对扫描中，共得到 `34,593` 个精确点对应。最小二乘拟合的变换为：

```text
source: utlidar_lidar
target: base_link

translation [m]:
  x =  0.2821600275
  y =  0.0000000170
  z = -0.0000000349

rotation matrix:
   0.526113905  -0.810135815   0.258619645
  -0.838668172  -0.544642725   0.000001579
   0.140854029  -0.216896895  -0.965979233

quaternion [x, y, z, w]:
  [-0.8713116353, 0.4730810194, -0.1146184836, 0.0622333233]

RPY [rad]:
  [-2.920720112, -0.141323991, -1.010529934]
```

拟合质量：

- aggregate mean residual：`0.0000000768 m`
- P95 residual：`0.0000002110 m`
- max residual：`0.0000009849 m`
- 帧间 translation 最大范围：`18.5 nm`
- 帧间 rotation 最大偏差：`4.71e-8 rad`

这足以证明当前固件确实对 `cloud_base` 使用一个稳定的固定刚体变换。但该数值是从输出点云反求的**实测外参候选**，不是已获官方来源确认的标定参数，因此本阶段不得把它发布为生产 TF。

## 5. 与 Go2 URDF `radar_joint` 的对比

反求变换与当前 Go2 URDF 中 `base_link → radar` 候选的差异：

- translation 差异范数：`0.047389 m`
- rotation 差异：`2.146760 rad`（约 `123°`）

所以 `radar_joint` 不能重命名或直接替代 `base_link → utlidar_lidar`。这与 Phase 5.4 的 HOLD 判断一致。

## 6. 官方来源审计

公开的 Unitree 资料目前只能确认：

1. `unitree_ros2` 的 LiDAR 示例公开原始 `/utlidar/cloud`/`utlidar_lidar` 使用方式；
2. `unilidar_sdk` ROS 2 示例发布原始 LiDAR cloud 与 IMU；
3. 公共仓库没有给出本机 `/utlidar/cloud_base`、`/utlidar/cloud_deskewed` 的生成算法或 Go2 L1 的 `base_link → utlidar_lidar` 标定来源；
4. 当前 `unitree_sdk2` 的 `LidarState_` IDL 包含 `dirty_percentage`，而当前 `unitree_ros2` 公共 `LidarState.msg` 缺少该字段，存在接口版本漂移风险。

在隔离的只读消息工作区补齐该字段后，设备报告：

```text
software_version: 1.0.0.38
error_state: 0
dirty_percentage: 5
cloud_frequency: 15.396606 Hz
cloud_packet_loss_rate: 0
imu_frequency: 248.426743 Hz
imu_packet_loss_rate: 0
```

`firmware_version` 与 `sdk_version` 字段为空，因此只能将 `software_version 1.0.0.38` 作为本次结果的版本锚点。

官方参考：

- [Unitree SDK2 `LidarState_` IDL](https://github.com/unitreerobotics/unitree_sdk2/blob/main/include/unitree/idl/go2/LidarState_.hpp)
- [Unitree ROS2 `LidarState.msg`](https://github.com/unitreerobotics/unitree_ros2/blob/master/cyclonedds_ws/src/unitree/unitree_go/msg/LidarState.msg)
- [Unitree UniLiDAR ROS2 示例](https://github.com/unitreerobotics/unilidar_sdk/tree/main/unitree_lidar_ros2)
- [Unitree ROS2 LiDAR/RViz 说明](https://github.com/unitreerobotics/unitree_ros2#rviz)

## 7. SLAM 可用性判断

| 输入 | 判断 | 原因 |
|---|---|---|
| `/utlidar/cloud` | 当前不可直接进入 SLAM | 仍缺官方可信的 `base_link → utlidar_lidar` TF |
| `/utlidar/cloud_base` | 技术上最有希望，但暂不放行 | 已在 `base_link`、时间戳完好、固定变换实测高度一致；但只保留约 29% 点，公开来源/过滤策略/运动 deskew 行为未知 |
| `/utlidar/cloud_deskewed` | 不建议作为原始 SLAM 传感器输入 | 已在 `odom`、是固件处理/累积产品，会把板载里程计引入 SLAM 数据链并造成潜在循环依赖 |
| `/utlidar/robot_pose` | 不作为独立定位证据 | 只是 `robot_odom` 的降采样 |

`cloud_base` 可以继续用于 RViz、离线数据质量和运动工况验证，但在外参来源、过滤定义及运动状态一致性得到确认前，不批准作为 Phase 5.5 的正式 SLAM 输入。

## 8. 最终 Gate

```text
[x] 每个 topic 的 frame、频率、字段与时间戳关系已枚举
[x] cloud → cloud_base 固定变换已由点对应实测验证
[x] robot_pose / robot_odom / bridge odom 关系已验证
[x] cloud_deskewed 不是当前单帧 base cloud 的简单位姿变换
[x] 官方公开来源已审计
[x] 未发布猜测 TF
[x] 未启动 SLAM/Nav2
[x] 未触碰运动控制

[ ] 官方/设备配置给出可追溯的 base_link → utlidar_lidar 外参
[ ] 运动工况下 cloud_base 的 deskew、过滤和坐标一致性通过验证
[ ] Phase 5.4 TF 连通性验收
```

**Gate 结论：Phase 5.4.1 分析完成；Phase 5.4 继续 HOLD；Phase 5.5 禁止进入。**

建议下一步仍是只读验证：

1. 从 Go2/L1 设备配置、出厂标定或匹配本机硬件版本的官方描述包获取外参来源；
2. 在人工牵引/安全静态姿态变化等不发布控制命令的条件下，验证 `cloud_base` 的运动 deskew 与过滤行为；
3. 只有外参来源可追溯，或 `cloud_base` 的接口契约得到官方确认后，再决定 Phase 5.4 的解除条件。

## 9. 原始证据

- `phase541_capture.json`：20 秒 topic 元数据、时间戳与 odometry 捕获
- `phase541_clouds.npz`：抽样点云
- `phase541_analysis.json`：全部统计、逐帧刚体拟合与误差
- `phase541_lidar_state_corrected.txt`：匹配当前 SDK2 IDL 后的只读 LidarState
- `phase54_tools/phase541_capture.py`：采样脚本
- `phase54_tools/phase541_analyze.py`：离线分析脚本

