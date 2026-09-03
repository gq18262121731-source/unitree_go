# Go2 实时画面接入远端视觉服务器实施手册

版本：`2026-08-21`  
适用对象：机器狗设备工程师、桥接电脑运维工程师、网络工程师、视觉服务器工程师、现场验收人员  
实施范围：只读获取 Go2 前置摄像头画面，将其转换为标准 RTSP，并交给远端 `camera-service` 处理  
不包含：机器狗运动控制、真实生产告警放开、模型重新训练或阈值标定

---

## 1. 交付结论

本次推荐采用以下链路：

```text
Go2 前置摄像头
  │ 原生 WebRTC（只读视频，信令通常为 TCP 9991）
  ▼
桥接电脑（Windows）
  │ 已有 wireless_collector，输出 127.0.0.1:8093/stream.mjpg
  │ FFmpeg 转码为 H.264，并主动向远端发布
  ▼
视觉服务器上的 MediaMTX
  │ 接收 RTSP 发布，提供 rtsp://127.0.0.1:8554/go2_front
  ▼
camera-service
  │ CaptureWorker → FrameBuffer → PFV2
  ▼
HTTP / WebSocket / WebRTC / 视觉结果
```

选择这条路线的原因：

1. 当前已有 Go2 WebRTC 取流实现，不需要修改机器狗固件。
2. 已有桥接服务可输出快照和 MJPEG，历史实测达到 `1280×720`、约 `8 FPS`。
3. `camera-service` 当前正式支持 RTSP、文件和 Mock，不直接支持 Go2 WebRTC/DDS/SDK 帧。
4. 桥接电脑主动向服务器发布视频，职责清晰，也避免远端服务器直接占用 Go2 的单视频会话。
5. 视频链与机器狗控制链完全隔离，不启用任何运动控制 API。

> 首次联调只证明“画面稳定到达视觉服务”。当前 PFV2 阈值仍是工程基线，目标机器狗相机尚未完成独立标定和验收，因此不得把首次联通等同于生产级跌倒识别验收。

---

## 2. 已确认信息与待确认信息

### 2.1 已从现有工程确认的信息

| 项目 | 已确认情况 |
| --- | --- |
| Go2 原生视频获取 | 已有基于 `unitree_webrtc_connect` 的只读 WebRTC 实现 |
| 本地桥接工程 | `E:\笨笨狗\go2_dev\go2-wireless-camera\wireless_collector` |
| 本地桥接端口 | `8093/TCP` |
| 本地状态接口 | `GET /status` |
| 本地快照接口 | `GET /snapshot` |
| 本地 MJPEG | `GET /stream.mjpg` |
| 历史实测画面 | `1280×720`，约 `8.2–8.8 FPS`，帧龄约百毫秒量级 |
| 历史 Go2 AP 地址 | `192.168.12.1`，仅可作为历史参考 |
| Go2 WebRTC 信令 | 历史实测 `9991/TCP` 可达 |
| camera-service 输入 | RTSP、RTSPS（需现场验证）、本地文件、Mock |
| 正式算法模式 | `PIPELINE_MODE=post_fall_v2` |
| 算法采样率 | `5 FPS` |
| 推荐 camera_id | `go2_front_camera` |

### 2.2 现场必须重新确认的信息

下表必须在操作前填写。禁止直接复制历史 IP、账号或密码。

| 参数 | 现场填写 | 负责人 |
| --- | --- | --- |
| 联调日期/地点 |  | 现场负责人 |
| Go2 设备标识（脱敏） |  | 设备工程师 |
| Go2 接入模式：AP / STA-L / 有线 |  | 设备工程师 |
| Go2 当前 IP |  | 设备工程师 |
| 桥接电脑业务网卡名称 |  | 网络工程师 |
| 桥接电脑业务 IP |  | 网络工程师 |
| 视觉服务器操作系统 |  | 服务器工程师 |
| 视觉服务器业务 IP/域名 |  | 网络工程师 |
| MediaMTX RTSP 端口 | 默认 `8554` | 服务器工程师 |
| RTSP 路径 | 默认 `go2_front` | 服务器工程师 |
| RTSP 发布用户名 | 通过安全渠道提供 | 服务器工程师 |
| RTSP 读取用户名 | 通过安全渠道提供 | 服务器工程师 |
| camera-service 实际目录 |  | 视觉工程师 |
| camera-service HTTP 端口 | 默认 `8000` | 视觉工程师 |
| 是否允许保存截图/关键帧 | 是 / 否 | 数据负责人 |
| 是否允许本地 Qwen 读取关键帧 | 是 / 否 | 数据负责人 |

### 2.3 当前材料中的重要缺口

1. 交接材料引用的视觉工程目录是 `E:\国赛\camera-service`，但本次检查的工作区内没有该完整工程；`E:\笨笨狗\camera-service` 只有文档文件。部署人员必须先取得完整 `camera-service` 包、配置、模型和测试文件。
2. 早期联调文档曾出现 `10.12.14.9` 等服务器地址，但这属于历史信息，不能视为当前服务器地址。
3. Go2 的 AP 名称、STA-L IP 和路由器地址可能已经变化，必须现场探测。
4. 不得把 RTSP 密码、Unitree 账号密码、AES Key、主系统 Token 写入本文档、仓库或聊天记录。

---

## 3. 人员分工

| 角色 | 主要任务 | 交付证据 |
| --- | --- | --- |
| 设备工程师 | 确认 Go2 在线、相机可用、关闭冲突视频会话、提供当前 IP | Go2 IP、网络模式、App 会话已关闭确认 |
| 桥接电脑工程师 | 启动 8093 WebRTC 桥、验证帧更新、启动 FFmpeg 发布 | `/status`、快照、FFmpeg 日志 |
| 网络工程师 | 打通桥接电脑到视觉服务器 `8554/TCP`，限制访问源 | 端口测试、路由和防火墙记录 |
| 服务器工程师 | 部署 MediaMTX、创建最小权限账号、检查 RTSP | MediaMTX 日志、`ffprobe` 结果 |
| 视觉工程师 | 启动 camera-service、接入 RTSP、验证 PFV2 | `/status`、最新帧、最新结果 |
| 验收人员 | 完成 30 分钟稳定性测试和断线恢复测试 | 验收表、时间戳、脱敏截图和日志摘要 |

---

## 4. 安全边界

本手册只允许执行视频读取和视频转发操作：

- 不创建 DDS publisher。
- 不调用 `SportClient`、运动控制或低层控制接口。
- 不设置 `ROBOT_ASSIST_MODE=go2` 来获取视频；该配置与相机取流无关。
- 保持 `GO2_LOW_LEVEL_CONTROL_ENABLED=false`。
- 保持 `GO2_CONTROL_ENABLED=false`。
- 保持 `MAIN_SYSTEM_ALERT_ENABLED=false`。
- 保持 `EVENT_DELIVERY_ENABLED=false`。
- 保持 `EVENT_DELIVERY_MODE=dry_run`。
- 不修改模型权重、输入预处理、ROI 或二分类阈值。
- 不在公网直接暴露无加密、无鉴权的 RTSP。

如果桥接电脑与视觉服务器跨公网，优先通过单位 VPN、站点到站点 VPN 或受控专网建立通路。不要把 `8554/TCP` 对整个互联网开放。

---

## 5. 联通验收标准

必须同时满足以下五层条件，才能判定“画面联通完成”。

| 层级 | 验收条件 |
| --- | --- |
| L1 Go2 到桥接电脑 | Go2 `9991/TCP` 可达；8093 状态中 `connected=true`、`hasFrame=true` |
| L2 桥接电脑本地画面 | `frameCount`/`latestFrame.sequence` 持续增加；`frameAgeMs < 1000` 为主；快照可见当前场景 |
| L3 桥接电脑到 RTSP 服务器 | FFmpeg 持续运行；MediaMTX 显示 `go2_front` 有 publisher；服务器本机 `ffprobe` 成功 |
| L4 RTSP 到 camera-service | `running=true`、`connected=true`、`stream_state=connected`、`frame_seq` 持续增加 |
| L5 视觉处理 | `pipeline_mode=post_fall_v2`、模型已加载、采样数和处理数持续增加、最新结果可查询 |

稳定性要求：

- 首次验收连续运行不少于 10 分钟。
- 正式交接建议连续运行 30 分钟。
- 期间不应持续增加 `reconnectCount`。
- 断流时必须显示不可用或降级，不能把旧的 `NON_FALL` 解释为安全。

---

## 6. 网络拓扑选择

### 6.1 推荐：Go2 和桥接电脑接入同一 STA-L 路由网络

```text
Go2 ── Wi-Fi/路由器 ── 桥接电脑 ── 单位网络/VPN ── 视觉服务器
```

优点：桥接电脑可以同时访问 Go2 和远端服务器；最适合持续发布。

现有脚本 `start_sta_wireless.ps1` 支持通过 `GO2_WEBRTC_IP` 覆盖 Go2 地址。文件内的默认 `192.168.8.248` 只是已有环境值，现场必须确认。

### 6.2 可用但需双网卡：电脑连接 Go2 自身 AP

```text
Go2 AP ── Wi-Fi 网卡 ── 桥接电脑 ── 第二网卡/有线/VPN ── 视觉服务器
```

Go2 AP 常见历史地址为 `192.168.12.1`，但必须现场验证。单块 Wi-Fi 被 Go2 热点占用后通常无法同时访问远端服务器，因此必须有第二条上行链路。

### 6.3 有线 SDK 备用路径

如 WebRTC 无法使用，可在 Linux/WSL 中通过已有 SDK 采集器读取 Go2 前置摄像头，再输出 MJPEG。现有参考工程：

```text
E:\笨笨狗\go2_dev\go2-wireless-camera\collector
```

该路径历史上使用：

```text
GO2_NETWORK_INTERFACE=eth0
本地服务端口 8091
GET /status
GET /snapshot
GET /stream.mjpg
```

备用路径最终仍应通过 FFmpeg 发布到服务器 RTSP，不要直接把 SDK/DDS 帧塞入 `camera-service` 的 `CaptureWorker`。

---

## 7. 阶段 A：桥接电脑启动 Go2 视频

以下命令在桥接电脑的 Windows PowerShell 中执行。

### 7.1 检查文件和 Python 环境

```powershell
$BridgeRoot = "E:\笨笨狗\go2_dev\go2-wireless-camera\wireless_collector"
$WebRtcPython = "E:\笨笨狗\go2_dev\unitree_webrtc_connect\.venv312\Scripts\python.exe"

Test-Path -LiteralPath $BridgeRoot
Test-Path -LiteralPath $WebRtcPython
Test-Path -LiteralPath (Join-Path $BridgeRoot ".go2_aes_key.dpapi")
```

三个结果都应为 `True`。如果 Python 只存在于 `.venv` 而不是 `.venv312`，不得盲目改脚本；先由维护人员确认实际环境并运行测试。

### 7.2 首次配置设备密钥

只有 `.go2_aes_key.dpapi` 不存在时才执行：

```powershell
Set-Location "E:\笨笨狗\go2_dev\go2-wireless-camera\wireless_collector"
.\setup_wireless.ps1
```

要求：

1. 在本机交互式输入绑定 Go2 的 Unitree 账号和密码。
2. 使用 Unitree Go App 账号密码，不是邮箱本身的密码。
3. 脚本将设备 AES Key 使用当前 Windows 用户的 DPAPI 加密保存。
4. 不复制、打印或上传解密后的 AES Key。
5. DPAPI 文件通常只能由创建它的 Windows 用户解密；更换运行账号后需要重新配置。

### 7.3 确认没有视频会话冲突

Go2 视频可能只允许一个有效会话。启动桥接服务前：

1. 关闭手机 Unitree Go App 中的实时视频页面。
2. 关闭其他电脑上的 Go2 视频程序。
3. 确认本机没有旧的 8093 进程：

```powershell
Get-NetTCPConnection -LocalPort 8093 -State Listen -ErrorAction SilentlyContinue
```

如果输出旧进程，先使用已有 `stop_wireless_video.cmd` 停止，再重新启动。

### 7.4 检查 Go2 信令端口

把下面地址替换为现场实际值：

```powershell
$Go2Ip = "<GO2_IP>"
Test-NetConnection $Go2Ip -Port 9991
```

预期：`TcpTestSucceeded : True`。

若失败，先处理网卡、SSID、VLAN、路由和 Go2 在线状态，不要继续启动 FFmpeg。

### 7.5 推荐方式：STA-L 启动

```powershell
$env:GO2_WEBRTC_IP = "<GO2_IP>"
Set-Location "E:\笨笨狗\go2_dev\go2-wireless-camera\wireless_collector"
.\start_sta_wireless.ps1 -NoOpenBrowser
```

脚本会：

- 检查 Python 环境和 DPAPI 密钥；
- 检查 Go2 `9991/TCP`；
- 使用 `LocalSTA` 建立 WebRTC；
- 在后台启动 `127.0.0.1:8093`；
- 最多等待约 35 秒，直到获得有效帧。

### 7.6 AP 模式启动

如果电脑直接连接 Go2 AP，且已有脚本中的 SSID 与现场完全一致：

```powershell
Set-Location "E:\笨笨狗\go2_dev\go2-wireless-camera\wireless_collector"
.\start_wireless.ps1
```

注意：当前脚本包含历史 SSID 检查。如果现场 Go2 SSID 已变化，脚本可能主动拒绝启动。此时应由维护人员确认设备身份后更新配置或使用经批准的手动启动方式，不要通过删除安全检查来绕过。

### 7.7 验证本地桥接服务

```powershell
$Status1 = Invoke-RestMethod "http://127.0.0.1:8093/status" -TimeoutSec 5
Start-Sleep -Seconds 5
$Status2 = Invoke-RestMethod "http://127.0.0.1:8093/status" -TimeoutSec 5

$Status1.data | ConvertTo-Json -Depth 10
$Status2.data | ConvertTo-Json -Depth 10

Invoke-WebRequest "http://127.0.0.1:8093/snapshot" `
  -OutFile "$env:TEMP\go2_bridge_snapshot.jpg" `
  -TimeoutSec 10
```

必须检查：

```text
serviceState = running
videoState = ready
connected = true
hasFrame = true
latestFrame.sequence 在 5 秒内增加
frameAgeMs 不持续增加，通常 < 1000 ms
captureFps > 0
lastErrorCode = null
```

人工打开临时快照，确认不是旧画面、黑屏或错误摄像头：

```powershell
Start-Process "$env:TEMP\go2_bridge_snapshot.jpg"
```

只有本节通过，才进入 RTSP 发布阶段。

---

## 8. 阶段 B：视觉服务器部署 RTSP 中继

推荐在视觉服务器上部署 MediaMTX。其职责仅是接收桥接电脑发布的视频，并在本机向 `camera-service` 提供 RTSP。

### 8.1 安装要求

1. 从 MediaMTX 官方发布页下载与服务器系统、CPU 架构匹配的固定版本。
2. 记录版本号和下载文件 SHA256。
3. 不使用来历不明的二进制包。
4. 生产环境应作为系统服务运行；首次联调可前台运行以便观察日志。

官方安装说明：<https://mediamtx.org/docs/kickoff/install>

### 8.2 最小权限配置示例

在服务器创建 `mediamtx.yml`。下列账号和密码都是占位符，必须替换，并通过安全渠道交付：

```yaml
logLevel: info
logDestinations: [stdout, file]
logFile: mediamtx.log

rtsp: true
rtspAddress: :8554
rtspTransports: [tcp]

api: true
apiAddress: 127.0.0.1:9997

authMethod: internal
authInternalUsers:
  - user: <PUBLISH_USER>
    pass: <PUBLISH_PASSWORD>
    ips: ["<BRIDGE_IP>/32"]
    permissions:
      - action: publish
        path: go2_front

  - user: <READ_USER>
    pass: <READ_PASSWORD>
    ips: ["127.0.0.1/32"]
    permissions:
      - action: read
        path: go2_front

  - user: any
    pass:
    ips: ["127.0.0.1/32", "::1/128"]
    permissions:
      - action: api
      - action: metrics
      - action: pprof

paths:
  go2_front:
```

说明：

- 发布账号只能从桥接电脑 IP 发布 `go2_front`。
- 读取账号仅允许服务器本机使用。
- API 只监听 `127.0.0.1`，不对外开放。
- 如果 camera-service 运行在容器中，`127.0.0.1` 不是宿主机；应由服务器工程师按容器网段调整读取账号的 `ips`，并使用宿主机网关或同一 Docker 网络中的服务名。
- 如果密码包含 `@`、`:`、`/`、`?`、`#` 等字符，放入 RTSP URL 前必须做 URL 编码。
- 配置文件不得提交到代码仓库。可使用哈希密码或受控密钥注入。

MediaMTX 官方说明支持 RTSP 客户端发布和读取，并支持按用户、IP、动作和路径限制权限：

- <https://mediamtx.org/docs/publish/rtsp-clients>
- <https://mediamtx.org/docs/features/authentication>

### 8.3 启动 MediaMTX

Linux 示例：

```bash
./mediamtx ./mediamtx.yml
```

Windows PowerShell 示例：

```powershell
.\mediamtx.exe .\mediamtx.yml
```

检查端口：

Linux：

```bash
ss -lntp | grep 8554
curl -sS http://127.0.0.1:9997/v3/paths/list
```

Windows：

```powershell
Get-NetTCPConnection -LocalPort 8554 -State Listen
Invoke-RestMethod "http://127.0.0.1:9997/v3/paths/list" | ConvertTo-Json -Depth 20
```

### 8.4 防火墙要求

只放行：

```text
源：<BRIDGE_IP>
目标：<VISION_SERVER_IP>
协议：TCP
端口：8554
用途：RTSP 发布
```

不要开放 UDP 端口范围；本方案强制 RTSP over TCP。不要将 MediaMTX 管理 API `9997` 暴露到外部。

网络工程师完成规则后，在桥接电脑验证：

```powershell
Test-NetConnection "<VISION_SERVER_IP>" -Port 8554
```

预期：`TcpTestSucceeded : True`。

---

## 9. 阶段 C：桥接电脑将 MJPEG 发布为 RTSP

### 9.1 检查 FFmpeg

```powershell
ffmpeg -version
ffprobe -version
```

如果命令不存在，安装受信任来源的 FFmpeg，并记录版本。不要下载来源不明的打包程序。

### 9.2 首次前台发布

先确保 `http://127.0.0.1:8093/status` 中 `hasFrame=true`，再执行：

```powershell
$VisionServer = "<VISION_SERVER_IP_OR_DNS>"
$PublishUser = "<PUBLISH_USER_URL_ENCODED>"
$PublishPassword = "<PUBLISH_PASSWORD_URL_ENCODED>"
$PublishUrl = "rtsp://${PublishUser}:${PublishPassword}@${VisionServer}:8554/go2_front"

ffmpeg `
  -hide_banner `
  -loglevel info `
  -f mpjpeg `
  -i "http://127.0.0.1:8093/stream.mjpg" `
  -an `
  -vf "fps=8,scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2" `
  -c:v libx264 `
  -preset veryfast `
  -tune zerolatency `
  -pix_fmt yuv420p `
  -g 16 `
  -keyint_min 16 `
  -sc_threshold 0 `
  -b:v 1800k `
  -maxrate 2200k `
  -bufsize 3600k `
  -rtsp_transport tcp `
  -f rtsp `
  $PublishUrl
```

参数说明：

| 参数 | 用途 |
| --- | --- |
| `-f mpjpeg` | 明确输入是 8093 的 MIME multipart JPEG 流 |
| `fps=8` | 匹配历史实测帧率，避免制造重复高帧率 |
| `libx264` | 转成视觉服务和媒体服务器普遍支持的 H.264 |
| `veryfast + zerolatency` | 控制编码耗时和缓冲 |
| `-g 16` | 约每 2 秒一个关键帧 |
| `1800k–2200k` | 适用于当前 720p、约 8 FPS 的初始码率范围 |
| `-rtsp_transport tcp` | 简化防火墙并减少 UDP 丢包影响 |

如果 CPU 占用过高，可先将输出调整为 `960×540`、`1200k`；任何画质调整都要重新保存验收截图并记录。

> RTSP URL 可能在进程命令行中可见。必须使用仅能发布单一路径的低权限账号，并把链路限制在受控内网或 VPN 中。不要复用管理员密码或其他系统密码。

### 9.3 FFmpeg 正常表现

前台日志应持续出现：

```text
frame=...
fps=...
bitrate=...
speed=...
```

以下情况视为失败：

- `Connection refused`：MediaMTX 未监听或端口错误。
- `Connection timed out`：路由、ACL 或防火墙不通。
- `401 Unauthorized`：发布账号、密码或权限错误。
- `404 Not Found`/路径错误：发布路径与配置不一致。
- 输入端持续 `Connection reset`：8093 桥接服务停止或画面停滞。
- 日志仍在运行但 8093 的 `frameAgeMs` 持续增加：不得视为有效直播。

### 9.4 后台化要求

首次联调通过后，再把 FFmpeg 做成 Windows 计划任务或受控服务。服务要求：

1. 在 8093 桥接服务启动成功后运行。
2. FFmpeg 异常退出后延迟 2–5 秒重启。
3. 连续失败时退避，避免高频重启。
4. 日志按日期轮转，不记录完整带密码 RTSP URL。
5. 运行账号必须能解密 `.go2_aes_key.dpapi`；不要随意改为其他 Windows 用户。
6. 停止顺序为先停 FFmpeg，再停 8093 桥接。

---

## 10. 阶段 D：在视觉服务器验证 RTSP

### 10.1 检查 MediaMTX 路径

```bash
curl -sS http://127.0.0.1:9997/v3/paths/list
```

应看到 `go2_front` 路径存在 publisher 和视频轨道。

### 10.2 使用 ffprobe 检查编码和尺寸

Linux：

```bash
export GO2_RTSP_URL='rtsp://<READ_USER_URL_ENCODED>:<READ_PASSWORD_URL_ENCODED>@127.0.0.1:8554/go2_front'
ffprobe -v error -rtsp_transport tcp \
  -select_streams v:0 \
  -show_entries stream=codec_name,width,height,r_frame_rate \
  -of default=noprint_wrappers=1 "$GO2_RTSP_URL"
```

Windows PowerShell：

```powershell
$Go2RtspUrl = "rtsp://<READ_USER_URL_ENCODED>:<READ_PASSWORD_URL_ENCODED>@127.0.0.1:8554/go2_front"
ffprobe -v error -rtsp_transport tcp `
  -select_streams v:0 `
  -show_entries stream=codec_name,width,height,r_frame_rate `
  -of default=noprint_wrappers=1 `
  $Go2RtspUrl
```

期望示例：

```text
codec_name=h264
width=1280
height=720
r_frame_rate=8/1
```

### 10.3 生成验证截图

```bash
ffmpeg -y -rtsp_transport tcp -i "$GO2_RTSP_URL" -frames:v 1 go2_rtsp_check.jpg
sha256sum go2_rtsp_check.jpg
```

Windows：

```powershell
ffmpeg -y -rtsp_transport tcp -i $Go2RtspUrl -frames:v 1 .\go2_rtsp_check.jpg
Get-FileHash .\go2_rtsp_check.jpg -Algorithm SHA256
```

由现场人员确认截图内容与 Go2 当前朝向一致。截图按隐私规范保存；未获授权时验收后删除。

---

## 11. 阶段 E：部署和启动 camera-service

### 11.1 先确认收到完整工程

视觉服务器必须具备至少以下内容：

```text
app/main.py
app/camera/source_manager.py
app/camera/capture_worker.py
app/post_fall_v2/post_fall_runtime_service.py
configs/post_fall_v2_candidate.env
scripts/validate_config.py
requirements.txt
models/post_fall_v2/efficientnet_b0/fall_candidate_efficientnet_b0.onnx
```

如果缺少任何关键文件，不得从零猜测重建服务，应向项目负责人索取正式部署包和版本号。

### 11.2 检查 Python、依赖和模型

下面以 Windows 工程目录为例，实际目录由现场参数表确定：

```powershell
$CameraServiceRoot = "<CAMERA_SERVICE_ROOT>"
Set-Location $CameraServiceRoot

Test-Path .\.venv\Scripts\python.exe
Test-Path .\configs\post_fall_v2_candidate.env
Test-Path .\models\post_fall_v2\efficientnet_b0\fall_candidate_efficientnet_b0.onnx

.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts\validate_config.py `
  --profile configs\post_fall_v2_candidate.env

Get-FileHash `
  .\models\post_fall_v2\efficientnet_b0\fall_candidate_efficientnet_b0.onnx `
  -Algorithm SHA256
```

交接材料记录的模型 SHA256 为：

```text
e9871cdb4687055fb5d934dd994c2ad883adac0c2acab375c240f8e005ee04cc
```

若哈希不一致，停止操作并向模型负责人核实。不要用其他 ONNX 文件替换。

### 11.3 安全启动视觉服务

建议先启动服务，再通过 `/stream/start` 动态传入 RTSP，避免把密码写进脚本：

```powershell
Set-Location $CameraServiceRoot

$env:VISION_SERVICE_ENV_FILE = (Resolve-Path ".\configs\post_fall_v2_candidate.env")
$env:DEFAULT_RTSP_URL = ""
$env:MOCK_CAMERA_ENABLED = "false"
$env:DEVICE_DISCOVERY_ENABLED = "false"
$env:MAIN_SYSTEM_ALERT_ENABLED = "false"
$env:EVENT_DELIVERY_ENABLED = "false"
$env:EVENT_DELIVERY_MODE = "dry_run"
$env:GO2_CONTROL_ENABLED = "false"
$env:GO2_LOW_LEVEL_CONTROL_ENABLED = "false"

.\.venv\Scripts\python.exe -m uvicorn app.main:app `
  --host 0.0.0.0 `
  --port 8000
```

此窗口保持运行，并观察启动错误。另开一个 PowerShell 窗口继续。

### 11.4 启动 RTSP 源

MediaMTX 与 camera-service 位于同一台服务器时，使用环回地址：

```powershell
$CameraId = "go2_front_camera"
$Go2RtspUrl = "rtsp://<READ_USER_URL_ENCODED>:<READ_PASSWORD_URL_ENCODED>@127.0.0.1:8554/go2_front"

$Body = @{
  camera_id = $CameraId
  rtsp_url = $Go2RtspUrl
} | ConvertTo-Json

Invoke-RestMethod "http://127.0.0.1:8000/stream/start" `
  -Method POST `
  -ContentType "application/json" `
  -Body $Body | ConvertTo-Json -Depth 20

$Body = $null
$Go2RtspUrl = $null
```

同一个 `camera_id` 只能有一个权威视频源。不要同时给它配置不同的 `rtsp_url`、`analysis_rtsp_url` 和 `main_rtsp_url`。

### 11.5 验证服务状态

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/healthz" |
  ConvertTo-Json -Depth 10

Invoke-RestMethod "http://127.0.0.1:8000/stream/source?camera_id=go2_front_camera" |
  ConvertTo-Json -Depth 20

$Status1 = Invoke-RestMethod "http://127.0.0.1:8000/status?camera_id=go2_front_camera"
Start-Sleep -Seconds 5
$Status2 = Invoke-RestMethod "http://127.0.0.1:8000/status?camera_id=go2_front_camera"

$Status1 | ConvertTo-Json -Depth 30
$Status2 | ConvertTo-Json -Depth 30
```

必须观察：

```text
running = true
connected = true
stream_state = connected
frame_seq 在 5 秒内增加
frame_width = 1280（或现场实际值）
frame_height = 720（或现场实际值）
frame_age_ms 不持续增加
capture_fps > 0
last_error = null
pipeline_mode = post_fall_v2
binary_model_loaded = true
frames_sampled 持续增加
frames_processed 持续增加
```

字段名称可能位于嵌套对象中，以实际 `/status` 返回为准；但语义条件不能省略。

### 11.6 获取 camera-service 最新帧

```powershell
Invoke-WebRequest `
  "http://127.0.0.1:8000/stream/latest-frame.jpg?camera_id=go2_front_camera" `
  -OutFile ".\artifacts\go2_latest_frame.jpg"

Get-FileHash ".\artifacts\go2_latest_frame.jpg" -Algorithm SHA256
```

由现场人员比较：

1. 8093 的本地快照；
2. MediaMTX RTSP 截图；
3. camera-service 最新帧。

三张图应来自同一摄像头，内容和方向一致，时间差符合现场预期。

### 11.7 验证视觉结果

```powershell
Invoke-RestMethod `
  "http://127.0.0.1:8000/integration/results/go2_front_camera/latest" |
  ConvertTo-Json -Depth 30
```

实时结果：

```text
ws://<VISION_SERVER_IP>:8000/ws/results?camera_id=go2_front_camera
```

演示页（若部署包包含并启用）：

```text
http://<VISION_SERVER_IP>:8000/demo
```

注意：

- WebRTC 画面正常不代表推理正常。
- WebSocket 有结果不代表画面没有延迟。
- PFV2 当前 `bbox` 是整幅画面占位框，不是人体检测框。
- `track_id=0` 是摄像头级占位，不是可靠的人员跟踪 ID。
- `safe_to_interpret_as_no_fall=false` 时，`NON_FALL` 不能解释为安全。

---

## 12. 30 分钟稳定性验收

### 12.1 验收动作

按以下动作连续测试：

1. Go2 静止 5 分钟。
2. Go2 缓慢前进、后退、左右转向。
3. 经历亮暗变化和普通遮挡。
4. 保持视频 30 分钟持续运行。
5. 人工断开 Go2 网络 10–20 秒后恢复。
6. 确认桥接服务重连、FFmpeg 继续发布、camera-service 恢复新帧。

本阶段不要求人员真实摔倒。需要验证模型时必须使用受控视频或安全测试流程。

### 12.2 每分钟记录字段

| 时间 | 8093 sequence | 8093 frameAgeMs | 8093 FPS | bridge reconnectCount | RTSP 可读 | camera frame_seq | camera frame_age_ms | PFV2 processed | last_error |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- |
|  |  |  |  |  |  |  |  |  |  |

### 12.3 通过条件

- `8093 latestFrame.sequence` 持续增加。
- MediaMTX publisher 未意外消失。
- camera-service `frame_seq` 单调增加。
- 绝大多数时间 `frame_age_ms < 1000 ms`。
- 没有持续 `stale`、黑屏或冻结。
- 无无法解释的频繁重连。
- 人工断线时系统进入不可用/重连状态。
- 网络恢复后无需修改 URL 即可恢复。
- 算法采样和处理计数恢复增长。

### 12.4 证据包

每次联调创建独立目录，建议包含：

```text
deployment-summary-redacted.md
bridge-status-start.json
bridge-status-end.json
mediamtx-version.txt
mediamtx-log-redacted.txt
ffmpeg-version.txt
ffmpeg-log-redacted.txt
camera-status-start.json
camera-status-end.json
rtsp-probe.txt
snapshot-sha256.txt
acceptance-checklist.md
```

所有日志必须先移除密码、Token、AES Key、完整账号和不必要的个人信息。

---

## 13. 故障排查矩阵

| 现象 | 首要检查 | 常见原因 | 处理 |
| --- | --- | --- | --- |
| `9991/TCP` 不通 | `Test-NetConnection <GO2_IP> -Port 9991` | IP 变化、未连正确网络、VLAN/防火墙、Go2 离线 | 重新确认 Go2 当前 IP 和网卡路由 |
| 8093 无法启动 | 端口占用、Python 路径、stderr 日志 | 旧进程、`.venv`/`.venv312` 不一致、依赖缺失 | 停止旧进程，使用项目已验证环境 |
| `connected=true` 但 `hasFrame=false` | `videoState`、`frameAgeMs` | App/其他程序占用视频会话、H.264 解码警告、视频轨未到达 | 关闭其他视频会话，重启桥接，观察是否持续有新帧 |
| 本地画面正常，服务器 8554 不通 | `Test-NetConnection` | 路由、ACL、服务器防火墙、监听地址错误 | 由网络工程师放行桥接 IP 到 8554/TCP |
| FFmpeg 返回 401 | MediaMTX 权限配置 | 用户名/密码错误，路径权限不匹配 | 核对 publish 用户和 `go2_front` 路径 |
| FFmpeg 正常但服务器无路径 | MediaMTX 日志/API | 发布到了错误服务器/端口/路径 | 核对 URL 和 DNS 解析 |
| RTSP 可读但 camera-service 404 最新帧 | `/stream/source`、`/status` | 源未启动、解码失败、camera_id 不一致 | 重新核对 `/stream/start` 返回和 camera_id |
| `connected=true` 但画面冻结 | `frame_seq`、`frame_age_ms` | 后端只保持连接，源已停止发帧 | 按 stale 处理，检查桥接和 FFmpeg，不得解释为安全 |
| 画面正常但 PFV2 不处理 | `pipeline_mode`、模型状态和计数 | 模式错误、模型文件缺失/哈希不符、onnxruntime 缺失 | 恢复候选配置和正式模型，执行 `pip check`/配置校验 |
| 延迟持续升高 | 三段截图时间、FFmpeg `speed`、CPU/GPU、带宽 | 编码速度不足、网络拥塞、播放器缓冲 | 降低分辨率/码率，确保 `speed >= 1`，继续用 TCP |
| 手机 App 黑屏 | 8093/FFmpeg 是否仍占会话 | Go2 单视频会话冲突 | 先停 FFmpeg，再停 8093，之后再打开 App |
| 断线后不恢复 | 三层重连计数和日志 | Go2 WebRTC、FFmpeg 或视觉服务其中一层未重连 | 从 8093 → MediaMTX → camera-service 顺序定位 |

推荐排查顺序固定为：

```text
1. Go2 IP 和 9991/TCP
2. 8093 /status
3. 8093 /snapshot
4. 桥接电脑到 8554/TCP
5. FFmpeg 日志
6. MediaMTX 路径/API
7. ffprobe RTSP
8. camera-service /healthz
9. /stream/source
10. /status 和 frame_seq
11. /latest-frame.jpg
12. PFV2 采样、处理和结果接口
```

---

## 14. 停止与回滚

正常停止顺序：

1. 停止 camera-service 的当前视频源。
2. 停止 camera-service 进程。
3. 停止桥接电脑上的 FFmpeg。
4. 停止 8093 Go2 WebRTC 桥。
5. 如无其他流使用，再停止 MediaMTX。

停止 camera-service 源时，以部署版本 `/stream/stop` 的实际请求模型为准；不要猜测请求体。最保守做法是先停止 uvicorn 进程。

停止 8093：

```text
E:\笨笨狗\go2_dev\go2-wireless-camera\wireless_collector\stop_wireless_video.cmd
```

回滚边界：

- 不删除 DPAPI 密钥文件。
- 不删除模型和验收证据。
- 不修改 Go2 网络配置、固件或控制参数。
- 不打开真实告警和事件 POST。
- 回滚后手机 App 需要观看视频时，确认 8093 已停止并释放会话。

---

## 15. 从联通到生产前还必须完成的工作

完成本文档只能证明视频链路可用。生产使用前还需要：

1. 采集目标机器狗相机的标定集，建议至少 18 个 session。
2. 采集独立验收集，建议至少 12 个 session。
3. 标定 PFV2 阈值、窗口、连续帧、恢复和冷却参数。
4. 冻结参数后仅在独立验收集运行一次。
5. 验证机器狗行走抖动、转向、曝光变化和遮挡下的误报。
6. 完成多人画面限制说明；当前只输出摄像头级跌倒信号。
7. 完成隐私审批，明确关键帧和本地 Qwen 的使用范围。
8. 先完成事件 `dry_run` 和 staging，再申请真实告警投递。
9. 机器狗控制侧必须另行完成只读、安全门禁、人工审批和急停验收。

当前基线阈值 `0.3979087471961975` 不是目标机器狗相机的生产阈值。

---

## 16. 最终签字验收表

| 检查项 | 结果 | 证据位置 | 签字/日期 |
| --- | --- | --- | --- |
| 当前 Go2 IP 和网络模式已确认 | PASS / FAIL |  |  |
| 机器狗控制功能保持关闭 | PASS / FAIL |  |  |
| 8093 连续获得新帧 | PASS / FAIL |  |  |
| 桥接快照内容正确 | PASS / FAIL |  |  |
| 桥接电脑到服务器 8554/TCP 可达 | PASS / FAIL |  |  |
| MediaMTX 鉴权和最小权限生效 | PASS / FAIL |  |  |
| FFmpeg 正常发布 H.264 | PASS / FAIL |  |  |
| 服务器 ffprobe 成功 | PASS / FAIL |  |  |
| camera-service 模型哈希正确 | PASS / FAIL |  |  |
| camera-service 获得最新帧 | PASS / FAIL |  |  |
| PFV2 采样和处理计数增长 | PASS / FAIL |  |  |
| 断线时没有伪装成安全状态 | PASS / FAIL |  |  |
| 网络恢复后链路自动恢复 | PASS / FAIL |  |  |
| 30 分钟稳定性测试通过 | PASS / FAIL |  |  |
| 外部真实告警仍保持关闭 | PASS / FAIL |  |  |

最终判断只能选择一项：

- [ ] 已完成 Go2 实时画面到远端视觉服务器的联通验收。
- [ ] 视频链路未通过，阻塞层级为 L1 / L2 / L3 / L4 / L5。
- [ ] 视频已联通，但算法目标域标定尚未完成，不能进入生产告警。

---

## 17. 现场最短执行清单

```text
□ 填写实际 Go2、桥接电脑、视觉服务器 IP
□ 关闭手机 App 和其他 Go2 视频会话
□ 桥接电脑验证 <GO2_IP>:9991
□ 启动 8093，确认 hasFrame=true 且 sequence 增长
□ 服务器启动 MediaMTX 8554，并限制发布源 IP
□ 桥接电脑验证 <VISION_SERVER_IP>:8554
□ FFmpeg 把 8093 MJPEG 转为 H.264 并发布 /go2_front
□ 服务器用 ffprobe 读取 127.0.0.1:8554/go2_front
□ 启动 camera-service，保持告警和控制关闭
□ POST /stream/start，camera_id=go2_front_camera
□ 验证 frame_seq、frame_age_ms、PFV2 sampled/processed
□ 获取最新帧并人工确认画面
□ 完成断线恢复和 30 分钟稳定性测试
□ 保存脱敏证据并签字
```

---

## 18. 参考材料

本手册依据以下现有工程资料编写：

```text
D:\微信\文件\xwechat_files\wxid_xhqvvgxovvzw22_2851\msg\file\2026-08\go2_camera_vision_handoff_2026-08-21.md
E:\笨笨狗\go2_dev\go2-wireless-camera\wireless_collector\README.md
E:\笨笨狗\go2_dev\go2-wireless-camera\wireless_collector\app.py
E:\笨笨狗\go2_dev\go2-wireless-camera\wireless_collector\start_sta_wireless.ps1
E:\笨笨狗\go2_dev\go2-wireless-camera\WIRED_BRIDGE_RUNBOOK.md
E:\笨笨狗\go2_dev\go2-gateway\docs\VIDEO_LINK_CHECK_PHASE_5_1_2_D.md
```

外部官方参考：

- MediaMTX 安装：<https://mediamtx.org/docs/kickoff/install>
- FFmpeg 向 MediaMTX 发布：<https://mediamtx.org/docs/publish/ffmpeg>
- MediaMTX RTSP 客户端：<https://mediamtx.org/docs/publish/rtsp-clients>
- MediaMTX 鉴权：<https://mediamtx.org/docs/features/authentication>
- FFmpeg RTSP 协议选项：<https://ffmpeg.org/ffmpeg-protocols.html#rtsp>
