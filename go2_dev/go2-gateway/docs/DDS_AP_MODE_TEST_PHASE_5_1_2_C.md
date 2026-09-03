# Phase 5.1.2-C：Go2 AP 模式 DDS Discovery A/B 对照报告

测试日期：2026-07-25  
测试模式：Go2 自身 AP，SDK2 DDS 只读  
结论：网络通过，远端 DDS discovery 未通过

## 1. 范围与安全边界

本轮仅验证 Go2 自身 AP 网络模式下是否能恢复 SDK2 DDS discovery 和状态读取。

本轮未执行：

- 业务代码、Provider、Mock、`health_new` 或 CycloneDDS 持久配置修改；
- ROS2、Nav2、SLAM 或 L1 测试；
- DDS publisher 或业务 topic 发布；
- `SportClient`、`move()` 或任何机器人控制命令。

## 2. Go2 AP 信息

| 项目 | 实测值 |
| --- | --- |
| AP SSID | `Go2_57838_ea9717f3` |
| AP BSSID | `96:ba:06:f8:e5:1f` |
| 网络模式 | Go2 自身 AP，5 GHz / 802.11ac |
| Go2 IP | `192.168.12.1` |
| IP 确认方式 | Windows WLAN 邻居项 MAC 与 AP BSSID 完全一致 |
| 信号 | 93%，RSSI `-40 dBm` |
| 硬件版本 | `V2.0`，沿用 Phase 5.1.0 基线 |
| 机器人软件版本 | `V1.1.14`，沿用 Phase 5.1.0 基线 |
| Unitree Go App | `v1.12.7 c`，沿用 Phase 5.1.0 基线 |

本阶段没有升级或修改固件，也没有重新写入网络设置。

## 3. Windows 网络

| 项目 | 实测值 |
| --- | --- |
| 接口 | `WLAN` |
| 网卡 | Intel(R) Wi-Fi 6 AX201 160MHz |
| IPv4 | `192.168.12.62/24` |
| Default Gateway | 未下发 |
| Go2 邻居 | `192.168.12.1 -> 96-ba-06-f8-e5-1f` |
| 与 Go2 同网段 | 是 |

## 4. WSL2 网络

| 项目 | 实测值 |
| --- | --- |
| DDS 接口 | `eth1` |
| IPv4 | `192.168.12.62/24` |
| Go2 网段路由 | `192.168.12.0/24 dev eth1` |
| WSL 默认路由 | `default via 192.168.135.48 dev eth2` |

虽然 WSL 默认路由仍指向 `eth2`，但 Go2 AP 网段存在更具体的 `eth1`
直连路由。实际 WSL ping 和 DDS discovery 包均从 `eth1` 发出。

## 5. 基础网络测试

### Windows

```text
20 packets sent
20 packets received
0% packet loss
RTT min/max/avg: 2/5/2 ms
```

### WSL2

```text
20 packets transmitted
20 packets received
0% packet loss
RTT min/avg/max/mdev: 2.604/3.791/8.966/1.845 ms
```

判定：

```text
Windows -> Go2 AP: PASS
WSL eth1 -> Go2 AP: PASS
```

## 6. DDS 配置核对

| 项目 | 结果 |
| --- | --- |
| Domain ID | `0` |
| Network interface | `eth1` |
| `ROS_DOMAIN_ID` | 未设置 |
| `CYCLONEDDS_URI` | 未设置 |
| Unitree robot IP/Peer 环境变量 | 未设置 |
| 本轮固定 Peer 重写 | 未使用 |
| CycloneDDS 持久 XML 修改 | 无 |

本轮命令省略 `--peer`，因此诊断脚本没有调用 Peer 重写逻辑。DDS 仅使用
Domain 0、现有 `eth1` 和 SDK 原始接口配置。

## 7. DDS 只读验证

订阅候选：

```text
rt/lowstate
rt/lf/lowstate
rt/sportmodestate
rt/lf/sportmodestate
```

### LowState

| 项目 | 结果 |
| --- | --- |
| Subscriber 创建 | 成功 |
| 首样本 | 未收到 |
| 样本数 | 0 |
| 频率 | 无法计算 |
| 超时码 | `LOW_STATE_TIMEOUT` |

### SportModeState

| 项目 | 结果 |
| --- | --- |
| Subscriber 创建 | 成功 |
| 首样本 | 未收到 |
| 样本数 | 0 |
| 频率 | 无法计算 |
| 超时码 | `SPORT_STATE_TIMEOUT` |

整体结果：

```text
DDS initialized: true
Remote Go2 participant: not observed
LowState samples: 0
SportModeState samples: 0
Error: DDS_NO_ROBOT_SAMPLES
```

## 8. 同步抓包

在确认 `tcpdump` 已监听 `eth1` 后，运行 15 秒相同的只读订阅。过滤条件仅覆盖
Go2 AP IP 和 DDS discovery 组播。

```text
3 packets captured
3 packets received by filter
0 packets dropped by kernel
```

方向统计：

| 方向 | 数量 | 说明 |
| --- | ---: | --- |
| `192.168.12.62 -> 239.255.0.1:7400` | 3 | PC/WSL 发出的 DDS discovery |
| `192.168.12.1 -> PC/multicast` | 0 | 未观察到 Go2 UDP/RTPS |

发送侧显示的 `bad udp cksum` 属于 checksum offload 的常见抓包表现，不影响
来源、目的地及“没有 Go2 入站包”的方向判定。

## 9. 与 E5576 WLAN 模式对比

| 项目 | E5576 同 WLAN | Go2 自身 AP |
| --- | --- | --- |
| PC/WSL IP | `192.168.8.254` | `192.168.12.62` |
| Go2 IP | `192.168.8.250` | `192.168.12.1` |
| Ping | 通过 | 通过 |
| CycloneDDS 初始化 | 成功 | 成功 |
| Domain | 0 | 0 |
| 接口 | `eth1` | `eth1` |
| LowState | 0 | 0 |
| SportModeState | 0 | 0 |
| Go2 入站 UDP/RTPS | 0 | 0 |
| Remote participant | 未发现 | 未发现 |

AP 模式与 E5576 WLAN 模式结果相同。关闭手机 App 的 WLAN 对照也得到相同
结果。

## 10. 最终判断

本轮符合情况 B：

```text
Go2 AP network: PASS
Local CycloneDDS: PASS
Remote Go2 DDS discovery: FAIL
LowState: 0 samples
SportModeState: 0 samples
```

Go2 自身 AP 没有恢复 SDK2 DDS。由此可以排除“仅由 E5576 路由器过滤
multicast”这一单一解释。后续排查重点应转向：

1. Go2 SDK2 DDS 服务是否运行；
2. Go2 是否在 WLAN/AP 模式暴露 SDK2 DDS；
3. Go2 实际 DDS Domain 或运行模式；
4. 机器人软件/固件与当前 SDK2 的兼容行为；
5. Unitree 官方支持的 EDU 外部开发网络方式。

当前停止在 Phase 5.1.2-C，不进入 Phase 5.1.3 L1。

