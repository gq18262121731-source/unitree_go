# Go2 实时画面阻塞问题解决方案与文件交付清单

版本：`2026-08-21`  
适用对象：Go2 设备工程师、桥接电脑工程师、网络工程师、视觉服务器工程师、现场验收人员  
目标：解除当前 L1 网络和 L2 桥接阻塞，使机器狗实时画面稳定进入 `camera-service` 并被 PFV2 处理

---

## 1. 最终问题判断

当前无法看到跌倒检测画面的根因不在模型，而在模型之前的视频输入链路：

```text
Go2 有线网络没有形成有效 IPv4 通信
  +
接收电脑缺少 Go2 WebRTC 桥接工程及运行环境
  +
FFmpeg / MediaMTX / camera-service 均未启动
  =
camera-service 没有任何真实 Go2 帧可处理
```

阻塞层级：

| 层级 | 当前状态 | 直接证据 |
| --- | --- | --- |
| L1 Go2 → 接收电脑 IP 网络 | `FAIL` | 接收电脑以太网只有 `169.254.62.97`，无有效 Go2 邻居 |
| L2 Go2 WebRTC → 本地 MJPEG | `BLOCKED` | 对方电脑缺少 `wireless_collector` 和 `unitree_webrtc_connect`，8093 未监听 |
| L3 本地 MJPEG → RTSP | `BLOCKED` | FFmpeg 未运行，MediaMTX 8554 未监听 |
| L4 RTSP → camera-service | `READY / NOT STARTED` | 对方已有完整视觉服务，但没有 RTSP 输入 |
| L5 PFV2 处理 | `NOT STARTED` | 未收到任何真实 Go2 帧 |

结论：

1. 不需要先修改 PFV2 模型、阈值或推理代码。
2. 必须先恢复 Go2 有线 IP，再部署本地 WebRTC 桥。
3. 必须先看到 `8093/snapshot`，然后才配置 FFmpeg、MediaMTX 和 camera-service。
4. `camera-service` 不是 Go2 原生 WebRTC/DDS 客户端，不能绕过桥接层。

---

## 2. 本次新增交付物

已在有完整 Go2 工程的源电脑上整理安全交付目录：

```text
E:\笨笨狗\handoff\go2_video_bridge_handoff_2026-08-21
```

最终压缩包位置：

```text
E:\笨笨狗\handoff\go2_video_bridge_handoff_2026-08-21.zip
```

交付目录包含：

```text
go2_dev/
  go2-wireless-camera/
    wireless_collector/            # 主路径：Go2 WebRTC → 8093 MJPEG
    collector/                      # 备用路径：Linux/WSL SDK 取帧
  unitree_webrtc_connect/           # 主路径依赖，源码提交 611ea370...
  unitree_sdk2_python/              # 备用 collector 的 Python SDK 依赖
  tools/                            # 有线网卡配置和只读网络检查脚本
docs/                               # 阻塞、实施、历史验证文档
FILE_MANIFEST_SHA256.csv            # 包内逐文件哈希清单
```

特别增加：

```text
unitree_webrtc_connect/requirements-bridge-lock-2026-08-21.txt
wireless_collector/start_go2_bridge_flexible.ps1
```

`start_go2_bridge_flexible.ps1` 相比历史脚本：

- 优先寻找 `.venv312`，兼容回退到 `.venv`；
- 同时探测新固件常用的 `9991/TCP` 和旧信令可能使用的 `8081/TCP`；
- 不打印 AES Key；
- 等待有效帧后才报告成功；
- 只启动只读视频桥，不调用任何运动控制接口。

该新增脚本已经完成 PowerShell 语法检查，但必须在真实 Go2 上完成现场验证后才能标记为硬件验收通过。

---

## 3. 三台机器/角色必须区分

### 3.1 源电脑

指当前拥有完整 `E:\笨笨狗\go2_dev` 的电脑。它负责：

- 提供安全源码交付包；
- 提供已验证版本和哈希；
- 不提供可解密的设备密钥；
- 不提供另一台电脑可直接使用的虚拟环境。

### 3.2 目标桥接电脑

指当前通过网线连接 Go2、但只有 `169.254.62.97` 的电脑。它负责：

- 把有线网卡配置到 Go2 网段；
- 重建 Python 环境；
- 用自己的 Windows 账号重新生成 DPAPI 设备密钥；
- 启动 `8093` Go2 WebRTC 视频桥；
- 使用 FFmpeg 将 MJPEG 发布为 H.264 RTSP。

### 3.3 视觉服务器

指运行 MediaMTX 和 `camera-service` 的服务器。它负责：

- 接收桥接电脑的 RTSP 发布；
- 在本机向 camera-service 提供 RTSP；
- 运行 PFV2；
- 暂时关闭真实告警、事件 POST 和机器狗控制。

如果目标桥接电脑与视觉服务器实际是同一台电脑，也必须按这三个逻辑角色逐层验证，不能跳过 8093 和 RTSP 验收。

---

## 4. 本地有哪些文件，而对方可能没有

### 4.1 必须交给目标桥接电脑的主路径文件

| 本地文件/目录 | 用途 | 对方当前状态 | 交付方式 |
| --- | --- | --- | --- |
| `go2_dev\go2-wireless-camera\wireless_collector\app.py` | 建立 Go2 WebRTC 视频会话，输出 8093 | 缺失 | 已放入安全交付包 |
| `wireless_collector\fetch_device_key.py` | 通过受控账号流程获取设备 AES Key | 缺失 | 已放入安全交付包 |
| `wireless_collector\setup_wireless.ps1` | 将 AES Key 以当前 Windows 用户 DPAPI 加密保存 | 缺失 | 已放入安全交付包 |
| `wireless_collector\start_sta_wireless.ps1` | STA/有线 IP 模式启动 8093 | 缺失 | 已放入安全交付包 |
| `wireless_collector\start_wireless.ps1` | Go2 自身 AP 模式启动 | 缺失 | 已放入安全交付包 |
| `wireless_collector\stop_wireless_video.cmd` | 停止 8093 并释放单视频会话 | 缺失 | 已放入安全交付包 |
| `wireless_collector\tests\test_status_contract.py` | 验证状态字段和 stale 语义 | 缺失 | 已放入安全交付包 |
| `unitree_webrtc_connect\unitree_webrtc_connect\` | WebRTC、鉴权、视频轨实现 | 缺失 | 已放入安全交付包 |
| `unitree_webrtc_connect\pyproject.toml` | Python 包和依赖声明 | 缺失 | 已放入安全交付包 |
| `unitree_webrtc_connect\LICENSE` | 开源许可证 | 缺失 | 已放入安全交付包 |
| `requirements-bridge-lock-2026-08-21.txt` | 本地成功环境的锁定版本 | 缺失 | 本次新增并已放入交付包 |
| `tools\configure_go2_ethernet_admin.ps1` | 为 Go2 专用网卡添加静态 IPv4 | 缺失 | 已放入安全交付包 |
| `tools\check_go2_network.ps1` | 只读检查网卡和历史候选 Go2 地址 | 缺失 | 已放入安全交付包 |

### 4.2 备用有线 SDK 路径文件

主路径 WebRTC 无法使用时，才考虑以下文件：

| 目录 | 用途 | 是否已打包 | 注意事项 |
| --- | --- | --- | --- |
| `go2-wireless-camera\collector` | Linux/WSL 下通过 Unitree SDK 读取相机并输出 8091 MJPEG | 是 | 需要正确 Linux 网卡和 SDK 环境 |
| `unitree_sdk2_python\unitree_sdk2py` | collector 使用的 Unitree Python SDK | 是 | 依赖 `cyclonedds==0.10.2`，Windows 安装可能困难 |

备用路径不应作为第一选择。当前已经有真实 WebRTC 视频成功记录，先恢复 WebRTC 能更快隔离问题。

### 4.3 视觉服务文件

阻塞文档确认对方电脑已有：

```text
E:\国赛\camera-service\.venv\Scripts\python.exe
E:\国赛\camera-service\app\main.py
E:\国赛\camera-service\app\camera\source_manager.py
E:\国赛\camera-service\app\camera\capture_worker.py
E:\国赛\camera-service\app\post_fall_v2\post_fall_runtime_service.py
E:\国赛\camera-service\configs\post_fall_v2_candidate.env
E:\国赛\camera-service\models\post_fall_v2\efficientnet_b0\fall_candidate_efficientnet_b0.onnx
```

源电脑当前并没有完整 `camera-service`：

```text
E:\国赛\camera-service                   不存在
E:\笨笨狗\camera-service\app\main.py    不存在
```

因此：

- 本次交付包不包含完整 `camera-service`。
- 对方已有的视觉服务不需要从本机覆盖。
- 如果另一台视觉服务器也缺少 `camera-service`，必须由持有 `E:\国赛\camera-service` 完整工程的负责人另行提供正式部署包。
- 不得用 `dog.zip` 的离线训练/评估脚本替代实时视觉服务。

---

## 5. 哪些内容可以复制、必须重建或禁止传输

### 5.1 可以直接复制

```text
Python 源代码
PowerShell/CMD 启停脚本
requirements 和 pyproject.toml
README、接口文档、验收文档
开源 LICENSE
网络只读检查脚本
文件 SHA256 清单
```

### 5.2 必须在目标电脑重新安装或重建

| 项目 | 原因 | 目标动作 |
| --- | --- | --- |
| Python 3.12 | 解释器与系统绑定 | 安装 64 位 Python 3.12，确认 `py -3.12` 可用 |
| `.venv312` | venv 含绝对路径和机器相关入口 | 用锁定依赖在目标电脑重新创建 |
| `.venv` | 历史脚本仍引用该名字 | 指向 `.venv312` 的目录联接，或由维护人员统一脚本 |
| FFmpeg | 本地二进制不应假设可移植 | 在目标电脑安装并验证 `libx264` |
| MediaMTX | 本机当前没有已部署实例 | 在视觉服务器从官方发布页安装固定版本 |
| 防火墙规则 | 与目标 IP/网卡相关 | 网络工程师按实际源 IP配置 |
| RTSP 用户和密码 | 必须是服务器侧最小权限凭据 | 在视觉服务器重新生成 |

### 5.3 必须在目标 Windows 用户下重新生成

```text
wireless_collector\.go2_aes_key.dpapi
```

原因：DPAPI 密文通常绑定创建它的 Windows 用户和电脑。即使复制源电脑的文件，对方也可能无法解密；复制还会扩大设备凭据暴露面。

正确做法：目标电脑以最终运行 8093 的 Windows 账号执行：

```powershell
.\setup_wireless.ps1
```

### 5.4 禁止放入交付包

```text
.go2_aes_key.dpapi
明文 Unitree 账号、密码或 AES Key
明文 RTSP 密码和主系统 Token
.venv / .venv312
.git
__pycache__ / *.pyc
运行日志、临时截图和个人数据
未经授权的原始视频或关键帧
```

本次安全包已经排除了上述本地密钥、虚拟环境、Git 元数据和缓存。

---

## 6. 已核实的本地版本

### 6.1 WebRTC 源码

```text
package version: unitree_webrtc_connect 2.1.2
Git base commit: 611ea3706be3acf096d5aa00e6b75abcd011024c
```

工作树不是完全干净：

```text
M unitree_webrtc_connect/unitree_cloud.py
```

本地补丁用于避免中文 Windows 时区名称无法写入 Latin-1 HTTP Header：

```text
如果 time.strftime("%Z") 不能按 Latin-1 编码：
中国区域使用 Asia/Shanghai；其他区域使用 UTC。
```

交付包包含该补丁后的文件。不能把它误称为上游提交 `611ea370...` 的原样源码。

### 6.2 已验证 Python 环境

```text
Python 3.12.13
aiortc 1.15.0
av 17.1.0
opencv-python 5.0.0.93
fastapi 0.116.1
uvicorn 0.35.0
unitree_webrtc_connect 2.1.2（本地源码安装）
```

验证结果：

```text
pip check: No broken requirements found
import unitree_webrtc_connect, aiortc, av, cv2, fastapi, uvicorn: PASS
wireless_collector 状态契约测试：4 passed
```

### 6.3 已验证 FFmpeg

源电脑当前命令路径：

```text
C:\Users\Test1\Downloads\ffmpeg\bin\ffmpeg.exe
```

版本：

```text
N-124387-gaa14727cd5-20260504
```

已确认包含：

```text
libx264 H.264 encoder
mpjpeg MIME multipart JPEG demuxer
RTSP muxer/demuxer
```

该路径仅用于记录源电脑状态，不应复制成对方电脑的固定路径。

### 6.4 关键源码 SHA256

```text
B124B7E2E5637B970B58B4495882B815AE363AA61F588142B4E7C7CC1D4FE80D  wireless_collector/app.py
3D994FAE7D20B3A1779CC7E88C7D9F59B6D80CE11F5203A241001C61DCA2170B  wireless_collector/fetch_device_key.py
25C6C4168FB9D575569EF6BC60BEBB96EF6F780B415D5C537981F0962648F780  wireless_collector/requirements.txt
43DDC8B95F1D70FE0409BB7B7106F2A02B0C9B9E7DD944DF66EB954759E71A95  wireless_collector/setup_wireless.ps1
4D783EECECC1805B67307D17F389CB54DE65C1CF288110D934792E4533AE3603  wireless_collector/start_sta_wireless.ps1
FBE4A7B26A352C3B5AF6567EBB6162455EBE2ECFE93D7781C837E314B7FB4B56  wireless_collector/start_wireless.ps1
8E8DA5F9E6AB7CB1E7E7EEB44B6A6C8409E9DDEDE1E0FDF30815AB2BB795A62D  unitree_webrtc_connect/pyproject.toml
4F70633DE24A96236C4465187D29065547B158F92858836673930EB10715AC9B  unitree_webrtc_connect/unitree_webrtc_connect/unitree_cloud.py
```

包内其他文件以 `FILE_MANIFEST_SHA256.csv` 为准。

---

## 7. 推荐恢复策略：先本地闭环，再接远端服务器

为了最快定位问题，按两步实施：

### 第一步：在目标桥接电脑完成本地闭环

```text
Go2 有线 192.168.123.x
  → WebRTC
  → 127.0.0.1:8093/stream.mjpg
  → FFmpeg
  → 本机 MediaMTX 127.0.0.1:8554/go2_front
  → 本机 ffprobe/VLC
```

这一步不依赖远端服务器路由。通过后可以证明 Go2、桥接代码、密钥和转码都正常。

### 第二步：将 RTSP 发布目标改为视觉服务器

```text
桥接电脑 FFmpeg
  → <VISION_SERVER_IP>:8554/go2_front
  → 视觉服务器 camera-service
```

如果第二步失败，问题就被限定在桥接电脑到服务器的路由、防火墙、MediaMTX 鉴权或 camera-service 配置，不会再与 Go2 本身混淆。

---

## 8. P0 解决：恢复 Go2 有线 IP 网络

### 8.1 最可能的现网参数

源电脑的多份历史实测记录一致表明：

```text
Go2 有线 IP：192.168.123.161
电脑有线 IP：192.168.123.222/24
默认网关：不设置
```

这比阻塞文档中随机扫描多个历史地址更有依据。但是，现场仍需确认：

- 当前确实连接同一台 Go2；
- Go2 固件/网络配置未被更改；
- `192.168.123.222` 没有被其他在线设备占用。

如果使用交换机而不是直连网线，由网络工程师分配 `192.168.123.0/24` 中未占用的电脑地址。

### 8.2 以管理员身份配置网卡

将交付包解压到不含权限限制的目录，例如：

```text
D:\go2_video_bridge_handoff_2026-08-21
```

打开“以管理员身份运行”的 PowerShell：

```powershell
$HandoffRoot = "D:\go2_video_bridge_handoff_2026-08-21"
Set-Location "$HandoffRoot\go2_dev"

Get-NetAdapter | Format-Table Name,Status,LinkSpeed,MacAddress,InterfaceDescription
Get-NetIPConfiguration -InterfaceAlias "以太网"
```

确认接口名确实为“以太网”后：

```powershell
.\tools\configure_go2_ethernet_admin.ps1 `
  -InterfaceAlias "以太网" `
  -IPAddress "192.168.123.222" `
  -PrefixLength 24
```

该脚本不配置默认网关。Go2 专用网卡不应抢占电脑原有互联网默认路由。

### 8.3 验证路由、ARP 和信令端口

```powershell
Get-NetIPAddress -InterfaceAlias "以太网" -AddressFamily IPv4
Get-NetRoute -InterfaceAlias "以太网" -AddressFamily IPv4 |
  Sort-Object DestinationPrefix |
  Format-Table DestinationPrefix,NextHop,RouteMetric,InterfaceMetric

ping -S 192.168.123.222 192.168.123.161
Get-NetNeighbor -InterfaceAlias "以太网" -AddressFamily IPv4

Test-NetConnection 192.168.123.161 -Port 9991 -InformationLevel Detailed
Test-NetConnection 192.168.123.161 -Port 8081 -InformationLevel Detailed
```

通过标准：

```text
本机存在 192.168.123.222/24
192.168.123.161 的路由明确走“以太网”
ARP/邻居表中出现 192.168.123.161
9991 或 8081 至少一个 TCP 端口可达
```

说明：本地 WebRTC 库会在 `9991` 和 `8081` 之间选择可用信令方法；旧脚本只预检 9991，因此本次交付的 flexible 脚本同时检查两者。

### 8.4 如果仍然只有 169.254 或无邻居

按顺序检查：

1. Go2 完全启动，而不是只接通电源。
2. 网线连接 Go2 正确的有线开发接口。
3. 更换已知正常网线。
4. 确认 Windows 网卡没有桥接、ICS、VLAN 或第三方安全软件重写。
5. 暂时断开与 `192.168.123.0/24` 冲突的其他 VPN/虚拟网卡。
6. 在 Go2 App 或设备管理信息中确认有线 IP。
7. 如果 Go2 有线 IP不是 `.161`，使用设备实际 IP，不继续盲扫整个网段。

物理网卡 `Up` 只代表电气链路协商成功，不代表 IP、ARP 或 WebRTC 已建立。

### 8.5 网络配置回滚

只有在需要恢复原 DHCP 时执行：

```powershell
Remove-NetIPAddress `
  -InterfaceAlias "以太网" `
  -IPAddress "192.168.123.222" `
  -Confirm:$false

Set-NetIPInterface `
  -InterfaceAlias "以太网" `
  -AddressFamily IPv4 `
  -Dhcp Enabled
```

执行后重新查看 `Get-NetIPConfiguration`，不要删除其他业务网卡地址。

---

## 9. P0 解决：部署 Go2 WebRTC 桥接工程

### 9.1 验证压缩包

接收方先验证交付 ZIP 的 SHA256，与发送方提供的值一致后再解压：

```powershell
Get-FileHash ".\go2_video_bridge_handoff_2026-08-21.zip" -Algorithm SHA256
```

解压后保留目录层级：

```text
<HANDOFF_ROOT>\go2_dev\unitree_webrtc_connect
<HANDOFF_ROOT>\go2_dev\go2-wireless-camera\wireless_collector
```

脚本通过相对目录定位依赖，不能只复制 `wireless_collector` 单独一个文件夹。

### 9.2 安装 Python 3.12

```powershell
py -0p
py -3.12 --version
```

目标基线是 Python `3.12.x`，源电脑实测为 `3.12.13`。

### 9.3 重建 `.venv312`

```powershell
$WebRtcRoot = "<HANDOFF_ROOT>\go2_dev\unitree_webrtc_connect"
Set-Location $WebRtcRoot

py -3.12 -m venv .venv312
.\.venv312\Scripts\python.exe -m pip install --upgrade pip
.\.venv312\Scripts\python.exe -m pip install `
  -r .\requirements-bridge-lock-2026-08-21.txt
.\.venv312\Scripts\python.exe -m pip install -e . --no-deps
```

历史 `setup_wireless.ps1` 和 AP 启动脚本寻找 `.venv`，STA 脚本寻找 `.venv312`。为保持交付源码不被现场随意修改，可创建目录联接：

```powershell
if (-not (Test-Path -LiteralPath .\.venv)) {
  New-Item -ItemType Junction `
    -Path .\.venv `
    -Target (Resolve-Path .\.venv312) | Out-Null
}
```

不要复制源电脑的 `.venv` 或 `.venv312`。

### 9.4 验证环境

```powershell
.\.venv312\Scripts\python.exe -m pip check
.\.venv312\Scripts\python.exe -c `
  "import unitree_webrtc_connect,aiortc,av,cv2,fastapi,uvicorn; print('IMPORTS_OK')"

.\.venv312\Scripts\python.exe -m unittest discover `
  -s "..\go2-wireless-camera\wireless_collector\tests" `
  -v
```

预期：

```text
No broken requirements found
IMPORTS_OK
Ran 4 tests ... OK
```

### 9.5 在目标用户下生成设备密钥

关闭手机 Unitree Go App 的实时视频，以及其他 Go2 视频会话。

以未来实际运行 8093 的 Windows 用户执行：

```powershell
Set-Location "<HANDOFF_ROOT>\go2_dev\go2-wireless-camera\wireless_collector"
.\setup_wireless.ps1
```

只验证文件存在和长度，不打印或解密内容：

```powershell
Get-Item -LiteralPath .\.go2_aes_key.dpapi |
  Select-Object Name,Length,LastWriteTime
```

### 9.6 启动有线 WebRTC 桥

推荐使用本次交付的 flexible 脚本：

```powershell
Set-Location "<HANDOFF_ROOT>\go2_dev\go2-wireless-camera\wireless_collector"

.\start_go2_bridge_flexible.ps1 `
  -RobotIp "192.168.123.161" `
  -NoOpenBrowser
```

如果现场 Go2 地址不同，替换为设备工程师确认的地址。

### 9.7 验证 8093 实时画面

```powershell
$S1 = Invoke-RestMethod "http://127.0.0.1:8093/status" -TimeoutSec 5
Start-Sleep -Seconds 5
$S2 = Invoke-RestMethod "http://127.0.0.1:8093/status" -TimeoutSec 5

$S1.data | ConvertTo-Json -Depth 10
$S2.data | ConvertTo-Json -Depth 10

Invoke-WebRequest "http://127.0.0.1:8093/snapshot" `
  -OutFile "$env:TEMP\go2_bridge_snapshot.jpg" `
  -TimeoutSec 10

Start-Process "$env:TEMP\go2_bridge_snapshot.jpg"
```

必须满足：

```text
serviceState=running
videoState=ready
connected=true
hasFrame=true
latestFrame.sequence 在 5 秒内增加
frameAgeMs 不持续增加，主要时间 < 1000 ms
captureFps > 0
lastErrorCode=null
快照显示机器狗当前真实前视画面
```

在本节通过前，不启动 camera-service，不处理模型问题。

---

## 10. P1 解决：建立本地 RTSP 闭环

### 10.1 安装并启动 MediaMTX

源电脑当前没有可交付的 MediaMTX 实例，因此需要在目标电脑或视觉服务器从官方发布页安装固定版本：

```text
https://mediamtx.org/docs/kickoff/install
```

首次本地闭环可只监听本机，路径使用：

```text
go2_front
```

生产联调时必须配置发布/读取用户、IP 和路径权限。不要对公网匿名开放 8554。

启动后验证：

```powershell
Get-NetTCPConnection -LocalPort 8554 -State Listen
```

### 10.2 安装并验证 FFmpeg

```powershell
ffmpeg -version
ffmpeg -hide_banner -encoders | Select-String libx264
ffmpeg -hide_banner -formats | Select-String mpjpeg
```

三项都必须成功。

### 10.3 8093 MJPEG 转 H.264 RTSP

本地无鉴权烟雾测试示例：

```powershell
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
  "rtsp://127.0.0.1:8554/go2_front"
```

生产/跨机时使用服务器分配的低权限发布账号：

```text
rtsp://<PUBLISH_USER>:<PUBLISH_PASSWORD>@<VISION_SERVER>:8554/go2_front
```

密码必须 URL 编码，不写入普通日志或仓库。

### 10.4 本地读取验证

```powershell
ffprobe -v error -rtsp_transport tcp `
  -select_streams v:0 `
  -show_entries stream=codec_name,width,height,r_frame_rate `
  -of default=noprint_wrappers=1 `
  "rtsp://127.0.0.1:8554/go2_front"

ffmpeg -y -rtsp_transport tcp `
  -i "rtsp://127.0.0.1:8554/go2_front" `
  -frames:v 1 "$env:TEMP\go2_rtsp_check.jpg"
```

期望：

```text
codec_name=h264
width=1280
height=720
r_frame_rate 接近 8/1
```

只有本地 RTSP 可读，才把发布地址改为远端视觉服务器。

---

## 11. P1 解决：发布到视觉服务器

网络工程师放行：

```text
源：目标桥接电脑业务 IP
目标：视觉服务器 IP
协议：TCP
端口：8554
```

桥接电脑验证：

```powershell
Test-NetConnection "<VISION_SERVER_IP>" -Port 8554
```

MediaMTX 建议创建两个独立账号：

```text
发布账号：仅允许桥接电脑 IP，对 go2_front 执行 publish
读取账号：仅允许 camera-service 所在主机，对 go2_front 执行 read
```

MediaMTX API `9997` 只监听环回地址，不对外开放。

如果两台机器之间没有路由、存在 NAT 或需要跨公网，先建立单位 VPN/受控专网。不要把无加密 RTSP 直接暴露到互联网。

---

## 12. P1 解决：camera-service 接入

在视觉服务器的 `E:\国赛\camera-service` 中：

### 12.1 校验模型

```powershell
Set-Location "E:\国赛\camera-service"

Get-FileHash `
  .\models\post_fall_v2\efficientnet_b0\fall_candidate_efficientnet_b0.onnx `
  -Algorithm SHA256
```

预期：

```text
e9871cdb4687055fb5d934dd994c2ad883adac0c2acab375c240f8e005ee04cc
```

### 12.2 安全启动

```powershell
$env:VISION_SERVICE_ENV_FILE = `
  (Resolve-Path ".\configs\post_fall_v2_candidate.env")
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

### 12.3 动态接入 RTSP

MediaMTX 与 camera-service 同机时使用 `127.0.0.1`：

```powershell
$RtspUrl = "rtsp://<READ_USER>:<READ_PASSWORD>@127.0.0.1:8554/go2_front"
$Body = @{
  camera_id = "go2_front_camera"
  rtsp_url = $RtspUrl
} | ConvertTo-Json

Invoke-RestMethod "http://127.0.0.1:8000/stream/start" `
  -Method POST `
  -ContentType "application/json" `
  -Body $Body

$Body = $null
$RtspUrl = $null
```

### 12.4 验证真实帧和推理

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/healthz"
Invoke-RestMethod "http://127.0.0.1:8000/stream/source?camera_id=go2_front_camera" |
  ConvertTo-Json -Depth 20

$C1 = Invoke-RestMethod "http://127.0.0.1:8000/status?camera_id=go2_front_camera"
Start-Sleep -Seconds 5
$C2 = Invoke-RestMethod "http://127.0.0.1:8000/status?camera_id=go2_front_camera"
$C1 | ConvertTo-Json -Depth 30
$C2 | ConvertTo-Json -Depth 30

Invoke-WebRequest `
  "http://127.0.0.1:8000/stream/latest-frame.jpg?camera_id=go2_front_camera" `
  -OutFile ".\artifacts\go2_latest_frame.jpg"

Invoke-RestMethod `
  "http://127.0.0.1:8000/integration/results/go2_front_camera/latest" |
  ConvertTo-Json -Depth 30
```

必须满足：

```text
running=true
connected=true
stream_state=connected
frame_seq 在 5 秒内增加
frame_age_ms 不持续增加
capture_fps > 0
last_error=null
pipeline_mode=post_fall_v2
binary_model_loaded=true
frames_sampled 持续增加
frames_processed 持续增加
latest-frame.jpg 是当前 Go2 真实画面
```

---

## 13. 必须按层留证，不能只说“页面打不开”

每一层保存以下证据：

| 层级 | 必须保存的证据 |
| --- | --- |
| L1 | 网卡 IPv4、路由、邻居、9991/8081 端口结果 |
| L2 | 两次 8093 `/status`、本地 snapshot、桥接 stderr 脱敏摘要 |
| L3 | FFmpeg 版本、MediaMTX 路径状态、ffprobe 输出、RTSP 截图哈希 |
| L4 | `/stream/source`、两次 `/status`、camera-service 最新帧哈希 |
| L5 | 模型哈希、采样/处理计数、最新结果、断流降级状态 |

建议目录：

```text
go2-video-acceptance-YYYYMMDD-HHMM/
  01-network.txt
  02-bridge-status-start.json
  03-bridge-status-end.json
  04-ffprobe.txt
  05-camera-source.json
  06-camera-status-start.json
  07-camera-status-end.json
  08-file-sha256.txt
  09-acceptance.md
```

日志中禁止出现明文密码、Token、AES Key、Unitree 账号或未经批准的人脸截图。

---

## 14. 常见失败与准确处理

| 现象 | 问题层级 | 处理 |
| --- | --- | --- |
| 网卡 Up 但仍是 169.254 | L1 | 使用管理员权限配置正确的 `192.168.123.x/24` 静态地址 |
| `.161` 无 ARP 邻居 | L1 | 核实线缆、接口和真实 Go2 IP，不继续处理 Python |
| 9991 失败但 8081 成功 | L1/L2 | 使用 flexible 脚本；库支持旧信令端口 |
| 两个端口都失败 | L1 | 网络/设备问题，桥接代码无法解决 |
| `setup_wireless.ps1` 报 Python 不存在 | 环境 | 创建 `.venv312`，并建立 `.venv` 目录联接 |
| DPAPI 解密失败 | 凭据 | 用实际运行 8093 的 Windows 用户重新执行 setup，不复制旧密钥 |
| WebRTC 已连接但无帧 | L2 | 关闭手机 App/其他视频会话，检查 H.264 解码和日志 |
| 8093 有图但 FFmpeg 无输入 | L3 | 使用 `-f mpjpeg`，不是原始 `mjpeg` demuxer |
| FFmpeg 401 | L3 | 核对 publish 用户、密码 URL 编码和路径权限 |
| RTSP 可读但 camera-service 无帧 | L4 | 核对 camera_id、读取账号、RTSP URL、OpenCV/FFmpeg 解码支持 |
| `connected=true` 但画面冻结 | L2/L4 | 检查 sequence/frame_seq 和 frameAge，按 stale 处理 |
| 画面有但没有 PFV2 处理 | L5 | 检查模式、模型哈希、onnxruntime 和 sampled/processed 计数 |

---

## 15. 验收顺序和通过标准

### 15.1 最短成功路径

```text
□ 对方收到 ZIP 且 SHA256 一致
□ 管理员配置目标电脑有线 IP
□ Go2 192.168.123.161 出现在邻居表
□ 9991 或 8081 至少一个可达
□ Python 3.12 环境重建且 imports=OK
□ 目标 Windows 用户重新生成 DPAPI Key
□ 8093 hasFrame=true 且 sequence 增长
□ 8093 snapshot 是当前 Go2 前视画面
□ 本机 MediaMTX 8554 闭环通过
□ FFmpeg 发布 H.264，ffprobe 可读
□ 远端 8554/TCP 可达并有 publisher
□ camera-service frame_seq 增长
□ PFV2 sampled/processed 增长
□ 断流时 safe_to_interpret_as_no_fall=false
□ 30 分钟稳定性测试通过
```

### 15.2 绝不能用作“成功”证据的现象

```text
网卡显示 Connected
能看到模型文件
camera-service /healthz 返回 200
connected=true 但 frame_seq 不增加
仍显示上一帧旧画面
只在手机 App 中能看到视频
只获得一张历史截图
```

真正成功必须以持续增长的新帧和 PFV2 处理计数为准。

---

## 16. 交付责任边界

### 源电脑负责人已完成

- 整理 Go2 WebRTC 主路径源码。
- 整理有线 SDK 备用源码。
- 排除本地 DPAPI 密钥、venv、Git 元数据和缓存。
- 记录 Python/FFmpeg/包版本。
- 生成锁定依赖和文件哈希清单。
- 提供网络配置和启动文档。

### 目标桥接电脑负责人必须完成

- 以管理员权限恢复有线 IP。
- 获取并核对压缩包。
- 安装 Python 和 FFmpeg。
- 重建 venv。
- 使用自己的运行账号配置 DPAPI Key。
- 在真实 Go2 上验证 flexible 启动脚本。
- 提供 8093 状态与快照证据。

### 视觉服务器负责人必须完成

- 安装并配置 MediaMTX。
- 提供 RTSP 最小权限账号。
- 放通受限的 8554/TCP。
- 启动现有完整 camera-service。
- 提供 FrameBuffer/PFV2 处理证据。

---

## 17. 当前仍无法由文档替代的现场条件

即使文件完整，以下条件仍必须由现场人员提供：

1. Go2 已开机并完成正常启动。
2. 网线连接正确的 Go2 端口。
3. 当前 Go2 有线 IP 未被重新配置；若已变化，提供真实地址。
4. Unitree 账号具备读取该设备密钥的权限。
5. 手机 App 或其他程序未占用唯一视频会话。
6. 目标电脑具备管理员权限。
7. 视觉服务器允许桥接电脑访问 8554/TCP。
8. 已取得保存截图、关键帧或视频的隐私授权。

在真实硬件和上述权限不可用时，任何人都不能仅靠复制文件保证实时画面出现。

---

## 18. 最终交接结论

本次整理已经补齐对方电脑明确缺失的 Go2 视频桥接源码和复现信息，并形成安全交付包。最可能的第一处现场修复是：

```text
将目标电脑“以太网”从 169.254.62.97
配置为历史验证网段中的 192.168.123.222/24（无网关），
验证 Go2 192.168.123.161 的 9991 或 8081 端口，
然后启动交付包中的 wireless_collector。
```

但 `.161/.222` 必须由设备工程师结合当前机器狗确认，不能作为所有 Go2 的永久默认值。

完成判定：

```text
8093 sequence 持续增长
  + RTSP 可被 ffprobe 持续读取
  + camera-service frame_seq 持续增长
  + PFV2 sampled/processed 持续增长
  + 断流时系统明确降级
= Go2 实时画面接收完成
```

