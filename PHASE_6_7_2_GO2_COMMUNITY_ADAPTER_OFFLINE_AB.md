# Phase 6.7.2：Go2 专用 IMU Calibration / Transform 离线复现实验

日期：2026-08-15  
状态：**PARTIAL_IMPROVEMENT_HOLD**

## 1. 结论

同一个 Phase 5.4.5 失败 rosbag 经 `autonomy_stack_go2` 风格的完整适配后，
Point-LIO 的灾难性发散从最终净位移 `29,461.38 m` 降至 `7.45 m`，改善
`99.9747%`。地图稳健范围也恢复到十几米尺度，证明该社区适配路线具有很高
价值。

但是定位 Gate **仍未通过**：

- bag 内 `/utlidar/robot_odom` 是约 `18.09 m` 的闭环轨迹，最终回到距起点
  `0.22 m`；
- 新 Point-LIO 路径长 `58.78 m`，是机载里程计的 `3.25` 倍；
- 新 Point-LIO 闭环误差 `7.45 m`，是机载里程计闭环误差的 `33.84` 倍；
- 新轨迹净 yaw 为 `-4282.13°`，机载里程计为 `+643.82°`；
- 输出中有 `3034` 个重复时间戳和 `1` 次时间回退。

因此：

```text
29 km catastrophic divergence eliminated     PASS
trajectory consistency                       FAIL
Phase 5.4 HOLD released                      NO
Phase 5.5 authorized                         NO
```

## 2. 安全边界

本实验严格离线：

- 未连接 Go2；
- 未运行社区 `calibrate_imu` 在线节点；
- 未创建 `/api/sport/request` 或 `/cmd_vel` publisher；
- 未调用 `SportClient`、`Move()` 或 `StopMove()`；
- 未启动 Nav2、在线 SLAM 或真实 TF；
- 原始 bags 和 Phase 5.4.5 基线未修改；
- ROS 进程运行在临时 Linux network namespace 内；
- namespace 内只有 `dummy0=10.200.0.1/24`，无外部路由；
- 实验退出后 `dummy0` 已自动消失。

第一次尝试因 WSL2 localhost-only DDS discovery 失败而得到 `0` 输出样本；该次
结果已单独保存为：

```text
phase672_pointlio_result_discovery_fail/
```

它不计入算法 A/B。随后用最小 pub/sub 在隔离 namespace 中确认
`LOCAL_DDS_PASS`，才执行正式回放。

## 3. 上游源码核验

核验基线：

```text
repository: jizhang-cmu/autonomy_stack_go2
branch:     foxy-humble
commit:     43d5f54b389b251713f0097893c30fa76c870d54
```

关键事实：

1. `calibrate_imu.cpp` 会进行轴符号变换、15.1° 旋转、静态 bias 和 yaw
   交叉耦合估计；它同时创建运动 publisher 并调用 SportClient，因此没有运行。
2. `transform_everything.py` 对 cloud 做 164.9° pitch、Z 偏移和机身盒过滤。
3. Point-LIO 消费的 `/utlidar/transformed_imu` 把 orientation 设为单位四元数，
   并把三轴 linear acceleration 全部置零，只保留校正后的 angular velocity。
4. `mapping_utlidar.launch` 覆盖 `use_imu_as_input=false`，同时保持
   `mapping.imu_en=true`。
5. Phase 5.4.5 本地基线本来就已经使用 `use_imu_as_input=false`。因此，本次改善
   不能归因于这个 flag；新增变量是点云变换/过滤、gyro 变换/校正以及主 IMU
   流加速度置零的组合。

上游资料：

- https://github.com/jizhang-cmu/autonomy_stack_go2
- https://github.com/jizhang-cmu/autonomy_stack_go2/blob/foxy-humble/src/utilities/calibrate_imu/src/calibrate_imu.cpp
- https://github.com/jizhang-cmu/autonomy_stack_go2/blob/foxy-humble/src/utilities/transform_sensors/transform_sensors/transform_everything.py
- https://github.com/jizhang-cmu/autonomy_stack_go2/blob/foxy-humble/src/slam/point_lio_unilidar/launch/mapping_utlidar.launch
- https://github.com/jizhang-cmu/autonomy_stack_go2/blob/foxy-humble/src/slam/point_lio_unilidar/config/utlidar.yaml
- https://github.com/unitreerobotics/unilidar_sdk/issues/34

## 4. 离线标定复刻

使用已有只读 bags：

```text
level static:  phase548_20260729_131744_level_static
positive yaw:  phase548_20260729_133703_yaw_ccw_manual
```

按照社区源码数学得到：

| 参数 | 值 |
|---|---:|
| static samples | 2486 |
| yaw samples | 4869 |
| acc_bias_x | 2.3476958364 |
| acc_bias_y | 0.0002169089 |
| acc_bias_z | -19.3463296991 |
| ang_bias_x | 0.0025493851 |
| ang_bias_y | -0.0002469990 |
| ang_bias_z | 0.0000797620 |
| ang_z2x_proj | 0.1603638633 |
| ang_z2y_proj | -0.2031538128 |

`acc_bias_z=-19.35` 本身再次说明原 acceleration 字段不能被当成标准原始比力。
该参数文件被明确标记为 `EXPERIMENTAL OFFLINE REPRODUCTION ONLY`。Point-LIO
实际消费的 transformed IMU acceleration 是 `[0, 0, 0]`，所以本实验不是“已经
修复物理加速度”的证明。

## 5. 转换 bag

输入：

```text
phase545_20260728_135403_lab_demo_3x3
duration: 299.567648 s
input sha256: 7ea0dc3ed9b4c49bc1b93c9bcdd263d0cdb7d8faa2e195943906dca9b9863b5b
```

输出：

```text
phase672_bags/phase545_go2_community_transform
output sha256: 91126e74b33033049d5795e4bcb7df2ada82149cc6eadb59150221cd62b6c914
```

| Topic | 样本数 |
|---|---:|
| `/utlidar/transformed_cloud` | 4615 |
| `/utlidar/transformed_imu` | 75021 |
| `/utlidar/robot_odom` | 44853 |
| `/odom` | 44853 |

点云输入 `19,013,973` 点，输出 `17,624,168` 点，保留率 `92.6906%`。

## 6. 同包 Point-LIO A/B

| 指标 | 原始输入基线 | 社区适配输入 | bag 内 robot_odom 参考 |
|---|---:|---:|---:|
| 时长 | 298.07 s | 299.04 s | 299.57 s |
| 最终净位移 | 29,461.38 m | **7.45 m** | 0.22 m |
| 10 Hz 路径长度 | 30,203.57 m | **58.78 m** | 18.09 m |
| 起点最大半径 | 29,461.38 m | **8.08 m** | 约场景尺度 |
| 净 yaw | 已发散 | **-4282.13°** | +643.82° |
| 超过 10 m 时间 | 206.46 s | **未超过** | 未超过 |

新地图：

```text
points: 2,724,210
finite ratio: 100%
robust XYZ span: 14.62 x 12.88 x 8.68 m
within 10 m radius: 99.609%
```

`/utlidar/robot_odom` 不是外部真值，只作为同包参考；但闭环误差、路径尺度和
yaw 的差异已经足够阻止 PASS。

## 7. 实现偏差与限制

本次传感器适配数学、topic 和参数来自社区 commit，但算法二进制使用现有
Phase 5.4.5 ROS2 Point-LIO 移植工作区。GitHub sparse clone 取得了 commit/tree，
但 blob 下载因当前网络连接失败，未能编译 `point_lio_unilidar` 的精确社区版本。

因此本实验能证明：

> 社区 Go2 适配链在当前数据上消除了原来的灾难性发散，并把结果拉回合理场景
> 尺度。

本实验不能证明：

> `autonomy_stack_go2` 完整、原样运行时已经满足本项目定位验收标准。

## 8. Gate 与下一步

当前决定：

```text
Phase 6.7.2 route value                  HIGH
catastrophic divergence                 RESOLVED OFFLINE
localization accuracy                   FAIL
Phase 5.4                               HOLD
Phase 5.5 / Nav2                        NOT AUTHORIZED
motion                                  NOT AUTHORIZED
```

信息增益最高的下一步：

1. 在可取得完整 blob 后，用同一 commit 的 `point_lio_unilidar` 精确二进制重跑
   同一 transformed bag；
2. 若仍不一致，做严格离线消融：cloud transform、gyro transform、zero-accel
   三组变量逐项对照；
3. 优先定位 yaw 符号/尺度和输出时间戳回退，不进入在线 SLAM；
4. 只有轨迹闭环、路径尺度、yaw 和时间连续性全部通过，才重新评估 Phase 5.4
   HOLD。

## 9. 产物

```text
phase672_tools/phase672_build_offline_inputs.py
phase672_tools/phase672_run_pointlio_offline.sh
phase672_tools/phase672_analyze_pointlio.py
phase672_artifacts/phase672_experimental_imu_calib_data.yaml
phase672_artifacts/phase672_transform_manifest.json
phase672_artifacts/phase672_utlidar.yaml
phase672_artifacts/phase672_pointlio_ab_analysis.json
phase672_bags/phase545_go2_community_transform/
phase672_pointlio_result/pointlio_odom.json
phase672_pointlio_result/scans.pcd
```
