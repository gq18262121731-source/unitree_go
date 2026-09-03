# Phase 5.4.7：Go2 X V2.0 L1 数据语义审计

## 最终判定

```text
Phase 5.4.7 数据语义审计：完成
Point-LIO 根因：尚未唯一定位
Phase 5.4：HOLD
Phase 5.5：NOT ENTERED
```

本阶段使用 Phase 5.4.5 的同一份 rosbag 和 Unitree 官方 L1 示例包进行只读审计，
没有重新采集数据、运行 SLAM、修改 TF、发布外参、调用运动控制或进入 Nav2。

已经排除：

- ROS2/ROS1 消息字段或 payload 转换污染；
- `PointCloud2.time` 被误当成毫秒、微秒或纳秒；
- 固定 `scan_rate=10 Hz` 参数不匹配；
- ROS `base_link -> utlidar_lidar` 缺失导致 Point-LIO 内部失败；
- 官方 L1 配置中 `extrinsic_T` 的实际代码方向含糊。

已经确认但尚不能单独解释发散：

- Go2 固件输出约 15.41 Hz、单帧约 62.28 ms；官方示例约 9.90 Hz、单帧约
  97.26 ms；
- Go2 每个点的 `ring` 恒为 1，官方示例为 0～17；
- Go2 有 824/4615 帧出现少量逐点时间局部逆序，官方示例为 0 帧；
- Go2 静止加速度模长均值为 10.3785 m/s²，比标准重力高约 5.83%；
- 现有 bag 没有“向前倾、向左倾、正向旋转”等人工动作标签，因此不能从数据
  独立证明 IMU 的每一根轴和正负号完全符合硬件定义。

Point-LIO 会在逐点更新前按 `curvature`（点时间）重新排序，而且
`unilidar_handler` 不使用 `ring`。因此，时间局部逆序和单一 ring 是明确的固件
帧组织差异，但当前证据不足以把其中任一项认定为 29 km/122 km 发散的唯一根因。

## 1. 审计输入

Go2 X V2.0 / Firmware V1.1.15：

```text
E:\笨笨狗\phase546_ros1_input\
phase545_20260728_135403_lab_demo_3x3

/utlidar/cloud       4,615
/utlidar/imu        75,021
/utlidar/robot_odom 44,853
时长约 299.6 s
```

Unitree 官方 L1 示例：

```text
E:\笨笨狗\phase546_sources\official_l1_dataset\
unilidar-2023-09-22-12-42-04.bag

/unilidar/cloud       4,312
/unilidar/imu       108,813
```

只读分析脚本：

```text
E:\笨笨狗\phase547_tools\phase547_semantic_audit.py
```

完整机器可读结果：

```text
E:\笨笨狗\phase547_semantic_audit.json
```

## 2. 官方坐标定义与消息发布路径

Unitree `unilidar_sdk` 对 L1 的定义为右手系：

- LiDAR 原点位于底部安装面中心；
- +X 与出线方向相反；
- +Y 为 +X 逆时针旋转 90°；
- +Z 向上；
- IMU 三轴与点云坐标轴对齐、平行；
- IMU 原点在 LiDAR 坐标系中的位置为
  `[-0.007698, -0.014655, 0.00667] m`。

官方 ROS2 发布器把 SDK 原始数组直接复制到 ROS 消息：

```cpp
imu.quaternion[]          -> orientation x/y/z/w
imu.angular_velocity[]    -> angular_velocity x/y/z
imu.linear_acceleration[] -> linear_acceleration x/y/z
```

没有发现轴交换、符号翻转或单位转换。点云中的 `x/y/z/intensity/ring/time` 同样由
SDK 点类型直接进入 PointCloud2。

来源：

- Unitree `unilidar_sdk` README 的坐标定义；
- `unitree_lidar_ros2.h` 第 141～152 行的 IMU 直接复制逻辑。

这证明官方接口语义，但不能证明 Go2 固件 `/utlidar/imu` 一定走了完全相同的内部
发布路径；后者仍需带已知动作标签的实测才能完成最终轴向验收。

## 3. IMU 审计

### 3.1 消息与时间

| 指标 | Go2 | 官方示例 |
|---|---:|---:|
| frame_id | `utlidar_imu` | `unilidar_imu` |
| 样本数 | 75,021 | 108,813 |
| 平均频率 | 250.420 Hz | 249.740 Hz |
| header 时间回拨 | 0 | 0 |
| 中位间隔 | 4.479 ms | 约 4 ms |

Go2 IMU 的最大相邻间隔为 23.596 ms，但没有时间回拨。频率和单位满足官方配置
`imu_time_inte: 0.004` 的数量级。

### 3.2 静止重力与陀螺

Go2 首 60 秒、15,025 个样本：

```text
linear_acceleration mean:
[+3.395270, -0.000385, +9.806213] m/s²

acceleration norm mean:
10.378477 m/s²

acceleration norm deviation from 9.80665:
+5.831%

angular_velocity mean:
[+0.000701, +0.000333, +0.000496] rad/s

angular_velocity norm median:
0.007820 rad/s
```

重力向量相对 IMU +Z 的夹角约 19.098°。这与 L1 在机器人上的倾斜安装相容，不能
单凭该向量判定轴错误。陀螺静止均值接近零，没有发现明显单位错误或静态饱和。

官方示例前 5 秒的加速度模长均值为 9.697362 m/s²，偏离标准重力约 -1.114%。
Go2 的 +5.83% 是需要保留的标定/偏置观察项。官方 L1 配置使用：

```yaml
acc_norm: 9.81
```

Point-LIO 因而按 m/s² 使用输入；不存在把 Go2 的 m/s² 当成 `g` 的简单单位错误。

### 3.3 轴向响应的现有证据

把运动阶段的 IMU 角速度与 `/utlidar/robot_odom` 的 `base_link` 角速度对齐后，
得到 23,351 个运动样本。主要相关性为：

```text
corr(imu gyro z, odom angular z) = -0.985314
```

最小二乘得到的 IMU→base 角速度映射接近一个正常三维旋转：

```text
det(raw mapping) = 0.8451
singular values  = [0.9872, 0.9486, 0.9025]
closest SO(3) residual (Frobenius) = 0.1110
```

这说明 IMU 三轴不是随机错位；它与 `base_link` 之间存在稳定安装旋转，其中
base yaw 与 IMU z 近似反号。该结果不表示 LiDAR→IMU 外参应使用这个 base 旋转：
Point-LIO 只需要同一 L1 内部的 LiDAR/IMU 关系，而不是传感器到机器人基座的关系。

限制：

- `/utlidar/robot_odom` 是固件融合结果，不是完全独立的标定基准；
- rosbag 没有标注每段动作是前倾、后倾、左倾或右倾；
- 因此“每根轴的物理方向和正负号”只能判为 `PARTIAL / INSUFFICIENT LABELS`，
  不能猜测为 PASS 或 FAIL。

ROS Imu 的 `orientation` 四元数不会被官方 Point-LIO 状态估计路径使用；本次不以
其欧拉角作为发散根因证据。

## 4. PointCloud2 字段审计

Go2 与官方示例字段布局完全一致：

| 字段 | datatype | offset |
|---|---:|---:|
| `x` | float32 | 0 |
| `y` | float32 | 4 |
| `z` | float32 | 8 |
| `intensity` | float32 | 16 |
| `ring` | uint16 | 20 |
| `time` | float32 | 24 |

```text
point_step = 32 bytes
height = 1
```

所以不存在因字段名、datatype、offset 或 point_step 不匹配而把数据读错的证据。

## 5. 逐点时间与扫描周期

| 指标 | Go2 | 官方示例 |
|---|---:|---:|
| 点云频率 | 15.4059 Hz | 9.8972 Hz |
| header 周期中位数 | 64.9772 ms | 100.0021 ms |
| 单帧 point time 跨度中位数 | 62.2766 ms | 97.2615 ms |
| 跨度/header 周期比 | 0.9584 | 0.9726 |
| 每帧点数中位数 | 4,125 | 2,149 |
| 首点接近 0 的帧 | 4,141/4,615 | 4,259/4,312 |
| 末点不是最大时间的帧 | 0 | 0 |
| 非有限 time | 0 | 0 |

结论：

```text
time 单位 = 秒
time 语义 = 相对当前帧起始的逐点采样时间
```

如果把 Go2 的 `time` 当成 ms、µs 或 ns，单帧跨度将无法与约 65 ms 的 header
周期吻合。官方配置的 `timestamp_unit: 0`（秒）是正确的。

官方 L1 配置没有固定的 `scan_rate` 参数。`cut_frame` 默认关闭，因此 Go2 的
15.41 Hz 与官方示例的 9.90 Hz 不会形成一个简单的“配置仍写 10 Hz”错误。

## 6. 点时间排序与 ring 组织

### 6.1 Go2 的局部逆序

Go2：

```text
相邻点时间对：19,009,358
负跳变：955
包含负跳变的帧：824 / 4,615 = 17.855%
非递减相邻对比例：99.994976%

负跳变幅度：
median 0.7873 ms
P95    1.5070 ms
max    1.9209 ms
```

该现象从数据包开始就存在：

```text
首 1 s： 4 / 16 帧
首 5 s：18 / 78 帧
首 10 s：32 / 155 帧
```

官方示例的 4,312 帧、9,199,736 个相邻时间对全部单调递增。

但是官方 Point-LIO 的 `unilidar_handler` 只是把 `time` 乘单位缩放后存入
`curvature`；随后 `laserMapping.cpp` 第 1017～1029 行会按 `curvature` 对点排序，
再生成 `time_seq`。同步逻辑也会扫描最大点时间，而不是盲信未排序的最后一点。

因此：

```text
局部逆序 = CONFIRMED FORMAT DIFFERENCE
局部逆序 = NOT PROVEN ROOT CAUSE
```

### 6.2 ring

```text
Go2：          每个点 ring = 1
官方示例：     ring = 0..17，每帧 18 个 ring
```

官方配置仍写有 `scan_line: 18`，但 `unilidar_handler` 的当前代码路径只复制
`x/y/z/intensity/time`，不读取 `ring`。所以单一 ring 是固件帧组织与官方示例
不同的强证据，但不是当前 Point-LIO 代码中的直接索引错误。

## 7. LiDAR↔IMU 外参方向

官方 YAML 的注释写着：

```yaml
# transform from imu to lidar
extrinsic_T: [0.007698, 0.014655, -0.00667]
extrinsic_R: identity
```

注释容易误导。代码实际执行：

```cpp
p_body_imu =
    Lidar_R_wrt_IMU * p_body_lidar
    + Lidar_T_wrt_IMU;
```

因此配置的实际数学语义是：

```text
p_IMU = R_IMU_LiDAR * p_LiDAR + t_IMU_LiDAR
```

即 `T_imu_lidar`，把 LiDAR 点变到 IMU 坐标系。

官方 SDK 给出的 IMU 原点在 LiDAR 坐标系中的位置是：

```text
t_lidar_imu = [-0.007698, -0.014655, +0.00667] m
```

两者轴平行、旋转为单位阵时，其逆变换平移正好是：

```text
t_imu_lidar = [+0.007698, +0.014655, -0.00667] m
```

所以官方 L1 数值与实际代码方向一致。它不是
`base_link -> utlidar_lidar`，也不依赖当前缺失的标准机器人 TF。

判定：

```text
Point-LIO 内部外参方向：CONFIRMED LiDAR -> IMU
官方 L1 平移符号：CONSISTENT
Go2 X V2.0 是否使用硬件版本完全相同的 L1 内部几何：尚无序列号/标定文件追溯
```

## 8. 官方样例与 Go2 差异汇总

| 项目 | 官方 L1 示例 | Go2 X V2.0 | 直接导致当前代码错误？ |
|---|---:|---:|---|
| cloud fields/layout | 标准布局 | 相同 | 否 |
| point `time` 单位 | 秒 | 秒 | 否 |
| point time span | 97.26 ms | 62.28 ms | 无固定 scan-rate 冲突 |
| cloud Hz | 9.90 | 15.41 | 观察项 |
| point time 排序 | 严格单调 | 少量局部逆序 | 代码会排序，未证实 |
| ring | 0..17 | 恒为 1 | handler 不使用 ring |
| IMU Hz | 249.74 | 250.42 | 否 |
| 静止加速度模长 | 9.697 m/s² | 10.378 m/s² | 标定/偏置观察项 |
| LiDAR/IMU 轴定义 | 官方声明平行 | payload 尚缺带标签物理验证 | 未闭环 |
| 内部外参方向 | LiDAR→IMU | 使用同一配置 | 方向已确认 |

## 9. 根因候选排序

### 高优先级：Go2 固件输出与官方 raw L1 数据的未记录语义差异

证据：

- 15.4 Hz / 4,125 点与官方 9.9 Hz / 2,149 点明显不同；
- ring 从 18 路变成恒定 1；
- 点时间存在官方样例没有的局部逆序；
- 官方 Point-LIO 在官方样例正常，在 Go2 数据失败。

但当前开源 handler 会忽略 ring 并重新排序点时间，因此还需要确认固件是否对点做了
其他未记录的聚合、过滤、重排或坐标处理。

### 中高优先级：Go2 `/utlidar/imu` 的物理轴向/标定语义

静止陀螺与频率正常，运动角速度与 odom 之间可由稳定旋转解释；没有“完全错轴”的
证据。但加速度模长比标准重力高 5.83%，且现有 bag 没有带标签倾斜动作，无法完成
独立轴向验证。

### 低优先级 / 已基本排除

- 点时间单位错误；
- 固定 10 Hz 参数错误；
- PointCloud2 字段布局错误；
- 官方 L1 外参正负号或方向错误；
- 缺失 `base_link -> utlidar_lidar` ROS TF；
- ROS2→ROS1 转换污染。

## 10. Gate 与后续边界

```text
[PASS] PointCloud2 fields/datatype/offset
[PASS] point time unit = seconds
[PASS] point time span matches actual frame period
[PASS] IMU frequency and timestamp continuity
[PASS] Point-LIO internal extrinsic direction resolved
[OBS ] Go2 accelerometer norm +5.83%
[OBS ] Go2 15.4 Hz / single-ring frame organization
[OBS ] small local point-time inversions, handled by current sort path
[HOLD] physically labeled IMU axis/sign verification
[HOLD] firmware raw-L1 equivalence / calibration provenance

Phase 5.5:
NOT READY
```

在补齐以下任一强证据前，不建议调大范围算法参数或切换 SLAM：

1. 在不运行 SLAM 的情况下，做带明确标签的前倾、后倾、左倾、右倾、正反向 yaw
   只读 IMU 试验；或
2. 获取 Go2 固件 `/utlidar/*` 的发布实现/接口说明，确认其与 `unilidar_sdk`
   raw L1 输出的聚合、ring 和 point-time 语义；或
3. 对同一台 L1 同时记录官方 `unilidar_sdk` 直出和 Go2 固件 `/utlidar/*`，
   做逐帧只读 A/B。

本报告到此停止，不进入 Phase 5.5。

## 11. 参考

- Unitree `unilidar_sdk`：
  https://github.com/unitreerobotics/unilidar_sdk
- Unitree `point_lio_unilidar`：
  https://github.com/unitreerobotics/point_lio_unilidar
- 官方 L1 配置：
  https://github.com/unitreerobotics/point_lio_unilidar/blob/main/config/unilidar_l1.yaml
- Point-LIO 点预处理：
  https://github.com/unitreerobotics/point_lio_unilidar/blob/main/src/preprocess.cpp
- Point-LIO 映射与外参代码：
  https://github.com/unitreerobotics/point_lio_unilidar/blob/main/src/laserMapping.cpp
