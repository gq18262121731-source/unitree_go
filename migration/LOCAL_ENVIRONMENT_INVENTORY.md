# 本机环境清单

生成日期：2026-08-30  
用途：Go2 机器狗运行环境盘点、后续精简迁移和双机联调基线。  
范围约定：`health_new` 保留在本机，不作为 Go2 新电脑的文件迁移对象；新电脑默认运行目录为 `D:\Go2Runtime`。

> 安全说明：本清单不记录任何 API Key、Token、账号密码、摄像头密码、设备序列号或 Go2 AES Key。涉及敏感配置时只记录文件位置和迁移要求。

## 1. 主机与操作系统

| 项目 | 当前值 | 迁移备注 |
|---|---|---|
| 计算机名 | `TEST` | 新电脑可使用新名称，业务配置不要依赖主机名 |
| Windows 产品标识 | `Windows 10 Home China` | 系统 API 返回值 |
| Windows 构建号 | `26200` | 新电脑建议使用同等或更新的 64 位 Windows 10/11 |
| 系统架构 | 64 位 | Go2、Python、Docker、VirtualBox 均按 x64 准备 |
| CPU | Intel Core i7-11800H，8 核 16 线程 | 新电脑建议不低于该等级 |
| 内存 | 17,007,173,632 字节，约 15.84 GiB | 最低建议 16 GB；并行运行 WSL、Docker、VM 时建议 32 GB |
| 独立 GPU | NVIDIA GeForce RTX 3060 Laptop GPU，约 4 GB 显存 | 视觉推理需要 NVIDIA GPU 时，新机建议不低于此规格 |
| NVIDIA 驱动 | `31.0.15.4630` | 新机应安装兼容 CUDA 12.1 的驱动 |
| 虚拟显示设备 | ToDesk Virtual Display Adapter | 非项目必需，可不迁移 |

## 2. 本机磁盘

| 盘符 | 文件系统 | 总容量 | 当前可用 | 主要项目资产 |
|---|---:|---:|---:|---|
| `C:` | NTFS | 198.9 GB | 74.8 GB | 用户配置、Conda `health` 环境、Go2 用户级启动器 |
| `D:` | NTFS | 275.7 GB | 31.1 GB | Anaconda、Flutter、Git、Docker 数据、`D:\health_new` |
| `E:` | NTFS | 476.9 GB | 226.7 GB | `E:\笨笨狗`、WSL、VirtualBox 虚拟机 |

注意：`D:` 目前只剩约 31.1 GB，不适合作为完整镜像级备份的临时落点。WSL、Docker 和 VirtualBox 完整导出时应使用 `E:` 或容量足够的移动硬盘。

## 3. 主要开发工具

| 工具 | 当前版本 | 当前路径 | 新电脑处理方式 |
|---|---|---|---|
| Python（系统/Conda base） | 3.9.13 | `D:\anaconda3\python.exe` | 不作为正式项目运行解释器 |
| Conda | 22.9.0 | `D:\anaconda3\Scripts\conda.exe` | 新机安装 Miniconda/Anaconda 后重建环境 |
| Git | 2.53.0.windows.1 | `D:\Git\cmd\git.exe` | 新机重装 |
| Node.js | 24.14.1 | `C:\Program Files\nodejs\node.exe` | 新机安装 Node.js 18+；优先选择受支持的 LTS 版本 |
| npm | 11.11.0 | `C:\Program Files\nodejs\npm.ps1` | 使用项目 lock 文件重装依赖 |
| Docker CLI | 29.7.2 | `C:\Program Files\Docker\Docker\resources\bin\docker.exe` | 新机重装 Docker Desktop |
| Docker Compose | 5.4.0 | 随 Docker Desktop 安装 | 新机重装 |
| FFmpeg | 2026-05-04 构建 | `C:\Users\Test1\Downloads\ffmpeg\bin\ffmpeg.exe` | 不依赖用户下载目录；新机放入 `D:\Go2Runtime\tools\ffmpeg` |
| Flutter | 3.41.5 / Dart 3.11.3 | `D:\flutter_windows_3.41.5-stable\flutter\bin` | 仅构建移动端时需要 |
| Java/JDK | 当前 PATH 未发现 | 无 | 构建 Android/Flutter 时另行安装并配置 `JAVA_HOME` |
| VirtualBox | 7.2.14r174565 | `C:\Program Files\Oracle\VirtualBox` | 需要恢复 ROS2 VM 时安装同版本或兼容版本 |
| WSL | 2.7.10.0 | Windows 功能 | 需要恢复 ROS WSL 环境时启用 |

FFmpeg 当前构建已包含 `libx264`。正式恢复时仍需再次验证 `libx264` 编码器和 `mpjpeg` 解复用器。

## 4. Python 与 Conda 环境

### 4.1 Conda 环境

| 环境名 | Python | 位置 | 状态/用途 |
|---|---|---|---|
| `base` | 3.9.13 | `D:\anaconda3` | Conda 基础环境，不作为项目正式运行环境 |
| `health` | 3.11.15 | `C:\Users\Test1\.conda\envs\health` | health-new 当前 GPU 运行环境，保留在本机 |

本机不存在名为 `helth` 的环境。部分旧文档仍使用 `helth`，实际启动脚本默认使用 `health`。

`health` 关键包：

| 包 | 当前版本 |
|---|---|
| PyTorch | `2.5.1+cu121` |
| CUDA Runtime（PyTorch） | `12.1` |
| CUDA 可用 | `True` |
| NumPy | `2.4.4` |
| OpenCV | `4.13.0` |
| FastAPI | `0.115.8` |
| Uvicorn | `0.34.0` |

### 4.2 项目内虚拟环境

| 目录 | Python | 处理原则 |
|---|---|---|
| `E:\笨笨狗\health_new_p04\.venv` | 3.12.13、CPU 版 PyTorch | health-new 留在本机；不可作为新机正式环境复制 |
| `E:\笨笨狗\go2_dev\unitree_webrtc_connect\.venv312` | 3.12.13 | 新电脑使用 Python 3.12 x64 重建 |
| `E:\笨笨狗\go2_dev\unitree_webrtc_connect\.venv` | 旧环境 | 排除，不迁移 |
| `E:\笨笨狗\phase546_tools\rosbags_venv` | 旧实验环境 | 排除，不迁移 |

项目虚拟环境引用当前电脑上的解释器路径，不能依靠目录复制恢复。新电脑应按 requirements/lock 文件重新安装。

## 5. WSL 与 ROS

当前所有 WSL 发行版均处于 `Stopped` 状态。

| 发行版 | WSL 版本 | 存储位置 | VHDX 文件大小 | 已确认 ROS |
|---|---:|---|---:|---|
| Ubuntu 20.04 | 2 | `E:\WSL\Ubuntu-20.04` | 98.80 GB | ROS Noetic，`/opt/ros/noetic` |
| Ubuntu 22.04 | 2 | `E:\WSL\Ubuntu-22.04` | 22.36 GB | ROS Humble，`/opt/ros/humble` |
| docker-desktop | 2 | `D:\DockerData\DockerDesktopWSL\main` | 由 Docker Desktop 管理 | 不手工当作普通 Ubuntu 迁移 |

迁移决定：

- 当前系统若只依赖 Windows Go2 网关和视频桥，可不迁移 WSL；
- 若新电脑需要复现 ROS1/ROS2 工具链，使用 `wsl --export` / `wsl --import`；
- 不复制正在运行的 VHDX；导出前先执行 `wsl --shutdown`；
- 新机导入后重新检查网卡、DDS、挂载盘符和 `/mnt/*` 路径。

## 6. VirtualBox ROS2 虚拟机

| 项目 | 当前值 |
|---|---|
| 虚拟机名称 | `Ubuntu-22.04.5-ROS2` |
| UUID | `022e2ae6-8ae0-4e77-b056-0aba584b907e` |
| VM 目录 | `E:\VirtualBox VMs\Ubuntu-22.04.5-ROS2` |
| 虚拟磁盘 | `Ubuntu-22.04.5-ROS2.vdi` |
| 虚拟容量 | 100 GB |
| 当前实际目录占用 | 约 25.66 GB |
| 磁盘加密 | 未加密 |
| 当前运行状态 | 已关机 |

如果新电脑必须继续使用该 VM，优先导出 OVA；也可在完全关机后复制整个 VM 目录。恢复后必须重新检查桥接网卡、Host-only 网卡、共享目录和 USB 映射。

## 7. Docker Desktop

| 项目 | 当前值 |
|---|---|
| Docker Desktop 服务 | 已安装，检查时未运行 |
| Docker 数据盘 | `D:\DockerData\DockerDesktopWSL\disk\docker_data.vhdx` |
| 数据盘文件大小 | 99.88 GB |
| Docker 系统盘 | `D:\DockerData\DockerDesktopWSL\main\ext4.vhdx`，约 0.09 GB |

因为 Docker Engine 未运行，本次清单没有确认当前容器、镜像和 volume 的实际使用情况。health-new 保留在本机后，新 Go2 电脑原则上不迁移这块约 100 GB 的 Docker 数据盘；只有确认 Go2 独立运行依赖某个容器时，才对该容器和 volume 做逻辑导出。

## 8. 网络环境

### 8.1 当前有效网卡

| 网卡 | 设备 | 状态 | 当前 IPv4 |
|---|---|---|---|
| `WLAN` | Intel Wi-Fi 6 AX201 160MHz | 已连接，约 229 Mbps | `192.168.8.254/24` |
| `以太网 3` | VirtualBox Host-Only Ethernet Adapter | 已连接 | `192.168.56.1/24` |
| `以太网` | Realtek PCIe GbE | 当前未连接 | 系统仍保留 `192.168.123.222/24` 地址记录 |
| 蓝牙网络连接 | Bluetooth PAN | 未连接 | 无当前业务地址 |

迁移注意：

- 当前 WLAN 地址为 `192.168.8.254`，与旧文档中的 `.250` 不同，说明不能把旧 IP 当作永久值；
- 新电脑应使用 DHCP 地址保留或重新规划固定 IP；
- Go2、摄像头和双机 health-new 通信必须先确认实际子网；
- DDS/ROS 配置中的网卡名和接口地址需要在新机重选；
- 手环串口 COM 号和 USB 设备编号也需要重新识别。

### 8.2 当前关注端口

检查时仅发现：

| 端口 | 监听地址 | 进程 | 判断 |
|---:|---|---|---|
| 8093 | `0.0.0.0` | `python` | Go2 视频桥正在运行 |

未发现 5173、5432、6379、8000、8001、8090、8554、8768、9997、11434 的监听进程。

安全提醒：8093 当前监听 `0.0.0.0`，会暴露到可达网卡。双机正式方案中应明确其访问边界；建议视频桥本身监听 `127.0.0.1`，跨电脑视频通过带鉴权的 MediaMTX/RTSP 提供。

## 9. Windows 防火墙与用户级安装

### 9.1 防火墙

当前发现以下项目相关入站规则：

- `ffmpeg-win-x86_64-v7.1.exe`：允许，Public；存在两条；
- `MediaMTX`：允许，Public；存在两条。

新电脑不要机械复制宽泛的 Public 规则。应按固定程序、指定端口、指定远端旧机 IP 和 Private 网络配置最小权限规则。

### 9.2 Go2 URL 启动器

当前用户已注册 `go2bridge://` 协议：

- 安装目录：`C:\Users\Test1\AppData\Local\Go2VideoBridgeLauncher`；
- 注册位置：`HKCU\Software\Classes\go2bridge`；
- 当前桥接脚本路径：`E:\笨笨狗\go2_dev\go2-wireless-camera\wireless_collector\start_sta_wireless.ps1`。

该配置绑定当前 Windows 用户和绝对路径。新电脑应在 `D:\Go2Runtime` 文件恢复并确认脚本哈希后，重新运行安装脚本，不复制注册表项。

当前没有发现指向本项目的计划任务或 Windows 服务；Docker Desktop 自身服务除外。

## 10. 项目与数据位置

| 资产 | 当前位置 | 当前决定 |
|---|---|---|
| Go2 开发与运行代码 | `E:\笨笨狗\go2_dev` | 从中筛选当前可运行组件迁移 |
| Go2 可移植视频包 | `E:\笨笨狗\handoff\go2_video_portable_runtime_2026-08-21` | 可作为视频桥恢复基础，但缺 FFmpeg、MediaMTX 和密钥 |
| health-new 当前工作树 | `E:\笨笨狗\health_new_p04` | 保留本机，不复制到 Go2 新电脑 |
| health-new 主工程 | `D:\health_new` | 保留本机 |
| ROS/点云/历史 phase 数据 | `E:\笨笨狗\phase*` | 默认排除；只保留明确需要的标定、地图和当前运行数据 |
| VirtualBox VM | `E:\VirtualBox VMs\Ubuntu-22.04.5-ROS2` | 仅在新机继续承担 ROS2 时迁移 |
| WSL Ubuntu | `E:\WSL` | 仅在新机继续承担 ROS1/ROS2 时导出迁移 |
| Docker 数据 | `D:\DockerData` | 默认留在本机，不随 Go2 迁移 |

截至 2026-08-29 的工作区盘点：`E:\笨笨狗` 约 13.014 GB、116,230 个文件。主要可重建/可排除内容包括：

- Python 虚拟环境约 2.67 GB；
- `node_modules` 约 0.53 GB；
- ISO/安装程序约 4.46 GB；
- ROS bag、点云和压缩历史数据约 3.91 GB；
- 缓存、构建和日志中还存在重复统计内容。

因此“仅迁移当前 Go2 可运行系统”可以远小于整个工作区，但需要先确定 ROS VM/WSL 是否属于新机运行链路。

## 11. 已发现的路径绑定和兼容风险

1. Go2 用户级启动器写死 `E:\笨笨狗\...`。
2. 部分摄像头脚本写死 `C:\Users\YANG\.conda\envs\AI` 或 `...\health`。
3. LLaMA-Factory 配置引用 `D:\Program\LLaMA-Factory` 和 `D:\Program\health(5-12)`；这些目录当前本机也不存在。
4. ROS 脚本包含 `/home/go2`、`/home/est1`、`/mnt/e/笨笨狗` 等绝对路径。
5. `health_new_p04` 是 Git worktree，指针绑定 `E:`；但 health-new 已决定留在本机，不纳入 Go2 文件迁移。
6. Python 虚拟环境、Conda 环境和 Windows DPAPI 都绑定当前机器或用户，不能原样作为恢复方案。

## 12. 敏感配置清单（不含值）

| 类型 | 当前位置 | 新电脑处理方式 |
|---|---|---|
| Go2 DPAPI AES Key | `go2_dev\go2-wireless-camera\wireless_collector\.go2_aes_key.dpapi` | 禁止复制；由最终运行用户在新机重新生成 |
| health-new 云 API 配置 | `health_new_p04\.env` | health-new 留本机，不进入 Go2 迁移包 |
| 摄像头账号和密码 | `health_new_p04\camera_runtime_external\camera_live_config*.json` 等 | health-new 留本机；跨机接入时重新生成最小权限凭据 |
| MediaMTX 用户和密码 | 正式 `mediamtx.yml` | 新机本地创建，不放入普通压缩包或 Git |
| 双机回调 Token | 尚待正式配置 | 两台电脑分别落地相同的新 Token，配置文件仅限目标账号读取 |

## 13. 新 Go2 电脑建议环境基线

- Windows 10/11 x64，启用硬件虚拟化；
- 16 GB 内存最低，32 GB 推荐；
- NVIDIA GPU 与兼容驱动（若承担视觉推理）；
- 至少 80 GB 可用空间用于精简运行环境；若同时迁移 ROS VM，建议至少 150 GB；
- Git；
- Python 3.12 x64，用于 Go2 WebRTC 视频桥；
- 按 Go2 Gateway 实际依赖准备 Python 3.11 或锁定版本环境；
- FFmpeg，支持 `libx264` 和 `mpjpeg`；
- MediaMTX Windows x64；
- 根据最终架构选择 WSL2/ROS 或 VirtualBox/ROS，避免同时迁移所有历史环境；
- 与本机 health-new 可互通的有线或 Wi-Fi 局域网；
- 独立、受限的双机认证 Token 和 RTSP 读写凭据。

## 14. 后续需要补充的清单

在正式生成备份脚本前，还需要完成以下确认：

- [ ] 新 Go2 电脑是否继续使用 ROS Noetic；
- [ ] 新 Go2 电脑是否继续使用 ROS Humble；
- [ ] 选择 WSL ROS 还是 VirtualBox ROS 作为唯一正式运行环境；
- [ ] 列出 Go2 Gateway 当前正式启动入口和依赖文件；
- [ ] 确认必须保留的地图、标定、导航和模型文件；
- [ ] 确认新旧两台电脑的固定 IP 或 DHCP 地址保留；
- [ ] 定义 health-new 与 Go2 Gateway 的 HTTP/WebSocket 接口和 Token；
- [ ] 定义跨机视频使用 RTSP、MJPEG 还是仅传输结构化视觉结果；
- [ ] 对备份包生成 SHA-256 文件清单；
- [ ] 新机完成 30 分钟双机联调和断线恢复测试。

## 15. 清单更新规则

以下变化发生后，应更新本文件：

- 更换 Python、PyTorch、CUDA、Node、Docker、WSL、VirtualBox 或 ROS 版本；
- Go2 正式运行入口、模型、地图或配置目录变化；
- 本机或新机 IP、网卡、端口、防火墙规则变化；
- health-new 与 Go2 的通信契约变化；
- 新增 Windows 服务、计划任务或用户级协议注册；
- 敏感配置存储方式变化。
