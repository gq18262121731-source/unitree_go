# Phase 5.2.3：Ubuntu 22.04 物理 ROS2 运行主机准备与验收

状态：**WAITING_FOR_PHYSICAL_HOST**  
Phase 5.3：**BLOCKED / NOT STARTED**  
文档性质：执行清单与报告模板；本文不代表物理主机已经通过验收。

## 1. 当前冻结结论

已确认：

| 项目 | 状态 |
| --- | --- |
| Go2 官方 Ethernet SDK2 DDS | PASS |
| LowState / SportModeState | PASS |
| L1 PointCloud / IMU / Odometry / Pose | PASS |
| Ubuntu 22.04 WSL2 | PASS（开发环境） |
| ROS2 Humble / CycloneDDS 静态环境 | PASS（开发环境） |
| WSL2 guest clock 连续性 | FAIL |
| 物理 Ubuntu 22.04 运行主机 | 待准备 |
| DDS → ROS2 Bridge | 未开始 |
| TF / SLAM / Nav2 | 未开始 |

WSL2 在 90 秒测试中出现约 30 秒周期的墙上时钟回拨：

```text
-546.96 ms
-393.62 ms
-412.15 ms
```

因此，现有 Ubuntu 22.04 WSL2 只用于 ROS2 编译、消息定义、launch
文件开发和不依赖真实时间的测试，不作为 LiDAR、IMU、TF、SLAM 或 Nav2
的真实运行主机；不增加固定时间补偿。

## 2. 目标架构与角色隔离

```text
Windows
├── health_new / Mock 演示 / 前端 / 开发工具
├── WSL Ubuntu 20.04：SDK2 实验环境
└── WSL Ubuntu 22.04：ROS2 开发与编译环境

Ubuntu 22.04 物理机
└── ROS2 Humble + CycloneDDS
    └── Go2 官方 Ethernet
        └── SDK2 DDS / L1 / IMU / Odometry（只读）
```

物理机验收不修改 Mock Provider、Mock API、Mock WebSocket、比赛页面或
`health_new`。

## 3. 全阶段安全闸门

只允许：

- 操作系统、时钟、软件包和网卡的只读检查；
- ROS2 CLI 静态检查；
- 官方 SDK2 只读 Subscriber；
- 订阅已验证的状态和传感器 Topic；
- 短时统计样本数量、频率、时间戳和时钟偏差。

明确禁止：

- `move()`、`SportClient`、locomotion API、velocity、`cmd_vel`；
- 创建 DDS Publisher 或发布任何 Topic；
- 订阅或调用运动命令 Topic；
- DDS → ROS2 Bridge、TF 广播、LaserScan 转换；
- SLAM、Nav2、建图、路径规划、巡逻、返航；
- 修改 Go2 固件或运行配置；
- 修改现有 Mock 合同或真实 Provider 业务代码。

任何验收项失败时立即停止，不用“临时补偿”绕过。

## 4. 物理主机前置条件

建议使用裸机或双系统 Ubuntu 22.04，不使用 WSL2、虚拟机或容器作为本阶段
真实运行载体。

准备并记录：

```text
主机型号:
CPU:
内存:
磁盘:
Ubuntu版本:
Kernel:
网卡型号:
网卡接口名:
安装方式: 裸机 / 双系统
```

基础只读检查：

```bash
cat /etc/os-release
uname -a
lscpu
free -h
ip -br link
ip -br addr
```

要求：

- Ubuntu 22.04 LTS；
- 有可直接连接 Go2 官方 Ethernet 的物理网卡；
- 物理机有稳定的系统时钟源；
- 测试期间保持供电稳定；
- 不与当前 PC/WSL 同时占用相同 Ethernet 地址。

## 5. Gate A：系统时间同步状态

先连接普通网络完成时间同步；此时不要连接 Go2、不要启动 SDK2 或 ROS2
节点。

检查：

```bash
timedatectl status
timedatectl timesync-status
systemctl status systemd-timesyncd --no-pager
```

记录：

```text
Timezone:
System clock synchronized:
NTP service:
NTP server:
Offset:
Delay:
Jitter:
Last sync:
```

进入连续性测试前必须满足：

```text
System clock synchronized: yes
NTP service: active
```

如果使用 chrony，记录 `chronyc tracking` 和 `chronyc sources -v`，并以实际
启用的时间服务为准；不要同时运行多个相互竞争的 NTP 客户端。

## 6. Gate B：30 分钟时钟连续性测试

时间服务达到同步状态后，先等待 5 分钟稳定期，再运行以下测试。测试期间不
启动 ROS2、DDS、Go2 SDK 或高负载任务。

```bash
python3 - <<'PY'
import json
import time

duration_s = 30 * 60
interval_s = 0.05
jump_threshold_ns = 20_000_000

start_wall = time.time_ns()
start_mono = time.monotonic_ns()
previous_wall = start_wall
previous_offset = start_wall - start_mono

samples = 1
wall_backsteps = []
offset_jumps = []
max_abs_offset_jump_ns = 0

deadline = time.monotonic() + duration_s
while time.monotonic() < deadline:
    time.sleep(interval_s)
    wall = time.time_ns()
    mono = time.monotonic_ns()
    offset = wall - mono

    wall_delta = wall - previous_wall
    offset_jump = offset - previous_offset
    max_abs_offset_jump_ns = max(max_abs_offset_jump_ns, abs(offset_jump))

    if wall_delta < 0:
        wall_backsteps.append({
            "sample": samples,
            "wall_delta_ms": wall_delta / 1e6,
        })
    if abs(offset_jump) > jump_threshold_ns:
        offset_jumps.append({
            "sample": samples,
            "offset_jump_ms": offset_jump / 1e6,
        })

    previous_wall = wall
    previous_offset = offset
    samples += 1

end_wall = time.time_ns()
end_mono = time.monotonic_ns()
span_error_ns = (end_wall - start_wall) - (end_mono - start_mono)

result = {
    "duration_requested_s": duration_s,
    "samples": samples,
    "monotonic_span_s": (end_mono - start_mono) / 1e9,
    "wall_span_s": (end_wall - start_wall) / 1e9,
    "wall_minus_monotonic_span_ms": span_error_ns / 1e6,
    "wall_backstep_count": len(wall_backsteps),
    "offset_jump_over_20ms_count": len(offset_jumps),
    "max_abs_offset_jump_ms": max_abs_offset_jump_ns / 1e6,
    "wall_backsteps": wall_backsteps[:20],
    "offset_jumps": offset_jumps[:20],
}
print(json.dumps(result, indent=2))
PY
```

验收条件：

| 指标 | 要求 |
| --- | --- |
| 测试时长 | 不少于 30 分钟 |
| 墙上时钟回拨 | `0` 次 |
| wall-minus-monotonic 突变超过 20 ms | `0` 次 |
| 30 分钟 wall-minus-monotonic span error | 绝对值不大于 `5 ms` |
| 测试后 NTP 状态 | 仍为 synchronized / active |

说明：

- `time.monotonic()` 只作为本机连续性参考，不替代传感器时间戳；
- 首次 NTP 校时可能发生合法的时间步进，所以必须在同步和稳定期后测试；
- 任何回拨均为 FAIL；
- 如果 span error 超过 5 ms，先检查 NTP、RTC、内核日志和负载，不放宽标准，
  不写固定补偿。

测试后记录：

```bash
timedatectl status
timedatectl timesync-status
journalctl --since "-40 min" --no-pager | grep -Ei 'time|clock|ntp'
```

Gate B 未通过时，Phase 5.2.3 停止。

## 7. Gate C：ROS2 Humble 与 CycloneDDS 静态验收

只有 Gate B 通过后才安装或检查 ROS2。按照 ROS2 Humble 官方 Ubuntu deb
安装流程准备环境。

静态检查：

```bash
source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp

printenv ROS_DISTRO
printenv RMW_IMPLEMENTATION
ros2 --help >/dev/null && echo "ros2 CLI PASS"
python3 -c "import rclpy; print('rclpy PASS')"
dpkg-query -W \
  ros-humble-ros-base \
  ros-humble-cyclonedds \
  ros-humble-rmw-cyclonedds-cpp
```

不要使用 `ros2 --version` 作为验收项；ROS2 CLI 不保证提供该参数。

验收：

```text
ROS_DISTRO=humble
RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
ros2 CLI=PASS
rclpy=PASS
CycloneDDS packages=installed
```

本 Gate 不运行 talker/listener，不创建 ROS2 Node 或 DDS Participant。

## 8. Gate D：Go2 Ethernet 网络验收

已知历史基线：

```text
Go2 Ethernet: 192.168.123.161
原 PC/WSL:    192.168.123.222/24
DDS Domain:   0
```

新物理机必须选择 `192.168.123.0/24` 中确认未被占用的主机地址。不能假设或
直接复用 `192.168.123.222`；如果原 PC/WSL 网口仍在线，该地址会冲突。

在写入静态地址前：

1. 断开或确认原 PC/WSL Go2 Ethernet 链路不再占用候选地址；
2. 查看当前地址和路由；
3. 用网络管理员确认候选地址空闲；
4. 记录实际物理接口名和最终地址。

配置完成后的只读验证：

```bash
ip -br addr
ip route
ip route get 192.168.123.161
ping -c 20 192.168.123.161
ip neigh show 192.168.123.161
```

记录：

```text
Physical interface:
Host Ethernet IP:
Go2 Ethernet IP:
Route:
Ping success rate:
Ping min/avg/max:
Neighbor/MAC state:
```

网络不通过时停止，不启动 DDS。

## 9. Gate E：官方 SDK2 状态只读验收

前提：

- Gate A 至 D 全部通过；
- Go2 静止并处于安全环境；
- 只使用官方 `unitree_sdk2py` Subscriber 示例或已经审计过的只读诊断；
- Domain 保持 `0`；
- 网卡绑定为 Gate D 的实际物理 Ethernet 接口；
- 不创建 Publisher，不初始化 `SportClient`。

依次验证：

```text
rt/lowstate
rt/sportmodestate
```

每项记录至少 30 秒：

```text
SDK2 version:
CycloneDDS version:
Domain:
Interface:
Remote participant count:

LowState:
  samples:
  frequency:
  first timestamp:
  last timestamp:

SportModeState:
  samples:
  frequency:
  first timestamp:
  last timestamp:
```

验收：

- Remote Participant 可见；
- LowState 样本数大于 0；
- SportModeState 样本数大于 0；
- 时间戳连续；
- 全程没有 Publisher、运动客户端或控制调用。

任何一项失败时停止，不进入传感器验收。

## 10. Gate F：L1 / IMU / Odometry 只读复验

只订阅此前通过实际 discovery 确认的 Topic：

```text
rt/utlidar/cloud
rt/utlidar/imu
rt/utlidar/robot_odom
rt/utlidar/robot_pose
rt/utlidar/lidar_state
```

建议每项采样 10 至 30 秒，不保存大体积点云。

记录：

| Topic | 样本数 | 频率 | 时间戳连续 | Frame | 数据摘要 |
| --- | ---: | ---: | --- | --- | --- |
| `rt/utlidar/cloud` |  |  |  |  | points/frame |
| `rt/utlidar/imu` |  |  |  |  | quaternion/gyro/accel |
| `rt/utlidar/robot_odom` |  |  |  |  | pose/velocity |
| `rt/utlidar/robot_pose` |  |  |  |  | pose |
| `rt/utlidar/lidar_state` |  |  |  |  | error/dirty state |

历史参考值只用于发现明显异常，不作为硬编码要求：

```text
L1 cloud:     15.13 Hz，约 4070 points/frame，frame=utlidar_lidar
L1 IMU:       248.45 Hz（消息时间戳频率）
Odometry:     150.27 Hz（消息时间戳频率），odom -> base_link
Pose:         18.77 Hz，frame=odom
LidarState:   4.87 Hz
```

`LidarState` 设备端存在本地静态 IDL 未包含的 `dirty_percentage` 字段。物理机
复验不得通过修改 SDK 静态 IDL 临时绕过；沿用已验证的动态类型发现，或单独
记录为 Phase 5.3 前的兼容性设计项。

## 11. Gate G：跨设备时间基线

在物理机时钟 Gate B 通过后，重新测量：

```text
host_receive_time - sensor_timestamp
```

对 cloud、IMU、Odometry、Pose 同步采样至少 10 分钟，记录：

- 最小值、平均值、最大值；
- 线性漂移（ms/min）；
- 是否出现负向跳变；
- 不同 Topic 之间的相对偏移；
- NTP 状态在采样前后是否变化。

本阶段只测量，不把该差值直接当作网络延迟，也不写固定 `+800 ms` 补偿。
只有确认 Go2/L1 时间源含义和跨设备同步策略后，才能决定 ROS2 stamp 转换。

## 12. 验收矩阵

| 验收项 | 结果 |
| --- | --- |
| Ubuntu 22.04 物理主机 | PENDING |
| 30 分钟无回拨 | PENDING |
| span error ≤ 5 ms | PENDING |
| NTP synchronized / active | PENDING |
| ROS2 Humble CLI | PENDING |
| CycloneDDS / RMW | PENDING |
| Go2 Ethernet | PENDING |
| LowState | PENDING |
| SportModeState | PENDING |
| L1 PointCloud | PENDING |
| IMU | PENDING |
| Odometry / Pose | PENDING |
| 无 DDS Publisher | PENDING |
| 无运动调用 | PENDING |
| Phase 5.3 未启动 | PASS |

只有全部 PENDING 项变为 PASS，Phase 5.2.3 才能标记完成。

## 13. 实测报告模板

物理机到位后，基于本文另行生成：

```text
docs/PHYSICAL_ROS2_HOST_REPORT_PHASE_5_2_3.md
```

报告至少包含：

1. 主机硬件与 Ubuntu 版本；
2. 时间服务、NTP 源、offset、jitter；
3. 30 分钟连续性原始结果；
4. ROS2 与 CycloneDDS 软件包版本；
5. Ethernet 接口、主机 IP、Go2 IP、ping；
6. SDK2 版本、Domain、Remote Participant；
7. LowState 与 SportModeState 样本统计；
8. L1、IMU、Odometry、Pose、LidarState 统计；
9. 跨设备时间偏差与漂移；
10. 未创建 Publisher 的证明；
11. 未调用运动接口的证明；
12. 未启动 Bridge、TF、SLAM、Nav2 的证明；
13. 未完成事项和最终 PASS/FAIL 判断。

## 14. 停止点

当前没有可访问的 Ubuntu 22.04 物理主机，因此 Phase 5.2.3 保持：

```json
{
  "status": "WAITING_FOR_PHYSICAL_HOST",
  "wsl_role": "DEVELOPMENT_ONLY",
  "physical_time_gate": "PENDING",
  "physical_ros2_gate": "PENDING",
  "physical_sdk2_gate": "PENDING",
  "physical_sensor_gate": "PENDING",
  "phase_5_3_started": false
}
```

到此停止。物理主机完成全部验收前，不进入 Phase 5.3。
