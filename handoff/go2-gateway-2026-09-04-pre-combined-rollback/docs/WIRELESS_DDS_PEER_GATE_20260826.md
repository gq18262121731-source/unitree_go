# Go2 无线 DDS 显式 Peer 最终只读 Gate

## 目的

在不创建 Publisher、SportClient 或运动命令的前提下，对当前 Go2 无线链路执行以下四个组合：

```text
eth2 + Domain 0  + multicast + explicit Peer
eth2 + Domain 0  + unicast-only explicit Peer
eth2 + Domain 10 + multicast + explicit Peer
eth2 + Domain 10 + unicast-only explicit Peer
```

每个组合在独立 Python 进程中运行，因为 Unitree `ChannelFactory` 是进程单例，初始化后不能可靠切换 Domain。任意组合收到 `LowState` 或 `SportModeState` 样本后立即停止矩阵。

## 重要实现边界

本机 `unitree_sdk2py.core.channel.ChannelFactory.Init()` 将 SDK 内置 XML 字符串直接传给 `cyclonedds.domain.Domain(id, config)`。因此，本 Gate 在初始化前修改该进程内 XML；仅设置外部 `CYCLONEDDS_URI` 不能证明该 SDK 路径使用了实验配置。

只读保证：

- 只运行 `tools/dds_diagnostics.py`；
- 只创建 `ChannelSubscriber`；
- 不创建 `ChannelPublisher` 或应用 DataWriter；
- 不导入或初始化 `SportClient`；
- 不调用 `Move`、`StandUp`、`StopMove` 或任何运动 API；
- 不修改 Go2、E5576 或 Windows 网络配置。

## 与既有证据的关系

2026-07 已在同一 `E5576-822_D7E5`、同一 Go2 MAC `94:ba:06:f8:e5:1f` 上完成 Domain 0 + 显式 Peer 的并发抓包。结果显示 PC 的 multicast discovery 与发往 Go2 多个 DDS 端口的 unicast discovery 均已发出，但 Go2 入站 UDP/RTPS 为 0。

当前再次验证时，动态地址变为：

```text
PC/WSL eth2: 192.168.8.254
Go2:         192.168.8.252
```

因此本矩阵的主要新增信息是 pure-unicast 显式开关和 Domain 10。社区中 Domain 10 成功案例主要涉及 Go2 EDU 机载 Jetson/qre_go2 ROS2 转发，不能预先等同于 Go2 内置无线 SDK2。

## 运行

保持网线拔除、Companion `IDLE`、无其他 DDS/SportClient 工具运行，在 WSL 仓库目录执行：

```bash
python3 tools/wireless_dds_peer_gate.py \
  --interface eth2 \
  --peer 192.168.8.252 \
  --timeout 12
```

最长约 48 秒。退出码 `0` 表示至少一个组合收到状态样本；退出码 `1` 表示四个组合全部为 0 或探针失败。

## 判定

### 任意组合样本数大于 0

状态记为：

```text
WIRELESS_DDS_SAMPLES_DETECTED
```

立即停止继续扫描。先验证 SportClient request endpoint 匹配，再由人工重新批准是否执行 0.20 m 真机动作。

### 四个组合全部为 0

状态记为：

```text
WIRELESS_DDS_NO_SAMPLES_IN_MATRIX
```

冻结当前无线 SDK2 DDS 路线，不执行运动。结合 2026-07 的抓包证据，优先判定为当前 Go2 内置无线网络模式未向该 WLAN 暴露可响应的 SDK2 DDS participant，或设备侧策略阻断；不修改 Scripted Motion、伴随控制、Topic、QoS 或安全门禁。

## 2026-08-26 实测结果

执行环境：

```text
interface: eth2 / 192.168.8.254
peer:      192.168.8.252
Go2 MAC:   94:ba:06:f8:e5:1f
WLAN:      E5576-822_D7E5
timeout:   12 seconds per combination
```

结果：

| Domain | Discovery | DDS 初始化 | SportModeState | LowState | 结果 |
|---:|---|---|---:|---:|---|
| 0 | multicast + Peer | 成功 | 0 | 0 | `DDS_NO_ROBOT_SAMPLES` |
| 0 | unicast-only Peer | 成功 | 0 | 0 | `DDS_NO_ROBOT_SAMPLES` |
| 10 | multicast + Peer | 成功 | 0 | 0 | `DDS_NO_ROBOT_SAMPLES` |
| 10 | unicast-only Peer | 成功 | 0 | 0 | `DDS_NO_ROBOT_SAMPLES` |

工具安全字段：

```text
subscriberOnly: true
publisherCreated: false
sportClientCreated: false
motionCommandsSent: false
stoppedOnFirstSamples: false
```

最终状态：

```text
WIRELESS_DDS_NO_SAMPLES_IN_MATRIX
```

### 最终判定

当前 Go2 + E5576 + WSL `eth2` 的内置无线 SDK2 DDS 路径冻结为：

```text
WIRELESS_IP_UNICAST=PASS
WIRELESS_DDS_DOMAIN_0=FAIL
WIRELESS_DDS_DOMAIN_10=FAIL
WIRELESS_SPORTCLIENT_GATE=NOT_ENTERED
WIRELESS_REAL_MOTION=PROHIBITED
```

四组合结果与 2026-07 同一 WLAN、同一 Go2 MAC 的抓包证据一致。当前没有依据继续创建 SportClient、检查 UWB/LiDAR 或执行 0.20 m 运动。只有网络拓扑或机器人侧无线 DDS 暴露方式发生实质变化时，才重新开启此 Gate。
