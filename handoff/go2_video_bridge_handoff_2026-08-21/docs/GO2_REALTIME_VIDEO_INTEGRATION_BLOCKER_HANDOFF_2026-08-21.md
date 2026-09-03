# Go2 实时画面接入问题交付文档

版本：`2026-08-21`

文档状态：`BLOCKED - L1 网络与桥接层未打通`

适用对象：核心工程师、Go2 设备工程师、桥接电脑工程师、网络工程师、视觉服务工程师、现场验收人员

本文档用于交接当前“电脑通过网线连接 Go2，但无法实时接收到机器狗前置画面”的排查结果。本文档只涉及只读视频接收、网络诊断和视觉服务接入，不涉及机器狗运动控制、DDS 控制、告警投递或模型修改。

---

## 1. 最终结论

当前无法获取 Go2 实时画面的主要原因不是视觉模型缺失，而是视频输入链路尚未建立。

当前链路实际状态如下：

```text
Go2 前置相机
    -> Go2 网络/IP                  BLOCKED：当前 IP 未知，电脑未发现有效邻居
    -> WebRTC 信令 9991/TCP         BLOCKED：尚未连通
    -> wireless_collector            BLOCKED：桥接工程不在当前电脑
    -> 本地 MJPEG 8093               BLOCKED：无服务监听
    -> FFmpeg 转码                   BLOCKED：没有运行中的发布进程
    -> MediaMTX RTSP 8554            BLOCKED：没有运行中的 MediaMTX
    -> camera-service                READY FOR RTSP，但未收到 Go2 RTSP
    -> PFV2 视觉处理                 未进入真实 Go2 画面处理
```

当前判定：

```text
L1 Go2 到桥接电脑：FAIL / BLOCKED
L2 桥接电脑本地画面：BLOCKED
L3 桥接电脑到 RTSP 服务器：BLOCKED
L4 RTSP 到 camera-service：未开始
L5 视觉处理：未开始真实 Go2 画面处理
```

核心工程师需要补齐三类信息后，才能继续现场接入：

1. Go2 当前网络模式和真实 IP。
2. Go2 WebRTC 桥接工程及其 Python 运行环境。
3. 视觉服务器或本机 MediaMTX/RTSP 发布目标的实际部署信息。

---

## 2. 现场已确认事实

### 2.1 电脑硬件链路

已确认“以太网”网卡物理连接正常：

| 项目 | 当前值 |
| --- | --- |
| 网卡名称 | `以太网` |
| 网卡型号 | `Realtek Gaming GbE Family Controller` |
| 网卡状态 | `Up` |
| 链路状态 | `Connected` |
| 链路速率 | `500 Mbps` |
| 本机 MAC | `F8-ED-FC-EF-D3-A1` |
| 接口索引 | `19` |

因此，网线插入、网卡启用和物理层协商基本正常。

### 2.2 电脑当前网络配置

当前以太网 IPv4：

```text
169.254.62.97
```

当前以太网没有有效默认网关，网络配置显示为：

```text
网络类型：未识别的网络
IPv4 连通性：LocalNetwork
IPv4 默认网关：无
```

`169.254.0.0/16` 是 Windows 在 DHCP 未获得地址时自动生成的链路本地地址。它只能说明本机给自己分配了一个临时地址，不能证明 Go2 处于同一网段，也不能证明 Go2 已经获得地址。

### 2.3 邻居发现结果

在以太网接口上没有发现有效的 Go2 ARP 邻居：

```text
没有有效 Reachable/Stale IPv4 邻居
没有发现 Go2 MAC 地址
没有发现可确认的 Go2 IP
```

对当前 `169.254.62.0/24` 的链路本地地址进行只读探测，没有发现其他响应主机。

### 2.4 端口和进程结果

以下端口没有发现监听服务：

| 端口 | 预期用途 | 当前状态 |
| ---: | --- | --- |
| `8091` | 备用有线 SDK 采集器 | 未监听 |
| `8093` | WebRTC 桥接器本地 MJPEG | 未监听 |
| `8000` | camera-service HTTP | 未监听 |
| `8554` | MediaMTX RTSP | 未监听 |
| `9991` | Go2 WebRTC 信令 | 本机未监听；此端口应在 Go2 侧探测 |
| `9997` | MediaMTX API | 未监听 |

当前没有发现正在运行的：

```text
wireless_collector
FFmpeg 视频发布进程
MediaMTX
camera-service/Uvicorn
Go2 WebRTC 接收桥
```

### 2.5 Go2 地址探测结果

已对历史材料中出现的地址进行只读 TCP 探测，未获得有效的 `9991/TCP` 连接。

涉及的历史或候选地址包括：

```text
192.168.12.1
192.168.123.161
192.168.123.1
192.168.8.248
192.168.8.249
192.168.8.250
192.168.8.252
192.168.8.253
192.168.8.254
```

注意：上述地址只能作为排查候选，不能当作当前 Go2 地址。探测时电脑仍优先使用现有 WLAN 路由，未证明以太网与 Go2 处于同一 IP 网段。

---

## 3. 文件和工程核查结果

### 3.1 已存在且可继续使用的视觉服务工程

以下文件均已确认存在：

```text
E:\国赛\camera-service
E:\国赛\camera-service\.venv\Scripts\python.exe
E:\国赛\camera-service\app\main.py
E:\国赛\camera-service\app\camera\source_manager.py
E:\国赛\camera-service\app\camera\capture_worker.py
E:\国赛\camera-service\app\post_fall_v2\post_fall_runtime_service.py
E:\国赛\camera-service\configs\post_fall_v2_candidate.env
E:\国赛\camera-service\models\post_fall_v2\efficientnet_b0\fall_candidate_efficientnet_b0.onnx
```

当前模型文件 SHA256：

```text
e9871cdb4687055fb5d934dd994c2ad883adac0c2acab375c240f8e005ee04cc
```

该 SHA256 与交接材料记录一致。因此，当前“无法获取画面”不能归因于 PFV2 模型文件缺失或模型哈希不一致。

### 3.2 明确缺失的 Go2 开发目录

以下目录在当前电脑不存在：

```text
E:\笨笨狗\go2_dev
```

因此其下的所有工程都无法直接使用，包括：

```text
E:\笨笨狗\go2_dev\go2-wireless-camera\wireless_collector
E:\笨笨狗\go2_dev\unitree_webrtc_connect
E:\笨笨狗\go2_dev\go2-wireless-camera\collector
```

### 3.3 明确缺失的桥接脚本

根据核心工程师运行手册，现场应至少存在以下文件，但当前未找到：

```text
wireless_collector\README.md
wireless_collector\app.py
wireless_collector\start_sta_wireless.ps1
wireless_collector\start_wireless.ps1
wireless_collector\stop_wireless_video.cmd
wireless_collector\setup_wireless.ps1
```

这些文件负责：

```text
建立 Go2 WebRTC 只读视频会话
检查 Go2 9991/TCP
启动本地 8093 服务
输出 /status、/snapshot、/stream.mjpg
保存或读取设备密钥
停止旧的视频会话
```

缺少这些脚本时，不能根据手册中的命令直接启动桥接服务。

### 3.4 明确缺失或无法确认的 Python 环境

手册中指定的 Python 路径不存在：

```text
E:\笨笨狗\go2_dev\unitree_webrtc_connect\.venv312\Scripts\python.exe
```

`E:\国赛\camera-service\.venv` 虽然存在，且包含 `aiortc`、`av` 等通用视频库，但此前已确认其中没有：

```text
unitree_webrtc_connect
```

因此不能直接把 camera-service 的虚拟环境当作 Go2 WebRTC 桥接环境。

### 3.5 DPAPI 设备密钥无法确认

手册要求桥接目录可能存在：

```text
wireless_collector\.go2_aes_key.dpapi
```

由于桥接目录整体不存在，目前无法确认：

```text
设备 AES Key 是否已配置
密钥是否绑定当前 Windows 用户
当前运行账号是否能够解密密钥
```

不应在聊天、普通文档或代码仓库中索取或记录明文账号、密码和 AES Key。

### 3.6 压缩包核查结论

已检查微信文件中的 `dog.zip` 和 `dog(1).zip`。其中主要是：

```text
跌倒/非跌倒图片数据
离线训练和评估脚本
模型验证脚本
live_fall_monitor.py
模型输出和测试报告
```

未发现以下桥接工程目录：

```text
wireless_collector
unitree_webrtc_connect
go2-wireless-camera
start_sta_wireless.ps1
```

因此，`dog.zip` 不能替代缺失的 Go2 WebRTC 桥接工程。

### 3.7 FFmpeg 和 MediaMTX 状态

本机可以找到一个 FFmpeg 可执行文件：

```text
E:\MathModelingTools\Software\Octave\Octave-11.3.0\mingw64\bin\ffmpeg.exe
```

但该 FFmpeg 尚未完成本项目的版本、编码能力、路径和运行方式验收，也没有正在执行的视频发布进程。

当前未确认存在已部署且可运行的 MediaMTX 实例。至少可以确认：

```text
8554/TCP 未监听
9997/TCP 未监听
```

因此不能进入 RTSP 发布和服务器接收阶段。

---

## 4. 当前问题清单

### P0：网络地址未建立

现象：

```text
以太网有物理链路
但本机仅有 169.254.62.97
无默认网关
无 Go2 ARP 邻居
Go2 IP 未知
9991/TCP 未验证通过
```

影响：

```text
电脑无法向 Go2 发起 WebRTC 信令连接
桥接程序即使存在，也无法确定目标地址
后续所有画面处理服务都没有输入
```

初步原因：

1. Go2 可能没有通过 DHCP 给电脑分配地址。
2. Go2 可能处于 AP、STA-L 或其他网络模式。
3. 电脑可能需要配置与 Go2 相同网段的静态 IPv4。
4. 当前连接的网线可能只提供物理连接，但两端网络配置不匹配。
5. Go2 当前 IP 可能已经变化，历史地址不可直接复用。

### P0：视频桥接工程缺失

现象：

```text
E:\笨笨狗\go2_dev 不存在
wireless_collector 不存在
unitree_webrtc_connect 不存在
```

影响：

```text
无法建立 Go2 WebRTC 视频会话
无法产生 8093/stream.mjpg
无法获取 /status 和 /snapshot
无法给 FFmpeg 提供输入
```

### P1：下游视频转发链未启动

现象：

```text
FFmpeg 发布进程不存在
MediaMTX 未监听 8554
camera-service 未监听 8000
```

影响：

```text
即使 Go2 画面能够在桥接电脑本地显示，也无法送入远端视觉服务器
```

### P1：camera-service 不是 Go2 WebRTC 原生客户端

当前 camera-service 支持的主要输入是：

```text
RTSP
本地视频文件
Mock
```

它不会自动通过以下方式读取 Go2：

```text
Go2 WebRTC 9991
DDS
Unitree SDK
机器狗控制链
```

因此，直接启动 camera-service 不能解决 Go2 原生画面获取问题。必须先由独立桥接器输出 RTSP 或 MJPEG。

### P1：设备现场信息不完整

以下信息当前未确认：

```text
Go2 当前网络模式：AP / STA-L / 其他
Go2 当前 IP
Go2 前置相机是否已启用
Go2 是否被手机 App 或其他程序占用视频会话
当前 WebRTC 信令端口是否为 9991
视觉服务器真实 IP 或域名
MediaMTX 部署位置
RTSP 发布账号和读取账号
```

### P2：管理员权限不足

此前尝试为“以太网”配置临时静态地址时，Windows 返回：

```text
拒绝访问
```

这说明当前终端不是管理员权限。该操作没有成功，也没有改变现有网络配置。

当前仍是：

```text
Ethernet IPv4 = 169.254.62.97
```

---

## 5. 为什么现在无法获取画面

从工程链路看，画面必须经过以下连续步骤：

```text
1. 电脑知道 Go2 当前 IP
2. 电脑能够访问 Go2:9991/TCP
3. WebRTC 桥接器建立只读视频会话
4. 桥接器获得持续增长的视频帧
5. 桥接器在本机输出 MJPEG
6. FFmpeg 将 MJPEG 转为 H.264
7. FFmpeg 发布到 MediaMTX
8. MediaMTX 提供 RTSP
9. camera-service 读取 RTSP
10. CaptureWorker 写入 FrameBuffer
11. PFV2 从 FrameBuffer 采样并推理
```

当前第 1、2、3、4 步均未通过，因此第 5 至第 11 步没有可用输入。

更直接地说：

```text
网线连接成功 ≠ IP 网络连接成功
机器狗已启动 ≠ 相机视频会话已建立
camera-service 文件齐全 ≠ camera-service 能直接读取 Go2 WebRTC
模型文件存在 ≠ 系统已经收到实时画面
```

当前最主要的阻塞是：

```text
电脑不知道应该连接哪个 Go2 IP；
电脑没有处于可访问 Go2 的 IP 网段；
用于建立 WebRTC 并输出 8093 画面的桥接工程不在本机。
```

---

## 6. 需要核心工程师补交的文件包

建议核心工程师以压缩包或受控代码仓库形式补交以下内容。

### 6.1 Go2 WebRTC 桥接工程

建议目录结构：

```text
go2-wireless-camera/
  wireless_collector/
    README.md
    app.py
    requirements.txt
    start_sta_wireless.ps1
    start_wireless.ps1
    stop_wireless_video.cmd
    setup_wireless.ps1
    .env.example
  collector/
    README.md
    app.py
    requirements.txt
```

至少需要说明：

```text
Python 版本
启动入口
默认 Go2 IP
GO2_WEBRTC_IP 覆盖方式
WebRTC 信令端口
本地服务端口
视频会话冲突处理方式
断线重连方式
日志位置
停止方式
```

### 6.2 Unitree WebRTC Python 环境

需要提供以下之一：

```text
完整源代码和 requirements.txt
可复现的虚拟环境安装步骤
已验证的 Python 解释器路径
已验证的包版本清单
```

必须能验证：

```text
import unitree_webrtc_connect
import aiortc
import av
```

不建议直接复制其他 Windows 用户的 `.venv`，因为其中可能包含绝对路径、用户绑定信息或不可复现依赖。

### 6.3 设备密钥配置说明

不要求通过聊天交付明文密钥。需要交付：

```text
密钥首次配置命令
密钥绑定的 Windows 用户说明
如何验证密钥存在但不打印明文
更换运行用户后的重新配置步骤
设备账号权限要求
```

### 6.4 现场网络参数表

核心工程师或设备工程师需要填写：

| 参数 | 必填内容 |
| --- | --- |
| Go2 网络模式 | AP / STA-L / 其他 |
| Go2 当前 IP | 现场实际地址 |
| Go2 子网掩码 | 现场实际值 |
| Go2 WebRTC 端口 | 通常为 `9991`，需现场确认 |
| 电脑业务网卡 | `以太网` 或其他 |
| 电脑静态地址要求 | 若需要，提供具体地址 |
| 视觉服务器地址 | IP 或域名 |
| MediaMTX 地址 | IP、端口、路径 |
| RTSP 发布账号 | 通过安全渠道交付 |
| RTSP 读取账号 | 通过安全渠道交付 |
| 是否允许保存截图 | 是 / 否 |

---

## 7. 推荐恢复顺序

### 阶段 A：确认设备事实

由设备工程师确认：

```text
Go2 已开机且未处于故障状态
前置相机可用
手机 Unitree Go App 已退出实时视频页面
其他电脑没有占用 Go2 视频会话
Go2 当前网络模式
Go2 当前 IP
```

禁止直接把历史地址 `192.168.12.1` 或其他旧地址当成当前地址。

### 阶段 B：恢复电脑网络

在管理员 PowerShell 中执行前，先由网络工程师确认 Go2 网段。

如果确认 Go2 位于某个固定网段，例如 `192.168.123.0/24`，电脑可以临时使用同网段地址。示例：

```powershell
Get-NetIPConfiguration -InterfaceAlias "以太网"

Set-NetIPInterface `
  -InterfaceAlias "以太网" `
  -AddressFamily IPv4 `
  -Dhcp Disabled

New-NetIPAddress `
  -InterfaceAlias "以太网" `
  -IPAddress "<PC_STATIC_IP>" `
  -PrefixLength 24
```

`<PC_STATIC_IP>` 必须由现场网络参数决定，不能机械使用示例地址。

配置后验证：

```powershell
Get-NetIPConfiguration -InterfaceAlias "以太网"
Test-Connection "<GO2_IP>" -Count 2
Test-NetConnection "<GO2_IP>" -Port 9991
Get-NetNeighbor -InterfaceAlias "以太网" -AddressFamily IPv4
```

只有 `9991/TCP` 成功，才进入桥接器启动。

### 阶段 C：恢复桥接器

在桥接工程目录中：

```powershell
$env:GO2_WEBRTC_IP = "<GO2_IP>"
Set-Location "<WIRELESS_COLLECTOR_ROOT>"
.\start_sta_wireless.ps1 -NoOpenBrowser
```

验证：

```powershell
Invoke-RestMethod "http://127.0.0.1:8093/status" |
  ConvertTo-Json -Depth 20

Invoke-WebRequest `
  "http://127.0.0.1:8093/snapshot" `
  -OutFile "$env:TEMP\go2_bridge_snapshot.jpg"
```

必须满足：

```text
serviceState=running
videoState=ready
connected=true
hasFrame=true
frameCount 持续增加
latestFrame.sequence 持续增加
frameAgeMs 不持续增长
captureFps > 0
```

### 阶段 D：建立 RTSP 转发

如果视觉服务器在远端：

```text
桥接电脑 8093 MJPEG
    -> FFmpeg H.264
    -> 视觉服务器 MediaMTX 8554/go2_front
```

先由网络工程师确认：

```powershell
Test-NetConnection "<VISION_SERVER>" -Port 8554
```

再启动 FFmpeg。RTSP 用户名和密码不能写入普通日志或交付文档。

### 阶段 E：接入 camera-service

camera-service 运行目录：

```text
E:\国赛\camera-service
```

启动前保持安全配置：

```text
MOCK_CAMERA_ENABLED=false
DEVICE_DISCOVERY_ENABLED=false
GO2_CONTROL_ENABLED=false
GO2_LOW_LEVEL_CONTROL_ENABLED=false
MAIN_SYSTEM_ALERT_ENABLED=false
EVENT_DELIVERY_ENABLED=false
EVENT_DELIVERY_MODE=dry_run
```

通过唯一的 camera ID 接入：

```text
camera_id=go2_front_camera
```

然后验证：

```text
GET /healthz
GET /stream/source?camera_id=go2_front_camera
GET /status?camera_id=go2_front_camera
GET /stream/latest-frame.jpg?camera_id=go2_front_camera
GET /integration/results/go2_front_camera/latest
```

### 阶段 F：现场验收

至少完成：

```text
10 分钟连续实时画面
建议 30 分钟稳定性测试
Go2 静止、转向、移动场景
一次受控断网和恢复
frame_seq 持续增长
frame_age_ms 不持续增加
重连后无需重新修改 URL
真实告警和运动控制保持关闭
```

---

## 8. 当前不应采取的做法

以下做法不能解决当前问题，且可能扩大风险：

1. 直接启动 camera-service，期待它自动发现 Go2。
2. 把 `ROBOT_ASSIST_MODE` 改成 `go2` 来获取相机画面。
3. 在 camera-service 的 `CaptureWorker` 中直接嵌入 DDS 或运动控制 SDK。
4. 盲目尝试历史 IP，未确认网络模式就修改多块网卡。
5. 把 `dog.zip` 中的离线跌倒检测脚本当作实时 Go2 WebRTC 桥。
6. 在聊天或代码中记录 Unitree 账号、密码、AES Key、RTSP 密码。
7. 为了“先看到画面”而启动 SportClient、DDS publisher 或运动控制程序。
8. 画面断流时继续把旧的 `NON_FALL` 结果解释为安全。

---

## 9. 交付判定

### 当前判定

```text
问题类型：视频输入链路阻塞
阻塞层级：L1 网络 + L2 桥接
是否缺少视觉模型：否
是否缺少 camera-service 核心代码：否
是否缺少 Go2 视频桥接工程：是
是否缺少 Go2 WebRTC 运行环境：是/无法确认
是否已建立 Go2:9991/TCP：否
是否已获得任何实时帧：否
是否已启动 RTSP 链路：否
是否触碰运动控制：否
```

### 解除阻塞的最小条件

必须同时满足：

```text
1. 提供 Go2 当前 IP 和网络模式
2. 电脑以太网配置到正确网段
3. Go2:9991/TCP 可达
4. 提供并验证 wireless_collector
5. 8093/status 显示 connected=true、hasFrame=true
6. snapshot 能看到当前 Go2 前视画面
7. FFmpeg/MediaMTX 建立 H.264 RTSP
8. camera-service frame_seq 持续增长
```

在满足以上条件之前，不应对外宣称“已经完成机器狗实时画面接入”。

---

## 10. 给核心工程师的待办清单

```text
□ 提供 Go2 当前网络模式
□ 提供 Go2 当前 IP、子网和 WebRTC 端口
□ 提供 go2-wireless-camera/wireless_collector 工程
□ 提供 unitree_webrtc_connect 源码或可复现安装说明
□ 说明 DPAPI 密钥如何在当前 Windows 用户下配置
□ 关闭手机 App 和其他视频会话
□ 在管理员权限下完成电脑网卡临时配置
□ 验证 Go2:9991/TCP
□ 启动桥接器并验证 8093/status
□ 获取桥接器 snapshot
□ 提供视觉服务器地址和 MediaMTX 参数
□ 验证 8554/TCP
□ 启动 FFmpeg 并验证 RTSP
□ 启动 camera-service
□ 验证 go2_front_camera 的 frame_seq
□ 保存脱敏日志和截图
□ 保持运动控制、真实告警和事件投递关闭
```

---

## 11. 证据文件位置

核心交接手册：

```text
D:\WeChat\xwechat_files\wxid_k047egb6o87f22_adc9\msg\file\2026-08\GO2_REALTIME_VIDEO_TO_VISION_SERVER_RUNBOOK_2026-08-21.md
```

视觉服务交接文档：

```text
E:\国赛\camera-service\docs\go2_camera_vision_handoff_2026-08-21.md
```

当前问题交付文档：

```text
E:\国赛\docs\GO2_REALTIME_VIDEO_INTEGRATION_BLOCKER_HANDOFF_2026-08-21.md
```

视觉服务模型：

```text
E:\国赛\camera-service\models\post_fall_v2\efficientnet_b0\fall_candidate_efficientnet_b0.onnx
```

---

## 12. 交付摘要

当前电脑已经确认网线物理连接，但没有建立 Go2 可用的 IP 通信。与此同时，核心工程师手册中要求的 `wireless_collector`、`unitree_webrtc_connect` 和相关启动脚本不在当前电脑，导致无法建立 Go2 WebRTC 只读视频会话，也无法产生本地 `8093/stream.mjpg`。

`E:\国赛\camera-service` 本身是完整的视觉处理工程，模型文件也已存在且哈希一致，但它只接收 RTSP、文件或 Mock，不能直接读取 Go2 的 WebRTC/DDS/SDK 画面。因此，当前问题应由“设备网络 + Go2 视频桥接”工程师优先处理，视觉算法代码暂不需要修改。

当前最准确的交接结论是：

```text
视觉服务已具备接收标准视频源的能力；
Go2 真实视频源尚未接入；
阻塞原因是网络参数缺失和 Go2 视频桥接工程缺失；
尚未获取到任何实时 Go2 画面；
未执行机器狗运动控制。
```
