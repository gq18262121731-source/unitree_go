# Go2 当前跟随目标 UDP 转发

## 目的和边界

该功能把 Go2 已有的 UWB/伴随目标状态低延迟转发给
`camera-service` 电脑，供后者限制跌倒检测区域。它不做人脸识别，
不启动 YOLO、BoT-SORT、ReID 或人物跟踪，也不创建第二条 Go2 连接。

转发器直接读取唯一 `Go2WirelessRuntime` 已维护的最新状态：

```text
Go2 rt/uwbstate
  -> 现有 WebRTC DataChannel 订阅
  -> Go2WirelessRuntime 最新 UWB 快照（单份）
  -> Go2UwbFollowTargetSource
  -> 非阻塞 UDP，默认 20 Hz
  -> camera-service
```

视频接收、JPEG 编码、MJPEG 转发和运动控制路径均未改变，也不等待或
缓存视频来同步 UWB。

## 真实数据来源与字段映射

当前 Go2 EDU 通过同一个 WebRTC PeerConnection 的 DataChannel 订阅：

- `rt/uwbstate`
- `rt/multiplestate`（读取 `uwbSwitch`）
- `rt/lf/sportmodestate` / `rt/sportmodestate`（连接和状态新鲜度）

本机真机已经实际收到的 `rt/uwbstate` 字段为：

- `distance_est`：目标距离，米；
- `orientation_est`：原始目标方向，弧度；
- `yaw_est`：存在，但项目真机标定明确禁止用它作为目标 bearing；
- `enabled_from_app`：UWB 是否由 App/遥控侧启用；
- `error_state`：部分固件会省略。本项目现用固件已确认省略，是否接受
  由现有 `configs/webrtc_uwb_follow_3min.yaml` 的
  `allow_missing_error_state` 规则决定。

项目 Phase 7.1 真机标定结果是：

```text
calibrated_robot_bearing_rad =
    UWB_BEARING_SIGN * (orientation_est + UWB_BEARING_ZERO_OFFSET_RAD)
```

默认值：

```text
UWB_BEARING_SIGN=1
UWB_BEARING_ZERO_OFFSET_RAD=0.55
```

该既有标定的方向语义是“左正、右负”。UDP v1 协议要求“左负、右正”，
因此适配层在归一化到 `[-pi, pi]` 后明确反号：

```text
bearing_deg = -degrees(calibrated_robot_bearing_rad)
```

最终定义：

- `0°`：机器人/摄像头正前方；
- 负值：左侧；
- 正值：右侧。

当前真实 UWB payload 没有直接可用的相对 X/Y，故
`relative_x_m` 和 `relative_y_m` 始终为 `null`。实现不会用距离和角度
推算并冒充真实坐标。

三个状态彼此独立：

- `target_valid`：当前是否存在可靠可用的 UWB 监护目标；
- `follow_active`：本项目现有无线自动伴随会话（`START` 或
  `FOLLOW_3MIN`）是否运行，仅用于状态展示和审计；
- `monitoring_active`：跌倒监护目标绑定是否开启，由
  `FOLLOW_TARGET_MONITORING_ENABLED` 配置，默认开启。

`follow_active` 不参与 `target_valid` 门控。因此允许机器狗由遥控器人工
控制，同时持续用 UWB 目标约束 camera-service 的监护区域。有效目标必须
同时满足：

- `monitoring_active == true`；
- 唯一 WebRTC 连接在线；
- UWB 状态未超过 stale 时间；
- `enabled_from_app == 1`；
- 距离非负且有限，方向有限；
- 若 `error_state` 存在，则必须为 `0`；若固件省略，仅按既有明确配置
  决定是否允许。

任一条件失败均发送 `target_valid=false`，同时将 bearing、distance 和
relative X/Y 清为 `null`，不会继续把旧目标当成有效目标。

## UDP v1 协议

单包为紧凑 UTF-8 JSON，不等待 ACK、不重传、不补发历史：

```json
{"schema_version":"go2_follow_target.v1","sequence":1834,"target_valid":true,"follow_active":false,"monitoring_active":true,"bearing_deg":-12.4,"distance_m":1.36,"relative_x_m":null,"relative_y_m":null,"source_timestamp_ms":1787892000123,"sent_timestamp_ms":1787892000130}
```

- `sequence`：每次发送递增；
- `source_timestamp_ms`：当前固件无可靠机器人源时间戳，因此使用本机
  收到该 UWB 样本时的 Unix epoch 毫秒；
- `sent_timestamp_ms`：UDP 调用前的本机 Unix epoch 毫秒；
- `target_valid`：只有所有真实有效性条件均满足时才为 true；
- `follow_active`：本机现有自动伴随会话是否实际开启，不门控目标；
- `monitoring_active`：当前是否启用 UWB 监护目标绑定。

转发线程没有发送队列。UWB 即使连续更新 100 次，下一个周期也只发送
内存里的最后一份快照。

## 配置和启动

### 固定启动入口（推荐）

已提供统一启动脚本，它会在同一个 Runtime 中同时启动视频、运动、状态读取
和目标 UDP 转发：

```powershell
cd "E:\笨笨狗\go2_dev\go2-gateway"
.\scripts\Start-Go2WirelessRuntimeWithFollowTarget.ps1
```

脚本当前默认：

```text
Go2                 192.168.8.252
camera-service 电脑B 192.168.8.253
视频                 0.0.0.0:8093
目标状态 UDP          192.168.8.253:8766 / 20 Hz
health_new           http://127.0.0.1:8765
```

如果电脑 B 的实际 IP 不是 `192.168.8.253`，可临时覆盖：

```powershell
.\scripts\Start-Go2WirelessRuntimeWithFollowTarget.ps1 -CameraServiceIp "电脑B实际IP"
```

确认固定 IP 后，可修改脚本参数区第一行的默认 `CameraServiceIp`，以后仍然只
使用不带参数的一条固定命令。该脚本调用现有
`Start-Go2WirelessRuntime.ps1`，不会启动第二个 Go2 Runtime。

### 手工环境变量方式

转发默认关闭。启动 Runtime 前，在电脑 A 的同一个 PowerShell 窗口配置：

```powershell
$env:FOLLOW_TARGET_FORWARD_ENABLED="true"
$env:FOLLOW_TARGET_MONITORING_ENABLED="true"
$env:FOLLOW_TARGET_FORWARD_HOST="192.168.8.10"  # camera-service 电脑 B 的 IP
$env:FOLLOW_TARGET_FORWARD_PORT="8766"
$env:FOLLOW_TARGET_FORWARD_HZ="20"
$env:FOLLOW_TARGET_FORWARD_STALE_SECONDS="1.0"
$env:FOLLOW_TARGET_FORWARD_STATS_INTERVAL_SECONDS="10"

cd "E:\笨笨狗\go2_dev\go2-gateway"
.\scripts\Start-Go2WirelessRuntime.ps1 -RobotIp 192.168.8.252 -HealthNewUrl "http://127.0.0.1:8765" -ElderId "elder01_02" -ListenHost 0.0.0.0
```

把示例中的 `192.168.8.10` 换成电脑 B 在两台电脑共同网络上的实际
IPv4 地址。频率建议 10–20 Hz，默认 20 Hz。

只有执行 `START` 或 `FOLLOW_3MIN` 后，`follow_active` 才会变为 true；但
只要 monitoring 已开启且真实 UWB 数据有效，不启动自动伴随也可得到
`target_valid=true`、真实 bearing 和 distance。此时机器狗可由遥控器控制。

## camera-service 监听与 Windows 防火墙

电脑 B 应绑定所有本地接口（示例 `0.0.0.0:8766`），并按 `sequence`
只保留最后一包。最小诊断监听器：

```powershell
@'
import json, socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("0.0.0.0", 8766))
while True:
    data, peer = s.recvfrom(2048)
    print(peer, json.loads(data))
'@ | python -
```

在电脑 B 以管理员 PowerShell 开放入站 UDP 8766：

```powershell
New-NetFirewallRule -DisplayName "Go2 Follow Target UDP 8766" -Direction Inbound -Protocol UDP -LocalPort 8766 -Action Allow
```

如需移除规则：

```powershell
Remove-NetFirewallRule -DisplayName "Go2 Follow Target UDP 8766"
```

## 只读调试接口

统一无线 Runtime 的现有 FastAPI/MJPEG 服务新增：

```text
GET /debug/follow-target
```

例如电脑 A：

```powershell
Invoke-RestMethod "http://127.0.0.1:8093/debug/follow-target" | ConvertTo-Json -Depth 6
```

返回 sender 是否启用/运行、UDP 目标、配置/实际发送频率、错误数、最新
sequence、当前状态以及状态年龄。接口只读，不改变运动或伴随状态。

## 日志与故障行为

日志只在以下情况输出：

- sender 启动/停止；
- 目标首次有效/目标丢失；
- Go2 数据源异常/恢复；
- UDP 网络异常/恢复；
- `UWB_STATE: STALE -> FRESH` / `FRESH -> STALE` 状态变化。

默认不打印每 20 Hz 明细，也不打印周期性 UDP 统计。网络失败会累计错误并重建 UDP socket，主 Go2
Runtime、视频和运动线程不会因此退出。

需要单独调试 UWB 时可以临时启用：

```powershell
.\scripts\Start-Go2WirelessRuntimeWithFollowTarget.ps1 -UwbVerbose
```

需要恢复 LowState、MultiState、SportState、heartbeat、rtc_inner_req，以及
AudioHub 分块进度、request/response、Base64 `block_content` 和完整
`audio_list` 等底层协议原始日志时使用：

```powershell
.\scripts\Start-Go2WirelessRuntimeWithFollowTarget.ps1 -VerboseProtocolLog
```

等价环境变量分别是 `GO2_UWB_VERBOSE=1` 和
`GO2_VERBOSE_PROTOCOL_LOG=1`。两个开关默认均为 `0`，只改变日志，不改变
订阅、UDP 发送频率、视频、语音、伴随或运动控制。

## 测试

运行：

```powershell
cd "E:\笨笨狗\go2_dev\go2-gateway"
python -m pytest -q tests\test_follow_target_forwarder.py tests\test_shared_webrtc_video_bridge.py tests\test_go2_wireless_runtime.py tests\test_webrtc_uwb_follow.py
```

测试覆盖真实字段和方向映射、invalid 清除旧目标、100 次快速更新只发
最新值、UDP 失败隔离与恢复、Go2 数据源异常、伴随关闭强制 invalid、
字段缺失不制造相对坐标，以及调试接口与共享 Runtime 回归。
