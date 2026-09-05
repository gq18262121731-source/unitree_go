# Unitree Go2 综合项目跨电脑复现手册

版本：2026-09-05

复现基线提交：`6e8c06bf86532a73a1141795064d70b52af90046`

仓库：<https://github.com/gq18262121731-source/unitree_go>

本文用于在一台新电脑上，从零克隆并复现本仓库。建议先完成单机 Mock 复现，再接入 Go2 真机、视频、UWB、ROS 2、LiDAR 或 SLAM。

> 安全原则：首次复现只运行 Mock 或只读模式。真实运动必须在受控场地、急停可用且现场操作员明确授权后开启。本文中的 IP、账号、密钥和人员 ID 都是占位符，不能直接用于生产环境。

## 1. 复现范围与已知边界

GitHub 仓库可以复现：

- `health_new_p04` 智慧康养 FastAPI 后端；
- Vue 管理前端和 Flutter 客户端源码；
- Go2 HTTP 网关 Mock/真实 DDS 接入代码；
- Go2 WebRTC 视频、语音、UWB 和伴随统一 Runtime；
- Unitree SDK、ROS/ROS 2、LiDAR、Point-LIO/SLAM 相关源码；
- 模型、ROS bag、点云、交付 ZIP 和历史验证证据等 Git LFS 文件；
- 部署、回滚、验收和故障排查文档。

以下内容不会随 GitHub 自动恢复：

- 真实 `.env`、云 API Key、账号和密码；
- Go2 AES Key 及 Windows DPAPI 文件；
- 本机 Conda/venv、`node_modules`、Docker 数据卷和构建缓存；
- 未提交的 SQLite 数据库、现场日志、录音和临时文件；
- 新电脑的网卡地址、防火墙规则和设备驱动；
- 完整的独立 `camera-service` 实现、其模型权重及生产配置。

仓库中的 `camera-service/` 当前主要是接口资料。若要复现完整视觉服务/PFV2 链路，还需要向项目负责人取得独立交付包。

## 2. 推荐复现路线

| 阶段 | 环境 | 是否需要 Go2 | 目标 |
| --- | --- | --- | --- |
| A | 一台 Windows 10/11 电脑 | 否 | 克隆、LFS、Redis、主系统、Vue、8090 Mock |
| B | Windows 10/11 + Go2 Wi-Fi/LAN | 是 | 8093 WebRTC 视频/语音/UWB Runtime |
| C | Windows 主系统 + Ubuntu 网关 | 是 | 8090 有线 DDS 只读接入 |
| D | Ubuntu 22.04 + ROS 2 Humble | 是或离线 bag | LiDAR、TF、Point-LIO/SLAM |

第一次复现应完成阶段 A。阶段 A 通过后，再根据现场目标选择 B、C 或 D。

## 3. 新电脑要求

### 3.1 Windows 主机

- Windows 10/11 64 位；
- 建议 16 GB 以上内存，模型推理建议 32 GB；
- 建议至少预留 15 GB 可用空间；
- Git、Git LFS、PowerShell 7；
- Miniconda 或 Anaconda；
- Python 3.11，用于主系统和 8090 网关；
- Python 3.12，用于 Go2 WebRTC 8093 Runtime；
- Node.js 20 LTS 和 npm；
- Docker Desktop，用于 Redis；
- 可选 NVIDIA GPU 和匹配的驱动/CUDA 版 PyTorch。

当前仓库普通 Git 数据约 313 MiB，Git LFS 数据约 4.51 GiB。克隆、解包、虚拟环境和构建过程会需要更多空间。

### 3.2 Ubuntu 主机

- 有线 DDS 网关推荐 Ubuntu 20.04；
- ROS 2/LiDAR/SLAM 推荐 Ubuntu 22.04 + ROS 2 Humble；
- 独立网卡连接 Go2；
- 不建议在承载 SSH/互联网的同一网卡上直接修改 Go2 静态地址。

## 4. 记录新电脑基线

开始前记录版本，便于排障：

```powershell
$PSVersionTable.PSVersion
git --version
git lfs version
conda --version
py -0p
node --version
npm --version
docker version
```

建议同时记录：Windows 版本、CPU、内存、GPU/驱动、电脑 IP、Go2 IP、网卡名称和当前时间。

## 5. 克隆并锁定复现版本

### 5.1 Windows PowerShell

```powershell
git lfs install
git clone https://github.com/gq18262121731-source/unitree_go.git
Set-Location .\unitree_go
git checkout 6e8c06bf86532a73a1141795064d70b52af90046
git lfs pull
git lfs checkout
```

### 5.2 Linux

```bash
git lfs install
git clone https://github.com/gq18262121731-source/unitree_go.git
cd unitree_go
git checkout 6e8c06bf86532a73a1141795064d70b52af90046
git lfs pull
git lfs checkout
```

### 5.3 克隆验收

```powershell
git rev-parse HEAD
git status --short
git lfs fsck
git lfs ls-files
```

预期：

- `git rev-parse HEAD` 返回本文记录的完整提交号；
- `git status --short` 没有输出；
- `git lfs fsck` 返回成功；
- 模型、bag、点云、压缩包不是只有三行的 LFS 指针文本。

如果只需要最新版而不要求完全一致，可留在 `main`。正式复现实验必须记录实际提交号。

## 6. 仓库目录速查

| 路径 | 用途 |
| --- | --- |
| `health_new_p04/` | 当前智慧康养主系统 |
| `health_new_p04/frontend/vue-dashboard/` | Vue 前端 |
| `health_new_p04/mobile/flutter_app/` | Flutter 客户端 |
| `go2_dev/go2-gateway/` | 8090 HTTP 网关及 8093 统一 Runtime |
| `go2_dev/unitree_webrtc_connect/` | Go2 WebRTC Python SDK |
| `go2_dev/unitree_sdk2_python/` | Go2 Python DDS SDK |
| `go2_dev/unitree_ros2/` | Unitree ROS 2 工程 |
| `phase*` | ROS、LiDAR、SLAM 和验收数据 |
| `handoff/` | 跨电脑交付包、快照和回滚资料 |
| `DEPLOYMENT.md` | 完整部署手册 |

## 7. 阶段 A：单机 Mock 复现

建议在同一台 Windows 电脑上启动以下服务：

```text
Vue 5173
   └── FastAPI 8000
         ├── Redis 6379
         └── Go2 Mock Gateway 8090
```

### 7.1 创建主系统 Conda 环境

从仓库根目录执行：

```powershell
Set-Location .\health_new_p04
conda create -n helth python=3.11 -y
conda run -n helth python -m pip install --upgrade pip
conda run -n helth python -m pip install -r requirements.txt
conda run -n helth python -c "import fastapi, torch, pandas, cv2; print('python dependencies ok')"
```

不要混用 `helth` Conda 环境、系统 Python 和 8093 的 Python 3.12 venv。

### 7.2 创建主系统本机配置

```powershell
Copy-Item .env.example .env
conda run -n helth python -c "import secrets; print(secrets.token_urlsafe(48))"
```

将第二条命令生成的随机值填入 `.env` 的 `JWT_SECRET`。Mock 基线至少核对：

```env
ENVIRONMENT=development
DEBUG=true
HOST=0.0.0.0
PORT=8000

DATABASE_URL=sqlite+aiosqlite:///./data/app.db
REDIS_URL=redis://127.0.0.1:6379/0

USE_MOCK_DATA=true
DATA_MODE=mock
OFFLINE_ONLY_RUNTIME=true

QWEN_API_KEY=
JWT_SECRET=<在本机生成的随机值>
SEED_DEFAULT_ACCOUNTS=true
SEED_DEFAULT_PASSWORD=<本机演示密码>

ROBOT_GATEWAY_ENABLED=true
ROBOT_GATEWAY_BASE_URL=http://127.0.0.1:8090
COMPANION_GATEWAY_BASE_URL=http://127.0.0.1:8093

VISION_SERVICE_BASE_URL=http://127.0.0.1:8011
VISION_SERVICE_LOCAL_BASE_URL=http://127.0.0.1:8011
VISION_SERVICE_POLL_ENABLED=false
FALL_DETECTION_ENABLED=false
WEATHER_PROVIDER=mock
```

`.env` 只保存在新电脑，不要提交到 Git，也不要通过聊天或截图发送。

### 7.3 启动 Redis

确认 Docker Desktop 已启动：

```powershell
docker compose -f .\docker\docker-compose.yml up -d redis
docker compose -f .\docker\docker-compose.yml ps
docker exec ai-health-iot-redis redis-cli ping
```

预期返回 `PONG`。

### 7.4 创建并启动 8090 Mock 网关

打开新的 PowerShell，从仓库根目录执行：

```powershell
Set-Location .\go2_dev\go2-gateway
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
$env:GO2_MODE = 'mock'
.\.venv\Scripts\python.exe -m uvicorn app.main:app `
  --host 127.0.0.1 --port 8090 --workers 1
```

Go2 网关必须使用一个 worker。另开终端验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8090/health | ConvertTo-Json -Depth 10
Invoke-RestMethod http://127.0.0.1:8090/api/preflight | ConvertTo-Json -Depth 10
Invoke-RestMethod http://127.0.0.1:8090/api/capabilities | ConvertTo-Json -Depth 10
```

### 7.5 启动主系统后端

打开新的 PowerShell：

```powershell
Set-Location <仓库目录>\health_new_p04
powershell -ExecutionPolicy Bypass -File .\scripts\start_server.ps1 `
  -CondaEnv helth -ListenHost 127.0.0.1 -Port 8000
```

验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/healthz
Invoke-RestMethod http://127.0.0.1:8000/api/v1/system/info | ConvertTo-Json -Depth 10
Start-Process http://127.0.0.1:8000/docs
```

`/healthz` 应包含 `status=ok`。

### 7.6 启动 Vue 前端

打开新的 PowerShell：

```powershell
Set-Location <仓库目录>\health_new_p04
powershell -ExecutionPolicy Bypass -File .\scripts\start_frontend.ps1 `
  -ListenHost 127.0.0.1 -Port 5173
```

第一次运行会安装 npm 依赖。浏览器访问 <http://127.0.0.1:5173>。

### 7.7 阶段 A 验收

```powershell
Invoke-RestMethod http://127.0.0.1:8000/healthz
Invoke-RestMethod http://127.0.0.1:8090/health
Test-NetConnection 127.0.0.1 -Port 5173
Test-NetConnection 127.0.0.1 -Port 6379
```

还应人工确认：

- Vue 页面可以打开；
- 可以登录并刷新数据；
- 后端日志没有循环异常；
- 8090 明确显示 Mock，而不是真机在线；
- 未启动的 8093/视觉服务显示为离线或降级，而不是伪造成功。

## 8. 阶段 B：复现 Go2 无线 WebRTC Runtime

只有阶段 A 通过且新电脑可访问 Go2 时执行。

### 8.1 创建固定位置的 Python 3.12 环境

从仓库根目录执行：

```powershell
py -3.12 -m venv .\go2_dev\unitree_webrtc_connect\.venv312
.\go2_dev\unitree_webrtc_connect\.venv312\Scripts\python.exe `
  -m pip install --upgrade pip
.\go2_dev\unitree_webrtc_connect\.venv312\Scripts\python.exe `
  -m pip install -e .\go2_dev\unitree_webrtc_connect
.\go2_dev\unitree_webrtc_connect\.venv312\Scripts\python.exe `
  -m pip install -r .\go2_dev\go2-gateway\requirements.txt
```

启动脚本依赖这个固定路径，不要把另一台电脑的 venv 整体复制过来。

### 8.2 在新电脑重新生成 DPAPI 设备密钥

```powershell
Set-Location .\go2_dev\go2-wireless-camera\wireless_collector
.\setup_wireless.ps1
```

生成位置：

```text
go2_dev/go2-wireless-camera/wireless_collector/.go2_aes_key.dpapi
```

DPAPI 文件与 Windows 用户/电脑绑定。不能复制旧电脑生成的文件，也不能将明文 AES Key 写入 Git。

### 8.3 网络预检

关闭手机 Unitree Go App 的实时视频页面和其他 Go2 视频客户端：

```powershell
$RobotIp = '<GO2_IP>'
Test-NetConnection $RobotIp -Port 9991
Get-NetTCPConnection -LocalPort 8093 -State Listen -ErrorAction SilentlyContinue
```

`TcpTestSucceeded` 必须为 `True`，8093 不应被旧进程占用。电脑若连接 Go2 热点又需要访问互联网，通常还需要第二条上行网络。

### 8.4 启动 8093 Runtime

```powershell
Set-Location <仓库目录>\go2_dev\go2-gateway
.\scripts\Start-Go2WirelessRuntime.ps1 `
  -RobotIp '<GO2_IP>' `
  -ListenHost 127.0.0.1 `
  -HealthNewUrl 'http://127.0.0.1:8000' `
  -ElderId '<ELDER_ID>' `
  -NoOpenBrowser
```

此脚本是比赛统一 Runtime，可能设置真实模式和控制相关变量。若现场只允许视频或只读验证，不要未经确认直接运行，应改用交付包或由负责人确认启动参数。

### 8.5 Runtime 验收

```powershell
$First = Invoke-RestMethod http://127.0.0.1:8093/status -TimeoutSec 5
Start-Sleep -Seconds 5
$Second = Invoke-RestMethod http://127.0.0.1:8093/status -TimeoutSec 5
$Second | ConvertTo-Json -Depth 12
$Second.connectionDiagnostics | ConvertTo-Json -Depth 12

Invoke-WebRequest http://127.0.0.1:8093/snapshot `
  -OutFile "$env:TEMP\go2_snapshot.jpg" -TimeoutSec 10
Start-Process "$env:TEMP\go2_snapshot.jpg"
```

必须确认：

- `connected=true`；
- `hasFrame=true`；
- 帧序号或 `frameCount` 在 5 秒内增加；
- `frameAgeMs` 不持续增大；
- 快照是现场当前画面；
- `connectionDiagnostics` 有连接阶段信息，但不包含明文 ICE 密码/AES Key；
- 日志中没有持续重连风暴。

停止时优先在 Runtime 控制台输入 `EXIT`，让停止动作和 WebRTC 清理正常完成。

## 9. 阶段 C：Ubuntu 有线 DDS 只读复现

确认真实网卡名，以下示例中的 `enp3s0` 不能直接照抄：

```bash
ip -br link
ip -br addr
```

在确认不会断开 SSH/互联网后配置 Go2 专用网卡：

```bash
sudo ip addr flush dev enp3s0
sudo ip addr add 192.168.123.99/24 dev enp3s0
sudo ip link set enp3s0 up
ip route get 192.168.123.161
ping -c 3 192.168.123.161
```

安装环境：

```bash
cd go2_dev/go2-gateway
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cd ../unitree_sdk2_python
pip install -e .
cd ../go2-gateway
cp .env.example .env
```

在 `.env` 中使用只读基线：

```env
GO2_MODE=real
UNITREE_ROBOT_IP=192.168.123.161
UNITREE_NETWORK_INTERFACE=enp3s0
UNITREE_DOMAIN_ID=0
UNITREE_REQUIRE_DDS_STATE=true

GO2_CONTROL_ENABLED=false
GO2_READ_ONLY_MODE=true
FOLLOW_SIMULATION=true
FOLLOW_EXECUTION_ENABLED=false
PHASE7_MOTION_EXECUTION_ENABLED=false
```

启动前检查和启动：

```bash
GO2_MODE=real python scripts/check_environment.py --strict-real
GO2_MODE=real \
GO2_NETWORK_INTERFACE=enp3s0 \
GO2_CONTROL_ENABLED=false \
GO2_READ_ONLY_MODE=true \
uvicorn app.main:app --host 0.0.0.0 --port 8090 --workers 1
```

只读验收：

```bash
curl http://127.0.0.1:8090/health
curl http://127.0.0.1:8090/api/connection
curl http://127.0.0.1:8090/api/robot/diagnostics/dds
curl http://127.0.0.1:8090/api/status
python scripts/verify_preflight.py --base-url http://127.0.0.1:8090 --allow-readonly
```

能够 ping Go2 不代表 DDS 成功。必须看到新鲜的 `SportModeState` 或 `LowState`，时间戳持续更新且 `robotOnline=true`。

## 10. 阶段 D：ROS 2、LiDAR 与 SLAM

建议使用 Ubuntu 22.04 物理机和 ROS 2 Humble。虚拟机必须使用桥接网卡并单独验证 DDS 组播。

顺序：

1. 安装 ROS 2 Humble；
2. 阅读 `ROS2_HUMBLE_INSTALL_PLAN_PHASE_5_2_3_VM.md`；
3. 阅读 `go2_dev/unitree_ros2/README.md`；
4. 配置 Go2 专用网卡和 CycloneDDS；
5. 先验证只读 topic；
6. 再验证 TF、时间戳、点云帧和外参；
7. 使用仓库中的 `phase*` bag 做离线复现；
8. 最后才考虑在线 Point-LIO/SLAM 或导航。

基础检查：

```bash
source /opt/ros/humble/setup.bash
ros2 node list
ros2 topic list
ros2 topic hz <TOPIC_NAME>
ros2 topic echo <TOPIC_NAME> --once
```

不要在 TF 方向、时间戳和外参未验证时开启导航或真机运动。

## 11. 旧电脑状态迁移

代码复现与业务状态迁移是两件事。若需要保留旧电脑的实际数据，先停服务，再通过加密介质单独迁移。

| 内容 | 是否从 GitHub恢复 | 建议 |
| --- | --- | --- |
| 源码和文档 | 是 | 锁定提交后克隆 |
| Git LFS 模型/bag/点云 | 是 | 执行 `git lfs pull` |
| `health_new_p04/.env` | 否 | 在新电脑从示例重建，手工迁移必要配置 |
| `go2-gateway/.env` | 否 | 根据新网卡和设备重建 |
| SQLite 数据库 | 通常否 | 停服务后备份并校验，再单独恢复 |
| Docker 数据卷 | 否 | 使用数据库导出/导入，不直接复制运行中目录 |
| DPAPI 设备密钥 | 否 | 必须由新电脑当前 Windows 用户重新生成 |
| venv/Conda/`node_modules` | 否 | 在新电脑重新安装 |

迁移数据库前记录文件 SHA-256：

```powershell
Get-FileHash -Algorithm SHA256 <备份文件>
```

不要在旧服务仍写入数据库时直接复制文件。

## 12. 跨电脑端口和防火墙

| 端口 | 服务 | 建议范围 |
| --- | --- | --- |
| 5173/TCP | Vue | 本机或可信局域网 |
| 8000/TCP | 主系统 FastAPI | 本机或可信局域网 |
| 8090/TCP | Go2 HTTP 网关 | 主系统所在可信网段 |
| 8093/TCP | Go2 WebRTC Runtime/MJPEG | 本机或可信网段 |
| 6379/TCP | Redis | 仅本机/容器网络 |
| 8011/TCP | 视觉服务 | 本机或可信网段 |
| 8554/TCP | RTSP/MediaMTX | 桥接电脑与视觉服务器 |
| 9991/TCP | Go2 WebRTC 信令 | 新电脑到 Go2 |
| 8766/UDP | UWB 转发 | 指定接收主机 |

跨电脑访问时，把配置中的 `127.0.0.1` 改成目标服务电脑的可信局域网 IP。不要将 Redis、数据库、无鉴权 RTSP、Ollama 或开发服务器暴露到公网。

## 13. 完整验收清单

### 13.1 仓库

- [ ] Git 提交号已记录；
- [ ] `git status` 干净；
- [ ] `git lfs fsck` 通过；
- [ ] LFS 文件不是指针文本；
- [ ] `.env`、DPAPI 和真实密钥未进入 Git。

### 13.2 Mock 主系统

- [ ] Redis 返回 `PONG`；
- [ ] 8000 `/healthz` 返回成功；
- [ ] 8090 `/health` 和 `/api/preflight` 返回成功；
- [ ] 5173 页面可以登录并刷新；
- [ ] 未连接的硬件服务正确显示离线/降级。

### 13.3 Go2 真机

- [ ] Go2 IP 和目标网卡已重新确认；
- [ ] 只运行一个 8090/8093 实例；
- [ ] 只读模式下运动命令被拒绝；
- [ ] DDS 状态或 WebRTC 帧持续更新；
- [ ] 日志不包含明文凭据；
- [ ] 连续运行至少 30 分钟，没有重连风暴或内存失控；
- [ ] 停止流程能让机器狗保持静止。

## 14. 测试命令

主系统：

```powershell
Set-Location <仓库目录>\health_new_p04
conda run -n helth pytest -q
conda run -n helth powershell -ExecutionPolicy Bypass `
  -File .\scripts\smoke_backend_http.ps1
Set-Location .\frontend\vue-dashboard
npm run check
```

Go2 网关：

```powershell
Set-Location <仓库目录>\go2_dev\go2-gateway
.\.venv\Scripts\python.exe -m pytest -q
```

只做快速复现时，至少执行健康接口、系统信息、8090 Mock 预检和一次页面登录。

## 15. 常见问题

### 15.1 LFS 文件只有三行文本

```powershell
git lfs install
git lfs pull
git lfs checkout
git lfs fsck
```

### 15.2 启动脚本找不到 Python 环境

```powershell
conda env list
py -0p
Test-Path .\go2_dev\unitree_webrtc_connect\.venv312\Scripts\python.exe
```

主系统明确传入 `-CondaEnv helth`。不要依赖另一台电脑留下的绝对路径。

### 15.3 8000、8090 或 8093 被占用

```powershell
Get-NetTCPConnection -State Listen | `
  Where-Object LocalPort -In 8000,8090,8093
```

先确认 PID 对应的进程，再正常停止，不要直接结束未知服务。

### 15.4 8093 无画面

依次检查：

1. 关闭手机 App 和其他视频客户端；
2. 检查 `<GO2_IP>:9991`；
3. 在新电脑重新生成 DPAPI 文件；
4. 确认 `.venv312` 路径正确；
5. 确认 8093 没有旧实例；
6. 查看 `/status` 的 `lastErrorCode`、`frameAgeMs` 和 `connectionDiagnostics`；
7. 重新确认 Go2 当前 IP。

### 15.5 可以 ping Go2，但 DDS 离线

检查网卡名、路由、Domain ID、CycloneDDS、SDK 安装以及 `SportModeState`/`LowState` 是否持续出现新样本。ping 只证明 IP 层可达。

### 15.6 页面可以打开，但另一台设备无法访问

确认服务监听 `0.0.0.0`、Windows 网络类型为“专用”、防火墙仅对可信网段放行，并检查前端是否错误使用访问设备自身的 `127.0.0.1`。

## 16. 更新与回滚

复现成功后记录当前版本：

```powershell
git rev-parse HEAD
git status
```

更新：

```powershell
git switch main
git pull --ff-only
git lfs pull
```

更新前应备份本机 `.env`、数据库和运行记录。不要使用 `git reset --hard` 覆盖未备份配置。

回到本文基线：

```powershell
git switch --detach 6e8c06bf86532a73a1141795064d70b52af90046
git lfs pull
```

## 17. 复现记录模板

建议每次复现保存以下记录：

```text
复现日期：
操作人：
电脑/系统：
Git 提交：
Git LFS fsck：PASS / FAIL
Python 3.11：
Python 3.12：
Node.js：
Docker：
Go2 型号/固件：
Go2 IP：
网卡：
复现阶段：A / B / C / D
健康接口：
专项测试：
30 分钟稳定性：
未复现项：
异常与处理：
回滚点：
```

更完整的生产部署、安全配置和专项链路说明见 [DEPLOYMENT.md](DEPLOYMENT.md)。复现完成的标准不是“进程能启动”，而是版本可确认、状态可观察、异常可解释、服务可停止、变更可回滚。
