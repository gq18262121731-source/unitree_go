# Unitree Go2 EDU 资料补全记录

记录日期：2026-07-11

本记录根据用户确认的设备型号“宇树 Go2 EDU”补全第一阶段环境资料。与实体设备强绑定的信息，例如序列号、固件版本、App 版本和遥控器型号，仍需现场读取。

## 已确认设备

| 项目 | 记录 |
| --- | --- |
| 品牌 | Unitree / 宇树 |
| 型号 | Go2 EDU |
| 阶段目标设备名 | `go2-edu-001` |
| SDK 接入路线 | `unitree_sdk2_python` + DDS + FastAPI Gateway |
| 运行建议 | 官方 SDK2 基线为 Ubuntu 20.04 LTS；Gateway 建议在可直接访问 Go2 有线网卡的 Linux 主机/虚拟机运行，智慧康养主系统通过 HTTP 调用 |

## 官方资料来源

| 类型 | 链接 | 用途 |
| --- | --- | --- |
| Go2 官方页面 | https://www.unitree.com/go2 | 产品型号、官方入口、App 和基础信息 |
| 宇树文档中心 | https://support.unitree.com/home/zh/developer | SDK、开发者资料、设备支持说明 |
| SDK2 仓库 | https://github.com/unitreerobotics/unitree_sdk2 | C++ SDK2、官方基线环境和底层/高层能力示例 |
| Python SDK 仓库 | https://github.com/unitreerobotics/unitree_sdk2_python | `unitree_sdk2_python` 安装、示例、Go2 高层运动和前置相机代码 |
| ROS2 仓库 | https://github.com/unitreerobotics/unitree_ros2 | DDS/ROS2 话题、网络配置和后续导航路线参考 |
| Go2 用户手册 PDF | https://manuals.plus/unitree/go2-robot-dog-manual | 使用、充电、遥控器、App 和安全注意事项参考 |

## 本项目采用的资料结论

1. Go2 EDU 是本项目接入目标，第一阶段按 SDK2 / DDS 接入路线实施。
2. 官方 `unitree_sdk2` 基线环境为 Ubuntu 20.04 LTS；官方 `unitree_sdk2_python` 要求 Python >= 3.8。
3. 本项目不直接让业务系统导入宇树 SDK，而是通过 `go2-gateway` 暴露 HTTP 接口。
4. 第一阶段只开放基础能力：状态读取、前置相机快照、站立、趴下、停止、急停和短时低速移动。
5. 禁止开放跳跃、翻转、倒立、特技动作和低层电机控制。
6. Windows 当前只作为 Mock 开发和接口测试环境。WSL2 已能导入 SDK，但真实 DDS/有线网卡通信仍需现场验证。

## 当前环境记录

| 项目 | 当前值 |
| --- | --- |
| Windows 版本 | `10.0.26200.8655` |
| Windows Python | `3.9.13`，未安装 `unitree_sdk2py` |
| WSL2 系统 | `Ubuntu 20.04.6 LTS` |
| WSL2 Python | `python3 3.8.10` |
| WSL2 SDK 导入 | 已通过：`unitree sdk imported` |
| WSL2 Go2 网卡 | `eth0`，IP `192.168.123.99/24`，secondary `192.168.123.222/24` |
| WSL2 互联网网卡 | `eth1`，IP `10.10.226.236/17` |

## 待现场补全

| 字段 | 记录方式 | 当前状态 |
| --- | --- | --- |
| 机器人序列号 | 机身铭牌、Unitree App 或供应商资料 | `B42N6000Q3PABHGC` |
| 硬件版本 | Unitree App 版本信息 | `V2.0` |
| 固件/软件版本 | Unitree App 或设备管理界面 | 机器人软件版本 `V1.1.14` |
| 固件升级日期 | App 记录或交付记录 | 待填 |
| App 版本 | 手机 App 设置页 | Unitree Go `v1.12.7 c` |
| 电池软件版本 | 电池页面 | `1.23` |
| 电池电量 | 电池页面 | `59%` |
| 电池循环次数 | 电池页面 | `5` |
| 电池温度 | 电池页面 | BAT1 `25°C` |
| 遥控器型号 | 遥控器铭牌或供应商资料 | 待填 |
| Go2 专用网卡名称 | Linux 执行 `ip link` | WSL2 中为 `eth0` |
| Go2 专用网卡 IP | Linux 执行 `ip addr` | `192.168.123.99/24` 已存在；同时存在 `192.168.123.222/24` |
| 真实控制环境 SDK 导入 | venv 中执行 `python -c "import unitree_sdk2py"` | 待实机网络环境复验 |

## 现场首次验收顺序

1. 确认设备型号为 Go2 EDU。
2. 记录序列号、固件版本、App 版本和遥控器型号。
3. Linux 主机或可桥接有线网卡的虚拟机连接 Go2 专用网卡，设置 `192.168.123.99/24`。
4. 安装 `unitree_sdk2_python` 并保存 SDK commit。
5. 运行 SDK hello-world publisher/subscriber 测试 DDS。
6. 运行官方前置相机示例，只做取图验证。
7. 运行官方高层运动示例，只测试 `StandUp`、`StopMove`、`StandDown`。
8. 启动本项目 `go2-gateway` 的 real 模式。
9. 依次验证 `/health`、`/api/robot/status`、相机截图、站立、停止、趴下。
10. 在安全场地内进行短时低速移动测试。

## 本地配置同步

项目默认机器人编号已调整为：

```text
GO2_ROBOT_ID=go2-edu-001
```

真实设备验证前请复制 `.env.example` 并按现场网卡名称修改：

```bash
cp .env.example .env
```

Real 模式启动示例：

```bash
GO2_MODE=real GO2_NETWORK_INTERFACE=eth0 uvicorn app.main:app --host 0.0.0.0 --port 8090 --workers 1
```
