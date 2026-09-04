# Phase 5.1.3：Unitree SDK2 DDS 服务状态诊断报告

测试日期：2026-07-25  
测试网络：E5576 WLAN、Go2 自身 AP、官方以太网开发链路  
结论：情况 B；Go2 SDK2 DDS 服务正常，仅在有线开发链路可见

## 1. 范围与安全边界

本轮仅诊断 Unitree SDK2 DDS 状态订阅与 discovery。

未执行：

- go2-gateway 自定义业务链路；
- DDS publisher 或任何业务 topic 发布；
- `SportClient`、`move()` 或其他机器人控制；
- ROS2、Nav2、SLAM、L1 或 IMU 读取；
- Mock Provider、`health_new`、SDK、CycloneDDS 或持久配置修改。

## 2. SDK 与运行环境

| 项目 | 实测值 |
| --- | --- |
| `unitree_sdk2py` | `1.0.1` |
| SDK 源码仓库 | `unitreerobotics/unitree_sdk2_python` |
| SDK branch / commit | `master` / `37116c5` |
| SDK Python 路径 | `/mnt/e/笨笨狗/go2_dev/unitree_sdk2_python/unitree_sdk2py` |
| CycloneDDS Python | `0.10.2` |
| CycloneDDS 路径 | `/home/est1/.venvs/go2-gateway/lib/python3.8/site-packages/cyclonedds` |
| Domain ID | `0` |
| 无线阶段 Interface | `eth1` |
| 无线阶段 WSL / Go2 IP | `192.168.12.62/24` / `192.168.12.1` |
| 有线阶段 Interface | `eth0` |
| 有线阶段 WSL / Go2 IP | `192.168.123.222/24` / `192.168.123.161` |
| 最终 Go2 路由 | `192.168.123.161 dev eth0 src 192.168.123.222` |

环境变量：

```text
ROS_DOMAIN_ID: unset
CYCLONEDDS_URI: unset
UNITREE_DOMAIN_ID: unset
UNITREE_ROBOT_IP: unset
GO2_ROBOT_IP: unset
```

本轮没有使用固定 Peer、没有生成 CycloneDDS XML，也没有修改 Domain 或接口。

## 3. Go2 运行状态

| 项目 | 结果 |
| --- | --- |
| 在线状态 | 在线；无线视频和有线 SDK2 均已通过 |
| 测试网络模式 | 先测试 Go2 AP，最终切换到 Ethernet |
| AP SSID | `Go2_57838_ea9717f3`，有线测试时 Wi-Fi 已断开 |
| Go2 IP | AP `192.168.12.1`；Ethernet `192.168.123.161` |
| 硬件版本 | `V2.0`，沿用 Phase 5.1.0 基线 |
| 机器人软件版本 | `V1.1.14`，沿用 Phase 5.1.0 基线 |
| Unitree Go App | `v1.12.7 c`，沿用 Phase 5.1.0 基线 |
| 当前运动模式 | 未读取；本轮没有进入或切换运动模式 |
| 开发/SDK 开关 | 现有 App 记录未确认存在；本轮没有切换任何开关 |

视频回归已证明 Go2 的 AP 网络、`9991` 信令和 WebRTC 视频服务正常，但视频
链路与 SDK2 DDS/RTPS 是不同协议路径。

## 4. 官方 SDK2 示例核对

### 4.1 LowState 官方文件原样运行

官方文件：

```text
unitree_sdk2py/test/lowlevel/read_lowstate.py
```

该文件把接口示例值硬编码为：

```python
ChannelFactoryInitialize(0, "enp2s0")
```

官方当前上游文件也要求使用者把 `enp2s0` 替换为实际接口。本轮遵守“不修改
代码/配置”，没有编辑文件或更改网卡名。原样运行结果：

```text
enp2s0: does not match an available interface
[ChannelFactory] create domain error
Exception: channel factory init error
```

因此原始文件本身无法在当前 `eth1` 环境完成初始化；这不是 Go2 样本结论。

### 4.2 SportModeState 官方只读文件原样运行

本地官方仓库没有独立的 Go2-only SportModeState 只读文件；Go2 high-level
示例会创建 `SportClient`，按安全边界没有运行。

本轮运行官方仓库中的纯订阅文件：

```text
example/a2/sport/a2_sport_state.py eth1
topic: rt/lf/sportmodestate
type: unitree_go.msg.dds_.SportModeState_
duration: 15 seconds
```

该文件只创建 `ChannelSubscriber`。运行期间初始化成功、进程保持正常，但没有
打印任何回调样本：

```text
samples: 0
frequency: 0 Hz
```

### 4.3 官方 API 等价计数

为消除 LowState 文件硬编码接口和无限循环带来的限制，使用不落盘的计数外壳，
直接调用官方 SDK 的：

```text
ChannelFactoryInitialize(0, "eth1")
ChannelSubscriber
LowState_
SportModeState_
```

没有使用 go2-gateway，也没有创建 publisher。结果：

| Topic | 初始化 | 时长 | 样本数 | 频率 | 首样本 |
| --- | --- | ---: | ---: | ---: | --- |
| `rt/lowstate` | 成功 | 15 s | 0 | 0 Hz | null |
| `rt/sportmodestate` | 成功 | 15 s | 0 | 0 Hz | null |

## 5. Participant 与 Topic 发现

使用 SDK 创建的 Domain 0 / `eth1` participant 读取 CycloneDDS 内置 discovery
表 10 秒。

| 类别 | 数量 | 说明 |
| --- | ---: | --- |
| Participant | 1 | 仅本机 participant |
| Publication | 0 | 未发现远端 publication |
| Topic | 0 | 未发现远端 topic |
| Subscription | 4 | 本机 DCPS 内置元数据 reader |

本机内置 subscription：

```text
DCPSParticipant
DCPSPublication
DCPSSubscription
DCPSTopic
```

未发现：

```text
Go2 remote participant
rt/lowstate
rt/sportmodestate
rt/lf/lowstate
rt/lf/sportmodestate
```

## 6. 同步只读抓包

先确认 `tcpdump` 监听 `eth1`，再运行 15 秒官方 SDK API 状态订阅。过滤范围为
Go2 `192.168.12.1` 与 DDS discovery 组播。

```text
3 packets captured
3 packets received by filter
0 packets dropped by kernel
```

方向统计：

| 方向 | 数量 | 类型 |
| --- | ---: | --- |
| `192.168.12.62 -> 239.255.0.1:7400` | 3 | 本机 RTPS discovery |
| `192.168.12.1 -> PC/multicast` | 0 | Go2 RTPS discovery/data |

未观察到任何源地址为 `192.168.12.1` 的 UDP/RTPS discovery 或状态数据。
发送侧 `bad udp cksum` 是 checksum offload 的常见抓包表现，不影响方向判定。

## 7. 官方网络路径边界

Unitree 官方实机连接说明公开记录的 SDK2/DDS 路径是：

```text
PC -- Ethernet -- Go2
PC interface: 192.168.123.99/24
Domain: 0
CycloneDDS interface: connected Ethernet adapter
```

参考：

- [Unitree ROS2 / SDK2 官方连接说明](https://github.com/unitreerobotics/unitree_ros2)
- [Unitree SDK2 Python 官方仓库](https://github.com/unitreerobotics/unitree_sdk2_python)

当前 E5576 WLAN 和 Go2 AP 都不是上述官方文档验证的以太网 DDS 路径。完成
无线诊断后接入了网线；Windows 和 WSL 已存在正确的静态地址与直连路由，
因此没有修改网络配置。

## 8. 官方有线开发链路 A/B

### 8.1 网络

| 项目 | 实测值 |
| --- | --- |
| Windows 网卡 | Realtek PCIe GbE，`以太网` |
| Windows IP | `192.168.123.222/24` |
| WSL 接口 | `eth0` |
| WSL IP | `192.168.123.222/24` |
| Go2 IP | `192.168.123.161` |
| Go2 MAC | `7e:1d:75:60:f5:89` |
| WSL 路由 | `192.168.123.161 dev eth0 src 192.168.123.222` |
| 邻居状态 | `REACHABLE` |

Windows ping：

```text
20 sent / 20 received
0% packet loss
average RTT: <1 ms
```

WSL `eth0` ping：

```text
20 transmitted / 20 received
0% packet loss
RTT min/avg/max/mdev: 0.396/0.696/1.133/0.231 ms
```

### 8.2 官方 SportModeState 示例

运行：

```text
example/a2/sport/a2_sport_state.py eth0
```

5 秒内收到 59 个实际回调样本，输出包含：

```text
Position: real values
Velocity: real values
Mode: PASSIVE
Progress: 0.0
```

该示例只读取 `rt/lf/sportmodestate`，没有创建控制客户端。

### 8.3 官方 SDK API 四 topic 计数

10 秒结果：

| Topic | 样本数 | 频率 |
| --- | ---: | ---: |
| `rt/lowstate` | 4837 | 483.33 Hz |
| `rt/lf/lowstate` | 194 | 19.39 Hz |
| `rt/sportmodestate` | 2873 | 287.08 Hz |
| `rt/lf/sportmodestate` | 194 | 19.39 Hz |

两个状态类型及其低频 `lf` topic 全部存在，数据持续更新。

### 8.4 Discovery

5 秒内置 discovery：

| 类别 | 数量 |
| --- | ---: |
| Participant | 25 |
| Publication | 96 |
| Subscription | 97 |

明确发现：

```text
rt/lowstate
type: unitree_go::msg::dds_::LowState_

rt/lf/lowstate
type: unitree_go::msg::dds_::LowState_
```

SportModeState 的真实存在由官方示例和两个 sport topic 的持续样本直接证明。

### 8.5 同步抓包

同步轮次的 5 秒订阅：

```text
rt/lowstate: 2366 samples
rt/sportmodestate: 1405 samples
```

抓包：

```text
200 packets captured
573 packets received by filter
0 packets dropped by kernel
```

方向统计：

| 方向 | 捕获数量 |
| --- | ---: |
| Go2 `192.168.123.161 -> PC` | 131 |
| PC `192.168.123.222 -> Go2/multicast` | 69 |

有线模式与无线/AP 模式形成明确 A/B：

| 项目 | E5576 WLAN | Go2 AP | Ethernet |
| --- | --- | --- | --- |
| IP ping | PASS | PASS | PASS |
| 视频 | PASS | PASS | 未作为本轮门禁 |
| Remote participant | 不可见 | 不可见 | 可见，25 participants |
| LowState | 0 | 0 | >0 |
| SportModeState | 0 | 0 | >0 |
| Go2 入站 RTPS | 0 | 0 | >0 |

## 9. 最终判断

有线 A/B 将最终结果更新为情况 B：

```text
Official SDK/API initialization on eth0: PASS
Official read-only state samples: PASS
Remote participants/publications: FOUND
Inbound Go2 RTPS: FOUND
```

最终确认：

1. Go2 内部 SDK2 DDS 服务和状态 publisher 正在运行；
2. `unitree_sdk2py 1.0.1` 与 CycloneDDS `0.10.2` 可以在本机 Domain 0 /
   `eth0` 正常接收真实硬件数据；
3. E5576 WLAN 与 Go2 AP 的失败不是 SDK 安装、IDL 类型、Domain 0 或 Go2
   DDS 服务未启动造成；
4. 当前 Go2 固件只在官方以太网开发接口暴露 SDK2 DDS，WLAN/AP 可用于
   ping/WebRTC，但不能替代 SDK2 DDS 有线链路；
5. go2-gateway 自定义业务逻辑不是本次无线失败的根因，关键变量是物理接口
   和网络模式。

本阶段停止，不进入 Phase 5.1.4 L1 或 Phase 5.2 ROS2 Bridge。
