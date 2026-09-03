# Go2 设备密钥写入与跨电脑实时视频部署方案

日期：2026-08-21  
适用对象：现场设备负责人、视频服务器运维、视觉算法负责人  
目标：在当前视频服务器或另一台 Windows 电脑上，安全配置目标 Go2 的设备 AES Key，启动 `8093` 视频桥，并把真实画面送入 FFmpeg、MediaMTX、`camera-service` 和 PFV2。

---

## 1. 当前判断与完成标准

已确认网络层不是当前首要阻塞项：

```text
视频服务器 WLAN：192.168.8.250
目标 Go2：192.168.8.251
Go2 TCP/9991：可达
视频桥 TCP/8093：未启动
真实视频帧：未收到
PFV2：未处理真实 Go2 画面
```

当前第一阻塞项是：目标视频服务器缺少由本机、最终运行视频桥的 Windows 用户生成的 `.go2_aes_key.dpapi`。密钥检查未通过以前，程序不会进入 WebRTC 取流阶段；因此 `8093`、FFmpeg、RTSP、`camera-service` 和 PFV2 的后续失败都是预期结果。

完整成功必须同时满足：

1. 本机 DPAPI 文件可被实际运行用户解密；
2. `http://127.0.0.1:8093/status` 返回 `data.hasFrame=true`；
3. 同一接口的 `data.sequence` 持续增长；
4. MediaMTX 中 `go2_front` 路径存在发布者和读取者；
5. `camera-service` 的 `frame_seq` 持续增长；
6. PFV2 的 `frames_processed` 持续增长；
7. 连续运行 30 分钟无长期停帧、错误重启或旧进程串流。

`8090` 上的旧 `camear_new` 进程不属于本链路，不能作为任何一项成功证据。

---

## 2. 必须先理解的密钥边界

### 2.1 三种信息不是一回事

| 信息 | 用途 | 是否进入交付包 |
|---|---|---|
| Unitree Go App 账号和密码 | 向官方云端认证 | 否，只在目标电脑本机交互输入 |
| 目标 Go2 序列号 | 指定从账号名下取哪台设备的 Key | 否，只在目标电脑本机输入 |
| Go2 设备 AES Key | 建立该设备的视频会话 | 否，取得后立即 DPAPI 加密 |

Go2 设备 AES Key 不是 Wi-Fi 密码、Windows 登录密码或运动控制密钥。

### 2.2 `.go2_aes_key.dpapi` 为什么不能复制

Windows `ConvertFrom-SecureString` 在未提供共享密钥时使用当前 Windows 用户的 DPAPI。它绑定：

```text
目标电脑 + Windows 用户配置文件
```

所以以下做法全部无效：

- 从主系统电脑复制 `.go2_aes_key.dpapi` 到视频服务器；
- 在电脑 A 生成后复制到电脑 B；
- 管理员账号生成后，让普通账号或服务账号运行；
- 交互用户生成后，让 `LocalSystem` 服务运行；
- 把旧 Go2 的 DPAPI 文件用于当前目标 Go2。

每台目标电脑都要重新生成；同一台电脑更换运行用户时也要重新生成。若用任务计划程序，必须用最终任务账号登录一次、加载用户配置文件，并由该账号执行密钥配置。

### 2.3 多电脑不等于可同时取流

可以给多台电脑分别配置同一目标 Go2，但现场验证时只保留一个视频客户端。Unitree Go App、其他 WebRTC 客户端、另一台视频桥都应关闭。否则会话占用可能导致当前桥接程序连接失败或停帧。

---

## 3. 交付目录与必须补齐的文件

建议把本交付包解压到固定、无中文且不随用户变化的目录，例如：

```text
D:\Go2VideoRuntime\
├─ README_FIRST.md
├─ REQUIRED_EXTERNAL_COMPONENTS.md
├─ docs\
│  └─ GO2_DEVICE_KEY_AND_CROSS_PC_VIDEO_RUNTIME_SOLUTION_2026-08-21.md
├─ go2_dev\
│  ├─ go2-wireless-camera\
│  │  └─ wireless_collector\
│  │     ├─ app.py
│  │     ├─ fetch_device_key_portable.py
│  │     ├─ setup_go2_device_key.ps1
│  │     ├─ write_go2_device_key_dpapi.ps1
│  │     ├─ test_go2_device_key_dpapi.ps1
│  │     ├─ start_go2_bridge.ps1
│  │     ├─ stop_wireless_video_bridge.cmd
│  │     ├─ requirements.txt
│  │     └─ tests\test_status_contract.py
│  └─ unitree_webrtc_connect\
│     ├─ pyproject.toml
│     ├─ requirements-bridge-lock-2026-08-21.txt
│     └─ unitree_webrtc_connect\...
├─ runtime\
│  ├─ mediamtx\
│  │  ├─ mediamtx.exe                 ← 目标电脑自行补齐
│  │  ├─ mediamtx.yml.example         ← 本包提供，不含凭据
│  │  └─ mediamtx.yml                 ← 目标电脑本机创建，不进交付包
│  ├─ logs\
│  └─ evidence\
├─ tools\
│  └─ ffmpeg\bin\ffmpeg.exe          ← 目标电脑自行补齐，或使用 PATH
└─ vision\
   └─ camera-service\...             ← 项目方提供正式完整工程
```

### 3.1 本包已经提供

- 不含硬编码账号、密码、序列号或 AES Key 的 Go2 视频桥源码；
- `unitree_webrtc_connect` 源码和已验证版本锁定清单；
- 云端获取并本机 DPAPI 加密脚本；
- 已有明文 Key 时的本机隐藏输入脚本；
- DPAPI 有效性检查脚本；
- `8093` 启动脚本；
- MediaMTX 无凭据配置模板；
- 状态接口契约测试和本操作文档。

### 3.2 另一台电脑必须另行提供

| 必需项 | 来源/要求 | 不能直接复制的部分 |
|---|---|---|
| Python 3.12 x64 | Python 官方安装或现场软件仓 | `.venv312` 必须重建 |
| FFmpeg | 项目已批准的软件包 | 必须显示 `libx264` 编码器和 `mpjpeg` 解复用器 |
| MediaMTX | 项目已批准的 Windows x64 包 | 正式 `mediamtx.yml` 和凭据须本机生成 |
| `camera-service` | 视觉服务项目负责人提供的正式完整目录 | 不要拿旧 `camear_new` 代替 |
| PFV2 模型与环境 | 算法负责人提供 | 模型权重、推理依赖、GPU/CPU 配置必须配套 |
| Unitree 现场信息 | 设备负责人提供并本机输入 | 账号、密码、序列号、明文 Key 不发聊天、不进压缩包 |

本机当前工作区没有完整、可授权交付的 `camera-service`、PFV2 模型、FFmpeg 和 MediaMTX 二进制，因此这些内容没有伪装成已包含。另一方缺少任一项时，要向对应负责人取得正式文件。

### 3.3 永远不要复制进交付包

```text
.go2_aes_key.dpapi
.env
mediamtx.yml（正式凭据版）
Unitree 账号/密码/序列号记录
明文 AES Key
Python 虚拟环境 .venv 或 .venv312
日志、截图、缓存、__pycache__、*.pyc
```

---

## 4. 目标电脑一次性准备

以下命令在目标电脑 PowerShell 中执行。示例根目录为 `D:\Go2VideoRuntime`；实际目录不同就修改 `$RuntimeRoot`。

```powershell
$RuntimeRoot = 'D:\Go2VideoRuntime'
Set-Location $RuntimeRoot
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### 4.1 确认网络与会话占用

1. 连接与 Go2 相同的 Wi-Fi；当前现场为 `E5576-822_D7E5`。
2. 关闭 Unitree Go App 视频页、其他电脑的视频桥、VLC/浏览器中的旧实时流。
3. 执行：

```powershell
Get-NetIPConfiguration
Test-NetConnection 192.168.8.251 -Port 9991
```

必须看到当前网卡地址属于 `192.168.8.0/24`，并且 `TcpTestSucceeded : True`。如果 Go2 使用 DHCP 后 IP 改变，应先现场确认新 IP，不能继续盲用 `192.168.8.251`。

### 4.2 重建 Python 3.12 环境

不要复制另一台电脑的虚拟环境。执行：

```powershell
$WebRtcRoot = Join-Path $RuntimeRoot 'go2_dev\unitree_webrtc_connect'
$Collector = Join-Path $RuntimeRoot 'go2_dev\go2-wireless-camera\wireless_collector'

py -3.12 -m venv (Join-Path $WebRtcRoot '.venv312')
$Python = Join-Path $WebRtcRoot '.venv312\Scripts\python.exe'

& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $WebRtcRoot 'requirements-bridge-lock-2026-08-21.txt')
& $Python -m pip install --no-deps -e $WebRtcRoot
& $Python -m pip install -r (Join-Path $Collector 'requirements.txt')
& $Python -m pip check
```

验证核心导入：

```powershell
& $Python -c "import aiortc,av,cv2,fastapi,uvicorn,unitree_webrtc_connect; print('bridge imports OK')"
```

若 `PyAudio` 安装失败，应从项目软件仓取得匹配 Python 3.12 x64 的轮子，不要临时更换 Python 主版本。

---

## 5. 在目标电脑写入设备密钥

必须以最终运行视频桥的 Windows 用户操作。二选一，不要同时执行。

### 5.1 方式 A：从 Unitree 云端获取并立即 DPAPI 加密（推荐）

前提：

- 账号已绑定当前目标 Go2；
- 知道 Unitree Go App 密码和目标 Go2 序列号；
- 目标电脑能访问 Unitree 云端 HTTPS；
- 现场确认云区域，中国大陆账号通常使用 `cn`；
- 手机 App 和其他视频客户端已关闭。

执行：

```powershell
Set-Location 'D:\Go2VideoRuntime\go2_dev\go2-wireless-camera\wireless_collector'
.\setup_go2_device_key.ps1 -Region cn
```

脚本会在本机提示输入：

```text
Unitree Go App 账号
目标 Go2 序列号
Unitree Go App 密码（隐藏）
```

实现的安全边界：密码只进入子进程环境，不放入 PowerShell 参数；AES Key 只从子进程标准输出在内存中短暂传递；随后立刻写成当前用户 DPAPI 密文，并把文件 ACL 收紧为当前 Windows SID。脚本不会显示 AES Key。

如果账号区域不是中国区，改用：

```powershell
.\setup_go2_device_key.ps1 -Region global
```

需要替换已经存在的文件时，先确认设备和账号无误，再显式执行：

```powershell
.\setup_go2_device_key.ps1 -Region cn -Force
```

### 5.2 方式 B：设备负责人已有目标 Go2 AES Key

仅当设备负责人已从受控渠道取得当前目标 Go2 的 32 位十六进制 AES Key 时使用。不要把 Key 粘贴到聊天、邮件正文、命令参数或记事本文档。

```powershell
Set-Location 'D:\Go2VideoRuntime\go2_dev\go2-wireless-camera\wireless_collector'
.\write_go2_device_key_dpapi.ps1
```

在隐藏提示框中本机粘贴 Key。脚本验证长度和十六进制格式后写入 DPAPI 文件，不回显、不进入 PowerShell 历史。替换旧文件需要显式加 `-Force`。

### 5.3 验证 DPAPI 文件

```powershell
.\test_go2_device_key_dpapi.ps1
```

预期：

```text
Valid       : True
CurrentUser : <最终运行账号>
Owner       : <同一账号或其 SID>
KeyFile     : ...\wireless_collector\.go2_aes_key.dpapi
```

该检查只验证当前用户能解密且内容格式正确，不会显示 Key。若出现“Key not valid for use in specified state”之类错误，说明文件来自别的电脑/用户或用户配置文件不可用；删除错误副本后由正确账号重新生成。

---

## 6. 启动 8093 视频桥并证明收到真实帧

```powershell
Set-Location 'D:\Go2VideoRuntime\go2_dev\go2-wireless-camera\wireless_collector'
.\start_go2_bridge.ps1 -RobotIp '192.168.8.251' -NoOpenBrowser
```

脚本会：

1. 检查 `.venv312` 和 DPAPI 文件；
2. 检查目标 Go2 的 `9991`，必要时尝试兼容端口 `8081`；
3. 仅在内存中解密 Key，并通过短生命周期环境传给子进程；
4. 让 HTTP 服务只监听 `127.0.0.1:8093`；
5. 最多等待 45 秒，只有出现真实帧才返回成功。

连续验证两次：

```powershell
$s1 = Invoke-RestMethod 'http://127.0.0.1:8093/status'
Start-Sleep -Seconds 3
$s2 = Invoke-RestMethod 'http://127.0.0.1:8093/status'
$s1 | ConvertTo-Json -Depth 8
$s2 | ConvertTo-Json -Depth 8
```

通过条件：

```text
$s1.data.hasFrame = True
$s2.data.hasFrame = True
$s2.data.sequence > $s1.data.sequence
$s2.data.lastFrameAgeSec 保持较小
```

再保存一张真实画面用于现场人工确认：

```powershell
Invoke-WebRequest 'http://127.0.0.1:8093/snapshot' -OutFile 'D:\Go2VideoRuntime\runtime\evidence\go2_snapshot.jpg'
```

注意：截图可能包含现场隐私，验收结束后按项目数据规范归档或删除。

---

## 7. FFmpeg 转 H.264 并发布到 MediaMTX

### 7.1 验证 FFmpeg

```powershell
$Ffmpeg = 'D:\Go2VideoRuntime\tools\ffmpeg\bin\ffmpeg.exe'
& $Ffmpeg -hide_banner -encoders | Select-String 'libx264'
& $Ffmpeg -hide_banner -demuxers | Select-String 'mpjpeg'
```

两项必须有输出。默认保留 Go2 原始分辨率，仅保证宽高为偶数，不先强制缩小；PFV2 是否缩放应由视觉服务配置决定。

### 7.2 配置并启动 MediaMTX

把：

```text
D:\Go2VideoRuntime\runtime\mediamtx\mediamtx.yml.example
```

复制为目标电脑本机的 `mediamtx.yml`，替换：

```text
<PUBLISH_USER>
<PUBLISH_PASSWORD>
<BRIDGE_IP>
<READ_USER>
<READ_PASSWORD>
```

如果 FFmpeg、MediaMTX、camera-service 都在同一台电脑，`<BRIDGE_IP>` 可使用 `127.0.0.1`，并把发布者限制为本机。正式密码不要写回模板、聊天或交付包。

启动：

```powershell
Set-Location 'D:\Go2VideoRuntime\runtime\mediamtx'
.\mediamtx.exe .\mediamtx.yml
```

保持窗口运行，确认 `8554` 监听：

```powershell
Get-NetTCPConnection -LocalPort 8554 -State Listen
```

### 7.3 启动 FFmpeg 发布进程

以下占位值在目标电脑本地替换，不要把正式密码写入共享文档：

```powershell
$Ffmpeg = 'D:\Go2VideoRuntime\tools\ffmpeg\bin\ffmpeg.exe'
$PublishUser = '<PUBLISH_USER>'
$PublishPassword = '<PUBLISH_PASSWORD>'
$RtspPublish = "rtsp://${PublishUser}:${PublishPassword}@127.0.0.1:8554/go2_front"

& $Ffmpeg -hide_banner -loglevel info `
  -fflags nobuffer -flags low_delay `
  -f mpjpeg -i 'http://127.0.0.1:8093/stream.mjpg' `
  -an -vf 'scale=trunc(iw/2)*2:trunc(ih/2)*2' `
  -c:v libx264 -preset ultrafast -tune zerolatency `
  -pix_fmt yuv420p -g 16 -keyint_min 16 -sc_threshold 0 `
  -f rtsp -rtsp_transport tcp $RtspPublish
```

说明：在交互式首验中，凭据仍会存在于当前 PowerShell 进程和 FFmpeg 参数中。正式长期运行建议使用受限服务账号、受控配置文件或项目既有密钥管理方案；不要把包含凭据的启动命令保存到公共脚本或日志。

MediaMTX API 验证：

```powershell
Invoke-RestMethod 'http://127.0.0.1:9997/v3/paths/list' | ConvertTo-Json -Depth 10
```

`go2_front` 应显示已就绪并存在发布者。

---

## 8. 启动正式 camera-service 和 PFV2

本包不含完整 `camera-service` 和 PFV2 模型，因此本节给出接口级要求，实际启动命令以视觉服务项目的正式 README/启动脚本为准。

在 `camera-service` 的本机生产配置中设置：

```text
RTSP URL = rtsp://<READ_USER>:<READ_PASSWORD>@127.0.0.1:8554/go2_front
服务监听 = 127.0.0.1:8000（若主系统必须远程访问，再按安全策略放行）
模型 = PFV2 正式模型路径
输入源 = go2_front，不能是旧 camear_new 或测试视频
```

启动前核对：

- 完整源码、requirements/lock、配置模板、模型权重均已从视觉项目负责人处取得；
- Python/Conda 环境在目标电脑重建；
- GPU 驱动、CUDA、推理框架版本与 PFV2 要求一致；
- `8000` 未被旧进程占用；
- 配置中的 RTSP URL 和 MediaMTX 读账号一致。

端口检查：

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
```

服务启动后，通过该项目实际状态接口读取两次计数。必须证明：

```text
frame_seq(t2) > frame_seq(t1)
frames_processed(t2) > frames_processed(t1)
source/path = go2_front
最近错误为空或不影响持续处理
```

如果服务没有状态接口，负责人应补充最小监控字段，不能只凭窗口存在或端口监听判断成功。

---

## 9. 正确启动顺序

严格按下面顺序执行；前一步未通过，不启动后一步：

```text
目标电脑、最终运行用户生成 DPAPI 密钥
→ test_go2_device_key_dpapi.ps1 通过
→ 启动 8093 视频桥
→ hasFrame=true 且 sequence 增长
→ 启动 MediaMTX
→ 启动 FFmpeg 发布 H.264 RTSP
→ go2_front 显示发布者
→ 启动正式 camera-service:8000
→ frame_seq 增长
→ PFV2 frames_processed 增长
→ 连续运行 30 分钟验收
```

主系统电脑不参与当前取流验证。等视频服务器整条链本机闭环通过后，才把经鉴权的服务结果提供给主系统。

---

## 10. 30 分钟验收表

| 检查项 | 命令/证据 | 通过标准 |
|---|---|---|
| Windows 用户 | `whoami` | 与生成 DPAPI 的用户一致 |
| Go2 网络 | `Test-NetConnection 192.168.8.251 -Port 9991` | `True` |
| 密钥文件 | `test_go2_device_key_dpapi.ps1` | `Valid=True`，不显示 Key |
| 8093 | 两次 `/status` | `hasFrame=true`，`sequence` 增长 |
| 真实画面 | `/snapshot` | 现场人员确认是当前 Go2 实景 |
| H.264 | FFmpeg 日志 | 无持续解码/编码错误，使用 `libx264` |
| RTSP | MediaMTX API | `go2_front` 有 publisher/reader |
| camera-service | 正式状态接口 | `frame_seq` 增长 |
| PFV2 | 正式状态接口 | `frames_processed` 增长 |
| 稳定性 | 30 分钟抽查 | 无长期停帧，计数持续增加 |
| 旧进程隔离 | `Get-NetTCPConnection` + PID | `8090 camear_new` 未被误认或接入 |

建议在 `runtime\evidence` 保存脱敏后的状态 JSON、进程/PID、启动时间和验收时间。任何证据文件都不得包含账号、密码、序列号、明文 AES Key 或完整 RTSP 凭据。

---

## 11. 故障定位

### 11.1 DPAPI 检查失败

原因优先级：文件来自其他电脑；运行用户不同；服务账号配置文件未加载；文件损坏。处理：删除错误副本，由最终运行账号在目标电脑重新执行第 5 节。不要尝试解密或迁移旧 DPAPI 文件。

### 11.2 云端取 Key 失败

依次确认：账号是否绑定当前 Go2、App 密码是否正确、序列号是否属于当前设备、`cn/global` 区域是否正确、系统时间是否准确、HTTPS 是否可达。脚本错误信息经过脱敏，不会打印账号、序列号或 Key。

### 11.3 9991 可达但 45 秒内无帧

依次确认：手机 App/其他桥是否占用会话；设备 Key 是否属于当前 Go2；Go2 IP 是否变化；电脑是否有多条到 `192.168.8.0/24` 的冲突路由；系统防火墙或安全软件是否阻断 WebRTC/UDP；再查看 `%TEMP%\go2-webrtc-stderr.log`。日志可交付前必须做敏感信息检查。

### 11.4 8093 有帧但 RTSP 无发布者

确认 FFmpeg 输入 URL 为 `8093/stream.mjpg`；`mpjpeg` 和 `libx264` 可用；MediaMTX 已先启动；发布用户名、密码、路径、来源 IP 与 `mediamtx.yml` 一致；RTSP 使用 TCP。

### 11.5 RTSP 正常但 `frame_seq` 不增长

确认 `camera-service` 读取的是 `go2_front`；读凭据正确；没有连到旧 IP、旧路径、旧 `8090` 或测试文件；服务日志没有解码超时；PFV2 模型加载成功。

### 11.6 视频尺寸或大帧问题再次出现

先记录 Go2 实际宽高、JPEG 平均/峰值大小、FFmpeg 输出宽高和 camera-service 输入宽高。桥默认保留真实分辨率，FFmpeg 只修正奇数宽高。只有在测得带宽/解码压力确实超限后，才在 FFmpeg 增加明确的限幅缩放，例如 `scale='min(1280,iw)':-2`；修改后必须重新验证跌倒检测精度。

---

## 12. 安全与交付结论

1. 本包可以复制到其他电脑；其中的源码、模板和脚本不包含现场账号、密码、序列号或设备 Key。
2. `.go2_aes_key.dpapi` 不能复制。每台电脑、每个最终运行用户都要本地生成。
3. Python 虚拟环境不能复制，应按锁定清单重建。
4. FFmpeg、MediaMTX、正式 `camera-service`、PFV2 模型仍须由对应负责人提供。
5. 多台电脑可以分别完成配置，但取流验收时只允许一个活跃 Go2 视频会话。
6. 任何账号、App 密码、序列号和明文 AES Key 都不要发送到聊天中。

只要按第 9 节逐级通过，另一台电脑即可在不依赖主系统目录、旧 IP、旧密钥和旧进程的情况下，独立接收并处理当前目标 Go2 的实时画面。
