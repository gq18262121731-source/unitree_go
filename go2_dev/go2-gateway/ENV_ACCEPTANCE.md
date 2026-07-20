# Go2 EDU 第一阶段环境与设备验收记录

记录日期：2026-07-11

本文件用于跟踪第一阶段 `ENV-01` 到 `ENV-08` 的验收状态。当前环境包含 Windows 原生环境和 WSL2 Ubuntu。根据官方 `unitree_sdk2` 仓库，SDK2 构建环境基线为 Ubuntu 20.04 LTS；根据 `unitree_sdk2_python` 仓库，Python 依赖为 Python >= 3.8。真实 Go2 EDU 控制仍需结合有线网卡、DDS 通信和实体机器人现场复验。

## 当前结论

| 编号 | 验收项 | 验收指标 | 优先级 | 当前状态 | 证据 / 备注 |
| --- | --- | --- | ---: | --- | --- |
| ENV-01 | 机器人型号确认 | 明确记录设备型号为 Go2 EDU，并记录序列号 | P0 | 通过 | 用户已确认为宇树 Go2 EDU 版本；序列号为 `B42N6000Q3PABHGC`。 |
| ENV-02 | 固件版本记录 | 记录当前机器人固件版本和升级日期 | P0 | 部分完成 | App 截图显示机器人硬件版本 `V2.0`、软件版本 `V1.1.14`；升级日期仍待补充。 |
| ENV-03 | 开发主机系统 | Ubuntu 20.04 LTS SDK 开发环境可正常启动和联网 | P0 | 部分通过 | WSL2 为 `Ubuntu 20.04.6 LTS`，版本符合官方 SDK2 基线；WSL2 已能看到 Go2 专用网段 `eth0: 192.168.123.99/24`。真实 DDS 通信仍需用 SDK 示例复验。Windows 原生环境不作为真实控制环境。 |
| ENV-04 | Python环境 | Python >= 3.8 环境可用 | P0 | 部分通过 | Windows 原生 Python 为 `3.9.13`；WSL2 `python3` 为 `3.8.10`，符合 Python SDK 最低版本要求。后续建议在项目 venv 中复验依赖。 |
| ENV-05 | SDK安装 | `unitree_sdk2_python` 可以正常导入 | P0 | 部分通过 | Windows 原生环境执行 `import unitree_sdk2py` 失败；WSL2 中 `python3 -c "import unitree_sdk2py"` 已通过。真实控制环境仍需复验。 |
| ENV-06 | SDK版本冻结 | 保存 Git Commit ID 和依赖版本文件 | P0 | 部分完成 | SDK commit 已保存到 `SDK_COMMIT.txt`：`37116c521f1588482e238d8450e471ba78ab9863`；当前 Windows 依赖已保存到 `PIP_FREEZE_CURRENT_WINDOWS.txt`。真实控制环境需重新保存 `PIP_FREEZE_UBUNTU.txt`。 |
| ENV-07 | 遥控器 | 遥控器能够连接机器人并人工接管 | P0 | 待现场确认 | 需要实体遥控器、机器人上电后测试人工接管。 |
| ENV-08 | 项目运行用户 | 使用非 root 用户运行主要业务服务 | P1 | 待 Ubuntu 确认 | 在 Ubuntu/WSL2 中运行 `whoami`，确认不要以 `root` 启动 `go2-gateway`。 |

## 设备信息表

| 字段 | 当前记录 |
| --- | --- |
| 机器人型号 | Unitree Go2 EDU |
| 机器人序列号 | `B42N6000Q3PABHGC` |
| 硬件版本 | `V2.0` |
| 固件/软件版本 | 机器人软件版本 `V1.1.14` |
| 固件升级日期 | 待填 |
| App 版本 | Unitree Go `v1.12.7 c` |
| 电池软件版本 | `1.23` |
| 电池电量 | `59%` |
| 电池状态 | 放电 |
| 电池循环次数 | `5` |
| 电池温度 | BAT1 `25°C` |
| SDK Commit | `37116c521f1588482e238d8450e471ba78ab9863` |
| 遥控器型号 | 待填 |
| 开发主机网卡名称 | Windows 以太网为 Go2 专用网卡；WSL2 中对应为 `eth0` |
| Go2 专用网卡 IP | Windows `以太网` 已清理为仅保留 `192.168.123.222/24`，默认网关为空；待 `wsl --shutdown` 后复查 WSL2 是否同步为 `.222` |
| 互联网网卡 | Windows WLAN / WSL2 `eth1`，IP `10.10.226.236/17` |
| 开发主机系统 | Windows `10.0.26200.8655`；WSL2 `Ubuntu 20.04.6 LTS`；官方 SDK2 基线为 Ubuntu 20.04 LTS |
| WSL 版本 | `2.7.10.0`，Ubuntu-20.04 为 WSL2 |
| WSL 网络模式 | `%USERPROFILE%\.wslconfig` 已设置 `networkingMode=mirrored`、`localhostForwarding=true`、`firewall=false` |
| Hyper-V WSL 防火墙 | `{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}` 当前 `DefaultInboundAction=NotConfigured`，尚未显式设置为 `Allow` |
| Python 版本 | Windows 为 3.9.13；WSL2 为 3.8.10；Python SDK 要求 Python >= 3.8 |
| SDK 导入 | WSL2 已通过；Windows 原生未通过 |
| DDS 状态读取 | `GO2_NETWORK_INTERFACE=eth0 python3 scripts/verify_state.py` 已运行，但返回 `online=False`，尚未收到状态话题 |
| Go2 邻居发现 | WSL2 `ip neigh show dev eth0` 已发现 `192.168.123.161 lladdr 7e:1d:75:60:f5:89 STALE` |
| Go2 IP 连通性 | 早前 WSL2 `ping -c 3 192.168.123.161` 曾通过；清理 Windows 双 IP 后，Windows 侧 `ping 192.168.123.161` 目前出现超时和 `来自 192.168.123.222 的回复: 无法访问目标主机`，基础 IP/ARP 链路当前不稳定，需先恢复物理/ARP 连通性 |
| UDP/DDS 抓包 | `tcpdump -i any 'udp or icmp'` 已看到 WSL 发出 DDS 发现包：`192.168.123.99 > 239.255.0.1:7400`，并尝试向 `192.168.123.161` 的 `7410/7412/...` 端口发送 UDP；随后出现 `ICMP host 192.168.123.161 unreachable`。当前优先怀疑 WSL/Windows 双 IP 导致 DDS 源地址使用 `.99`，需要切换为官方示例中的 `.222` 后复测。 |
| 相机 RPC 验证 | `python3 scripts/verify_camera.py` 失败，SDK 返回 `3102`；在 SDK 中 `3102 = RPC_ERR_CLIENT_SEND`，表示客户端请求发送失败或 DDS/RPC 链路未建立 |
| 官方相机示例 | `python3 example/go2/front_camera/capture_image.py eth0` 同样失败，返回 `3102`；说明问题不在 `go2-gateway`，而在当前 WSL2 + DDS/RPC 通信链路 |
| Hyper-V WSL 防火墙放行 | 管理员 PowerShell 曾执行完整 `Set-NetFirewallHyperVVMSetting ... -DefaultInboundAction Allow` 成功；随后已执行 `wsl --shutdown`。重开 Ubuntu 后官方相机示例仍返回 `3102`，仍需结合同步抓包确认是否有 Go2 回包。 |
| 项目运行用户 | 待 Ubuntu/WSL2 确认 |

## Ubuntu / WSL2 环境验收命令

在真实开发主机、虚拟机或当前 WSL2 中执行：

```bash
lsb_release -a
python3 --version
whoami
ip link
ip addr
```

进入网关目录后执行：

```bash
cd go2-gateway
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cd ../unitree_sdk2_python
pip install -e .

cd ../go2-gateway
python scripts/check_environment.py
python -c "import unitree_sdk2py; print('unitree sdk imported')"
pip freeze > PIP_FREEZE_UBUNTU.txt
```

冻结 SDK 版本：

```bash
cd ../unitree_sdk2_python
git rev-parse HEAD > ../go2-gateway/SDK_COMMIT.txt
git log -1
```

## 遥控器与机器人现场验收

1. 确认机器人型号为 Go2 EDU。
2. 记录序列号、固件版本、App 版本、遥控器型号。
3. 机器人放在不小于 `3m x 3m` 的清空区域。
4. 遥控器开机并连接机器人。
5. 确认遥控器可让机器人停止或人工接管。
6. 现场记录测试人、时间和结果。

## 当前下一步

在 WSL2 中，Go2 专用网卡应使用：

```bash
export GO2_MODE=real
export GO2_NETWORK_INTERFACE=eth0
```

先运行只读/低风险验证，不要直接运动：

```bash
cd /mnt/e/笨笨狗/go2_dev/go2-gateway
python3 scripts/check_environment.py
python3 scripts/verify_state.py
```

如果状态读取成功，再验证相机：

```bash
python3 scripts/verify_camera.py
```

只有状态和相机都稳定后，再进入 `verify_motion.py`，并且只先测站立、停止、趴下。
