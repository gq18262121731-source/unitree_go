# WSL2 DDS 最终验证清单

记录日期：2026-07-11

本清单用于在正式放弃 WSL2 前，做最后一次完整验证。结论需要保持谨慎：电脑能 `ping` 到 Go2 只说明普通 IP 网络可达；Unitree SDK2 依赖 CycloneDDS 的 UDP/DDS 发现、发布订阅和 RPC 链路。当前证据高度指向 WSL2 网络、UDP 组播、Hyper-V 防火墙或 SDK 绑定接口问题，但仍应用下面步骤做最终确认。

## 当前已知事实

| 项目 | 结果 |
| --- | --- |
| Go2 IP | `192.168.123.161` |
| Windows Go2 网卡 | `以太网`，`192.168.123.222/24` 和 `192.168.123.99/24` |
| WSL Go2 接口 | `eth0`，`192.168.123.99/24` 和 secondary `192.168.123.222/24` |
| WSL 互联网接口 | `eth1`，`10.10.226.236/17` |
| Ping | `ping -c 3 192.168.123.161` 成功 |
| UDP 抓包 | 已看到 WSL 发出 DDS 发现包：`192.168.123.99 > 239.255.0.1:7400`，并尝试向 `192.168.123.161` 的 `7410/7412/...` 端口发送 UDP；随后出现 `ICMP host 192.168.123.161 unreachable`。当前优先怀疑 WSL/Windows 双 IP 导致 DDS 源地址使用 `.99`，需要切换为官方示例中的 `.222` 后复测。 |
| 状态订阅 | `verify_state.py` 返回 `online=False` |
| 网关相机验证 | 返回 SDK `3102` |
| 官方相机示例 | `example/go2/front_camera/capture_image.py eth0` 同样返回 SDK `3102` |
| 3102 含义 | SDK 中 `RPC_ERR_CLIENT_SEND`，即客户端请求发送失败或 DDS/RPC 链路未建立 |
| Hyper-V WSL 防火墙 | 管理员 PowerShell 放行曾执行成功，已 `wsl --shutdown`；重开 Ubuntu 后官方相机示例仍返回 `3102`，仍需同步抓包确认是否有 Go2 回包 |

## 安全边界

在 DDS/RPC 链路未验证通过前，不要运行任何运动控制：

```bash
sportmode_test.py
lowlevel_control.py
verify_motion.py
```

也不要测试站立、移动、速度控制或低层控制。当前只允许验证：

1. UDP/DDS 数据包；
2. 状态订阅；
3. 前置相机；
4. 其他非运动接口。

## Windows 侧检查

在 Windows PowerShell 中执行：

```powershell
Get-NetIPAddress -AddressFamily IPv4 |
Format-Table InterfaceAlias,IPAddress,PrefixLength
```

当前已确认 `以太网` 有：

```text
192.168.123.222/24
192.168.123.99/24
```

Go2 本体地址是 `192.168.123.161`，电脑不能配置成这个地址。

## WSL 版本与网络模式

在 Windows PowerShell 中执行：

```powershell
wsl --version
wsl -l -v
```

当前已确认：

```text
WSL 2.7.10.0
Ubuntu-20.04  VERSION 2
```

当前 `.wslconfig` 已设置：

```ini
[wsl2]
networkingMode=mirrored
localhostForwarding=true
firewall=false
```

如果需要显式放行 Hyper-V WSL 入站流量，在管理员 PowerShell 中执行：

```powershell
Set-NetFirewallHyperVVMSetting `
  -Name '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' `
  -DefaultInboundAction Allow
```

执行后：

```powershell
wsl --shutdown
```

然后重新打开 Ubuntu。

## WSL 接口确认

在 Ubuntu 中执行：

```bash
ip -br addr
ip route
ip route get 192.168.123.161
```

理想结果应显示到 `192.168.123.161` 走 Go2 接口，例如：

```text
192.168.123.161 dev eth0 src 192.168.123.222
```

如果实际输出不是 `eth0`，后续 SDK 示例参数必须改成实际接口名。

## 全接口抓包 + 官方示例

打开两个 Ubuntu 终端。

终端 A：

```bash
sudo timeout 60 tcpdump -i any -nn -vv 'udp or icmp'
```

终端 B 立刻运行官方相机示例：

```bash
cd /mnt/e/笨笨狗/go2_dev/unitree_sdk2_python
python3 example/go2/front_camera/capture_image.py eth0
```

如果 `ip route get 192.168.123.161` 显示不是 `eth0`，把 `eth0` 换成实际接口名。

## 状态订阅验证

SDK 目录中当前没有 Go2 专用 `read_highstate.py`，因此使用本项目只读状态脚本：

```bash
cd /mnt/e/笨笨狗/go2_dev/go2-gateway
export GO2_MODE=real
export GO2_NETWORK_INTERFACE=eth0
python3 scripts/verify_state.py
```

成功条件是输出中：

```text
'online': True
```

## 判断标准

如果最终仍满足以下任一条件：

```text
tcpdump 看不到 UDP
verify_state.py 仍然 online=False
官方相机示例继续返回 3102
```

则将当前结论定为：

> 当前 WSL2 环境不作为 Go2 第一阶段正式验收环境，切换到原生 Ubuntu 或独立 USB 网卡直通的 Ubuntu 虚拟机。

## 推荐替代环境

优先：

```text
Ubuntu 20.04 LTS 或 Ubuntu 22.04 LTS 实机
独立有线 USB 网卡直连 Go2
电脑地址 192.168.123.222/24
```

次选：

```text
VMware / VirtualBox Ubuntu
USB 千兆网卡直通给虚拟机
虚拟机内配置 192.168.123.222/24
不使用 NAT
不让 Windows 同时占用该 USB 网卡
```
