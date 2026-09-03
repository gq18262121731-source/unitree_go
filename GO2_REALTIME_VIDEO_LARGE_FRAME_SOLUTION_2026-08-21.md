# Go2 实时画面与“大画面”问题详细解决方案

版本：`2026-08-21`  
适用对象：Go2 设备负责人、Windows 桥接电脑工程师、网络工程师、视觉服务工程师、现场验收人员  
目标：先解除当前零帧阻塞，再准确判断源分辨率、显示尺寸和转码尺寸，最终让真实 Go2 画面稳定进入 `camera-service` 与 PFV2

---

## 1. 最终问题判断

当前不是“画面太小”，而是“尚未收到任何真实视频帧”。

当前链路：

```text
Go2 当前 IP 192.168.8.251              PASS
Go2 WebRTC 信令 9991/TCP               PASS
当前 Windows 用户的 DPAPI 设备密钥      FAIL
Go2 WebRTC 认证与 SDP                    未开始
8093 MJPEG                               不存在
真实源分辨率                             未知
FFmpeg / MediaMTX 真实流                 未开始
camera-service 真实 Go2 帧               未开始
PFV2 真实 Go2 推理                       未开始
```

直接阻塞：

```text
wireless_collector\.go2_aes_key.dpapi 不存在
```

但需要准确说明：这是启动器的前置检查失败。由于程序尚未向 Go2 发起认证，还没有通过现场握手证明该固件一定要求 `data2=3` 设备密钥。现代 Go2 固件通常需要每设备 AES-128 Key；旧固件可能不需要。禁止为了通过检查而创建空文件或伪造密钥。

处理优先级：

```text
P0 生成并验证正确设备的 DPAPI Key
P0 启动 8093 并证明持续收到新帧
P1 实测源分辨率和 JPEG 大小
P1 保持原始分辨率完成 RTSP 闭环
P1 将真实 RTSP 接入 camera-service
P2 根据证据处理浏览器显示大小或码流尺寸
```

---

## 2. 当前已确认事实

| 项目 | 当前值 | 结论 |
| --- | --- | --- |
| 网络 | `E5576-822_D7E5` | Go2 与电脑位于同一 STA-L 网络 |
| 桥接电脑 WLAN | `192.168.8.250` | 当前有效地址 |
| Go2 | `192.168.8.251` | 当前有效地址 |
| Go2 `9991/TCP` | 可达 | 使用新信令路径 |
| Go2 `8081/TCP` | 不可达 | 不影响当前 9991 路径 |
| `Unitree.local` | 解析到历史地址 | 当前不得使用主机名 |
| 8093 | 未监听 | 桥接器未启动 |
| 设备 DPAPI Key | 不存在 | 当前直接阻塞 |
| FFmpeg/MediaMTX | 合成流已通过 | 不能证明真实 Go2 已接入 |
| camera-service/PFV2 | 合成流已通过 | 不能证明真实 Go2 已接入 |

当前必须显式使用：

```text
GO2_WEBRTC_MODE=sta
GO2_WEBRTC_IP=192.168.8.251
```

不要继续使用：

```text
Unitree.local
192.168.8.248
192.168.123.161
```

Go2 地址来自 DHCP，机器狗或路由器重启后可能变化。正式运行前应由网络工程师在路由器上为 Go2 配置 DHCP 地址保留，或每次启动前重新验证 `9991/TCP`。不要在未授权的情况下直接修改 Go2 本体网络配置。

---

## 3. “大画面”必须拆成四类问题

### 3.1 零帧

表现：

```text
8093 未监听
或 hasFrame=false
或 snapshot 返回 503
```

这是当前真实问题。解决认证、视频会话和收帧，不能调整 CSS、FFmpeg 分辨率或模型。

### 3.2 源分辨率低

表现：

```text
hasFrame=true
但 status.resolution 低于预期
```

这属于 Go2 WebRTC 协商、设备通道或源端能力问题。只能在真实出帧后判断。

### 3.3 源分辨率正常，但网页显示区域小

表现：

```text
status.resolution 正常
snapshot 原图正常
浏览器中的画面占用区域较小
```

这是前端布局问题。放大网页不会增加源图像细节，拉伸到 1920×1080 也不会把 720p 变成真实 1080p。

### 3.4 单帧文件过大或码流带宽过高

表现：

```text
latestFrame.size 很大
8093 客户端延迟增加
FFmpeg speed < 1
网络丢包、RTSP stale 或 CPU 占用过高
```

这是 JPEG 质量、帧率、H.264 码率或网络容量问题，与显示窗口大小不同。

---

## 4. 代码核对结论

### 4.1 桥接器不会缩小收到的 WebRTC 帧

当前 `app.py` 的核心过程：

```python
image = frame.to_ndarray(format="bgr24")
cv2.imencode(".jpg", image, ...)
width = image.shape[1]
height = image.shape[0]
```

其中没有 `cv2.resize()`。因此：

```text
8093/status 中的 resolution
= aiortc 解码后的实际帧尺寸
```

当前 JPEG 质量默认是 `80`，桥接采样上限默认是 `15 FPS`；如果 Go2 只提供约 8 FPS，桥接器不会制造真实的新帧。

### 4.2 当前网页已经按窗口等比显示

当前 CSS：

```css
.video img {
  width: 100%;
  height: calc(100vh - 90px);
  object-fit: contain;
}
```

它会完整显示画面，不主动裁剪。两侧或上下出现黑边通常是显示区域和源宽高比不同，不代表源分辨率下降。

### 4.3 PFV2 不要求浏览器显示为大画面

PFV2 会把输入按训练规则处理到 `224×224` 模型张量。浏览器是否全屏不影响模型输入。盲目把 720p 上采样到 1080p只会增加带宽和编码开销，不会产生新细节。

### 4.4 当前密钥脚本存在两个环境约束

1. `setup_wireless.ps1` 固定寻找：

```text
unitree_webrtc_connect\.venv\Scripts\python.exe
```

而 flexible 启动器优先寻找：

```text
unitree_webrtc_connect\.venv312\Scripts\python.exe
```

如果目标电脑只有 `.venv312`，密钥脚本会先报 Python 环境缺失。

2. `fetch_device_key.py` 当前固定了一个设备序列号、`cn` 区域和 `Go2` 类型。完整序列号属于设备标识，不在本文档中传播。执行 setup 前必须由设备负责人私下核对：

```text
脚本中的目标 SN = 当前现场 Go2
账号注册区域 = cn
该 Go2 已绑定到输入的 Unitree Go App 账号
```

如果任何一项不一致，账号密码正确也可能无法取得 Key。

---

## 5. 实施前安全条件

由设备负责人确认：

```text
□ 当前操作对象确实是现场目标 Go2
□ Go2 前置摄像头正常
□ 手机 Unitree Go App 已退出实时视频页面
□ 其他电脑没有占用 Go2 视频会话
□ 当前账号已在官方 App 中绑定该 Go2
□ 当前账号区域是中国区，或已明确其他区域
□ 最终运行 8093 的 Windows 用户已经确定
□ 允许该电脑访问 Unitree 云端 HTTPS 以获取设备 Key
□ 不会在聊天、日志或文档中保存账号密码和明文 Key
```

本流程只接收视频，不运行：

```text
SportClient
DDS publisher
运动控制示例
障碍物开关
灯光/音频控制
任何机器狗动作指令
```

---

## 6. 第一步：固定工作目录和变量

实际解压位置可能不同，不要机械复制历史绝对路径。以下示例使用变量：

```powershell
$HandoffRoot = "E:\笨笨狗\go2_video_bridge_handoff_2026-08-21"
$WebRtcRoot = Join-Path $HandoffRoot "go2_dev\unitree_webrtc_connect"
$BridgeRoot = Join-Path $HandoffRoot "go2_dev\go2-wireless-camera\wireless_collector"
$Go2Ip = "192.168.8.251"
```

如果实际目录位于 `E:\笨笨狗\handoff\...` 或其他盘符，只修改 `$HandoffRoot`。

检查：

```powershell
Test-Path -LiteralPath $BridgeRoot
Test-Path -LiteralPath (Join-Path $BridgeRoot "app.py")
Test-Path -LiteralPath (Join-Path $BridgeRoot "setup_wireless.ps1")
Test-Path -LiteralPath (Join-Path $BridgeRoot "start_go2_bridge_flexible.ps1")
Test-Path -LiteralPath (Join-Path $WebRtcRoot "unitree_webrtc_connect")
```

五项都应为 `True`。

---

## 7. 第二步：解决 `.venv` 与 `.venv312` 路径不一致

检查：

```powershell
Test-Path -LiteralPath (Join-Path $WebRtcRoot ".venv\Scripts\python.exe")
Test-Path -LiteralPath (Join-Path $WebRtcRoot ".venv312\Scripts\python.exe")
```

### 情况 A：两者都存在

分别确认版本和导入：

```powershell
& (Join-Path $WebRtcRoot ".venv\Scripts\python.exe") --version
& (Join-Path $WebRtcRoot ".venv312\Scripts\python.exe") --version

& (Join-Path $WebRtcRoot ".venv312\Scripts\python.exe") -c `
  "import unitree_webrtc_connect,aiortc,av,cv2,fastapi,uvicorn; print('IMPORTS_OK')"
```

### 情况 B：只有 `.venv312`

不要复制虚拟环境。建立目录联接，让旧 setup 脚本使用同一个环境：

```powershell
Set-Location $WebRtcRoot

if (-not (Test-Path -LiteralPath .\.venv)) {
  New-Item -ItemType Junction `
    -Path .\.venv `
    -Target (Resolve-Path .\.venv312) | Out-Null
}
```

再验证：

```powershell
& .\.venv\Scripts\python.exe -c `
  "import unitree_webrtc_connect; print('SETUP_RUNTIME_OK')"
```

### 情况 C：两个环境都不存在

使用交付包中的锁定依赖重建：

```powershell
Set-Location $WebRtcRoot
py -3.12 -m venv .venv312
.\.venv312\Scripts\python.exe -m pip install --upgrade pip
.\.venv312\Scripts\python.exe -m pip install `
  -r .\requirements-bridge-lock-2026-08-21.txt
.\.venv312\Scripts\python.exe -m pip install -e . --no-deps

New-Item -ItemType Junction `
  -Path .\.venv `
  -Target (Resolve-Path .\.venv312) | Out-Null
```

---

## 8. 第三步：密钥生成前预检

### 8.1 验证当前 Go2 地址

```powershell
Test-NetConnection $Go2Ip -Port 9991
```

必须：

```text
TcpTestSucceeded=True
```

不要使用 `Unitree.local`，因为现场 DNS 仍返回历史地址。

### 8.2 验证云端 HTTPS

`setup_wireless.ps1` 需要登录 Unitree 云端获取设备列表，因此目标电脑必须暂时具备互联网访问能力：

```powershell
Resolve-DnsName robot-api.unitree.com
Test-NetConnection robot-api.unitree.com -Port 443
```

如果账号属于 global 区域，应由维护人员改用对应区域地址和参数，不能把网络错误误判成账号错误。

### 8.3 私下核对设备序列号

由设备负责人通过机身标签、官方 App 设备信息或受控资产台账核对 `fetch_device_key.py` 中的目标 SN。

禁止：

```text
把完整 SN 发到公共群聊
运行会把所有设备 Key 打印到终端的列表命令并截图传播
为了让脚本成功而使用其他设备的 SN
```

如果 SN 不一致，应由代码维护人员把 SN 改成显式参数或环境变量，不应长期继续硬编码。

---

## 9. 第四步：由最终运行用户生成 DPAPI Key

必须使用未来实际运行 8093 的同一个 Windows 用户。

```powershell
Set-Location $BridgeRoot
.\setup_wireless.ps1
```

交互输入要求：

1. 输入绑定该 Go2 的 Unitree Go App 账号。
2. 输入 Unitree Go App 密码，不是邮箱服务密码。
3. 密码通过 SecureString 输入，不复制到命令行。
4. 不截取或传播终端中的敏感错误上下文。

成功标志：

```text
The device key was encrypted for the current Windows user.
```

并生成：

```powershell
$KeyFile = Join-Path $BridgeRoot ".go2_aes_key.dpapi"
Get-Item -LiteralPath $KeyFile |
  Select-Object Name,Length,LastWriteTime
```

只检查文件存在、大小大于 0 和修改时间。不要打印解密后的 Key。

setup 脚本最后提示连接某个历史 Go2 AP SSID，这是旧提示；当前采用 STA-L，应忽略该 AP 提示，继续使用 `$Go2Ip=192.168.8.251` 和 flexible 启动器。

---

## 10. 密钥生成失败的分支处理

### 10.1 `WebRTC environment is missing`

原因：`setup_wireless.ps1` 找不到 `.venv\Scripts\python.exe`。

处理：完成第 7 节的 `.venv` 目录联接或环境重建。

### 10.2 DNS、TLS、连接超时或证书错误

检查：

```powershell
Test-NetConnection robot-api.unitree.com -Port 443
Get-Item "$env:LOCALAPPDATA\Go2Wireless\cacert.pem" -ErrorAction SilentlyContinue
```

处理：

- 确认路由器能访问互联网；
- 确认系统时间正确；
- 确认企业代理或安全软件没有拦截 TLS；
- 不关闭证书校验；
- 重新执行 setup，让脚本复制本地 CA Bundle。

### 10.3 登录失败

可能原因：

```text
输入了邮箱服务密码而不是 Unitree App 密码
账号区域不正确
账号未完成登录/绑定
云端接口或 App 版本策略变化
```

处理：先在官方 App 中验证账号能够看到目标 Go2，再由设备负责人确认区域。不要连续高频重试触发风控。

### 10.4 `SN is not bound to this account`

原因优先级：

1. 脚本中固定的目标 SN 不是现场设备。
2. 输入账号没有绑定这台 Go2。
3. 使用了错误云区域。

处理：由设备负责人私下核对设备身份、账号绑定和区域。不要索取或传播其他设备的 Key。

### 10.5 云端返回空 Key

可能表示：

- 固件较旧，仍使用不需要每设备 Key 的旧认证；
- 设备信息或云端记录不完整；
- 固件/账号区域不匹配。

此时不要创建空 `.go2_aes_key.dpapi`。由设备负责人确认固件版本，再决定是否使用明确的 legacy 无 Key 启动方式。当前 flexible 启动器会阻止无 Key 启动，这是安全设计，不应直接删除检查。

### 10.6 DPAPI 文件生成但启动时无法解密

原因通常是：

```text
setup 与启动使用了不同 Windows 用户
DPAPI 文件从其他电脑复制而来
用户配置文件或权限发生变化
文件被截断
```

处理：删除操作必须由负责人确认后执行；最稳妥的是在最终运行用户下重新生成新的 DPAPI 文件。不要尝试导出明文 Key 排障。

---

## 11. 第五步：启动真实 Go2 视频桥

再次关闭手机 App 和其他视频客户端，然后：

```powershell
Set-Location $BridgeRoot

.\start_go2_bridge_flexible.ps1 `
  -RobotIp $Go2Ip `
  -NoOpenBrowser
```

该脚本应：

```text
确认 Python 环境
确认 DPAPI 文件存在
确认 8093 没有异常占用
确认 9991 或 8081 可达
在内存中解密 Key
启动 LocalSTA WebRTC
等待 hasFrame=true
从当前进程环境中移除明文 Key
```

最长等待约 45 秒。脚本显示成功前不能认为桥已经可用。

日志位置：

```powershell
$BridgeStdout = Join-Path $env:TEMP "go2-webrtc-stdout.log"
$BridgeStderr = Join-Path $env:TEMP "go2-webrtc-stderr.log"
```

查看错误前先确认日志不会意外包含敏感值。正常代码不会主动打印 AES Key。

---

## 12. 第六步：证明收到的是实时新帧

### 12.1 两次状态采样

```powershell
$S1 = Invoke-RestMethod "http://127.0.0.1:8093/status" -TimeoutSec 5
Start-Sleep -Seconds 5
$S2 = Invoke-RestMethod "http://127.0.0.1:8093/status" -TimeoutSec 5

$S1.data | ConvertTo-Json -Depth 10
$S2.data | ConvertTo-Json -Depth 10

[pscustomobject]@{
  Connected       = $S2.data.connected
  HasFrame         = $S2.data.hasFrame
  VideoState       = $S2.data.videoState
  SequenceStart    = $S1.data.latestFrame.sequence
  SequenceEnd      = $S2.data.latestFrame.sequence
  SequenceAdvanced = $S2.data.latestFrame.sequence - $S1.data.latestFrame.sequence
  Width            = $S2.data.resolution.width
  Height           = $S2.data.resolution.height
  FrameAgeMs       = $S2.data.frameAgeMs
  CaptureFps       = $S2.data.captureFps
  JpegBytes        = $S2.data.latestFrame.size
} | Format-List
```

必须满足：

```text
Connected=True
HasFrame=True
VideoState=ready
SequenceAdvanced > 0
Width > 0
Height > 0
FrameAgeMs 不持续增加，主要时间 < 1000 ms
CaptureFps > 0
```

### 12.2 现场动作验证

保存两张间隔 3 秒的快照：

```powershell
Invoke-WebRequest "http://127.0.0.1:8093/snapshot" `
  -OutFile "$env:TEMP\go2_snapshot_1.jpg"
Start-Sleep -Seconds 3
Invoke-WebRequest "http://127.0.0.1:8093/snapshot" `
  -OutFile "$env:TEMP\go2_snapshot_2.jpg"

Get-FileHash "$env:TEMP\go2_snapshot_1.jpg" -Algorithm SHA256
Get-FileHash "$env:TEMP\go2_snapshot_2.jpg" -Algorithm SHA256
```

让现场人员在镜头前做一个安全、明显但不涉及机器狗运动的标记变化。两张图片内容与 SHA256 都应变化。

只有“sequence 增长 + frameAge 正常 + 现场内容变化”同时成立，才证明不是旧帧或缓存。

---

## 13. 第七步：判断真实源分辨率

使用 8093 状态作为第一权威证据：

```text
data.resolution.width
data.resolution.height
data.latestFrame.width
data.latestFrame.height
```

判断表：

| 实测结果 | 判定 | 动作 |
| --- | --- | --- |
| `1280×720` 左右 | 达到历史成功基线 | 不改桥接分辨率，继续 RTSP |
| 高于 `1280×720` | 源端提供更高分辨率 | 第一轮 RTSP 保持原始尺寸，评估 CPU/带宽 |
| 明显低于 `1280×720` | 源分辨率确实低 | 保存 SDP/状态证据，检查设备通道与会话占用 |
| 宽高为 0/null | 没有有效帧 | 回到 WebRTC/解码排障 |
| 尺寸正常但 JPEG Bytes 异常大 | 编码/画面复杂度问题 | 监控质量、CPU和网络，不改模型 |

历史 `1280×720、约 8 FPS` 只能作为基线，不能替代当前现场实测。

---

## 14. 如果源分辨率低，如何继续定位

必须按顺序取证：

1. 确认手机 App 和其他客户端已经关闭。
2. 记录 8093 `resolution`、`captureFps` 和 `latestFrame.size`。
3. 记录 WebRTC SDP 中的视频 codec、profile 和协商参数，但日志必须脱敏。
4. 确认 Go2 当前固件和视频通道能力。
5. 用同一网络、同一时刻比较官方 App 中的画面清晰度。
6. 检查库的 `switchVideoChannel(True)` 是否成功执行、数据通道是否 ready。
7. 确认解码后的 `image.shape` 与 status 完全一致。

当前接口只执行“开启视频通道”，没有一个已验证的“高清/低清切换参数”。在没有厂商或协议证据前，不要猜测 RPC、修改 data channel 命令或调用其他控制接口。

如果 Go2 WebRTC 本身只提供 720p，FFmpeg 上采样到 1080p不会增加真实细节。需要更高源分辨率时，应由设备/SDK负责人确认相机通道能力，而不是让视觉工程师修改 PFV2。

---

## 15. 如果只是网页显示不够大

先确认：

```text
status.resolution 正常
snapshot 原图正常
```

无需改后端，可先：

1. 打开 `http://127.0.0.1:8093/`。
2. 浏览器按 `F11` 进入全屏。
3. 将浏览器缩放恢复为 `100%`。
4. 确认不是远程桌面窗口在二次缩放。

如需专门的全屏预览页面，前端可以采用：

```css
html, body {
  width: 100%;
  height: 100%;
  margin: 0;
  overflow: hidden;
  background: #000;
}

img {
  width: 100vw;
  height: 100vh;
  object-fit: contain;
}
```

`contain` 保留完整画面，可能有黑边；`cover` 填满屏幕但会裁剪，不适合作为跌倒检测取证默认模式。

不要把 CSS 放大后的尺寸记录成摄像头源分辨率。

---

## 16. 第八步：第一轮 RTSP 必须保持源尺寸

为了判断真实“大画面”，第一轮 FFmpeg 不应固定缩放到 1280×720。只把宽高修正为偶数，以兼容 H.264：

```powershell
ffmpeg `
  -hide_banner `
  -loglevel info `
  -f mpjpeg `
  -i "http://127.0.0.1:8093/stream.mjpg" `
  -an `
  -vf "fps=8,scale=trunc(iw/2)*2:trunc(ih/2)*2" `
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
  "rtsp://<PUBLISH_USER>:<PUBLISH_PASSWORD>@<MEDIA_SERVER>:8554/go2_front"
```

说明：

- `-f mpjpeg` 对应 8093 的 MIME multipart JPEG。
- 不把小图强制放大，也不把大图强制缩到 720p。
- `fps=8` 与历史 Go2 实测接近；以当前实测 FPS 为准。
- 发布凭据必须 URL 编码，并通过安全渠道配置。

服务器验证：

```powershell
$RtspUrl = "rtsp://<READ_USER>:<READ_PASSWORD>@127.0.0.1:8554/go2_front"

ffprobe -v error -rtsp_transport tcp `
  -select_streams v:0 `
  -show_entries stream=codec_name,width,height,r_frame_rate `
  -of default=noprint_wrappers=1 `
  $RtspUrl
```

RTSP 的 width/height 应与 8093 源尺寸一致，最多因偶数修正相差 1 像素。

---

## 17. 码率和单帧大小处理原则

初始建议：

| 源画面 | 输出 FPS | H.264 初始码率 |
| --- | ---: | ---: |
| 1280×720 | 8–10 | 1.8–2.5 Mbps |
| 1920×1080 | 8–10 | 3–4.5 Mbps |

现场以画面复杂度、CPU和网络为准。验收时观察：

```text
FFmpeg speed >= 1.0
frame 持续增长
没有持续 buffer/queue 警告
MediaMTX publisher 持续存在
camera-service frame_age_ms 不持续增加
```

如果 `latestFrame.size` 很大：

1. 先确认源分辨率和画面复杂度。
2. 确认没有同时打开大量 MJPEG 客户端。
3. 监控桥接电脑 CPU、内存和网络发送速率。
4. 只有确认 JPEG 是瓶颈后，才把质量从 80 调整到 75 等较小幅度。
5. 质量变化需要保存同场景 A/B 截图，不能仅凭文件大小决定。

当前 `app.py` 没有通过环境变量配置 JPEG 质量。若要生产化调整，应先把质量和采样 FPS 做成显式配置并增加状态字段，而不是现场直接修改构造函数中的数字。

---

## 18. 第九步：camera-service 接入与尺寸核对

启动真实源后：

```powershell
$CameraId = "go2_front_camera"
$Go2RtspUrl = "rtsp://<READ_USER>:<READ_PASSWORD>@127.0.0.1:8554/go2_front"

$Body = @{
  camera_id = $CameraId
  rtsp_url = $Go2RtspUrl
} | ConvertTo-Json

Invoke-RestMethod "http://127.0.0.1:8000/stream/start" `
  -Method POST `
  -ContentType "application/json" `
  -Body $Body
```

两次状态采样：

```powershell
$C1 = Invoke-RestMethod "http://127.0.0.1:8000/status?camera_id=go2_front_camera"
Start-Sleep -Seconds 5
$C2 = Invoke-RestMethod "http://127.0.0.1:8000/status?camera_id=go2_front_camera"
$C1 | ConvertTo-Json -Depth 30
$C2 | ConvertTo-Json -Depth 30
```

必须验证：

```text
running=true
connected=true
stream_state=connected
frame_seq 增长
frame_width/frame_height 与 RTSP 一致
frame_age_ms 不持续增加
capture_fps > 0
binary_model_loaded=true
frames_sampled 增长
frames_processed 增长
last_error=null
```

获取 camera-service 最新帧：

```powershell
Invoke-WebRequest `
  "http://127.0.0.1:8000/stream/latest-frame.jpg?camera_id=go2_front_camera" `
  -OutFile ".\artifacts\go2_camera_service_frame.jpg"
```

对比三层尺寸和内容：

```text
8093 status / snapshot
MediaMTX RTSP / ffprobe
camera-service status / latest-frame
```

如果 8093 是 1280×720，而 camera-service 变成其他尺寸，问题发生在 FFmpeg/RTSP/capture 层；如果三层尺寸一致而页面小，问题只在展示层。

---

## 19. PFV2 解释边界

真实画面进入 PFV2 后仍必须保持：

```text
PIPELINE_MODE=post_fall_v2
MAIN_SYSTEM_ALERT_ENABLED=false
EVENT_DELIVERY_ENABLED=false
EVENT_DELIVERY_MODE=dry_run
GO2_CONTROL_ENABLED=false
GO2_LOW_LEVEL_CONTROL_ENABLED=false
```

注意：

- 画面更大不等于模型更准确。
- 当前阈值仍未完成目标 Go2 相机标定。
- 机器狗运动、抖动、曝光变化会改变目标域。
- 断流时不能把最后一个 `NON_FALL` 当作安全状态。
- `safe_to_interpret_as_no_fall=false` 时必须显示视频不可用/结果降级。

---

## 20. 分层故障排查表

| 现象 | 层级 | 优先处理 |
| --- | --- | --- |
| `Device key is missing` | Key 前检 | 以最终用户运行 setup；核对 `.venv` 路径 |
| setup 找不到 Python | 环境 | 建立 `.venv → .venv312` 目录联接 |
| setup 登录失败 | 云账号 | 核对 Unitree App 密码、账号区域和互联网 |
| setup 报 SN 未绑定 | 设备身份 | 私下核对脚本目标 SN、账号绑定和区域 |
| setup 返回空 Key | 固件/云端 | 核对固件，禁止伪造空 Key |
| DPAPI 文件存在但解密失败 | Windows 用户 | 使用同一最终运行用户重新生成 |
| 9991 可达但 RobotBusy | WebRTC 会话 | 关闭手机 App 和其他视频客户端 |
| connected 但 no-frame | 视频轨 | 检查数据通道、switchVideoChannel 和解码日志 |
| hasFrame=true 但 sequence 不增长 | stale | 重连 WebRTC，不能使用旧帧 |
| 8093 尺寸低 | 源端 | 核对会话占用、SDP、固件和视频通道 |
| 8093 正常但 RTSP 尺寸变化 | FFmpeg | 移除固定缩放，保持原始尺寸 |
| RTSP 正常但 camera-service 尺寸变化 | Capture | 核对输入 URL 和解码后端 |
| 三层尺寸正常但网页小 | 前端 | 全屏或调整 CSS，不改视频源 |
| 画面卡顿且 JPEG 很大 | 性能 | 监控 CPU/带宽后再调质量/帧率 |

---

## 21. 安全停止与回滚

停止顺序：

```text
1. 停止 camera-service 当前源或服务
2. 停止 FFmpeg 发布进程
3. 停止 8093 WebRTC 桥
4. 如无其他流，再停止 MediaMTX
5. 手机 App 需要看视频时，确认 8093 已释放会话
```

停止 8093 可使用交付包中的：

```text
wireless_collector\stop_wireless_video.cmd
```

不要在回滚时：

```text
删除模型
修改 Go2 固件
开启运动控制
把 Key 写入脚本
把 DPAPI 文件复制给其他电脑或用户
开放真实告警投递
```

---

## 22. 30 分钟验收标准

### 22.1 必须通过

```text
□ 9991/TCP 使用当前 Go2 IP 持续可达
□ DPAPI Key 由实际运行用户生成
□ 8093 hasFrame=true
□ 8093 sequence 持续增长
□ 8093 resolution 有明确值
□ 两张现场变化快照内容不同
□ RTSP width/height 与 8093 一致
□ camera-service frame_seq 持续增长
□ camera-service frame_width/height 与 RTSP 一致
□ PFV2 sampled/processed 持续增长
□ 静止、转向、移动期间不持续 stale
□ 人工断网后进入降级状态
□ 网络恢复后重新获得新帧
□ 真实告警和运动控制保持关闭
```

### 22.2 建议每分钟记录

| 时间 | 8093 seq | 8093 width×height | JPEG bytes | frameAgeMs | FPS | RTSP width×height | camera frame_seq | PFV2 processed | 错误 |
| --- | ---: | --- | ---: | ---: | ---: | --- | ---: | ---: | --- |
|  |  |  |  |  |  |  |  |  |  |

### 22.3 最终成功定义

```text
当前现场内容变化
  + 8093 sequence 持续增长
  + RTSP 保持源尺寸并持续可读
  + camera-service frame_seq 持续增长
  + PFV2 持续处理
  + 断流时明确降级
= Go2 实时大画面接收完成
```

只看到网页、端口监听或一张快照不构成完成。

---

## 23. 后续代码改进建议

这些改进不是首次联通的前置条件，但应在稳定后完成：

### 23.1 去除固定设备 SN

将 `fetch_device_key.py` 改为从受控参数读取：

```text
GO2_DEVICE_SN
GO2_CLOUD_REGION
GO2_DEVICE_TYPE
```

当 `GO2_DEVICE_SN` 缺失时应失败，不应默认连接某个固定设备。

### 23.2 统一 Python 环境目录

所有脚本统一优先：

```text
.venv312
```

并兼容回退 `.venv`，避免 setup 与启动器行为不同。

### 23.3 参数化桥接质量

增加并在 `/status` 中返回：

```text
GO2_CAPTURE_FPS
GO2_JPEG_QUALITY
```

这样现场可以有证据地调整性能，不修改源码常量。

### 23.4 增加安全诊断字段

可增加：

```text
sourceWidth/sourceHeight
jpegBytes
decodeFps
encodeFps
lastDecodeError
signalingPort
```

不得增加：

```text
AES Key
账号
密码
完整设备 SN
```

---

## 24. 现场最短操作清单

```text
□ 定义 HandoffRoot、WebRtcRoot、BridgeRoot、Go2Ip
□ 确认 Go2Ip=192.168.8.251 的 9991 可达
□ 确认 .venv 路径；必要时联接到 .venv312
□ 私下核对脚本目标 SN、账号区域和 App 绑定
□ 由最终运行用户执行 setup_wireless.ps1
□ 确认 DPAPI 文件存在，不打印 Key
□ 关闭手机 App 和其他视频会话
□ 用 flexible 脚本和当前 IP 启动 8093
□ 两次读取 status，确认 sequence 增长
□ 获取两张现场变化快照
□ 记录真实 resolution、FPS 和 JPEG Bytes
□ FFmpeg 第一轮保持原始尺寸发布 RTSP
□ ffprobe 核对 RTSP 尺寸
□ camera-service 接入 go2_front_camera
□ 核对 frame_seq、尺寸和 PFV2 处理数
□ 完成断线恢复和 30 分钟验收
```

---

## 25. 当前最短下一步

当前核心工程师应执行的第一条业务命令仍然是：

```powershell
Set-Location $BridgeRoot
.\setup_wireless.ps1
```

但在执行前必须先完成：

```text
.venv 路径检查
当前目标设备 SN 私下核对
Unitree App 账号绑定和区域核对
云端 443 连通性检查
最终 Windows 运行用户确认
```

完成密钥生成后，立即使用当前实际 IP：

```powershell
.\start_go2_bridge_flexible.ps1 `
  -RobotIp "192.168.8.251" `
  -NoOpenBrowser
```

然后以 `8093/status` 的真实 `resolution` 和持续增长的 `sequence` 判断“大画面”，不要先修改前端、FFmpeg 或模型。

