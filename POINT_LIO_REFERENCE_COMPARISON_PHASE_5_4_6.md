# Phase 5.4.6：Point-LIO 官方实现对照实验

## 最终结论

```text
同一 Phase 5.4.5 rosbag：固定
ROS2 → ROS1 输入转换：逐消息精确匹配
ROS2 Point-LIO：FAIL
Unitree 官方 ROS1 Point-LIO：FAIL
Unitree 官方示例包环境自检：PASS

“仅 ROS2 移植导致失败”：REJECTED
Phase 5.5：HOLD / NOT ENTERED
```

官方 ROS1 实现没有在 Go2 数据上得到正常轨迹。它在仍属静止段的数据中
几秒内发散，并在处理约 152 秒传感器时间后以 `SIGSEGV` 退出，未能保存
PCD。与此同时，同一个官方二进制使用 Unitree 官方 L1 示例包的前 60 秒
可以稳定运行、形成合理尺度轨迹并保存 PCD。

因此，本次结果排除了“问题只存在于 ROS2 移植”的假设。当前应优先审计
Go2 固件 `/utlidar/*` 输出与官方 `unilidar_sdk` 输出之间的数据语义、
LiDAR↔IMU 外参方向及 Point-LIO 共享参数适配。ROS2 移植差异仍可能影响
失稳出现的时间和严重程度，但不是唯一原因。

本阶段没有重新采集数据、修改 TF、发布猜测外参、运行在线 SLAM、运行
Nav2 或调用任何运动控制接口。

## 1. 官方基线

来源：

```text
Repository:
https://github.com/unitreerobotics/point_lio_unilidar

Commit:
18ed5976d8fab2bd8a5148c26a40692bd3c0dc91

官方说明测试环境：
Ubuntu 20.04 + ROS Noetic
```

本次隔离环境：

```text
Ubuntu 20.04.6 LTS / WSL2
ROS Noetic 1.17.4
PCL 1.10
Workspace:
/home/est1/phase546_ros1_ws
```

官方源码未经算法修改，使用 `catkin_make -j2` 构建成功。WSL 只承担固定
rosbag 的离线回放，不连接 Go2 DDS，不承担在线系统时间 Gate。

## 2. 固定输入

ROS2 原始 bag：

```text
E:\笨笨狗\phase546_ros1_input\
phase545_20260728_135403_lab_demo_3x3
```

转换后的 ROS1 bag：

```text
E:\笨笨狗\phase546_ros1_input\phase545_cloud_imu_ros1.bag

Duration: 299.566 s
Size: 638,174,566 bytes
/utlidar/cloud: 4,615
/utlidar/imu: 75,021
```

只转换两个标准消息 topic，没有插值、重采样、重新打时间或改写字段。
ROS1 回放时仅做名称重映射：

```text
/utlidar/cloud -> /unilidar/cloud
/utlidar/imu   -> /unilidar/imu
```

## 3. 转换等价性验证

对 ROS2 原包和 ROS1 包逐消息计算：

- header 时间戳序列 SHA-256；
- PointCloud2 `data` payload SHA-256；
- IMU orientation、angular velocity、linear acceleration 和 covariance
  数值 payload SHA-256。

结果：

| Topic | 数量一致 | header 哈希 | payload 哈希 |
|---|---:|---:|---:|
| `/utlidar/cloud` | PASS | EXACT MATCH | EXACT MATCH |
| `/utlidar/imu` | PASS | EXACT MATCH | EXACT MATCH |

哈希：

```text
cloud header:
8317fe598779083473e54ee22a2046fa1012407be2a06d781c5592974808a470

cloud payload:
33dc92680cc83897e4408242c0f30c717abcdbda5485c0ac989de63a41c1fc41

imu header:
413c4774d05b2efe49e74fb2e854081d1b649cd0ec10df5efeffb48496087b12

imu payload:
bef65b4ee105b79bbedd2b2ef16e2a4b0c116ec4c16d94f20daaf0b4a523e706
```

结论：ROS1 转换没有造成此次失败。

## 4. 点云字段与时间单位

Go2 bag 首帧：

```text
frame_id: utlidar_lidar
height: 1
width: 4085
point_step: 32
is_dense: true

fields:
x          float32 offset 0
y          float32 offset 4
z          float32 offset 8
intensity  float32 offset 16
ring       uint16  offset 20
time       float32 offset 24

first point time: 0.0 s
last point time: 0.0623199455 s
```

Unitree 官方 L1 示例包首帧字段布局完全相同；其首帧约 2,033 点，末点
时间约 0.096779 s。Go2 包首帧约 4,085 点、末点时间约 0.062320 s。
这表明消息字段兼容，但采样组织/扫描周期与官方示例存在明显差异，需要
在后续数据语义审计中确认固件模式是否与 Point-LIO 预期一致。

## 5. 配置对照

官方 ROS1 L1 配置与此前 ROS2 对照的核心数值一致：

```yaml
lidar_type: 5
timestamp_unit: 0
time_lag_imu_to_lidar: 0.0
imu_time_inte: 0.004
acc_norm: 9.81
extrinsic_est_en: false
extrinsic_T: [0.007698, 0.014655, -0.00667]
extrinsic_R:
  [1, 0, 0,
   0, 1, 0,
   0, 0, 1]
use_imu_as_input: false
```

此次官方实验没有更换外参、估计外参或调算法参数。

官方配置中的 `publish_odometry_without_downsample: enable` 保持原样。
它影响输出发布方式，不作为本次 estimator 参数差异。

## 6. ROS2 Point-LIO 结果

来自 Phase 5.4.5：

```text
节点启动：PASS
IMU 初始化：PASS
时间回拨：0

静止阶段：
约前 180 秒最大半径 < 0.085 m

首次超过 1 m：190.962 s
首次超过 10 m：206.463 s
最终净位移：29,461.38 m
10 Hz 轨迹长度：30,203.57 m

PCD：生成
地图质量：FAIL
```

ROS2 的失败模式是静止阶段稳定、真实运动后快速发散。

## 7. 官方 ROS1 + Go2 同一 bag

```text
官方节点启动：PASS
lidar_type=5：PASS
PointCloud2 接收：PASS
IMU 接收/Estimator 输出：PASS
输出时间戳回拨：0

/pointlio/odom：2,342 条
/pointlio/path：2,341 条
已处理传感器时间：151.948 s
```

轨迹：

```text
首次超过 0.1 m：0.454 s
首次超过 1 m：4.802 s
首次超过 5 m：6.296 s
首次超过 10 m：7.010 s
首次超过 100 m：11.423 s
首次超过 1 km：23.690 s
首次超过 10 km：56.206 s

最终净位移：122,692.93 m
轨迹长度：125,485.78 m
```

进程结果：

```text
exit code: -11
signal: SIGSEGV
PCD saved: FAIL
```

官方实现甚至没有处理到约 180 秒的真实运动起点；它在静止数据中已经
发散，并随后崩溃。因此地图质量 Gate 直接 FAIL，不能评估有效地图结构。

WSL 环境中官方实现处理速度低于实时：实际约 287 秒只处理约 152 秒传感
器时间。这是运行性能观察项，但不能解释在最初约 7 秒传感器时间内已经
超过 10 m 的数值发散。

## 8. 官方 L1 示例包环境自检

为排除“官方二进制或 WSL 构建本身完全不可用”，使用官方仓库 README
提供的 L1 示例数据前 60 秒运行同一二进制、同一配置：

```text
输入：
unilidar-2023-09-22-12-42-04.bag

运行时长：60 s
输出 odom：591
时间回拨：0
轨迹长度：25.096 m
净位移：10.895 m
最大半径：10.895 m
进程退出：正常 SIGINT
PCD saved：PASS
PCD size：30,720,603 bytes
```

该结果尺度合理，节点没有崩溃，PCD 正常写出。它证明本次官方环境和构建
至少能够正常处理官方预期格式的数据。

## 9. A/B 判断

| 项目 | ROS2 Point-LIO | 官方 ROS1 Point-LIO |
|---|---:|---:|
| 节点启动 | PASS | PASS |
| 输入读取 | PASS | PASS |
| 时间戳回拨 | 0 | 0 |
| 静止阶段 | 稳定约 180 s | 数秒内发散 |
| 运动阶段 | 快速发散 | 未处理到运动起点 |
| 最终/末次位移 | 29.46 km | 122.69 km |
| 进程 | 正常完成 | SIGSEGV |
| 地图 | 生成但失效 | 未保存 |

### 判定

```text
A：官方正常、ROS2失败
不成立

B：官方也失败
成立，但失败模式不完全相同
```

可确认：

- ROS2 移植不是唯一原因；
- rosbag1 转换不是原因；
- WSL/官方构建完全不可运行不是原因；
- 不能把当前失败归咎于 `base_link -> utlidar_lidar` TF，因为算法没有
  使用或发布该猜测 TF；
- 普通 header 时间连续性没有失败，但 point time、IMU frame/轴向、
  外参方向等“数据语义”仍可能不符合 Point-LIO 的具体假设。

## 10. 下一步建议（本阶段不执行）

建议定义后续只读阶段：

> Phase 5.4.7：Go2 `/utlidar/*` 与官方 `unilidar_sdk` 数据语义对照。

优先比较：

1. `/utlidar/imu` 与官方 `/unilidar/imu` 的轴定义、正负号、单位、
   gravity direction 和 frame 约定；
2. 每帧点云 `time` 的定义、排序、范围及是否严格代表相对采样时刻；
3. Go2 固件当前 L1 扫描模式与官方示例约 10 Hz/约 0.1 s 帧组织的差异；
4. `extrinsic_T/R` 的实际方向是 IMU→LiDAR 还是 LiDAR→IMU，以及是否匹配
   Go2 X V2.0 的 L1 安装版本；
5. 使用官方 `unilidar_sdk` 直出数据与 Go2 固件 `/utlidar/*` 数据做同场
   只读对照，不发布 TF、不重新采集当前诊断 bag。

在完成这些检查前，不建议大范围调协方差、修改 TF 或进入其他 SLAM。

## 11. Phase 5.5 判定

```text
Phase 5.4.6：FAIL / DIAGNOSTIC PASS
Phase 5.5 在线 SLAM：HOLD / NOT ENTERED
Nav2：NOT ENTERED
TF：NOT MODIFIED
运动控制：NOT TOUCHED
```

“Diagnostic PASS”表示 A/B 成功回答了问题；不表示 Point-LIO 建图通过。

## 12. 证据文件

```text
E:\笨笨狗\phase546_comparison_summary.json
E:\笨笨狗\phase546_ros1_input\conversion_verification.json
E:\笨笨狗\phase546_ros1_input\input_inspection.json
E:\笨笨狗\phase546_ros1_result\ros1_output_analysis.json
E:\笨笨狗\phase546_ros1_result\pointlio_output.bag
E:\笨笨狗\phase546_ros1_result\pointlio.log
E:\笨笨狗\phase546_ros1_result\bag_play.log
E:\笨笨狗\phase546_official_sample_result\output_analysis.json
E:\笨笨狗\phase546_official_sample_result\input_inspection.json
E:\笨笨狗\phase546_official_sample_result\scans.pcd
E:\笨笨狗\phase546_tools\run_official_ros1_pointlio.sh
E:\笨笨狗\phase546_tools\verify_bag_conversion.py
```
