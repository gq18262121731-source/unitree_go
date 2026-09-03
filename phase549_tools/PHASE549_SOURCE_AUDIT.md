# Phase 5.4.9 数据源审计说明

## 安全边界

- 只订阅 `/utlidar/imu`、`rt/lowstate` 等状态数据；
- 不创建任何控制 publisher；
- 不运行 SLAM、Nav2 或 Point-LIO；
- 不发送 L1 工作模式、LED、网络或串口配置命令；
- 未确认两路候选源前，不执行新的姿态动作。

## 当前候选源

1. `/utlidar/imu`
   - L1 固件经 DDS 发布；
   - Phase 5.4.8 已证明其 `linear_acceleration` 不满足原始静止比力不变性。

2. `/unilidar/imu`
   - Unitree 官方 ROS2 L1 驱动输出；
   - 该驱动必须直接打开 L1 的 USB 串口；
   - 当前 VM 没有 `/dev/ttyUSB*` 或 `/dev/ttyACM*`，因此不能启动。

3. `rt/lowstate.imu_state`
   - Go2 机身 IMU，不是 L1 内置 IMU；
   - 可作为原始比力语义的诊断候选；
   - 即使物理 Gate 通过，也不能直接替代 L1 IMU：仍缺传感器时间戳和
     LiDAR 到机身 IMU 的可信外参。

`phase549_lowstate_imu_capture` 仅把 `rt/lowstate` 内嵌 IMU 写入本地 CSV，
不发布 ROS/DDS topic。
