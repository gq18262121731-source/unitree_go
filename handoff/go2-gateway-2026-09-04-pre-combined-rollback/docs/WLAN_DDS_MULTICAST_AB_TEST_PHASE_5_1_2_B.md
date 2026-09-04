# WLAN DDS Multicast A/B Test — Phase 5.1.2-B

首次采集日期：2026-07-24

抓包复测日期：2026-07-25

固定拓扑：

```text
E5576 WLAN
    |
    +-- PC 192.168.8.254
    |       |
    |      WSL2 eth1
    |
    +-- Go2 192.168.8.250
```

状态：

```text
UNICAST_NETWORK_PASS
LOCAL_CYCLONEDDS_MULTICAST_JOIN_PASS
REMOTE_GO2_DDS_DISCOVERY_FAIL
PACKET_CAPTURE_COMPLETE
GO2_INBOUND_UDP_OBSERVED=0
ROUTER_MULTICAST_POLICY_PENDING
```

## 1. 测试变量更正

本阶段不测试 Go2 自身 AP 热点。PC 和 Go2 始终加入同一个外部 WLAN。

A/B 变量是：

- A：当前 E5576 配置；
- B1：E5576 关闭 WLAN/AP/Client Isolation 或 multicast filtering 后；
- B2：换用明确允许同网客户端 multicast 的简单路由器。

不得同时改变 SDK、Domain、Topic、QoS、Provider 或安全门禁，否则无法
归因。

## 2. A 组已知结果

| 项目 | 结果 |
| --- | --- |
| SSID | `E5576-822_D7E5` |
| AP BSSID | `4c:81:25:1c:d7:e5` |
| PC IP | `192.168.8.254/24` |
| Go2 IP | `192.168.8.250` |
| WSL 接口 | `eth1` |
| Windows ping | 20/20，0% 丢包 |
| WSL ping | 20/20，0% 丢包 |
| ARP/邻居 | `94:ba:06:f8:e5:1f`，Reachable |
| 视频无线链路 | 用户确认可用 |
| CycloneDDS 初始化 | 成功 |
| LowState | 0 样本 |
| SportModeState | 0 样本 |
| 远端 participant/publication | 未发现 |

单播、ARP 与视频通过，说明 WLAN 基础链路正常，但不能证明 DDS multicast
可用。

## 3. Windows multicast joins

在 WSL DDS Subscriber 活跃期间执行：

```powershell
netsh interface ip show joins
```

Windows WLAN 显示：

```text
224.0.0.1
224.0.0.251
224.0.0.252
239.255.255.250
```

Windows WLAN 列表中未显示：

```text
239.255.0.1
```

同一时刻 WSL `eth1` 明确显示：

```text
239.255.0.1 users 2
```

这说明 Linux/CycloneDDS 参与者确实加入了 discovery 组。WSL mirrored
networking 的 Linux 组成员不一定作为同名 Windows WLAN join 展示，因此
不能仅凭 Windows 列表缺少 `239.255.0.1` 判定组播失败。

微软官方 WSL 网络说明将 multicast support 列为 mirrored networking 的
能力：

https://learn.microsoft.com/windows/wsl/networking

## 4. E5576 隔离判断

完全的无线客户端隔离可能性较低，因为：

- PC 可以直接 ping Go2；
- PC 能解析 Go2 MAC；
- WSL 可以直接 ping Go2；
- 视频单播链路可用。

但仍不能排除 E5576：

- 丢弃 WLAN 客户端之间的 UDP multicast；
- 开启 multicast filtering；
- 对广播/组播做节能抑制或代理；
- 只允许部分常见 multicast 协议。

需要登录 E5576 管理页面只读检查以下设置：

```text
AP Isolation
Client Isolation
WLAN Isolation
Multicast filtering
IGMP snooping/proxy
```

任何设置变更必须由用户明确执行并记录原值、修改值与恢复方式。Codex 本次
没有登录路由器或修改配置。

## 5. 抓包权限

WSL 已安装：

```text
/usr/sbin/tcpdump
```

抓包前状态：

```text
运行用户: est1
tcpdump capability: 无
sudo -n: password required
```

Codex 不能无交互捕获接口数据。用户随后在可见 WSL 终端中自行输入 sudo
密码完成了前台抓包；密码没有经过 Codex，也没有修改 tcpdump capability、
sudo 配置或防火墙。

## 6. 已执行的前台只读抓包

用户在 WSL 终端 1 手动输入 sudo 密码并执行：

```bash
sudo timeout 60 tcpdump -ni eth1 -vv -c 300 \
  'udp and (host 192.168.8.250 or dst host 239.255.0.1)'
```

在 WSL 终端 2 同时运行现有只读 Subscriber：

```bash
cd "/mnt/e/笨笨狗/go2_dev/go2-gateway"
/home/est1/.venvs/go2-gateway/bin/python tools/dds_diagnostics.py \
  --interface eth1 \
  --domain-id 0 \
  --peer 192.168.8.250 \
  --timeout 15
```

这两个命令不保存大文件，不创建应用 Publisher，不调用 SportClient 或
运动接口。

### 抓包结果

第一次没有与 DDS 探针并发：

```text
0 packets captured
0 packets received by filter
0 packets dropped by kernel
```

第二次在抓包窗口显示 `listening on eth1` 后，由 Codex 立即运行 15 秒只读
DDS Subscriber。结果：

```text
20 packets captured
20 packets received by filter
0 packets dropped by kernel
```

方向统计：

| 方向 | 数量 | 说明 |
| --- | ---: | --- |
| `192.168.8.254 -> 239.255.0.1:7400` | 2 | 本机 DDS multicast discovery |
| `192.168.8.254 -> 192.168.8.250:7410-7426` | 18 | 本机向固定 Peer 的单播 discovery 探测 |
| `192.168.8.250 -> 192.168.8.254/*` | 0 | 未观察到 Go2 UDP 回复 |
| `192.168.8.250 -> multicast/*` | 0 | 未观察到 Go2 multicast |

每轮单播探测覆盖：

```text
7410, 7412, 7414, 7416, 7418, 7420, 7422, 7424, 7426
```

tcpdump 对本机发出的包显示 `bad udp cksum`，这与发送侧 checksum offload
的常见抓包表现一致；它不改变包的来源、目的地、端口和“没有 Go2 入站包”
这一方向性结论。

并发 DDS Subscriber 结果仍为：

```text
DDS initialized: true
LowState: 0 samples
SportModeState: 0 samples
Error: DDS_NO_ROBOT_SAMPLES
```

### 同步复测

2026-07-25 08:34，再次执行严格同步的 22 秒抓包与 15 秒只读订阅。
启动顺序为：先确认 `tcpdump` 正在监听 `eth1`，再启动 DDS Subscriber。

抓包结果：

```text
30 packets captured
30 packets received by filter
0 packets dropped by kernel
```

方向统计：

| 方向 | 数量 | 说明 |
| --- | ---: | --- |
| `192.168.8.254 -> 239.255.0.1:7400` | 3 | 本机 DDS multicast discovery |
| `192.168.8.254 -> 192.168.8.250:7410-7426` | 27 | 本机向固定 Peer 的单播 discovery 探测 |
| `192.168.8.250 -> 192.168.8.254/*` | 0 | 未观察到 Go2 UDP 回复 |
| `192.168.8.250 -> multicast/*` | 0 | 未观察到 Go2 multicast |

并发只读订阅结果：

```text
DDS initialized: true
LowState: 0 samples
SportModeState: 0 samples
Error: DDS_NO_ROBOT_SAMPLES
```

同步复测与第一次有效抓包方向一致，排除了抓包与订阅启动时序不足导致的
假阴性。当前结论保持不变：PC/WSL 的 discovery 已发出，但没有观察到任何
来自 Go2 `192.168.8.250` 的 UDP/RTPS 响应。

### 手机 App 关闭 A/B

2026-07-25 08:46，在手机 App 完全退出后再次执行相同的严格同步测试。

网络基线：

```text
Windows -> Go2: 4/4
Packet loss: 0%
RTT min/max/avg: 2/6/4 ms
```

抓包结果：

```text
30 packets captured
30 packets received by filter
0 packets dropped by kernel
```

方向统计仍为：

| 方向 | 数量 |
| --- | ---: |
| `192.168.8.254 -> 239.255.0.1:7400` | 3 |
| `192.168.8.254 -> 192.168.8.250:7410-7426` | 27 |
| `192.168.8.250 -> PC/multicast` | 0 |

只读订阅仍为：

```text
LowState: 0 samples
SportModeState: 0 samples
Error: DDS_NO_ROBOT_SAMPLES
```

App 开启与关闭时结果相同。因此，手机 App 占用控制会话或视频带宽不是本次
DDS discovery 完全无响应的主要原因。

## 7. 抓包判定

### 结果 A：只有 PC/WSL 发往 discovery 的包

例如看到本机发往：

```text
239.255.0.1
```

但没有任何：

```text
src 192.168.8.250 UDP/RTPS
```

本次实测符合此结果，而且固定 Peer 配置还产生了发往 Go2 多个 DDS 端口的
单播探测。结论仍不能做到绝对唯一，但概率已进一步偏向：

1. Go2 SDK2 DDS 服务没有在 `wlan0` / Domain 0 上监听或发布；
2. Go2 当前 WLAN 网络模式不支持 SDK2 DDS；
3. Go2 DDS 服务未运行或被设备侧策略阻断。

E5576 丢弃客户端 multicast 仍不能完全排除，但“只过滤 multicast”不足以
解释固定 Peer 单播探测也没有任何 UDP 回复。路由策略 A/B 或替代路由测试
仍可作为最终对照。

### 结果 B：看到 Go2 发来的 RTPS/UDP

如果看到：

```text
src 192.168.8.250
UDP/RTPS
```

但 Built-in Discovery 仍没有远端 participant，则排查重点转向：

- Domain；
- DDS security；
- Topic type/IDL；
- QoS；
- CycloneDDS 解析或防火墙路径。

### 结果 C：策略或路由器更换后出现样本

如果只改变 WLAN multicast 策略或路由器后：

```text
LowState > 0
SportModeState > 0
```

则可以判定当前 E5576 网络策略阻断或抑制 DDS discovery，SDK 与 Go2
DDS 服务本身可用。

### 结果 D：换路由后仍为 0

如果单播正常、multicast 允许，但仍无远端 RTPS/participant，则优先检查：

- Go2 WLAN 模式是否发布 SDK2 DDS；
- Go2 DDS 服务状态；
- Go2 实际 Domain；
- 固件与 SDK2 兼容性。

## 8. 当前门禁

```text
Phase 5.1.1 network: PASS
Phase 5.1.2 local DDS: PASS
Phase 5.1.2 remote discovery: FAIL
Phase 5.1.2-B packet capture: COMPLETE
Phase 5.1.2-B inbound Go2 UDP: NONE OBSERVED
Phase 5.1.2-B router policy A/B: PENDING
```

继续禁止：

- 修改 Provider、Mock API 或导航代码；
- 进入 L1、IMU、ROS2、Nav2 或 SLAM；
- 创建 SportClient；
- 发布 DDS 业务消息；
- 调用任何运动接口。
