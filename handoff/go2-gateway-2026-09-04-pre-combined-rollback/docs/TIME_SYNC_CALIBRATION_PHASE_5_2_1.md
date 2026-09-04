# Phase 5.2.1：时间同步校准报告

测试日期：2026-07-25  
诊断状态：**完成**  
时间同步状态：**NOT READY**  
停止点：未修改系统时间；未进入 Phase 5.3 ROS2 Bridge

## 1. 目标与边界

本轮只读比较：

```text
L1 sensor timestamp
        ↓
WSL wall clock (time.time)
        ↓
WSL monotonic clock
```

目标是判断此前约 0.8 秒差值属于固定 offset、持续 drift，还是时钟动态校正。

本轮没有：

- 修改 Windows、WSL、Go2 或 L1 的时间；
- 启停或重配 Windows Time、NTP、systemd-timesyncd；
- 安装 Ubuntu 22.04、ROS2 或任何软件包；
- 修改 SDK、CycloneDDS、Provider 或业务代码；
- 创建 DDS Publisher；
- 调用运动接口；
- 启动 ROS2、Nav2、SLAM。

## 2. 网络与运行基线

| 项目 | 实测值 |
| --- | --- |
| Go2 Ethernet | `192.168.123.161` |
| PC / WSL | `192.168.123.222/24` |
| DDS 接口 | `eth0` |
| DDS Domain | `0` |
| L1 IMU Topic | `rt/utlidar/imu` |
| L1 PointCloud Topic | `rt/utlidar/cloud` |
| WSL 系统 | Ubuntu 20.04.6 LTS |
| WSL 内核 | `6.18.33.2-microsoft-standard-WSL2` |
| Python | `3.8.10` |

## 3. 主机时间服务现状

### Windows

```text
Service: W32Time
Status: Stopped
StartType: Manual
```

`w32tm /query /status` 和 `/configuration` 均返回“服务尚未启动”。

### WSL Ubuntu 20.04

```text
systemd-timesyncd: active
Server: 91.189.91.157 (ntp.ubuntu.com)
Poll interval: 32 s
System clock synchronized: yes
```

两次状态读取中的关键值：

| 项目 | 第一次 | 第二次 |
| --- | ---: | ---: |
| NTP offset | `-977.046 ms` | `-883.627 ms` |
| NTP delay | `1997.926 ms` | `692.011 ms` |
| NTP jitter | `397.349 ms` | `634.622 ms` |
| Frequency | `-12.047 ppm` | `-12.308 ppm` |

当前 NTP 样本的 delay 和 jitter 很大，不适合作为机器人传感器时间校准基线。

## 4. 第一轮连续采样

持续时间：45.19 秒。

```text
WSL monotonic span: 45.190 s
WSL wall-clock span: 44.206 s
difference: -984.045 ms
```

这证明测试期间 WSL 墙上时钟相对 monotonic 时钟发生了约 984 ms 的向后校正。

### IMU

```text
samples: 10833
sensor timestamp span: 43.889 s
first/last apparent sensor rate error: -28732 ppm
```

5 秒窗口内的最小 `host wall - sensor timestamp`：

| 窗口 | 最小差值 |
| --- | ---: |
| 0–5 s | `568.04 ms` |
| 5–10 s | `734.23 ms` |
| 10–15 s | `896.12 ms` |
| 15–20 s | `603.18 ms` |
| 20–25 s | `161.23 ms` |
| 25–30 s | `227.14 ms` |
| 30–35 s | `383.63 ms` |
| 35–40 s | `545.70 ms` |
| 40–45 s | `715.13 ms` |

差值不是常量，并在 15–25 秒窗口出现明显跳变。

### PointCloud

点云与 IMU 同步呈现相同趋势：

```text
samples: 674
sensor timestamp span: 43.824 s
first/last apparent sensor rate error: -28732 ppm
```

点云窗口偏差曲线与 IMU 一致，并整体比 IMU 多约 67 ms，后者可能包含扫描与组帧时间。

## 5. 第二轮跳变定位

持续时间：40.03 秒；IMU 样本数：9576。

### WSL 墙上时钟事件

检测到两次超过 20 ms 的离散回拨：

| 发生时间 | 回拨量 |
| --- | ---: |
| 采样开始后 14.14 s | `-140.552 ms` |
| 采样开始后 17.86 s | `-1986.656 ms` |

合计回拨约 `-2127.21 ms`。

完整窗口：

```text
WSL monotonic span: 40.034 s
WSL wall-clock span: 37.906 s
```

### L1 IMU 时钟

```text
sensor timestamp span: 38.533 s
negative timestamp event: 0
discrete jump over 20 ms: 0
continuous rate error vs monotonic: approximately -37485 ppm
```

IMU 时间戳保持单调，没有离散倒退，但在该窗口内相对 WSL monotonic 时钟持续偏慢约 3.75%。

表面 offset 从：

```text
start: +231.26 ms
end:   -395.28 ms
```

这不是固定 offset，而是 WSL 墙上时钟回拨与传感器时钟速率差共同形成的动态结果。

## 6. 结论

此前约 0.8 秒的：

```text
receive_time - sensor_timestamp
```

不能解释为固定网络延迟，也不能作为固定 `clock_offset`。

当前至少存在两个问题：

1. WSL 的墙上时钟正在发生百毫秒到近 2 秒级的离散回拨；
2. 当前观测窗口内，L1 传感器时间戳相对 monotonic 时钟存在显著速率差。

WSL 回拨现象与 `systemd-timesyncd` 报告的高延迟、高抖动及约 `-0.9 s` NTP offset
高度一致，但本轮没有修改时间服务，因此不把相关性写成已验证的唯一根因。

## 7. 时间戳处理决策

当前禁止使用：

```text
corrected_timestamp = sensor_timestamp + fixed_offset
```

因为 offset 会随时间变化，固定修正会把错误时间写入后续 ROS2 消息。

在 Phase 5.3 前需要建立：

- 单一、稳定、明确的时间权威；
- WSL wall clock 不发生离散跳变；
- Go2/L1 与主机的 offset 和 drift 在足够长窗口内可重复；
- 校准后再次测量 PointCloud、IMU、Odometry、Pose；
- 区分“时钟偏差”和“采集/传输/组帧延迟”。

只有稳定后，才应选择：

- 保留传感器原始时间戳；
- 使用可追踪的动态 clock mapping；
- 或在桥接层使用接收时间并明确标注语义。

## 8. ROS2 环境只读检查

当前 WSL 列表只有：

```text
Ubuntu-20.04
docker-desktop
```

Ubuntu 20.04 中：

```text
ROS2 CLI: not installed
ROS packages: none found
```

Ubuntu 22.04 WSL2 尚未创建，ROS2 Humble 尚未安装。本轮没有执行安装。

## 9. 最终状态

```json
{
  "phase_5_2_sensor_readonly": "PASS",
  "phase_5_2_1_diagnosis": "COMPLETE",
  "time_sync_ready": false,
  "fixed_offset_valid": false,
  "wsl_wall_clock_stable": false,
  "sensor_timestamp_monotonic": true,
  "ubuntu_22_04_present": false,
  "ros2_installed": false,
  "phase_5_3_ros2_bridge": "NOT STARTED"
}
```

按安全边界在此停止。下一步应先决定并实施时间同步方案，再准备 Ubuntu 22.04 / ROS2 Humble。
