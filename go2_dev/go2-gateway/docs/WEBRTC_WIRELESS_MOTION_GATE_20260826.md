# Go2 比赛无线 WebRTC 运动最小 Gate

## 目标与边界

本 Gate 只验证以下链路：

```text
Windows PC
  -> Wi-Fi / LocalSTA
  -> WebRTC DataChannel
  -> rt/api/sport/request
  -> Go2 Sport Mode
```

本阶段不修改 `ScriptedMotionController`、Companion、FollowController、MotionArbiter、UWB/LiDAR 输入或有线 SDK2 adapter。只有三阶段全部通过后，才开始设计 `WebRTCMotionBackend`。

本机已经具备：

- `unitree_webrtc_connect 2.1.2` 本地 checkout；
- Python 3.12 + `aiortc 1.15.0` WebRTC 环境；
- 已加密保存的 Go2 per-device AES key；
- 既往 Go2 AP/WebRTC 视频成功证据。

## 安全规则

1. Unitree App 必须完全关闭。
2. 8093 WebRTC 视频桥必须停止，避免第二客户端占用。
3. Companion lifecycle 必须是 `IDLE`。
4. 不得同时运行 SDK2 motion tool、Companion Follow 或其他 WebRTC 控制器。
5. 机器狗由遥控器置于正常站立/运动模式；脚下为开放平整区域。
6. 原厂遥控器始终在现场操作员手中。
7. 三阶段严格按顺序执行；前一阶段失败时不得进入下一阶段。

Gate 不自动切换 motion mode、不自动站立，也不启用视频或音频。

## 阶段 1：只读连接和 SportModeState

在 Windows PowerShell 中执行：

```powershell
cd "E:\笨笨狗\go2_dev\go2-gateway"
.\scripts\Start-Go2WebRTCMotionGate.ps1 `
  -Stage ReadOnly `
  -RobotIp 192.168.8.252
```

通过标准：

```text
connected=true
sportStateReceived=true
motionCommandsSent=0
completed=true
reason=sport_state_received
```

失败时停止，不进入 Stop Gate。

## 阶段 2：StopMove + 零速度

阶段 1 通过后执行：

```powershell
.\scripts\Start-Go2WebRTCMotionGate.ps1 `
  -Stage Stop `
  -RobotIp 192.168.8.252
```

依次输入：

```text
WEBRTC_EXCLUSIVE_MOTION_WRITER
UNITREE_APP_CLOSED
OPEN_AREA_REMOTE_READY
```

工具依次发送 `StopMove → Move(0,0,0) → StopMove`，并在异常清理路径再次尝试 `StopMove`。通过标准是三个请求均收到 API status code 0、机器狗没有非预期动作、遥控器仍可接管。

## 2026-08-26 阶段 1 实测结果

Windows 上的 8093 WebRTC 视频桥最初仍在运行，启动器按设计拒绝创建第二个客户端。停止该桥并确认 8093 无监听后，LocalSTA ReadOnly Gate 成功：

```text
robotIp: 192.168.8.252
WebRTC signaling: con_notify / port 9991
ICE: completed
PeerConnection: connected
DataChannel verification: OK
stateTopic: rt/lf/sportmodestate
stateSampleCount: 4
startPose: x=0.113794, y=0.347273, yaw=-0.545693
latestStateFresh: true
motionCommandsSent: 0
completed: true
reason: sport_state_received
```

判定：

```text
WEBRTC_LOCALSTA_CONNECT=PASS
WEBRTC_DATACHANNEL=PASS
WEBRTC_SPORTMODESTATE=PASS
WEBRTC_READONLY_MOTION_CALLS=0
WEBRTC_STOP_GATE=READY_FOR_OPERATOR_CONFIRMATION
```

此结果只批准进入阶段 2 Stop Gate，不批准前进脉冲或正式 backend 集成。

## 2026-08-26 阶段 2 实测结果

Stop Gate 在 LocalSTA WebRTC DataChannel 上完成：

| 请求 | API status | 应答 |
|---|---:|---|
| 前置 `StopMove` | 0 | acknowledged |
| `Move(0,0,0)` | 0 | acknowledged |
| 后置 `StopMove` | 0 | acknowledged |
| finally 清理 `StopMove` | 0 | acknowledged |

其他软件证据：

```text
connected: true
sportStateReceived: true
stateSampleCount: 1
latestStateFresh: true
motionCommandsSent: 4
completed: true
```

现场人工确认：

```text
没有移动或扭动: PASS
姿态和步态没有异常: PASS
遥控器仍能立即接管: PASS
```

阶段判定：

```text
WEBRTC_STOPMOVE_ACK=PASS
WEBRTC_ZERO_VELOCITY_ACK=PASS
WEBRTC_FINAL_STOP_ACK=PASS
WEBRTC_REMOTE_TAKEOVER=PASS
WEBRTC_FORWARD_PULSE=READY_FOR_STAGED_TEST
```

此结果只批准一次默认参数 `0.23 m/s × 0.40 s` 前进脉冲，不批准扩大速度、延长时间或接入正式 Companion。

## 2026-08-26 阶段 3 实测结果

默认参数前进脉冲完成：

```text
speed: 0.23 m/s
duration: 0.40 s
maximum commanded distance: 0.092 m
motionCommandsSent: 5
SportModeState samples: 20
latestStateFresh: true
completed: true
reason: forward_pulse_stopped
```

API 请求结果：

| 请求 | API status | 结果 |
|---|---:|---|
| 前置 `StopMove` | 0 | acknowledged |
| `Move(0,0,0)` | 0 | acknowledged |
| 后置 `StopMove` | 0 | acknowledged |
| `Move(0.23,0,0)` | 0 | acknowledged |
| 脉冲结束 `StopMove` | 0 | acknowledged |

闭环状态数据：

```text
start: x=0.112866, y=0.347811, yaw=-0.561608 rad
end:   x=0.145948, y=0.327242, yaw=-0.559545 rad
local forward progress: 0.038955 m
local lateral drift:    0.000208 m
yaw change:             0.118 deg
```

现场六项人工观察全部通过：正确向前、正常四腿步态、无明显侧移/旋转、约 0.4 秒后及时停车、没有继续运动、遥控器可立即接管。

最终判定：

```text
WEBRTC_LOCALSTA_SPORT_STATE=PASS
WEBRTC_STOP_ZERO_STOP=PASS
WEBRTC_FORWARD_MOVE_ACK=PASS
WEBRTC_FORWARD_PHYSICAL_MOTION=PASS
WEBRTC_TIMELY_STOP=PASS
WEBRTC_REMOTE_TAKEOVER=PASS
WEBRTC_MINIMAL_MOTION_TRANSPORT_GATE=PASS
```

本 Gate 到此结束，不继续扩大速度、时长或距离。它证明比赛无线 WebRTC Sport transport 值得进入 backend 工程化，但尚未证明 Companion、UWB、LiDAR、持续刷新 Watchdog 或连续动作序列已经无线化。

## 阶段 3：极短前进脉冲

只有阶段 2 的软件和现场检查均通过后才能执行：

```powershell
.\scripts\Start-Go2WebRTCMotionGate.ps1 `
  -Stage ForwardPulse `
  -RobotIp 192.168.8.252 `
  -Speed 0.23 `
  -Duration 0.40
```

还需额外输入：

```text
WEBRTC_FORWARD_PULSE_APPROVED
```

硬边界：

```text
0.20 <= speed <= 0.23 m/s
0.20 <= duration <= 0.50 s
y = 0
yaw = 0
maximum commanded distance at defaults = 0.092 m
```

工具在前进前发送 Stop/零速度，收到 Move 成功应答后等待 0.40 秒并发送 StopMove。正常、异常和 Ctrl+C 路径均请求停止，但网络瞬断或进程硬崩溃仍可能使 Stop 无法送达，因此遥控器兜底不可省略。

现场验收：

1. 确实向前而非侧移或旋转；
2. 四腿形成正常步态；
3. 脉冲结束后及时停车；
4. 没有持续运动；
5. 遥控器可以立即接管。

## 后续门禁

三阶段通过只证明 WebRTC Sport transport 可用，不代表 Companion 已无线化。之后依次验证：

```text
WebRTCMotionBackend 刷新/Stop/watchdog
SportModeState 位姿语义与有线闭环一致性
rt/uwbstate 的真实样本与字段语义
WebRTC LiDAR 数据到现有 LidarSafetyGuard 的转换
唯一 WebRTC client 与比赛启动/停止流程
```

在 UWB、LiDAR、状态 stale、Watchdog 和异常 Stop 全部重新验收前，不得把 WebRTC backend 接入正式 Companion 比赛流程。
