# Phase 5.4.5：目标演示场景 Point-LIO 离线建图验证

## 最终结论

```text
正式目标场景 rosbag：PASS
传感器/时间数据 Gate：PASS
Point-LIO 初始化：PASS
Point-LIO 运动轨迹：FAIL
Point-LIO 地图质量：FAIL

Phase 5.4.5：FAIL
Phase 5.5：HOLD / NOT ENTERED
Nav2：NOT ENTERED
```

本阶段只进行了真实数据采集和隔离的离线回放验证。没有启动在线
SLAM、Nav2、`cmd_vel`、SportClient、Move、LowCmd 或其他运动控制接口，
也没有发布猜测的 `base_link -> utlidar_lidar` TF。

失败发生在机器人开始实际位移之后：Point-LIO 在约 180 秒之前的静止段
保持稳定，随后轨迹快速发散。因此，当前数据不能形成可交付地图，也不能
作为进入 Phase 5.5 的依据。

## 1. 场景与设备

```text
场景：实验室/比赛演示区域
面积：约 3 m × 3 m
有效活动区域：约 2 m × 2 m
环境：空地、桌子、显示器、玻璃隔断、墙面、地砖

机器人：Go2 X
硬件：V2.0
软件：V1.1.15
连接：Ethernet
Go2 IP：192.168.123.161
Ubuntu VM IP：192.168.123.223
```

目标是验证小尺度局部建图，不追求大范围地图。

## 2. 安全边界

- 人工遥控完成数据采集；
- 未运行在线 Point-LIO；
- 未运行 SLAM Toolbox、Cartographer 或 Nav2；
- 未发布任何控制 topic；
- 未启动任何自动运动；
- 未使用 URDF `radar_joint`；
- 未发布猜测的 LiDAR 外参 TF；
- 离线回放使用独立 ROS Domain 和 localhost-only 通信。

## 3. 正式 rosbag

```text
路径：
/home/go2/go2_validation/bags/phase545_20260728_135403_lab_demo_3x3

开始：2026-07-28 13:54:04
结束：2026-07-28 13:59:04
Duration：299.567648074 s
Size：884.3 MiB
Messages：175455
Storage：sqlite3
```

### Topic 数量

| Topic | 消息数 |
|---|---:|
| `/utlidar/cloud` | 4615 |
| `/utlidar/cloud_base` | 4615 |
| `/utlidar/imu` | 75021 |
| `/utlidar/robot_odom` | 44853 |
| `/odom` | 44853 |
| `/utlidar/lidar_state` | 1498 |
| `/tf` | 0 |
| `/tf_static` | 0 |

`/tf` 和 `/tf_static` 为 0 是预期结果：当时没有 TF 发布者，本阶段没有
为了补齐消息而发布猜测外参。

## 4. 数据质量与时间 Gate

### 频率

| Topic | 平均频率 |
|---|---:|
| `/utlidar/cloud` | 15.406 Hz |
| `/utlidar/imu` | 250.746 Hz |
| `/utlidar/robot_odom` | 约 150 Hz |
| `/odom` | 约 150 Hz |

### 时间与 frame

| 检查 | 结果 |
|---|---:|
| 核心 topic 时间戳回拨 | 0 |
| 核心 topic 重复时间戳 | 0 |
| raw/cloud_base 时间戳精确匹配 | 100% |
| robot_odom/odom 时间戳精确匹配 | 100% |
| IMU 到最近 cloud 时间差 P95 | 4.137 ms |
| `/utlidar/cloud` frame | `utlidar_lidar` |
| `/utlidar/cloud_base` frame | `base_link` |
| `/utlidar/imu` frame | `utlidar_imu` |
| `/utlidar/robot_odom` frame | `odom` |

### 点云与 L1 状态

| 检查 | 结果 |
|---|---:|
| raw points/frame 平均值 | 4120 |
| raw points/frame 中位数 | 4125 |
| cloud_base points/frame 平均值 | 1411 |
| cloud_base points/frame 中位数 | 1411 |
| L1 非零 error_state 样本 | 0 |
| dirty_percentage 中位数 | 1 |
| dirty_percentage P95 | 1 |
| dirty_percentage 最大值 | 3 |
| cloud loss 非零样本 | 8 |
| IMU loss 非零样本 | 0 |

结论：rosbag 可正常播放；传感器、时间和 frame 数据没有出现会直接解释
后续数万米漂移的异常。数据 Gate 判定为 PASS。

## 5. 实际采集轨迹说明

来自 `/utlidar/robot_odom` 的审计结果：

```text
10 Hz 降采样路径长度：18.087 m
净位移：0.220 m
位置跨度：3.010 × 2.518 × 0.043 m
净 yaw：643.82°
累计绝对 yaw：1179.04°
速度 P95：0.354 m/s
最大速度：1.220 m/s
角速度 P95：0.678 rad/s
最大角速度：2.411 rad/s
```

由于现场交互提示到达存在延迟，实际数据约为前 180 秒主要静止、后约
120 秒集中运动，而不是原计划的 60 秒静止加 4 分钟分段路线。最后
30 秒仍有运动，没有形成完整的结束静止段。此次运动与旋转也高于建议的
慢速目标，这些是离线建图失败的重要观察项，但不能单独断言为唯一根因。

## 6. Point-LIO 离线环境

```text
VM workspace：
/home/go2/phase545_pointlio_ws

ROS：
Ubuntu 22.04 / ROS 2 Humble

隔离设置：
ROS_DOMAIN_ID=145
ROS_LOCALHOST_ONLY=1
RMW=rmw_cyclonedds_cpp

输入：
/utlidar/cloud
/utlidar/imu
```

采用 ROS 2 Point-LIO 移植版本，并保留 Unitree L1 配置中的关键数值：

```yaml
lidar_type: 5
timestamp_unit: 0
imu_time_inte: 0.004
acc_norm: 9.81
extrinsic_T: [0.007698, 0.014655, -0.00667]
extrinsic_R: identity
```

只将输入 topic 改为实机的 `/utlidar/cloud` 和 `/utlidar/imu`，并使 IMU
订阅 QoS 与 rosbag 的 best-effort 传感器数据兼容。没有向真实 ROS
网络发布 SLAM 结果。

## 7. 初始化结果

```text
Point-LIO node start：PASS
rosbag play：PASS
IMU initialization：PASS（1%、6%、27%、65%、100%）
离线运行完成：PASS
PCD 保存：PASS
PCD 点数：2,701,277
```

初始化、回放、消息连接和地图文件写出均成功。失败不属于“节点没有启动”
或“IMU 完全没有接入”。

## 8. 轨迹结果

```text
Point-LIO 输出时长：298.071 s
输出时间戳回拨：0
前约 180 秒最大轨迹半径：约 0.085 m

首次超过 1 m：190.962 s
首次超过 5 m：205.753 s
首次超过 10 m：206.463 s
首次超过 100 m：210.921 s
首次超过 1 km：222.245 s
首次超过 10 km：258.923 s

最终净位移：29,461.38 m
10 Hz 轨迹长度：30,203.57 m
```

真实场景仅约 3 m × 3 m，因此该轨迹是灾难性发散。发散开始时间与机器
人实际位置开始变化的时间相符：静止初始化阶段稳定，进入运动阶段后状态
估计失稳。

轨迹 Gate：FAIL。

## 9. 地图质量

输出 PCD：

```text
E:\笨笨狗\phase545_pointlio_result\scans.pcd
大小：约 83 MiB
点数：2,701,277
有限坐标点：100%
```

范围：

```text
x：-12824 m 至 5.43 m
y：-241 m 至 19036 m
z：-19616 m 至 3.316 m
```

约 63.13% 的点位于起点 5 m 内，69.07% 位于 10 m 内；约 30.93% 的
点超出 10 m。在 3 m × 3 m 场景中，这说明地图被发散轨迹严重拉伸。
局部裁剪能够看到部分合理环境结构，但不能把裁剪结果视为有效地图。

地图 Gate：FAIL。

## 10. 失败分类

目前有证据支持：

- 不是 rosbag 损坏；
- 不是核心输入时间戳回拨；
- 不是初始化完全失败；
- 不是外部猜测 TF 造成，因为本阶段没有发布该 TF；
- 不是明显的 L1 error_state 或严重镜头污染；
- 失稳与实际运动开始强相关。

当前应将问题归类为：

> Point-LIO 配置、ROS 2 移植运行特性、场景特征与实际运动强度之间的
> 交互导致运动阶段估计失稳。

现有证据不足以进一步断言唯一根因。玻璃、小场景、集中旋转、最高速度和
角速度偏高都可能放大问题，但不能在没有对照实验时单独定责。

## 11. 后续建议（本阶段不执行）

首选判别实验不是立即进入在线 SLAM，也不是发布猜测 TF，而是：

1. 使用同一 rosbag，在 Ubuntu 20.04/ROS 1 Noetic 的 Unitree 官方
   `point_lio_unilidar` 实现中离线回放；
2. 若官方实现稳定，则重点审计 ROS 2 移植和参数/QoS/消息适配；
3. 若官方实现也发散，再采集严格受控数据：60 秒静止、0.05～0.1 m/s
   平移、角速度低于约 0.2 rad/s、转角处停顿、减少连续原地旋转；
4. 保持不使用 URDF `radar_joint`，不发布猜测 TF。

## 12. Phase 5.5 判定

```text
Phase 5.4.5：FAIL
Phase 5.5 在线 SLAM：HOLD / NOT ENTERED
Nav2：NOT ENTERED
运动控制：NOT TOUCHED
```

虽然正式 rosbag 和输入数据 Gate 通过，Point-LIO 也完成初始化，但轨迹
和地图验收均失败。因此不得进入 Phase 5.5。本阶段在此停止，等待下一步
确认。

## 13. 证据文件

```text
E:\笨笨狗\phase545_formal_audit.json
E:\笨笨狗\phase545_pointlio_analysis.json
E:\笨笨狗\phase545_pointlio_result\run_status.txt
E:\笨笨狗\phase545_pointlio_result\pointlio.log
E:\笨笨狗\phase545_pointlio_result\bag_play.log
E:\笨笨狗\phase545_pointlio_result\probe.log
E:\笨笨狗\phase545_pointlio_result\pointlio_map_topdown.png
E:\笨笨狗\phase545_pointlio_result\pointlio_map_views.png
E:\笨笨狗\phase545_pointlio_result\pointlio_map_local_10m.png
E:\笨笨狗\phase545_pointlio_result\scans.pcd
```
