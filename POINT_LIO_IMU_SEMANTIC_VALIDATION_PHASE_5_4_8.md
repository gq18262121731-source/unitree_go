# Phase 5.4.8：Go2 X L1 IMU 与 Point-LIO 输入语义验证

日期：2026-07-29  
状态：**完成，FAIL / HOLD**  
Phase 5.5：**不得进入**

## 1. 结论

本阶段已找到一个能够直接解释 Point-LIO “静止稳定、运动后快速发散”的高置信度根因：

> Go2 固件发布的 `/utlidar/imu.linear_acceleration` 在姿态变化时不保持静止比力向量约等于重力加速度的物理性质。它表现为 Z 分量固定在约 9.81 m/s²，而 X 分量随姿态按近似 `g·tan(pitch)` 增长。因此，该字段不是 Point-LIO 所假设的原始 IMU 比力，或至少经过了未文档化的姿态/坡度变换。

当前数据不得直接继续用于 Point-LIO：

```text
/utlidar/imu.linear_acceleration
        ↓
Point-LIO 原始比力积分
        ↓
FAIL：输入语义不匹配
```

本结论不依赖猜测 TF，也不依赖 SLAM 调参。

## 2. 安全边界

本阶段遵守：

- 未运行 Point-LIO、SLAM Toolbox、Cartographer 或 Nav2；
- 未发布新的 TF；
- 未修改 `base_link → utlidar_lidar`；
- 未调用 `cmd_vel`、`SportClient`、`Move()`、`LowCmd`；
- 机器人动作全部由用户使用原厂遥控器手动完成；
- Codex 仅启动只读录制、离线分析和源码审计。

## 3. 有效数据集

录制话题：

```text
/utlidar/imu
/utlidar/cloud
/utlidar/robot_odom
```

| 片段 | 时长 | IMU | 点云 | Odom | 时间戳回拨 |
|---|---:|---:|---:|---:|---:|
| 水平静止 | 29.592 s | 7,376 | 457 | 4,414 | 0 |
| 低头保持 | 19.605 s | 4,870 | 302 | 2,962 | 0 |
| 抬头保持 | 19.620 s | 4,881 | 302 | 2,959 | 0 |
| 左侧降低保持 | 19.601 s | 4,883 | 303 | 2,961 | 0 |
| 右侧降低保持 | 19.609 s | 4,879 | 303 | 2,961 | 0 |
| 逆时针人工旋转 | 19.604 s | 4,869 | 302 | 2,938 | 0 |
| 顺时针人工旋转 | 19.569 s | 4,865 | 301 | 2,933 | 0 |
| 合计 | — | 36,623 | 2,270 | 22,128 | 0 |

用户指出第一次旋转方向标签不完整后，逆时针片段已重新录制。旧片段
`phase548_20260729_133410_yaw_ccw_manual` 保留但未纳入分析。

有效数据包：

```text
phase548_capture/phase548_selected_20260729.tar
SHA-256:
750FF6889FEBCA43048FEC4BAEF596B44F7AB3CABC2651C52A5CA60FF967FEB4
```

分析使用各保持片段中间 80%，以排除进入和退出姿态的遥控过渡。

## 4. 动作标签交叉验证

`/utlidar/robot_odom` 的姿态变化与用户动作一致：

| 标签 | Odom roll | Odom pitch |
|---|---:|---:|
| 水平静止 | -1.96° | +0.15° |
| 低头保持 | +0.17° | +16.37° |
| 抬头保持 | -5.13° | -16.84° |
| 左侧降低保持 | -36.61° | +0.41° |
| 右侧降低保持 | +40.09° | +1.92° |

因此，俯仰与横滚结果不是动作误标或用户姿态混淆造成的。

限制：`robot_odom` 是固件融合输出，不作为独立标定真值；它在这里仅用于确认动作类别和方向。

## 5. 静止比力不变性检查

机器人在五个片段中均保持静止。原始加速度计在静止时应满足：

```text
||linear_acceleration|| ≈ g
```

方向会随传感器姿态改变，但模长不应随倾角持续增大。

实测中间 80% 均值：

| 姿态 | ax | ay | az | 向量模长 |
|---|---:|---:|---:|---:|
| 水平 | -0.2169 | +0.0004 | +9.8074 | 9.8098 |
| 低头 | -3.6110 | +0.0125 | +9.8058 | 10.4495 |
| 抬头 | +3.3505 | +0.0089 | +9.8112 | 10.3675 |
| 左侧降低 | +8.1606 | -0.0098 | +9.7848 | 12.7412 |
| 右侧降低 | -12.0637 | +0.0008 | +9.8235 | 15.5575 |

单位：m/s²。

关键特征：

```text
五个静止姿态的 az 均值：       9.806533 m/s²
五个 az 均值之间的标准差：     0.012536 m/s²
静止向量模长范围：              9.8098 ～ 15.5575 m/s²
```

用消息自身四元数解算的 IMU pitch 进行拟合：

```text
ax = 9.973890 · tan(IMU pitch) - 0.096114
R² = 0.999590
RMSE = 0.138049 m/s²
```

这与“保持 `az≈g`，再用坡度正切生成水平分量”的行为高度吻合。更谨慎地说，它证明
`linear_acceleration` 已经过某种未文档化的姿态相关变换；当前证据不足以给该变换指定官方名称。

### 官方 L1 示例对照

官方 Unitree L1 示例包前 5 秒：

```text
样本数：              1,249
加速度模长均值：       9.697362 m/s²
P05 / P95：           9.621955 / 9.783301 m/s²
```

其静止比力模长保持在合理的重力尺度。官方示例也能被官方 Point-LIO 正常处理。

## 6. 角速度轴向

人工旋转积分结果：

| 动作 | IMU gyro 积分 xyz | Odom angular 积分 xyz |
|---|---|---|
| 逆时针 | `[+2.428, -4.557, -22.540]` rad | `[-0.669, -0.135, +22.805]` rad |
| 顺时针 | `[-2.071, +3.184, +17.266]` rad | `[+0.409, +0.238, -17.574]` rad |

IMU Z 与 `base_link` yaw 近似反号，且双向响应对称。水平静止时 IMU 四元数解算 roll
约 178°，所以该反号与传感器相对机器人基座的稳定安装旋转一致，不是随机数据损坏。

Point-LIO 需要的是 L1 内部 LiDAR 与 IMU 的关系，而不是 IMU 与 `base_link` 的关系。
因此本阶段不根据 `robot_odom` 生成或修改外参。

## 7. PointCloud2、ring 与逐点时间

字段布局：

| 字段 | datatype | offset |
|---|---:|---:|
| x | float32 | 0 |
| y | float32 | 4 |
| z | float32 | 8 |
| intensity | float32 | 16 |
| ring | uint16 | 20 |
| time | float32 | 24 |

```text
point_step：                    32 bytes
点云频率：                     15.399 ～ 15.427 Hz
单帧 point-time 跨度中位数：   62.184 ～ 62.306 ms
ring：                         所有点恒为 1
点云 header 时间回拨：          0
```

本阶段 2,270 帧中，327 帧存在局部逐点时间逆序，占 14.405%；最大逆序约 1.72 ms。
官方示例为约 9.897 Hz、97.262 ms、ring 0～17，且没有逐点时间逆序。

这些仍是明确的输入差异，但当前 Point-LIO 源码路径显示：

- `unilidar_handler` 只读取 `x/y/z/intensity/time`，不读取 `ring`；
- `time` 按 `timestamp_unit=0` 作为秒使用；
- 进入时间压缩和估计前，点会按 `time` 排序；
- `SCAN_RATE` 仅用于 Velodyne/Hesai 等缺少逐点时间时的补时路径，Unitree L1 handler 不使用；
- 因此 `ring=1`、15 Hz 和小幅时间逆序当前列为次要观察项，不能解释已观测到的静止加速度物理不一致。

排序可能改变固件原始点组织，仍应在得到正确原始 IMU 后继续做 A/B，但不是当前优先修复项。

## 8. Point-LIO 实际消费路径

当前 `unilidar_l1.yaml`：

```yaml
timestamp_unit: 0
acc_norm: 9.81
imu_time_inte: 0.004
extrinsic_R: identity
extrinsic_T: [0.007698, 0.014655, -0.00667]
```

估计器直接执行：

```cpp
input_in.acc << imu.linear_acceleration.x,
                imu.linear_acceleration.y,
                imu.linear_acceleration.z;
input_in.acc = input_in.acc * G_m_s2 / acc_norm;
```

在 `acc_norm=9.81` 时，Go2 消息基本原值进入传播模型。Point-LIO 不使用
`sensor_msgs/Imu.orientation` 来恢复原始比力，因此无法自动识别或撤销固件的姿态相关变换。

这会把静止倾斜时多出的 X 分量当作真实线加速度积分。例如右侧降低保持片段中，估计器会看到
约 `-12.06 m/s²` 的持续 X 加速度，而机器人实际上保持静止。

### 外参方向

源码实际计算：

```cpp
point_imu = offset_R_L_I * point_lidar + offset_T_L_I;
```

所以 `extrinsic_R/T` 的消费语义是 **LiDAR 点转换到 IMU 坐标系**。YAML 中
“transform from imu to lidar”的注释与该实现命名/计算方向不一致，应以源码计算为准。

官方示例在官方配置下可以运行，本阶段不修改这组数值，也不把 `base_link` 外参混入其中。

## 9. Gate 判断

| 检查项 | 结果 |
|---|---|
| 有标签的 IMU 受控采集 | PASS |
| 时间戳连续、无回拨 | PASS |
| 动作标签由 odom 交叉确认 | PASS |
| 角速度双向响应 | PASS WITH FRAME ROTATION |
| 点云字段和时间单位 | PASS |
| ring / 局部时间顺序 | OBSERVATION |
| 静止比力物理一致性 | **FAIL** |
| `/utlidar/imu` 直接适配 Point-LIO | **FAIL** |
| Phase 5.5 准入 | **HOLD** |

## 10. 下一步（本阶段不执行）

优先顺序：

1. 使用 Unitree `unilidar_sdk` 从当前 L1 硬件直接获取官方定义的原始
   `/unilidar/imu`，或找到有官方语义说明的原始 DDS/UDP IMU 数据源；
2. 在同一姿态、同一时间窗中 A/B 对比：
   - 固件 `/utlidar/imu`；
   - L1 SDK 原始 `/unilidar/imu`；
3. 对候选原始 IMU 重复本报告的五姿态静止比力不变性 Gate；
4. 只有模长保持约 `g`、轴向和时间语义确认后，才使用同一 Phase 5.4.5 rosbag
   做离线 Point-LIO 对照；
5. 正确 IMU 输入确认后，再单独评估 ring/time 排序的影响。

禁止用以下方式“修复”：

- 按每帧把加速度强制归一化到 9.81；
- 根据姿态手工减去 `g·tan(pitch)`；
- 盲目交换轴或翻转符号；
- 修改 `acc_norm` 掩盖姿态相关误差；
- 通过修改 TF、外参或 SLAM 参数绕过输入语义问题。

这些操作会丢失真实动态加速度，无法构成可维护的 LIO 输入链路。

## 11. 证据文件

- `phase548_capture/phase548_selected_20260729.tar`
- `phase548_capture/selected/`
- `phase548_analysis.json`
- `phase548_analysis_stdout.txt`
- `phase548_official_comparison.json`
- `phase548_tools/phase548_analyze_segments.py`
- `phase548_tools/phase548_preflight.sh`
- `phase548_tools/phase548_record_segment.sh`
- `phase548_tools/PHASE548_CAPTURE_PROTOCOL.md`
- `phase545_sources/point_lio_ros2_src/`

最终状态：

```text
Phase 5.4.8：完成
根因定位：高置信度指向 /utlidar/imu.linear_acceleration 语义不适配
Phase 5.4：HOLD
Phase 5.5：NOT ENTERED
```
