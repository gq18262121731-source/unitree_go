# DDS Readonly Report — Phase 5.1.2 WLAN

采集日期：2026-07-24

网络模式：WLAN

链路：

```text
Windows / WSL2 192.168.8.254
        ->
Go2 wlan0 192.168.8.250
```

结论：

```text
PHASE_5_1_2_NO_ROBOT_DDS_SAMPLES
DDS_RUNTIME_INITIALIZED
REMOTE_GO2_DDS_TOPICS_NOT_DISCOVERED
LIDAR_AND_IMU_GATES_CLOSED
```

## 1. 前置网络门禁

Phase 5.1.1 已通过：

| 路径 | 结果 |
| --- | --- |
| Windows -> Go2 | 20/20，0% 丢包，平均 16 ms |
| WSL2 -> Go2 | 20/20，0% 丢包，平均 22.557 ms |
| Windows ARP | `94-BA-06-F8-E5-1F`，Reachable |
| WSL2 邻居 | `94:ba:06:f8:e5:1f`，REACHABLE |
| WSL2 接口 | `eth1`，`192.168.8.254/24` |
| WSL2 目标路由 | `192.168.8.250 dev eth1 src 192.168.8.254` |

## 2. DDS 环境

| 检查 | 结果 |
| --- | --- |
| `CYCLONEDDS_URI` | 未设置 |
| 其他包含 DDS 的环境变量 | 未发现 |
| Python hostname | `Test` |
| Python hostname 解析 | `127.0.1.1` |
| CycloneDDS import | 成功 |
| CycloneDDS | `0.10.2` |
| Unitree SDK | `unitree_sdk2py 1.0.1` |
| 显式绑定接口 | `eth1` |
| Domain ID | `0` |
| Peer | `192.168.8.250` |

由于 hostname 自动解析为 loopback，本次没有使用自动接口选择，明确将
CycloneDDS 绑定到当前 WLAN 镜像接口 `eth1`。

## 3. 只读边界审查

执行前检查了 `tools/dds_diagnostics.py`：

- 只创建 `ChannelSubscriber`。
- 不导入或创建 `ChannelPublisher`。
- 不创建 DataWriter。
- 不导入或创建 SportClient。
- 不包含 `move()`、`cmd_vel` 或运动 API。
- 不启动 gateway、ROS2、Nav2 或 SLAM。

DDS Built-in Discovery 只用于读取 DDS 元数据。没有创建应用业务 Topic
Publisher。

## 4. LowState

订阅候选：

```text
rt/lf/lowstate
rt/lowstate
```

结果：

| 字段 | 结果 |
| --- | --- |
| Subscriber 创建 | 成功 |
| 首样本 | 未收到 |
| 样本数 | 0 |
| 频率 | 无法计算 |
| 首样本时间 | null |
| 末样本时间 | null |
| 超时码 | `LOW_STATE_TIMEOUT` |

## 5. SportModeState

订阅候选：

```text
rt/lf/sportmodestate
rt/sportmodestate
```

结果：

| 字段 | 结果 |
| --- | --- |
| Subscriber 创建 | 成功 |
| 首样本 | 未收到 |
| 样本数 | 0 |
| 频率 | 无法计算 |
| 首样本时间 | null |
| 末样本时间 | null |
| 超时码 | `SPORT_STATE_TIMEOUT` |

整体错误：

```text
DDS_NO_ROBOT_SAMPLES
```

## 6. 实际 Topic 发现

使用 CycloneDDS Built-in Discovery 持续枚举 10 秒。只发现当前本机参与者
产生的内置元数据端点：

| Topic | Type | 发现来源 |
| --- | --- | --- |
| `DCPSPublication` | `org::eclipse::cyclonedds::builtin::DCPSPublication` | local subscription |
| `DCPSSubscription` | `org::eclipse::cyclonedds::builtin::DCPSSubscription` | local subscription |
| `DCPSTopic` | `org::eclipse::cyclonedds::builtin::DCPSTopic` | local subscription |

未发现：

- Go2 远端 DDS publication；
- `rt/lowstate`；
- `rt/sportmodestate`；
- 任何远端 `rt/*` topic；
- 任何 L1/UT LiDAR topic；
- 任何远端 IMU、pose 或 odometry topic。

这里的“未发现”是本次 WLAN + Domain 0 + 显式 `eth1` 绑定条件下的实测
结果，不代表设备在其他接口或运行模式下不存在这些 topic。

## 7. 判定

已经证明：

- PC 和 Go2 的普通 WLAN IP 链路可用。
- WSL2 路由与接口选择正确。
- CycloneDDS 本地 runtime 可以在 `eth1` 初始化。
- LowState 与 SportModeState Subscriber 可以创建。

尚未证明：

- Go2 在 `wlan0` 上发送 SDK2 RTPS/DDS discovery；
- Go2 在 WLAN 上发布 LowState 或 SportModeState；
- L1、IMU、pose 或 odometry 数据在 WLAN 上可见。

因此不能把“ping 成功”解释为“DDS 链路成功”。当前证据与此前 WLAN
诊断一致：普通 IP 单播可达，但没有收到 Go2 SDK2 DDS 样本。

## 8. 未调用运动证明

本次没有：

- 调用 `move()`；
- 发送 velocity 或 `cmd_vel`；
- 创建 Sport Client；
- 发布 DDS 业务消息；
- 启动 ROS2、Nav2、SLAM、导航、巡逻或返航；
- 修改 Go2、L1 或网络配置。

## 9. 后续门禁

Phase 5.1.2 当前未通过，停止在此处：

```text
Phase 5.1.3 L1 LiDAR: 未进入
Phase 5.1.4 IMU/里程计: 未进入
Phase 5.2 ROS2 Bridge: 禁止进入
```

继续排查前需要获得 WLAN 上是否存在 Go2 RTPS 流量的抓包证据，或确认
该 Go2 固件/网络模式是否仅在有线 SDK2 接口发布 DDS。没有新证据前，不
修改 topic 名称、QoS、TTL、readiness 门禁或运动安全逻辑。
