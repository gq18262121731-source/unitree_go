# Network Report — Phase 5.1.1 WLAN

采集日期：2026-07-24

网络模式：WLAN

目标：

```text
Windows / WSL2 -> Go2 192.168.8.235
```

结论：

```text
PHASE_5_1_1_PASSED
GO2_WLAN_ONLINE
CURRENT_GO2_WLAN_IP=192.168.8.250
DDS_READONLY_GATE_OPEN
```

## 最新在线复测（最终判定）

用户确认 Go2 App 已更新为：

```text
网络状态: 在线
wlan0: 192.168.8.250
```

Windows 复测：

```text
PC WLAN: 192.168.8.254/24
Go2: 192.168.8.250
Ping: 20 sent, 20 received, 0% loss
RTT: min 3 ms, avg 16 ms, max 72 ms
ARP: 94-BA-06-F8-E5-1F, Reachable
```

WSL2 复测：

```text
Interface: eth1, 192.168.8.254/24
Route: 192.168.8.250 dev eth1 src 192.168.8.254
Ping: 20 sent, 20 received, 0% loss
RTT: min 3.767 ms, avg 22.557 ms, max 138.707 ms
Neighbor: 94:ba:06:f8:e5:1f, REACHABLE
```

Windows 和 WSL2 均已收到来自 Go2 当前地址的 Echo Reply，且二层邻居解析
正常。Phase 5.1.1 WLAN 网络门禁通过，可以进入 Phase 5.1.2 DDS 只读
验证。本节复测期间仍未初始化 DDS。

## 0. App 截图更正（优先于后文旧地址记录）

用户随后提供的 Go2 App 网络信息截图显示：

```text
连接模式: Wi-Fi
网络状态: 离线
wlan0: 192.168.8.250
wlan1: 空
wwan0: 10.175.216.141
```

据此修正：

- `192.168.8.235` 是已经过期的历史地址。
- `192.168.8.250` 是 App 页面显示的 wlan0 地址，但在“网络状态：离线”
  的条件下，只能视为历史或缓存地址，不能视为当前可达地址。
- `wwan0` 显示 `10.175.216.141`，说明蜂窝接口有地址记录；仅凭截图不能
  进一步断言当前业务流量一定经过该接口。
- 当前没有可用于 Phase 5.1.2 的已确认 Go2 WLAN 地址。

后文针对 `192.168.8.235` 的 ping 结果仅保留为原始诊断证据，该地址不再
作为当前测试目标。在 Unitree App 显示“网络状态：在线”并给出新的
`wlan0` 地址前，不继续 ping `192.168.8.235` 或 `192.168.8.250`，也不
初始化 DDS。

## 1. 安全边界

本次只读取网卡、IP、路由、邻居表，并执行 ICMP ping。

- 未初始化 CycloneDDS。
- 未订阅或发布 DDS topic。
- 未启动完整 gateway。
- 未启动 ROS2、Nav2 或 SLAM。
- 未调用 Sport Client 或任何运动接口。
- 未修改 Windows、WSL2、路由器、Go2 或 L1 网络配置。

## 2. Windows WLAN

| 字段 | 实测值 |
| --- | --- |
| 接口 | `WLAN` |
| 适配器 | Intel(R) Wi-Fi 6 AX201 160MHz |
| 状态 | `Up` |
| 链路速率 | `229 Mbps` |
| IPv4 | `192.168.8.254/24` |
| 地址状态 | `Preferred` |
| 默认网关 | `192.168.8.1` |
| Go2 目标地址 | `192.168.8.235` |
| 是否同网段 | 是 |

## 3. Windows ping

命令：

```powershell
ping -n 20 -w 1000 192.168.8.235
```

结果：

| 指标 | 结果 |
| --- | --- |
| 发送 | 20 |
| Go2 Echo Reply | 0 |
| 请求超时 | 11 |
| 本机 Destination Host Unreachable | 9 |
| 有效 Echo 丢包率 | 100% |
| 平均延迟 | 无法计算 |

Windows 原始摘要显示 `Received = 9, Lost = 11 (55% loss)`，但这 9 个
“Received”是本机 `192.168.8.254` 返回的 `Destination host unreachable`，
不是 Go2 `192.168.8.235` 的 Echo Reply，因此不能视为 45% 成功。

邻居表：

```text
IPAddress:        192.168.8.235
LinkLayerAddress: 00-00-00-00-00-00
State:            Incomplete
```

这表示本机没有解析到目标地址对应的 WLAN 二层邻居。

## 4. WSL2 WLAN

WSL2 使用 mirrored networking。当前 WLAN 映射接口不是 `eth0`，而是
`eth1`：

```text
eth0  DOWN
eth1  UP  192.168.8.254/24
```

默认路由：

```text
default via 192.168.8.1 dev eth1
```

目标路由：

```text
192.168.8.235 dev eth1 src 192.168.8.254
```

判定：WSL2 对目标地址的接口选择和三层路由正确，没有错误走 Docker、
有线接口或其他网段。

## 5. WSL2 ping

命令：

```bash
ping -c 10 -W 1 192.168.8.235
```

结果：

| 指标 | 结果 |
| --- | --- |
| 发送 | 10 |
| Go2 Echo Reply | 0 |
| 本机 Host Unreachable | 4 |
| 有效丢包率 | 100% |
| 平均延迟 | 无法计算 |

## 6. 判定

已经确认：

- Windows WLAN 正常启用。
- Windows 与目标记录地址处于同一 `/24` 网段。
- WSL2 已正确镜像 WLAN 地址。
- WSL2 到目标地址的路由明确走 `eth1`。

尚未确认：

- Go2 当前是否在线并连接到同一个 WLAN。
- Go2 当前 WLAN IP 是否仍为 `192.168.8.235`。
- WLAN 是否启用了客户端隔离。

当前证据不足以说明 PC/WSL2 与 Go2 之间存在可用 IP 链路。由于基础
ICMP 和邻居解析均未通过，不启动 DDS；CycloneDDS 网卡绑定、
`CYCLONEDDS_URI` 和 topic 订阅检查全部延期。

## 7. 下一门禁

在不修改任何网络配置的前提下，先从 Unitree App 或路由器在线客户端
列表重新确认：

```text
Go2 当前在线状态
Go2 当前 WLAN IP
PC 与 Go2 是否连接同一 SSID
```

只有 Windows 和 WSL2 都收到来自 Go2 实际地址的 Echo Reply 后，才进入
Phase 5.1.2 DDS 只读订阅验证。
