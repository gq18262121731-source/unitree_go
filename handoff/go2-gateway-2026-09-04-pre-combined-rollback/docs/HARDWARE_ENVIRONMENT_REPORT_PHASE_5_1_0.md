# Hardware Environment Report — Phase 5.1.0

采集时间：2026-07-24 18:51:06 +08:00

阶段：Phase 5.1.0 环境确认

状态：环境盘点完成

后续更正：

- 用户确认 Phase 5.1.1 优先使用 WLAN，不要求恢复有线链路。
- WLAN 实测结果见 `NETWORK_REPORT_PHASE_5_1_1_WLAN.md`。
- 本报告中“必须恢复物理有线连接”的原判断已被 WLAN 路线取代。

## 1. 安全边界

本次仅执行操作系统、网络接口、Python、ROS2、SDK 安装状态和既有设备记录的只读查询。

- 未初始化 DDS。
- 未创建 DDS Publisher 或 DataWriter。
- 未启动 ROS2。
- 未启动 Nav2 或 SLAM。
- 未调用 Sport Client。
- 未调用任何运动接口。
- 未修改 `health_new`。
- 未修改 Go2、L1、网卡、固件或网络配置。

## 2. Go2 EDU 设备记录

| 字段 | 当前记录 | 证据状态 |
| --- | --- | --- |
| 品牌/型号 | Unitree Go2 EDU | 已由用户及既有验收记录确认 |
| 设备标识 | `go2-edu-001` | 项目记录 |
| 序列号 | `B42N6000Q3PABHGC` | 既有现场/App 记录 |
| 硬件版本 | `V2.0` | 既有 Unitree App 截图记录 |
| 机器人软件版本 | `V1.1.14` | 既有 Unitree App 截图记录 |
| Unitree Go App | `v1.12.7 c` | 既有手机 App 记录 |
| EDU 版本 | Go2 EDU | 已确认；无独立“EDU 软件版本号”记录 |
| SDK2 有线 IP | `192.168.123.161` | 项目与历史现场记录 |
| 历史 WLAN IP | `192.168.8.235` | 2026-07-21 WLAN 只读诊断记录 |
| 当前网络模式 | 未确认 | 当前有线链路断开，无法从设备实时读取 |
| 固件升级日期 | 未确认 | 既有记录缺失 |

说明：

- 当前不能通过设备实时查询固件/App 状态，因此以上版本值是已有现场记录，不代表本次在线复读结果。
- 历史 WLAN 诊断只能证明普通 IP 单播曾可达；当时未收到 SDK2 DDS 状态样本。
- Phase 5.1 后续应优先使用 Go2 SDK2 有线链路，不把 WLAN 普通 IP 可达误判为 DDS 可用。

## 3. Unitree L1 记录

| 字段 | 当前记录 |
| --- | --- |
| 设备 | Unitree L1 LiDAR |
| 接入目标 | DDS 只读发现与点云订阅 |
| 序列号 | 未确认 |
| 固件版本 | 未确认 |
| 当前在线状态 | 未确认 |
| Topic | 本阶段未枚举，不预设名称 |

本阶段没有初始化 DDS，因此没有探测 L1 topic、消息类型或点云。

## 4. Windows 开发环境

| 字段 | 实测值 |
| --- | --- |
| 操作系统 | Microsoft Windows 11 家庭中文版，64 位 |
| Windows 版本 | `10.0.26200.8875` |
| Build | `26200` |
| Python | `3.9.13` |
| Python 路径 | `D:\anaconda3\python.exe` |
| Go2 有线适配器 | Realtek PCIe GbE Family Controller |
| 有线状态 | `Disconnected` |
| 配置地址 | `192.168.123.222/24`，当前 AddressState 为 `Tentative` |
| WLAN | Intel Wi-Fi 6 AX201 160MHz，`Up` |

## 5. WSL / Ubuntu 环境

| 字段 | 实测值 |
| --- | --- |
| WSL | `2.7.10.0` |
| 分发版 | Ubuntu 20.04.6 LTS |
| WSL 类型 | WSL2 |
| Kernel | `6.18.33.2-microsoft-standard-WSL2` |
| Python | `3.8.10` |
| 运行用户 | `est1`，非 root |
| WSL 网络模式 | `mirrored` |
| `localhostForwarding` | `true` |
| WSL firewall 配置 | `false` |
| Go2 对应接口 | `eth0`，当前 `DOWN`，无 IPv4 |
| 其他活动接口 | `eth1`，`10.10.229.58/17` |
| 当前到 Go2 的错误路由 | `192.168.123.161 via 10.10.255.254 dev eth1` |

本段记录的是 2026-07-24 18:51 的环境快照。后续 WLAN 检查确认 WSL
镜像网络使用 `eth1`，不要求恢复 `eth0`；是否允许进入 DDS 以独立的
Phase 5.1.1 WLAN 报告为准。

## 6. ROS2 状态

| 检查 | 结果 |
| --- | --- |
| `ros2` 命令 | 未安装 |
| `/opt/ros` | 不存在 |
| ROS2 是否启动 | 否 |

Phase 5.1 不要求安装或启动 ROS2。保持当前状态。

## 7. Unitree SDK 状态

### WSL 真实硬件候选环境

| 字段 | 实测值 |
| --- | --- |
| 虚拟环境 | `/home/est1/.venvs/go2-gateway` |
| `unitree_sdk2py` | `1.0.1` |
| `cyclonedds` | `0.10.2` |
| 安装方式 | editable |
| Editable 项目路径 | `/mnt/e/笨笨狗/go2_dev/unitree_sdk2_python` |
| SDK commit | `37116c521f1588482e238d8450e471ba78ab9863` |
| Python import | 成功 |

SDK 仓库当前存在一项未提交修改：

```text
unitree_sdk2py/core/channel_config.py
```

修改内容是在 `ChannelConfigHasInterface` 中加入：

```xml
<Discovery>
    <Peers>
        <Peer Address="192.168.123.161"/>
    </Peers>
</Discovery>
```

该修改不是本次 Phase 5.1.0 产生的。后续报告必须把它视为当前 SDK 运行基线的一部分，不能把该工作树称为完全等同于上游 commit。

### Windows Python

| 字段 | 实测值 |
| --- | --- |
| `unitree_sdk2py` | `1.0.1` |
| `cyclonedds` | `0.10.2` |
| Python import | 成功 |
| SDK import 路径 | 本地 `unitree_sdk2_python` 工作树 |

真实 DDS 读取仍以 WSL 环境为候选，不以 Windows Python 的导入成功代替真实链路验证。

## 8. Phase 5.1.0 判定

| 检查项 | 结果 |
| --- | --- |
| Go2 EDU 型号及既有版本记录 | 通过 |
| Windows / WSL / Python 环境记录 | 通过 |
| WSL 非 root 运行 | 通过 |
| Unitree SDK Python 导入 | 通过 |
| SDK 版本与安装方式记录 | 通过 |
| ROS2 未启动 | 通过 |
| Go2 当前实时版本复读 | 未完成：设备链路不可达 |
| L1 当前固件/在线状态 | 未完成：设备链路不可达 |
| Phase 5.1.1 网络条件 | 不通过：物理以太网断开 |

总体结论：

```text
PHASE_5_1_0_RECORDED
NEXT_GATE_REEVALUATED_AS_WLAN
```

Phase 5.1.1 只允许执行 WLAN 网卡、路由和 ping 验证。不得自动进入
DDS、ROS2、Nav2、SLAM 或任何运动测试。
