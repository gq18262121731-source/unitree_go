# Phase 5.4.5：目标场景单场景采集与 Point-LIO 离线验证 Gate

## 状态

```text
方案：已锁定
VM/rosbag2 预检：PASS
正式目标场景录制：未开始
SLAM / Nav2：未启动
运动控制：未接入
```

本阶段只采集实验室/比赛演示区域这一目标场景。区域约 3 m × 3 m，机器人有效活动范围约 2 m × 2 m，包含空地、桌子、显示器、玻璃隔断、墙面和地砖。正式录制必须在机器人到达目标场景、路线清空、人员知情后开始。

## 采集架构

Point-LIO 的离线核心输入：

```text
/utlidar/cloud  +  /utlidar/imu
```

同步保存的只读验证数据：

```text
/utlidar/cloud_base
/utlidar/robot_odom
/utlidar/lidar_state
/odom
/tf
/tf_static
```

`/tf` 和 `/tf_static` 只被动录制当前已有数据。本阶段不为补齐 TF 而启动新 TF 发布器，也不发布猜测的 `base_link -> utlidar_lidar`。

明确排除：

```text
/utlidar/cloud_deskewed
```

该话题包含固件运动补偿结果，不作为 Point-LIO 原始输入，避免形成算法依赖或重复补偿。

## 2026-07-27 只读预检

| 项目 | 结果 |
|---|---:|
| Ubuntu | 22.04.5 LTS |
| Kernel | 6.8.0-136-generic |
| ROS 2 | Humble |
| RMW | rmw_cyclonedds_cpp |
| VM 可用磁盘 | 79 GiB |
| 系统时钟同步 | yes |
| rosbag2 record | PASS |
| SLAM / Cartographer / Point-LIO / Nav2 进程 | NONE |
| `/utlidar/cloud` | 约 15.39 Hz |
| `/utlidar/cloud_base` | 约 15.41 Hz |
| `/utlidar/imu` | 约 250.36 Hz |
| `/utlidar/robot_odom` | 约 150.15 Hz |
| 只读 Bridge `/odom` 联调 | PASS，约 150.54 Hz |
| L1 `error_state` | 0 |
| L1 `dirty_percentage` | 1 |
| L1 cloud packet loss | 0.0 |
| L1 IMU packet loss | 0.0 |

预检时只读取 DDS/ROS 2 数据，没有录制正式 rosbag，没有让机器人运动。
只读 Bridge 在联调后已停止，没有留在后台运行。

## 2026-07-28 现场刷新

第二次只读预检再次通过：

```text
cloud              约 15.413 Hz
cloud_base         约 15.414 Hz
IMU                约 250.126 Hz
robot_odom         约 149.417 Hz
error_state        0
dirty_percentage   1
cloud packet loss  0.0
IMU packet loss    0.0
VM free disk       79 GiB
```

约 6 秒静止 rosbag 冒烟测试成功，生成 17.1 MiB、3385 条消息，`ros2 bag info` 可正常读取。当前无 `/tf`、`/tf_static` 发布者，因此测试包中没有 TF；没有为测试补发任何 TF。

## 正式采集流程

总时长约 5 分钟，默认脚本上限为 300 秒。

1. 静止初始化 60 秒
   - 机器人放在起点；
   - 不移动；
   - 确认 `error_state=0`，记录 `dirty_percentage`。
2. 沿环境边界人工遥控约 2 分钟
   - 速度约 0.1–0.2 m/s；
   - 沿墙移动，经过桌子和玻璃隔断区域；
   - 不开启自主运动。
3. 绕桌运动约 1 分钟
   - 绕桌半圈或一圈；
   - 增加边缘与角点约束。
4. 返回起点约 1 分钟
   - 尽量恢复初始朝向；
   - 用于离线闭环与终点漂移判断。

## 采集前硬 Gate

必须全部满足：

```text
[ ] 目标场景名称与路线已记录
[ ] 路线已清空，人员已知情
[ ] Go2 电量足够完成 5 分钟低速路线
[ ] 机器人在起点静止
[ ] Ethernet 正常
[ ] 时间同步正常
[ ] L1 error_state = 0
[ ] dirty_percentage 已记录且无异常突升
[ ] /utlidar/cloud、/utlidar/imu、/utlidar/cloud_base 在线
[ ] /utlidar/robot_odom 与 /odom 在线
[ ] VM 可用磁盘不少于 15 GiB
[ ] 无 SLAM、Nav2、自主控制进程
```

任一项不满足则不开始正式录制。

## 离线 Point-LIO Gate

正式 bag 录制完成后先执行完整性审计，再进入隔离的 Point-LIO 离线环境。

完整性 Gate：

```text
- ros2 bag info 可正常读取；
- 核心输入消息数量 > 0；
- cloud、IMU 时间戳无回拨；
- cloud 与 cloud_base 时间对应关系保持稳定；
- LiDAR/IMU 丢包与约 5 分钟总时长相符；
- L1 error_state 全程为 0；
- dirty_percentage 无不可解释的持续异常；
- 起点与终点均有静止段。
```

Point-LIO 离线输出 Gate：

```text
- 离线处理完整跑完，无崩溃；
- 轨迹连续，无明显瞬跳或发散；
- 点云地图结构可辨，无大面积重影、倾斜或撕裂；
- 返回起点时闭环误差可测量并记录；
- 输出地图与轨迹可重复生成；
- 不把实验性外参声明成官方标定。
```

只有上述 Gate 通过，才讨论在线 Point-LIO。仍不自动进入 Nav2。

## 已准备的脚本

VM 内目标路径：

```text
/home/go2/phase545_preflight.sh
/home/go2/phase545_start_readonly_bridge.sh
/home/go2/phase545_record_target_scene.sh
/home/go2/phase545_stop_recording.sh
/home/go2/phase545_stop_readonly_bridge.sh
/home/go2/phase545_target_scene_manifest.yaml
```

正式采集不会自动开始，需要现场确认后单独执行。
