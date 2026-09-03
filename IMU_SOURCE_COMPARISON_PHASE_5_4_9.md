# Phase 5.4.9：L1 原始 IMU 源对照验证

日期：2026-07-29  
状态：**完成；未找到可直接用于 Point-LIO 的合格 L1 原始 IMU 源**  
Phase 5.5：**HOLD，不得进入**

## 1. 最终结论

本阶段枚举并验证了三个候选来源：

| 候选来源 | 可用性 | 物理语义 | Point-LIO 适用性 |
|---|---|---|---|
| `/utlidar/imu` | 在线可用 | **FAIL** | **FAIL** |
| `/unilidar/imu` | 当前不存在 | 未实测 | 不可判断 |
| `rt/lowstate.imu_state` | 在线可用 | **PASS WITH CALIBRATION OBSERVATION** | **不能直接替代 L1 IMU** |

关键发现：

1. 同步受控实验再次确认 `/utlidar/imu.linear_acceleration` 不是
   Point-LIO 所假设的原始静止比力：
   - 机器人俯倾时，Z 分量仍固定在约 `9.806 m/s²`；
   - 向量模长从 `10.393` 变为 `9.820 m/s²`，变化 `-5.50%`。
2. `rt/lowstate` 中的 Go2 机身 IMU 表现出正确的重力投影特征：
   - 水平到约 `18.7°` 俯倾时，X/Z 分量发生互相转移；
   - 向量模长仅变化 `+0.15%`。
3. 机身 IMU 不是 L1 内置 IMU，而且缺少 L1 点云所需的可追溯传感器时间戳和
   LiDAR→机身 IMU 外参，因此不能直接接入 Point-LIO。
4. Unitree 官方 `/unilidar/imu` 驱动需要直接连接 L1 USB 串口；当前 VM 没有
   `/dev/ttyUSB*` 或 `/dev/ttyACM*`，所以无法进行真正的 L1 原始 IMU A/B。

因此：

```text
L1 cloud                    可用
/utlidar/imu                语义不合格
/unilidar/imu               当前硬件路径不可达
Go2 body IMU                物理语义较合理，但不是即插即用的 L1 IMU

Point-LIO 输入闭环          未满足
Phase 5.5                   HOLD
```

## 2. 安全边界

本阶段遵守：

- 未运行 Point-LIO、SLAM、Nav2；
- 未发布或修改 TF；
- 未调用 `cmd_vel`、`SportClient`、`Move()` 或 `LowCmd`；
- 未向机器人或 L1 发送工作模式、LED、网络或标定命令；
- 机器人姿态仅由用户使用原厂遥控器手动保持；
- Codex 只执行只读 DDS subscriber、ROS bag 录制和离线分析；
- `phase549_lowstate_imu_capture` 的 `publisher_count=0`。

## 3. 数据源审计

### 3.1 `/utlidar/imu`

当前 Go2 固件在线发布：

```text
topic:       /utlidar/imu
frame_id:    utlidar_imu
frequency:   约 250 Hz
```

Phase 5.4.8 已证明其静止倾斜比力不变性 FAIL。本阶段又用与机身 IMU 同步录制的
新数据独立复现该结果。

### 3.2 `/unilidar/imu`

当前 ROS 2 graph 中不存在：

```text
/unilidar/imu
```

Unitree 官方 `unilidar_sdk` ROS 2 驱动默认：

```text
port:       /dev/ttyUSB0
imu_topic:  unilidar/imu
```

相关源码：

```text
phase547_sources/unilidar_sdk_extract/
  unitreerobotics-unilidar_sdk-1bd7d95/
  unitree_lidar_ros2/src/unitree_lidar_ros2/include/unitree_lidar_ros2.h
```

VM 中没有 `/dev/ttyUSB*` 或 `/dev/ttyACM*`。未在缺少直连硬件和官方操作依据时
尝试 UDP 工作模式命令，避免改变 L1 状态。

### 3.3 `rt/lowstate.imu_state`

这是 Go2 机身状态中的 IMU，不是 L1 内置 IMU。消息包含：

```text
quaternion[4]
gyroscope[3]
accelerometer[3]
rpy[3]
temperature
```

`LowState` 只有 `tick`，没有 ROS `Header` 或明确的 L1 传感器时间戳。录制工具同时
保存了本机 callback 的 system/steady 时间，但接收时间不能替代原始采样时间。

消息定义：

```text
go2_dev/unitree_ros2/cyclonedds_ws/src/unitree/unitree_go/msg/
  LowState.msg
  IMUState.msg
```

## 4. 同步实验

只执行了判别力最高的两个姿态：

1. 水平静止；
2. 前端降低、后端升高，保持约 20 秒。

水平和俯倾已经足以验证“静止时向量方向改变但模长保持约常量”的必要条件，因此
没有要求用户重复其余四个姿态。

### 4.1 数据量

| 片段 | LowState 样本 | LowState Hz | L1 IMU 样本 | L1 IMU Hz | L1 cloud | Odom |
|---|---:|---:|---:|---:|---:|---:|
| 水平静止 | 13,695 | 456.493 | 8,128 | 250.173 | 500 | 4,874 |
| 前低后高 | 9,584 | 479.224 | 5,676 | 250.396 | 348 | 3,407 |

两路 LowState callback 时间回拨均为 `0`；两路 L1 IMU header 时间回拨均为 `0`。

### 4.2 加速度中间 80% 均值

单位：`m/s²`。

| 数据源 | 姿态 | ax | ay | az | 模长 |
|---|---|---:|---:|---:|---:|
| `rt/lowstate` | 水平 | +0.0300 | -0.3529 | +9.4769 | 9.4837 |
| `rt/lowstate` | 前低后高 | -3.0493 | +0.0430 | +8.9949 | 9.4980 |
| `/utlidar/imu` | 水平 | +3.4368 | -0.0014 | +9.8070 | 10.3926 |
| `/utlidar/imu` | 前低后高 | -0.5371 | +0.0016 | +9.8050 | 9.8205 |

### 4.3 物理一致性

机身 IMU：

```text
模长变化：                 +0.150%
由 |ax| / ||a|| 推得俯角： 18.73°
az：                       9.477 → 8.995 m/s²
```

这符合机器人静止倾斜时，重力在传感器 X/Z 轴之间投影的基本性质。

需要保留的观察项：

```text
水平模长相对标准重力偏差：约 -3.29%
俯倾模长相对标准重力偏差：约 -3.15%
```

所以本阶段只判定其“原始比力形态”通过，不把它声明为已完成绝对尺度标定。

`/utlidar/imu`：

```text
模长变化：    -5.505%
az 变化：     -0.00199 m/s²
```

机器人发生约 18.7° 俯倾时，Z 分量几乎完全不变。这与 Phase 5.4.8 的
“`az≈g` 固定、水平分量随姿态改变”现象一致，独立复现了其非原始比力语义。

## 5. 为什么不能直接改用 LowState IMU

LowState IMU 通过了初步物理 Gate，但还缺少 LIO 必需条件：

1. **传感器不同**
   - 它位于 Go2 机身，不是 L1 内部 IMU。
2. **时间基准不同**
   - LowState 没有与 L1 逐点时间同源、可追溯的传感器时间戳；
   - callback 接收时间不能作为高精度 LIO 采样时间。
3. **外参缺失**
   - Point-LIO 官方 L1 配置描述的是 L1 LiDAR 与 L1 内部 IMU 的关系；
   - 当前没有可信的 L1 LiDAR→Go2 机身 IMU 六自由度外参。
4. **绝对尺度仍有观察项**
   - 静止模长约 `9.49 m/s²`，比标准重力低约 `3.2%`。

理论上可以建立“L1 点云 + 机身 IMU”的新 LIO 配置，但这需要独立完成：

```text
LiDAR↔body IMU 空间标定
+ 硬件级/传感器级时间同步
+ 加速度尺度、偏置和噪声标定
+ 同一 rosbag 离线验证
```

这不是简单改一个 topic 名称或翻转坐标轴，当前阶段不执行。

## 6. Gate 判断

| 检查项 | 结果 |
|---|---|
| IMU 相关 topic/DDS 源枚举 | PASS |
| `/utlidar/imu` 在线读取 | PASS |
| `/utlidar/imu` 原始比力语义 | **FAIL** |
| `/unilidar/imu` 在线读取 | **BLOCKED：无 L1 USB 直连** |
| LowState 机身 IMU 静止比力形态 | PASS WITH OBSERVATION |
| LowState 机身 IMU 直接用于 Point-LIO | **FAIL：时间/外参/传感器不匹配** |
| 找到合格 L1 原始 IMU 输入 | **FAIL** |
| Phase 5.5 准入 | **HOLD** |

本阶段任务本身已完成，但结果是“没有合格源”，不是 Phase 5.5 的通过。

## 7. 可追溯证据

### 同步 ROS bags

```text
phase549_sync_bags_20260729.tar
SHA-256:
24069AD4E6CDB2A7734EA30D3EFB5DBC8982B25FC4FA4BC3A78CD47694511E5F
```

### LowState CSV、manifest 和统计

```text
phase549_evidence_20260729.tar.gz
SHA-256:
D51C9081F5B6D565B95D78952AB97A9E62BF6F2B4E912D58847D216C5212C763
```

### L1 同步分析

```text
phase549_sync_l1_analysis.json
SHA-256:
221A0D01E16C5D7A4C3D41531D6BEA94F04178099C0E709B82EA256FE675B08E
```

分析和录制工具：

```text
phase549_tools/phase549_lowstate_imu_capture.cpp
phase549_tools/phase549_lowstate_stats.py
phase549_tools/phase549_record_comparison_segment.sh
phase549_tools/phase549_analyze_sync.py
```

## 8. 下一步（本阶段不执行）

优先级：

1. 获得 L1 USB 串口的物理访问路径，在 VM 中出现 `/dev/ttyUSB*`；
2. 运行 Unitree 官方 `unilidar_sdk` 只读发布器，取得 `/unilidar/imu`；
3. 首先只做“水平 + 一个俯倾”的最小 Gate；
4. 若模长不变性通过，再补全多姿态、轴向、时间与噪声验证；
5. 使用同一 Phase 5.4.5 数据做离线 Point-LIO 对照；
6. 全部通过前，不进入在线 SLAM 或 Nav2。

备选路线只有在无法取得 L1 原始 IMU 且项目决定扩大范围时，才评估
“L1 点云 + Go2 机身 IMU”的完整时空标定。不得把本阶段 LowState 的初步物理
PASS 误写为 Point-LIO 输入 PASS。

