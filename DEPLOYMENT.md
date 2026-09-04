# Unitree Go2 机器狗项目详细部署手册

版本：2026-09-03  
仓库：<https://github.com/gq18262121731-source/unitree_go>  
适用范围：智慧康养主系统、Go2 HTTP 网关、Go2 WebRTC 视频/语音/UWB 统一运行时、RTSP 视觉链、ROS 2/LiDAR/SLAM 验证环境。

> 安全原则：第一次部署必须从 Mock 或只读模式开始。只有在场地清空、机器狗架起或进入受控测试区、急停可用、现场操作员明确授权后，才允许开启真实运动。本文中的 IP、账号、Token 和密钥均为占位符或历史默认值，必须以现场实际值为准。

## 1. 系统组成

推荐的完整拓扑如下：

```text
浏览器 / 移动端
    │ HTTP / WebSocket
    ▼
智慧康养主系统 health_new_p04（8000）
    ├── Vue 管理端（5173）
    ├── Redis（6379，可选但推荐）
    ├── SQLite（默认）/ PostgreSQL（扩展）
    ├── Go2 HTTP 网关（8090，有线 DDS/SDK 路径）
    ├── Go2 统一无线 Runtime（8093，WebRTC 视频/语音/UWB）
    └── 视觉服务（默认 8011，需另行取得完整 camera-service 包）

Go2 前置摄像头
    │ WebRTC，信令通常为 TCP 9991
    ▼
桥接电脑 8093/stream.mjpg
    │ FFmpeg / H.264
    ▼
MediaMTX（8554）──► camera-service / PFV2
```

仓库中与部署直接相关的目录：

| 目录 | 用途 |
| --- | --- |
| `health_new_p04/` | 当前智慧康养主系统，FastAPI + Vue + Flutter + 模型 |
| `go2_dev/go2-gateway/` | Go2 HTTP 网关、任务编排、只读/运动安全门、统一无线 Runtime |
| `go2_dev/go2-wireless-camera/` | 摄像头采集与历史兼容入口 |
| `go2_dev/unitree_webrtc_connect/` | Go2 WebRTC 连接库 |
| `go2_dev/unitree_sdk2_python/` | Unitree Python SDK 源码 |
| `go2_dev/unitree_ros2/` | Unitree ROS 2 工程 |
| `handoff/go2_video_portable_runtime_2026-08-21/` | 跨电脑视频部署交付包 |
| `phase*` | ROS、LiDAR、SLAM、稳定性验证脚本和证据 |

## 2. 部署模式选择

### 2.1 模式 A：无机器狗的开发演示

适合前后端开发、接口联调和页面展示：

- `health_new_p04` 使用 Mock 数据；
- `go2-gateway` 使用 `GO2_MODE=mock`；
- 不需要 Unitree SDK、Go2、ROS 2 或设备密钥；
- 推荐先完成此模式，再接真机。

### 2.2 模式 B：主系统 + Go2 只读联调

适合验证状态、摄像头、DDS/WebRTC、UWB 和接口契约：

- 保持 `GO2_CONTROL_ENABLED=false`；
- 有线网关设置 `GO2_READ_ONLY_MODE=true`；
- 不发送站立、趴下、移动或伴随命令；
- 完成至少 30 分钟稳定性测试后再考虑真实运动。

### 2.3 模式 C：受控真实运动

仅用于现场受控验收：

- 有明确的操作员、观察员和急停人员；
- 场地满足专项文档的尺寸和安全距离；
- 网关状态新鲜、DDS/WebRTC 正常、风险源在线；
- 分阶段开启 `GO2_CONTROL_ENABLED`、`FOLLOW_EXECUTION_ENABLED` 和其他运动门；
- 禁止一次性打开全部运动开关。

### 2.4 模式 D：ROS 2 / LiDAR / SLAM

建议使用独立 Ubuntu 22.04 + ROS 2 Humble 主机或虚拟机。此路径用于传感器、TF、点云和离线 SLAM 验证，不应与比赛无线统一 Runtime 混用同一个 Go2 WebRTC 会话。

## 3. 硬件、系统和软件要求

### 3.1 主系统电脑

- Windows 10/11 64 位；
- 建议 16 GB 以上内存；涉及模型推理时建议 32 GB；
- 20 GB 以上可用空间，若拉取全部 Git LFS 数据建议预留 15 GB 额外空间；
- Miniconda/Anaconda；
- Python 3.11 Conda 环境；
- Node.js 20 LTS 或与项目 Vite 版本兼容的更新 LTS；
- Docker Desktop（Redis/可选基础设施）；
- Git、Git LFS、PowerShell 7；
- 可选 NVIDIA GPU 与兼容驱动。

### 3.2 Go2 有线 DDS 网关主机

- 推荐 Ubuntu 20.04 LTS，符合仓库中 `unitree_sdk2` 的既有基线；
- Python 3.8 或更高；
- 独立有线网卡连接 Go2；
- 建议网卡地址 `192.168.123.99/24`；
- Go2 常用有线地址 `192.168.123.161`，现场必须重新确认；
- CycloneDDS 0.10.x 和 `unitree_sdk2_python`。

### 3.3 Go2 无线/WebRTC 桥接电脑

- Windows 10/11；
- Python 3.12；
- 可访问 Go2 `9991/TCP`；
- 如果电脑连接 Go2 自身热点，同时还要上传远端视频，需要第二块网卡或其他上行链路；
- 设备 AES Key 必须由最终运行用户在目标电脑本机生成 DPAPI 文件。

### 3.4 ROS 2 主机

- Ubuntu 22.04 LTS；
- ROS 2 Humble；
- 与 Go2 通信的独立网卡；
- 建议物理机；虚拟机必须使用桥接网卡，并验证组播/DDS 可用。

## 4. 端口与防火墙规划

| 端口 | 协议 | 服务 | 建议暴露范围 |
| --- | --- | --- | --- |
| 5173 | TCP | Vue/Vite 开发前端 | 本机或可信局域网 |
| 8000 | TCP | 智慧康养 FastAPI | 本机或可信局域网 |
| 8090 | TCP | Go2 HTTP 网关 | 主系统所在可信网段 |
| 8093 | TCP | Go2 统一无线 Runtime/MJPEG | 本机；跨机时仅可信网段 |
| 6379 | TCP | Redis | 仅本机/容器网络 |
| 5432 | TCP | PostgreSQL/TimescaleDB（可选） | 仅本机/容器网络 |
| 8001 | TCP | ChromaDB 容器（可选） | 仅本机/容器网络 |
| 8011 | TCP | 视觉服务默认地址 | 本机或可信网段 |
| 8554 | TCP | MediaMTX RTSP | 桥接电脑与视觉服务器之间 |
| 9991 | TCP | Go2 WebRTC 信令 | 桥接电脑到 Go2 |
| 11434 | TCP | Ollama（可选） | 仅本机 |
| 8766 | UDP | UWB/follow target 转发（可选） | 指定接收主机 |

不要把 Redis、数据库、Ollama、无鉴权 RTSP 或开发用 Vite 服务直接暴露到公网。

Windows 局域网演示可按需放行端口，以下示例必须在管理员 PowerShell 中执行：

```powershell
New-NetFirewallRule -DisplayName "Unitree Health Backend 8000" `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 `
  -Profile Private

New-NetFirewallRule -DisplayName "Unitree Go2 Runtime 8093" `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8093 `
  -Profile Private
```

如果只在本机使用，不要创建入站规则。

## 5. 获取代码与 Git LFS 数据

Windows PowerShell：

```powershell
git lfs install
git clone https://github.com/gq18262121731-source/unitree_go.git
Set-Location .\unitree_go
git lfs pull
git lfs ls-files
git status
```

Linux：

```bash
git lfs install
git clone https://github.com/gq18262121731-source/unitree_go.git
cd unitree_go
git lfs pull
git lfs ls-files
git status
```

验收要求：

1. `git lfs pull` 不报缺失对象；
2. `git status` 没有意外修改；
3. 模型、ROS bag、点云和压缩包不是只有几十字节的 LFS 指针文本；
4. 不要在仓库中创建或提交真实 `.env`、设备密钥或账号文件。

## 6. 主系统 `health_new_p04` 部署

以下命令默认在仓库根目录执行。

### 6.1 创建 Python 环境

项目文档约定环境名为 `helth`。启动脚本的历史默认值是 `health`，所以本文始终显式传入环境名。

```powershell
Set-Location .\health_new_p04
conda create -n helth python=3.11 -y
conda run -n helth python -m pip install --upgrade pip
conda run -n helth python -m pip install -r requirements.txt
```

验证关键依赖：

```powershell
conda run -n helth python -c "import fastapi, torch, pandas, cv2; print('python dependencies ok')"
```

如使用 NVIDIA GPU，可按项目 README 安装经过验证的 PyTorch CUDA 组合；安装后确认：

```powershell
conda run -n helth python -c "import torch; print(torch.__version__); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
```

### 6.2 创建本机配置

```powershell
Copy-Item .env.example .env
```

在 `.env` 中按部署模式填写。下面是安全的 Mock/本机开发基线，可追加到现有文件；不要原样用于生产：

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

LLM_PROVIDER=qwen
QWEN_API_KEY=
QWEN_MODEL=qwen-plus
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:1.7b

JWT_SECRET=replace-with-a-long-random-secret
SEED_DEFAULT_ACCOUNTS=true
SEED_DEFAULT_PASSWORD=replace-for-shared-demo

ROBOT_GATEWAY_ENABLED=true
ROBOT_GATEWAY_BASE_URL=http://127.0.0.1:8090
COMPANION_GATEWAY_BASE_URL=http://127.0.0.1:8093

VISION_SERVICE_BASE_URL=http://127.0.0.1:8011
VISION_SERVICE_LOCAL_BASE_URL=http://127.0.0.1:8011
VISION_SERVICE_POLL_ENABLED=false
FALL_DETECTION_ENABLED=false

WEATHER_PROVIDER=mock
```

生产或共享演示环境至少要：

- 使用随机生成的 `JWT_SECRET`；
- 修改默认账户密码或关闭 `SEED_DEFAULT_ACCOUNTS`；
- 将云 API Key 只保存在目标机 `.env` 或密钥管理服务；
- 将 `DEBUG=false`；
- 根据真实设备选择 `DATA_MODE=serial` 或 `mqtt`；
- 在视觉链验收前保持 `FALL_DETECTION_ENABLED=false`；
- 在机器狗网关未就绪时可暂设 `ROBOT_GATEWAY_ENABLED=false`。

生成随机 JWT Secret 的示例：

```powershell
conda run -n helth python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 6.3 启动 Redis

推荐的 Windows 开发方式是仅用 Docker 启动 Redis，其余服务在主机运行：

```powershell
docker compose -f .\docker\docker-compose.yml up -d redis
docker compose -f .\docker\docker-compose.yml ps
docker exec ai-health-iot-redis redis-cli ping
```

预期返回 `PONG`。

`docker-compose.yml` 还包含 PostgreSQL、ChromaDB、Ollama 和后端容器。当前后端默认使用本地 SQLite 和本地 Chroma 路径；不要仅因为容器存在就假定代码已经切换到 PostgreSQL/远端 Chroma。切换存储前应单独验证数据库驱动、迁移和备份恢复。

如确需启动基础设施容器：

```powershell
docker compose -f .\docker\docker-compose.yml up -d postgres redis chromadb ollama
```

### 6.4 检查或训练健康模型

检查现有产物：

```powershell
$ModelDir = '.\data\artifacts\static_health'
Get-ChildItem $ModelDir -ErrorAction SilentlyContinue
```

至少应看到模型、缩放器和特征配置。若缺失，并且已经准备训练 Excel：

```powershell
conda run -n helth python .\scripts\train_static_model.py `
  --data ".\data\raw\patients_data_with_alerts.xlsx"
```

外部数据路径可在 `.env` 中配置：

```env
STATIC_HEALTH_DATA_PATH=D:/your-data/patients_data_with_alerts.xlsx
STATIC_HEALTH_SHEET_NAME=
MODEL_DEVICE=auto
```

### 6.5 启动后端

终端 1：

```powershell
Set-Location <仓库目录>\health_new_p04
powershell -ExecutionPolicy Bypass -File .\scripts\start_server.ps1 `
  -CondaEnv helth -ListenHost 0.0.0.0 -Port 8000
```

开发时需要热重载可增加 `-Reload`。生产环境不要启用热重载。

验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/healthz
Invoke-RestMethod http://127.0.0.1:8000/api/v1/system/info | ConvertTo-Json -Depth 10
Start-Process http://127.0.0.1:8000/docs
```

`/healthz` 预期包含 `"status": "ok"`。

### 6.6 启动 Vue 前端

终端 2：

```powershell
Set-Location <仓库目录>\health_new_p04
powershell -ExecutionPolicy Bypass -File .\scripts\start_frontend.ps1 `
  -ListenHost 127.0.0.1 -Port 5173
```

首次运行会自动执行 `npm install`。如已安装依赖：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_frontend.ps1 `
  -ListenHost 127.0.0.1 -Port 5173 -SkipInstall
```

浏览器访问：<http://127.0.0.1:5173>。

局域网演示时可改为 `-ListenHost 0.0.0.0`，并在手机访问 `http://<主系统电脑IP>:5173`。此时还要确保前端调用的后端地址不是错误地固定为手机自己的 `127.0.0.1`。

### 6.7 主系统测试

```powershell
conda run -n helth pytest -q
conda run -n helth powershell -ExecutionPolicy Bypass `
  -File .\scripts\smoke_backend_http.ps1

Set-Location .\frontend\vue-dashboard
npm run check
```

若只做部署验收，至少执行 `/healthz`、系统信息接口和一次页面登录/数据刷新测试。

## 7. Go2 HTTP 网关（8090）

### 7.1 Mock 模式

Mock 模式可在 Windows 或 Linux 运行，不连接机器狗。

Linux/macOS：

```bash
cd go2_dev/go2-gateway
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
GO2_MODE=mock uvicorn app.main:app --host 0.0.0.0 --port 8090 --workers 1
```

Windows PowerShell：

```powershell
Set-Location .\go2_dev\go2-gateway
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
$env:GO2_MODE = 'mock'
.\.venv\Scripts\python.exe -m uvicorn app.main:app `
  --host 0.0.0.0 --port 8090 --workers 1
```

网关必须始终使用一个 worker。多个实例可能争用同一机器狗连接和控制权。

Mock 验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8090/health | ConvertTo-Json -Depth 10
Invoke-RestMethod http://127.0.0.1:8090/api/preflight | ConvertTo-Json -Depth 10
Invoke-RestMethod http://127.0.0.1:8090/api/capabilities | ConvertTo-Json -Depth 10
```

### 7.2 真机有线网络

在 Ubuntu 网关主机上确认网卡名称：

```bash
ip -br link
ip -br addr
```

以下命令中的 `enp3s0` 必须替换为真实的 Go2 专用网卡：

```bash
sudo ip addr flush dev enp3s0
sudo ip addr add 192.168.123.99/24 dev enp3s0
sudo ip link set enp3s0 up
ip route get 192.168.123.161
ping -c 3 192.168.123.161
```

不要对承载 SSH 或互联网连接的网卡执行 `ip addr flush`。

### 7.3 安装 Unitree SDK

```bash
cd go2_dev/go2-gateway
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cd ../unitree_sdk2_python
pip install -e .
cd ../go2-gateway
```

如果 SDK 安装提示 CycloneDDS 缺失，按 `unitree_sdk2_python` 文档安装兼容的 CycloneDDS 0.10.x，并在安装 SDK 前设置 `CYCLONEDDS_HOME`。

### 7.4 只读真机配置

编辑 `go2_dev/go2-gateway/.env`：

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

GO2_MAX_VX=0.30
GO2_MAX_VY=0.0
GO2_MAX_WZ=0.30
GO2_MAX_MOVE_DURATION=1.0
```

先执行不运动的环境检查：

```bash
source .venv/bin/activate
GO2_MODE=real python scripts/check_environment.py --strict-real
```

然后启动：

```bash
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

成功 ping 不等于 DDS 已就绪。必须确认至少收到新鲜的 `SportModeState` 或 `LowState`，且 `robotOnline=true`、状态时间戳持续更新。

### 7.5 开启真实运动前的门禁

只有完成只读验收后才能进入本节：

1. 将机器狗放在防滑、无人员和障碍物的受控区域；
2. 确认电量、姿态、急停和网络稳定；
3. 由操作员逐项核对速度与持续时间上限；
4. 先启用普通控制，伴随/Phase 7 仍保持关闭；
5. 只执行站立、停止、趴下等最小动作；
6. 伴随功能还必须满足 UWB、LiDAR、风险心跳和人工恢复授权。

普通控制的最小开关：

```env
GO2_CONTROL_ENABLED=true
GO2_READ_ONLY_MODE=false
FOLLOW_SIMULATION=true
FOLLOW_EXECUTION_ENABLED=false
PHASE7_MOTION_EXECUTION_ENABLED=false
```

运行严格预检：

```bash
python scripts/verify_preflight.py --base-url http://127.0.0.1:8090 --require-ready
python scripts/verify_real_acceptance.py \
  --base-url http://127.0.0.1:8090 \
  --exercise-camera --require-dispatch-ready
```

不要在无人监护时运行 `verify_motion.py`，也不要绕过它的人工确认。

## 8. Go2 统一无线 Runtime（8093）

当前比赛路径已将独立 STA 视频客户端退役。应使用：

```text
go2_dev/go2-gateway/scripts/Start-Go2WirelessRuntime.ps1
```

它通过一个 WebRTC PeerConnection 统一提供视频、语音、UWB 和伴随生命周期，避免多个客户端争用 Go2 会话。

### 8.1 准备 Python 3.12 环境

脚本要求固定位置存在：

```text
go2_dev/unitree_webrtc_connect/.venv312/Scripts/python.exe
```

可在仓库根目录执行：

```powershell
py -3.12 -m venv .\go2_dev\unitree_webrtc_connect\.venv312
.\go2_dev\unitree_webrtc_connect\.venv312\Scripts\python.exe `
  -m pip install --upgrade pip
.\go2_dev\unitree_webrtc_connect\.venv312\Scripts\python.exe `
  -m pip install -e .\go2_dev\unitree_webrtc_connect
.\go2_dev\unitree_webrtc_connect\.venv312\Scripts\python.exe `
  -m pip install -r .\go2_dev\go2-gateway\requirements.txt
```

如果使用交付包，优先安装其中锁定的依赖文件：

```text
handoff/go2_video_portable_runtime_2026-08-21/
go2_dev/unitree_webrtc_connect/requirements-bridge-lock-2026-08-21.txt
```

### 8.2 在目标电脑生成设备密钥

密钥文件位置：

```text
go2_dev/go2-wireless-camera/wireless_collector/.go2_aes_key.dpapi
```

必须由最终运行 Runtime 的 Windows 用户在目标电脑生成：

```powershell
Set-Location .\go2_dev\go2-wireless-camera\wireless_collector
.\setup_wireless.ps1
```

禁止：

- 将明文 AES Key 写入仓库、文档或聊天；
- 复制其他电脑/其他 Windows 用户生成的 DPAPI 文件；
- 在日志中打印账号、密码或解密后的 Key。

### 8.3 网络预检

关闭手机 Unitree Go App 的实时视频页面和其他 Go2 视频客户端，然后检查：

```powershell
$RobotIp = '<GO2_IP>'
Test-NetConnection $RobotIp -Port 9991
Get-NetTCPConnection -LocalPort 8093 -State Listen -ErrorAction SilentlyContinue
```

`TcpTestSucceeded` 必须为 `True`，并且 8093 不应被未知进程占用。

### 8.4 启动统一 Runtime

```powershell
Set-Location .\go2_dev\go2-gateway
.\scripts\Start-Go2WirelessRuntime.ps1 `
  -RobotIp '<GO2_IP>' `
  -ListenHost 127.0.0.1 `
  -HealthNewUrl 'http://127.0.0.1:8000' `
  -ElderId '<ELDER_ID>' `
  -NoOpenBrowser
```

跨电脑访问视频时，将 `-ListenHost` 改为 `0.0.0.0`，并只对可信局域网放行 8093。

注意：该启动脚本面向比赛统一 Runtime，会设置真实模式和控制相关环境变量。若当前只允许视频只读，不要直接把它当成通用只读启动器；应按 [设备密钥与跨电脑视频方案](GO2_DEVICE_KEY_AND_CROSS_PC_VIDEO_RUNTIME_SOLUTION_2026-08-21.md) 使用视频交付包，或由负责人确认运行模式。

### 8.5 运行时验收

```powershell
$First = Invoke-RestMethod http://127.0.0.1:8093/status -TimeoutSec 5
Start-Sleep -Seconds 5
$Second = Invoke-RestMethod http://127.0.0.1:8093/status -TimeoutSec 5
$First | ConvertTo-Json -Depth 10
$Second | ConvertTo-Json -Depth 10

Invoke-WebRequest http://127.0.0.1:8093/snapshot `
  -OutFile "$env:TEMP\go2_snapshot.jpg" -TimeoutSec 10
Start-Process "$env:TEMP\go2_snapshot.jpg"
```

必须确认：

- `connected=true`；
- `hasFrame=true`；
- `latestFrame.sequence` 或 `frameCount` 在 5 秒内增长；
- `frameAgeMs` 不持续增加，通常应小于 1000 ms；
- 快照为当前现场画面，不是旧帧、黑屏或错误摄像头；
- Runtime 日志中没有持续重连风暴。

停止统一 Runtime 时，优先在其控制台输入 `EXIT`，让 `StopMove()` 和 WebRTC 清理正常执行。不要直接杀进程，除非正常停止完全失效。

## 9. RTSP 与视觉服务链

仓库中的 `camera-service/` 当前只有接口文档，不包含可独立启动的完整视觉服务实现。部署 PFV2 前必须从项目负责人处取得完整 `camera-service` 包、依赖、模型权重和配置。

推荐链路：

```text
Go2 8093/stream.mjpg
  └── FFmpeg 转 H.264
       └── MediaMTX rtsp://<SERVER>:8554/go2_front
            └── camera-service（建议仅服务器本机读取）
```

完整步骤见 [Go2 实时画面接入视觉服务器实施手册](GO2_REALTIME_VIDEO_TO_VISION_SERVER_RUNBOOK_2026-08-21.md)。最小验证：

```powershell
ffprobe -rtsp_transport tcp `
  "rtsp://<READ_USER>:<READ_PASSWORD>@<SERVER>:8554/go2_front"
```

RTSP 账号应分为只允许发布的账号和只允许读取的账号；密码中含特殊字符时必须 URL 编码。MediaMTX API 建议只监听 `127.0.0.1`。

主系统对视觉服务的配置示例：

```env
VISION_SERVICE_BASE_URL=http://127.0.0.1:8011
VISION_SERVICE_LOCAL_BASE_URL=http://127.0.0.1:8011
VISION_SERVICE_CAMERA_ID=go2_front_camera
VISION_SERVICE_POLL_ENABLED=true
VISION_SERVICE_POLL_HZ=2
VISION_BRIDGE_PRODUCTION_MODE=false
FALL_DETECTION_ENABLED=false
```

先完成持续取帧和结果查询，再开启跌倒事件投递。断流时必须显示不可用/降级，不能把旧的 `NON_FALL` 结果当作当前安全状态。

## 10. 主系统与 Go2 的集成配置

主系统电脑与网关电脑不是同一台时，将 `127.0.0.1` 替换为对应电脑的可信局域网 IP：

```env
ROBOT_GATEWAY_ENABLED=true
ROBOT_GATEWAY_BASE_URL=http://<GATEWAY_PC_IP>:8090
COMPANION_GATEWAY_BASE_URL=http://<RUNTIME_PC_IP>:8093
ROBOT_GATEWAY_TIMEOUT_SECONDS=1.5

COMPANION_BOUND_ELDER_ID=<ELDER_ID>
COMPANION_ROBOT_ID=go2_edu_01
COMPANION_ROBOT_NAME=小康01
COMPANION_ROBOT_MODEL=Go2 EDU
```

联通检查：

```powershell
Invoke-RestMethod http://<GATEWAY_PC_IP>:8090/health
Invoke-RestMethod http://<GATEWAY_PC_IP>:8090/api/preflight
Invoke-RestMethod http://<RUNTIME_PC_IP>:8093/status
Invoke-RestMethod http://127.0.0.1:8000/api/v1/system/info | ConvertTo-Json -Depth 10
```

主系统应显示配置的网关地址、视频运行时状态和真实的 `robotOnline`/降级原因，而不是仅凭端口可达显示“在线”。

## 11. ROS 2、LiDAR 与 SLAM

### 11.1 安装路线

1. 准备 Ubuntu 22.04 + ROS 2 Humble；
2. 按 [ROS 2 Humble 虚拟机安装计划](ROS2_HUMBLE_INSTALL_PLAN_PHASE_5_2_3_VM.md) 配置环境；
3. 阅读 [Unitree ROS 2 README](go2_dev/unitree_ros2/README.md)；
4. 配置与 Go2 同网段的接口和 CycloneDDS；
5. 先做 DDS 只读 topic 验证；
6. 再做 TF、LiDAR 坐标链和离线 Point-LIO；
7. 最后才考虑在线 SLAM 或导航。

### 11.2 验证顺序

```bash
source /opt/ros/humble/setup.bash
ros2 topic list
ros2 node list
ros2 topic hz <TOPIC_NAME>
ros2 topic echo <TOPIC_NAME> --once
```

按以下专项文档继续：

- [DDS/ROS 2 传感器桥](DDS_ROS2_BRIDGE_PHASE_5_3.md)
- [TF 坐标验证](TF_COORDINATE_VALIDATION_PHASE_5_4.md)
- [LiDAR 坐标链分析](LIDAR_COORDINATE_CHAIN_ANALYSIS_PHASE_5_4_1.md)
- [SLAM 路线选择](SLAM_ROUTE_SELECTION_PHASE_5_4_4.md)
- [Point-LIO 目标场景验证](POINT_LIO_TARGET_SCENE_PHASE_5_4_5.md)

禁止在没有验证 TF 方向、时间戳、点云帧和外参时直接启用导航。仓库中的 `phase*` 数据优先用于离线复现，不要在真机上边运动边排查基础坐标问题。

## 12. 推荐启动顺序

完整演示建议严格按以下顺序：

1. 检查电源、网络、场地、急停和 Go2 电量；
2. 启动 Redis；
3. 启动 `health_new_p04` 后端，确认 `/healthz`；
4. 启动 Vue 前端并验证页面；
5. 以 Mock 或只读模式启动 8090 网关；
6. 确认 `/api/preflight` 和 DDS/状态诊断；
7. 启动 8093 Runtime，确认帧序号持续增长；
8. 如需视觉链，启动 MediaMTX、FFmpeg 和完整 camera-service；
9. 验证主系统能看到机器人、视频和视觉服务的真实状态；
10. 完成 30 分钟只读稳定性测试；
11. 由现场负责人授权后，才分阶段开启真实运动。

推荐停止顺序：

1. 停止新的任务派发；
2. 调用停止/急停并确认机器狗静止；
3. 正常退出 8093 Runtime；
4. 停止 8090 网关；
5. 停止视觉链和前端；
6. 停止主系统后端；
7. 按需执行 `docker compose ... down`，生产数据卷不要加 `-v`。

## 13. 生产化建议

### 13.1 后端

- 使用反向代理终止 TLS；
- 后端仍保持合理的 worker 数。Go2 网关必须是单 worker；
- 使用服务账号运行，不使用管理员/root；
- `.env` 权限限制为运行账号可读；
- 日志做轮转和脱敏；
- 对 8000/8090/8093 实施来源 IP 限制；
- Redis/数据库只监听本机或容器网络；
- 为 SQLite/模型/配置和任务审计日志制定备份方案。

### 13.2 自动启动

主系统可使用 Windows 任务计划程序或 NSSM，Linux 网关可使用 systemd。无论采用哪种方式，都必须：

- 设置明确的工作目录；
- 使用固定 Python 环境；
- 启动失败自动重试但限制频率；
- 停止时给进程发送正常终止信号；
- 不把 API Key 写在服务命令行参数中；
- Go2 运动服务重启后默认回到未授权/停止状态。

### 13.3 更新与回滚

更新前：

```powershell
git status
git rev-parse HEAD
```

记录当前提交、备份 `.env`（不得上传）、数据库和模型产物。更新后执行：

```powershell
git pull --ff-only
git lfs pull
conda run -n helth python -m pip install -r .\health_new_p04\requirements.txt
```

不要用 `git reset --hard` 覆盖现场未备份配置。回滚应切换到已记录、已验收的提交或发布包，并恢复与该版本匹配的依赖和数据库备份。

## 14. 最终验收清单

### 14.1 代码与配置

- [ ] Git 提交号已记录；
- [ ] Git LFS 无缺失对象；
- [ ] `.env`、DPAPI、Token、账号和密码未进入 Git；
- [ ] 目标机时间和时区正确；
- [ ] 防火墙只开放必要端口。

### 14.2 主系统

- [ ] Redis `PING` 返回 `PONG`；
- [ ] `GET /healthz` 返回成功；
- [ ] `GET /api/v1/system/info` 配置符合现场；
- [ ] Vue 页面可访问且能刷新数据；
- [ ] WebSocket 不持续断开；
- [ ] 模型和数据文件存在，或已明确使用规则兜底。

### 14.3 Go2 网关

- [ ] 只运行一个网关实例；
- [ ] 网卡、机器人 IP、Domain ID 正确；
- [ ] 真实 DDS 样本持续更新；
- [ ] `/health`、`/api/preflight`、`/api/status` 返回真实状态；
- [ ] 只读模式下运动请求被拒绝；
- [ ] 真实运动前严格预检通过。

### 14.4 视频与视觉

- [ ] 8093 `connected=true`、`hasFrame=true`；
- [ ] 帧序号持续增长；
- [ ] 快照是当前现场画面；
- [ ] RTSP 可被 `ffprobe` 连续读取；
- [ ] camera-service 输入和处理计数持续增加；
- [ ] 断流时显示降级而非复用旧安全结果。

### 14.5 稳定性

- [ ] 连续运行至少 30 分钟；
- [ ] 无进程退出、持续重连或内存失控；
- [ ] 断网恢复后状态先降级再恢复；
- [ ] 日志不包含明文凭据或个人敏感数据；
- [ ] 停止流程会让机器狗进入静止状态。

## 15. 常见故障排查

### 15.1 后端启动脚本找不到 Conda Python

现象：提示找不到 `health` 或 `helth` 环境。

处理：

```powershell
conda env list
powershell -ExecutionPolicy Bypass -File .\scripts\start_server.ps1 `
  -CondaEnv helth
```

不要混用系统 Python 和 Conda Python。

### 15.2 8000 端口占用

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen
Get-Process -Id <PID>
```

先确认进程身份，再正常停止。不要盲目结束未知服务。

### 15.3 页面能打开但手机访问后端失败

检查：

- 后端是否监听 `0.0.0.0:8000`；
- Windows 网络是否为“专用网络”；
- 防火墙是否只对 Private Profile 放行；
- 手机和电脑是否在同一可互访网段；
- 页面是否错误使用了手机自身的 `127.0.0.1`。

### 15.4 Go2 可以 ping，但网关显示离线

ping 只证明 IP 层可达。继续检查：

```bash
curl http://127.0.0.1:8090/api/robot/diagnostics/dds
GO2_MODE=real python scripts/check_environment.py --strict-real
```

重点核对网卡名、路由、Domain ID、CycloneDDS、SDK 安装以及 `SportModeState`/`LowState` 是否有新样本。

### 15.5 8093 无画面

按顺序检查：

1. 关闭手机 App 和其他视频客户端；
2. `Test-NetConnection <GO2_IP> -Port 9991`；
3. DPAPI 文件是否由当前 Windows 用户生成；
4. `.venv312` 是否存在且依赖完整；
5. 8093 是否被旧进程占用；
6. `/status` 中 `lastErrorCode`、`frameAgeMs` 和重连次数；
7. Go2 IP 是否仍是历史地址。

### 15.6 LFS 文件只有指针内容

```powershell
git lfs install
git lfs pull
git lfs checkout
```

然后重新检查文件大小。不要手工编辑 LFS 指针。

### 15.7 语音、Qwen 或天气不可用

先确认离线演示是否可以接受；如需云服务，检查 `.env` 中的 Provider、API Host、模型名和 Key，随后阅读 [语音部署故障排查](health_new_p04/docs/VOICE_DEPLOYMENT_TROUBLESHOOTING.md)。不要在截图或日志中暴露 Key。

## 16. 专项文档索引

- [智慧康养主系统 README](health_new_p04/README.md)
- [主系统视频桥接对接](health_new_p04/docs/main-system-video-bridge-integration.md)
- [摄像头运行时](health_new_p04/camera_runtime_external/CAMERA_PROJECT_RUNTIME.md)
- [语音部署故障排查](health_new_p04/docs/VOICE_DEPLOYMENT_TROUBLESHOOTING.md)
- [Go2 网关 README](go2_dev/go2-gateway/README.md)
- [主系统与 Go2 集成契约](go2_dev/go2-gateway/HEALTH_NEW_INTEGRATION.md)
- [Go2 统一无线 Runtime](go2_dev/go2-gateway/docs/GO2_WIRELESS_UNIFIED_RUNTIME.md)
- [Robot Video Gateway](go2_dev/go2-gateway/docs/ROBOT_VIDEO_GATEWAY.md)
- [设备密钥与跨电脑视频方案](GO2_DEVICE_KEY_AND_CROSS_PC_VIDEO_RUNTIME_SOLUTION_2026-08-21.md)
- [Go2 到视觉服务器 Runbook](GO2_REALTIME_VIDEO_TO_VISION_SERVER_RUNBOOK_2026-08-21.md)
- [大帧、分辨率、RTSP 与 PFV2 排障](GO2_REALTIME_VIDEO_LARGE_FRAME_SOLUTION_2026-08-21.md)
- [只读稳定性验收](GO2_READONLY_STABILITY_PHASE_6_1_B.md)
- [ROS 2 Humble 安装计划](ROS2_HUMBLE_INSTALL_PLAN_PHASE_5_2_3_VM.md)

部署过程中的每次配置变更都应记录：时间、操作人、机器、Git 提交号、变更项、验证结果和回滚方式。真实设备部署以“可停止、可观察、可回滚”为完成标准。
