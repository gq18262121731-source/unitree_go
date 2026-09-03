# Phase 5.4.3-B — `cloud_base` 在线只读复验报告

日期：2026-07-27  
环境：Ubuntu 22.04 VMware、ROS2 Humble、CycloneDDS、Go2 Ethernet DDS  
结论：**Phase 5.4.3-B PASS WITH OBSERVATION；`cloud_base` 可列为后续 3D SLAM 路线评估的输入候选，但 Phase 5.4 HOLD 继续生效，禁止进入 Phase 5.5。**

## 1. 安全边界

本轮仅启动：

- 现有 Unitree 只读传感器桥；
- 只读采集探针；
- RViz2，Fixed Frame 为 `base_link`，仅显示 `/utlidar/cloud_base`。

本轮没有：

- 发布新的 `/tf` 或 `/tf_static`；
- 发布猜测的 `base_link -> utlidar_lidar`；
- 启动 SLAM Toolbox、Cartographer、Nav2 或 Unitree USLAM；
- 发布 `cmd_vel`、LowCmd 或其他控制 topic；
- 调用 `move()`、SportClient 或任何运动控制接口。

运动段完全由现场用户使用遥控器低速完成，Codex 未向机器人发送控制消息。

## 2. 测试覆盖

| 阶段 | 时长 | 操作 |
|---|---:|---|
| 预检 | 5.11 s | 机器人静止，确认 topic、消息类型和 LiDAR 状态 |
| 静止采集 | 30.09 s | 机器人原地静止 |
| 运动采集 | 120.11 s | 用户遥控低速平移、转向、停止 |

运动证据：

| 指标 | 结果 |
|---|---:|
| 10 Hz 降采样里程路径 | 12.348 m |
| 起终点净位移 | 1.814 m |
| XYZ 覆盖范围 | 1.963 / 1.800 / 0.034 m |
| 净航向变化 | -97.05° |
| 净姿态旋转 | 97.00° |
| 运动样本占比 | 47.0% |
| 线速度 P95 / 最大值 | 0.379 / 1.118 m/s |
| 角速度 P95 / 最大值 | 0.518 / 2.032 rad/s |

因此数据同时覆盖了静止、明显平移、约 90° 旋转以及停止状态。

## 3. `cloud_base` 静止结果

| 指标 | 结果 |
|---|---:|
| topic | `/utlidar/cloud_base` |
| frame | `base_link`，463 / 463 |
| 采样数 | 463 |
| 实测频率 | 15.389 Hz |
| 时间戳回拨 / 重复 | 0 / 0 |
| 相邻时间戳中位数 | 64.976 ms |
| 相邻时间戳最大值 | 72.384 ms |
| 每帧点数 最小 / 中位 / 最大 | 1,062 / 1,353 / 1,659 |
| 每帧点数均值 / 标准差 | 1,350.9 / 175.2 |
| raw/base 全量时间戳匹配率 | 100% |
| raw/base 保存点对 | 155 |
| 固定变换残差 P95 | 0.305 μm |
| 最近 odom 时间差 P95 | 3.027 ms |

静止 30 秒内 frame 未变化、时间戳连续、topic 未中断。里程计净位移为 3.8 mm，10 Hz 路径为 1.8 cm，符合本次静止采集的预期。

## 4. `cloud_base` 运动结果

| 指标 | 结果 |
|---|---:|
| frame | `base_link`，1,849 / 1,849 |
| 采样数 | 1,849 |
| 实测频率 | 15.395 Hz |
| 时间戳回拨 / 重复 | 0 / 0 |
| 相邻时间戳中位数 | 64.974 ms |
| 相邻时间戳最大值 | 77.127 ms |
| 每帧点数 最小 / 中位 / 最大 | 1,136 / 1,463 / 1,823 |
| 每帧点数均值 / 标准差 | 1,461.0 / 145.6 |
| raw/base 全量时间戳匹配率 | 100% |
| raw/base 保存点对 | 617 |
| 固定变换残差 P95 | 0.432 μm |
| 最近 odom 时间差 P95 | 3.199 ms |

运动期间没有出现整帧消失、frame 切换或时间戳回拨。raw cloud 与 cloud_base 的时间戳仍为全量一一匹配，已验证固定变换在运动数据中的残差仍处于亚微米量级。

odom 补偿到共同坐标后，相邻抽样点云的最近邻中位数：

- 静止段总体中位数：0.097 m；
- 运动段总体中位数：0.136 m；
- 运动段单对最大中位数：0.261 m。

该指标受 L1 扫描图案、遮挡、场景变化和运动速度共同影响，不能单独作为 deskew 或拖影证明。它没有显示数量级突变，但不能据此宣称已完成 SLAM 级运动畸变标定。

## 5. RViz 在线观察

配置：

```text
Fixed Frame: base_link
PointCloud2: /utlidar/cloud_base
Decay Time: 1 s
```

静止和运动抽样画面中：

- 点云持续显示；
- 没有 Fixed Frame 或 TF 错误导致的整云消失；
- 没有观察到整帧瞬移、坐标轴突变或明显的长距离拖尾；
- 点云随机器人本体坐标更新，停止后仍保持连续输出。

RViz 观察是现场定性证据，不替代后续 SLAM 前的定量 deskew/重定位评估。

## 6. 与 raw cloud 和 odom 的关系

本轮继续确认：

```text
/utlidar/cloud
  frame: utlidar_lidar
  stamp: sensor stamp
          |
          | Unitree 固件固定变换与过滤
          v
/utlidar/cloud_base
  frame: base_link
  stamp: 与 raw 完全相同
```

同时：

```text
/utlidar/robot_odom
  frame: odom
  child_frame: base_link
```

运动采集中，抽样 cloud_base 与最近 robot_odom 的时间差 P95 为 3.199 ms。证据支持 cloud_base 与 odom 在当前固件链路中具有可用的在线时间对应关系。

## 7. LiDAR 健康状态

| 指标 | 静止 30 s | 运动 120 s |
|---|---:|---:|
| LidarState 样本 | 150 | 596 |
| 非零 `error_state` | 0 | 0 |
| `dirty_percentage` | 恒为 1 | 0–1，中位数 1 |
| 固件报告点云频率均值 | 15.410 Hz | 15.405 Hz |
| 固件报告 IMU 频率均值 | 250.809 Hz | 250.686 Hz |
| IMU 非零丢包样本 | 0 | 0 |

运动段出现一次约 0.8 秒的点云包丢失指标非零窗口：

- 4 / 596 个 LidarState 样本；
- 最大报告值为 `0.552486`；
- 同期 `error_state=0`；
- `cloud_base` 最大消息间隔仍只有 77.127 ms，没有出现约 130 ms 的整帧缺口；
- 之后指标恢复为 0。

该短暂事件记录为观察项，不隐藏，也不据此判定链路失败。进入任何长时间 SLAM 实验前，应继续监控其出现频率和持续时间。

## 8. 验收判定

| Gate | 结果 |
|---|---|
| `cloud_base` 约 15 Hz 持续输出 | PASS |
| 全程 `frame_id=base_link` | PASS |
| 静止/运动时间戳无回拨、无重复 | PASS |
| raw/base 时间戳一一匹配 | PASS |
| 与 odom 时间对应 | PASS |
| 运动覆盖包含平移与约 90° 旋转 | PASS |
| LiDAR `error_state=0` | PASS |
| RViz 无整云跳变或 frame 错误 | PASS |
| 点云包丢失指标始终为 0 | OBSERVATION：短暂非零窗口 |
| SLAM 级 deskew 质量 | 本阶段未验证 |

总判定：

```text
Phase 5.4.3-B online readonly validation: PASS WITH OBSERVATION
cloud_base as future 3D SLAM input candidate: RECOMMENDED FOR ROUTE EVALUATION
Phase 5.4 HOLD: ACTIVE
Phase 5.5 SLAM: NOT AUTHORIZED
```

## 9. 后续边界

本轮通过只代表：

> Unitree 固件输出的 `/utlidar/cloud_base` 在本次静止与用户遥控运动数据中具备稳定 frame、连续时间戳、稳定频率、可用点数和可接受的 odom 时间对应，可作为后续 3D SLAM 路线评估的输入候选。

它不代表：

- 官方 `base_link -> utlidar_lidar` 外参来源已经找到；
- 实验性外参可以作为官方 TF 发布；
- 单层 LaserScan 质量 Gate 已经通过；
- Cartographer、FAST-LIO、LIO-SAM、USLAM 或 Nav2 已获准启动；
- SLAM 级运动畸变和闭环质量已经通过。

继续保持：

```yaml
official_calibration: false
publish_tf: false
```

下一阶段应先做 Phase 5.4.4 路线决策与输入契约设计；在明确批准前，不进入 Phase 5.5。

## 10. 证据文件

- `phase543b_online_analysis.json`
- `phase54_tools/phase543b_static_capture.json`
- `phase54_tools/phase543b_static_clouds.npz`
- `phase54_tools/phase543b_motion_capture.json`
- `phase54_tools/phase543b_motion_clouds.npz`
- `phase543b_rviz_static2.png`
- `phase543b_rviz_motion_2.png`
- `phase54_tools/phase543b_capture.py`
- `phase54_tools/phase543b_analyze.py`
- `phase54_tools/phase543b_cloud_base.rviz`
