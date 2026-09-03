# Phase 5.2.2：真实机器人 ROS2 基础环境报告

测试日期：2026-07-25  
环境建设状态：**PASS**  
时间稳定性状态：**FAIL（WSL2 guest clock 回拨）**  
最终结论：**环境可用于 ROS2 软件开发，但不能作为 SLAM/Nav2 的可信时间基准**

## 1. 范围与安全边界

本阶段只建设 Ubuntu 22.04 + ROS2 Humble 基础环境。

本阶段没有：

- 连接 Go2 Ethernet；
- 初始化 Unitree SDK2 DDS；
- 创建 ROS2 Node 或 DDS Participant；
- 启动 TF、SLAM、Nav2；
- 调用任何运动接口；
- 修改 Go2 配置；
- 修改 Mock Provider、Mock API、Mock WebSocket 或 `health_new`；
- 删除、升级或替换现有 Ubuntu 20.04 SDK2 验证环境。

## 2. Windows 与 WSL 基线

| 项目 | 实测值 |
| --- | --- |
| Windows build | `26200` |
| WSL | `2.7.10.0` |
| WSL kernel | `6.18.33.2-microsoft-standard-WSL2` |
| WSL networking | `mirrored` |
| 新发行版名称 | `Ubuntu-22.04` |
| WSL version | `2` |
| 安装位置 | `E:\WSL\Ubuntu-22.04` |
| 原 Ubuntu 20.04 | 保留，仍为默认发行版 |

Go2 Ethernet 在安装和验收期间处于断开状态。网络只通过 WLAN：

```text
Windows / WSL WLAN: 10.10.214.124/17
default gateway: 10.10.255.254
```

## 3. Ubuntu 22.04 建设方式

`wsl --install`、Microsoft Store/WebDownload、Canonical cloud image 和 Docker Hub 在当前网络中均出现
超时或极低下载速率。最终采用：

1. 从 Ubuntu 签名软件源读取 Jammy 软件包；
2. 使用 `debootstrap` 在临时目录建立 Jammy rootfs；
3. 使用 Ubuntu archive keyring 验证软件包；
4. 打包 rootfs；
5. 使用 `wsl --import ... --version 2` 导入为独立发行版。

软件源：

```text
https://mirrors.aliyun.com/ubuntu
```

仓库元数据与软件包仍通过 Ubuntu archive keyring 验证；镜像只作为传输端点。

参考：

- [Microsoft WSL import](https://learn.microsoft.com/en-us/windows/wsl/basic-commands)
- [Ubuntu WSL instance management](https://documentation.ubuntu.com/wsl/latest/howto/backup-and-restore/)

## 4. Ubuntu 22.04 结果

| 项目 | 实测值 |
| --- | --- |
| OS | `Ubuntu 22.04 LTS (Jammy Jellyfish)` |
| 默认用户 | `test1` |
| 用户 UID/GID | `1000/1000` |
| sudo | 可用；本地 WSL 环境使用免密码 sudo |
| systemd | `249.11-0ubuntu3.21` |
| PID 1 | `systemd` |
| system state | `running` |
| Python | `3.10.12` |
| Locale | `en_US.UTF-8`, `zh_CN.UTF-8` |
| Timezone | `Asia/Shanghai` |

`/etc/wsl.conf`：

```ini
[boot]
systemd=true

[user]
default=test1

[network]
generateResolvConf=true
```

## 5. ROS2 Humble

ROS 官方 GitHub Release 附件在当前网络超时，因此使用 ROS 官方传统 apt 配置：

```text
key:
https://raw.githubusercontent.com/ros/rosdistro/master/ros.key

repository:
http://packages.ros.org/ros2/ubuntu
```

安装包：

| Package | Version |
| --- | --- |
| `ros-humble-ros-base` | `0.10.0-1jammy.20260607.081808` |
| `ros-humble-cyclonedds` | `0.10.5-2jammy.20260226.013234` |
| `ros-humble-rmw-cyclonedds-cpp` | `1.3.4-1jammy.20260605.121029` |
| `ros-dev-tools` | `1.0.1` |

安装规模：

```text
457 packages
79.1 MB downloaded
382 MB installed
```

只读/静态验收：

```text
ROS_DISTRO=humble
ros2 CLI: PASS
rclpy import: PASS
rmw_cyclonedds_cpp package: PASS
CycloneDDS shared-library linkage: PASS
```

没有运行 talker/listener、节点列表、Topic discovery 或 multicast 测试，因为本阶段禁止启动 DDS。

用户环境已加入：

```bash
source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
```

参考：

- [ROS 2 Humble Ubuntu installation](https://docs.ros.org/en/humble/Installation/Ubuntu-Install-Debians.html)
- [ROS apt source](https://github.com/ros-infrastructure/ros-apt-source)

## 6. 时间服务状态

Ubuntu 22.04：

```text
systemd-timesyncd:
  loaded: yes
  enabled: yes
  active: inactive
  reason: ConditionVirtualization=!container not met

timedatectl:
  System clock synchronized: yes
  NTP service: inactive
```

现有 Ubuntu 20.04 在测试前已停止，因此其 `systemd-timesyncd` 不参与本轮时钟测试。

Windows：

```text
W32Time:
  Status: Stopped
  StartType: Manual
```

## 7. Ubuntu 22.04 时钟连续性

方法：

- 90 秒；
- 每 50 ms 比较 `time.time()` 与 `time.monotonic()`；
- 不运行 ROS2 或 DDS；
- Ubuntu 20.04 已停止。

结果：

```text
samples: 1744
monotonic span: 89.952 s
wall-clock span: 88.600 s
wall - monotonic span difference: -1352.732 ms
```

检测到三次墙上时钟回拨：

| 发生时间 | wall-minus-monotonic 跳变 | 实际 wall delta |
| --- | ---: | ---: |
| 28.94 s | `-546.96 ms` | `-494.69 ms` |
| 58.91 s | `-393.62 ms` | `-343.42 ms` |
| 88.91 s | `-412.15 ms` | `-361.85 ms` |

回拨周期约 30 秒。

## 8. Windows 对照测试

方法：

- 65 秒；
- 每约 50 ms 比较 Windows UTC wall clock 与 Stopwatch monotonic；
- 与 WSL 测试使用相同的 20 ms 跳变阈值。

结果：

```text
samples: 1022
monotonic span: 65.004 s
wall-clock span: 65.004 s
backward events: 0
events over 20 ms: 0
```

Windows 主机时钟在该窗口内稳定，而 Ubuntu 22.04 在 timesyncd inactive 的情况下仍约每 30 秒回拨。
因此问题已从“Ubuntu 20.04 的 NTP 配置”进一步定位到 **WSL2 guest 时钟同步路径**，不是 ROS2、
CycloneDDS 或某个发行版内 timesyncd 导致。

## 9. 验收

| 验收项 | 结果 |
| --- | --- |
| 独立 Ubuntu 22.04 WSL2 | PASS |
| 不影响 Ubuntu 20.04 | PASS |
| systemd | PASS |
| Python 3.10 | PASS |
| ROS2 Humble CLI | PASS |
| RCLPy | PASS |
| CycloneDDS / RMW | PASS（静态） |
| 不连接 Go2 | PASS |
| 不启动 DDS | PASS |
| 不启动 TF/SLAM/Nav2 | PASS |
| 系统时间无回拨 | **FAIL** |

## 10. 最终判断

```json
{
  "ubuntu_22_04": "PASS",
  "ros2_humble": "PASS",
  "cyclonedds": "PASS_STATIC",
  "python": "PASS",
  "environment_isolation": "PASS",
  "windows_clock_stable": true,
  "wsl_guest_clock_stable": false,
  "time_sync_ready": false,
  "go2_connected": false,
  "dds_started": false,
  "phase_5_3_started": false
}
```

Ubuntu 22.04 WSL2 环境可用于 ROS2 包管理、编译和不依赖真实时间的开发工作，但目前不能作为
LiDAR/IMU/TF/SLAM/Nav2 的可信运行主机。

进入 Phase 5.3 前，应迁移到独立 Ubuntu 22.04 物理机或双系统环境，并重新执行时间连续性与
Ethernet SDK2 DDS 只读基线。不要在 WSL2 中增加固定时间补偿。

按阶段边界在此停止。
