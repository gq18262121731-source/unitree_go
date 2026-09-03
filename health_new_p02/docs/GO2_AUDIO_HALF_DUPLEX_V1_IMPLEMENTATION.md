# Go2 Audio Half-Duplex V1 Implementation

## 1. 结论

P0-2 软件实现已完成，真实 Go2 麦克风和扬声器已通过受控依赖装配接入现有 ASR、Qwen 对话、健康/天气上下文和 TTS 链路。

本阶段只输出结构化语音意图，不调用 `SportClient.Move()`，不控制 CompanionSupervisor，不执行 START/STOP/RESUME，也未进入全双工、唤醒词或语音打断。

软件定向测试和现有智能体相关回归已通过；未连接真机，T1–T5 保持 `PENDING`。

## 2. 修改文件

| 文件 | 作用 |
| --- | --- |
| `backend/services/robot_audio/robot_audio_service.py` | 新增统一半双工音频服务、状态机、互斥锁、取消、超时收口和状态快照 |
| `backend/services/robot_audio/webrtc_source.py` | 增加可配置最大录音时长、静音 RMS 检测和静音结束 |
| `backend/services/robot_audio/__init__.py` | 导出统一音频服务和状态类型 |
| `backend/services/go2_companion_intent_service.py` | 新增 P0-2 冻结意图与跌倒回答分类；只分类、不执行 |
| `backend/services/go2_hardware_voice_turn_service.py` | 新增真实 Go2 半双工一轮编排和 ASR/TTS/LLM 超时 |
| `backend/services/go2_companion_voice_service.py` | 将现有上传链路拆出可复用 ASR、对话、TTS 阶段；旧 `process_turn()` 保持兼容 |
| `backend/dependencies.py` | 单例装配 WebRTC source/sink、统一音频服务和真机语音轮次服务；增加关闭清理 |
| `backend/config.py` | 增加 Go2 音频开关、地址和超时/静音配置 |
| `backend/api/go2_companion_api.py` | 增加真机语音接口并让状态接口读取统一音频状态 |
| `backend/schemas/go2_companion_schema.py` | 增加真机请求/响应和半双工状态字段 |
| `backend/main.py` | 后端关闭时取消并释放音频组件 |
| `tests/test_go2_audio_half_duplex.py` | 覆盖半双工、超时、取消、断线恢复、意图和 API |
| `tests/test_go2_webrtc_audio_source.py` | 增加真实 source 静音结束测试 |

未新增第三方依赖，未修改数据库、部署配置、模型权重、数据集、运动算法、UWB、LiDAR、MotionArbiter 或跌倒观察控制器。

## 3. 依赖装配

`backend/dependencies.py` 以受控单例方式装配：

```text
Go2WebRTCAudioSource ─┐
                     ├─ RobotAudioService
Go2WebRTCAudioSink ──┘          │
                                ├─ Go2HardwareVoiceTurnService
现有 Go2CompanionVoiceService ──┘
```

默认 `GO2_AUDIO_ENABLED=false`，后端启动后状态为 idle，不主动连接机器人。启用后必须配置机器人地址；source 和 sink 分别构造、分别记录初始化错误，一个失败不会阻塞另一个，也不会影响机器人运动线程。

配置项：

| 环境变量 | 默认值 | 说明 |
| --- | ---: | --- |
| `GO2_AUDIO_ENABLED` | `false` | 真机音频总开关 |
| `GO2_AUDIO_ROBOT_IP` | 空 | Go2 LocalSTA 地址 |
| `GO2_AUDIO_AES_128_KEY` | 空 | 可选 WebRTC AES key |
| `GO2_AUDIO_PLAY_TIMEOUT_SECONDS` | `20` | 单次播放超时 |
| `GO2_AUDIO_RECORD_MAX_DURATION_SECONDS` | `8` | 默认最大录音时长 |
| `GO2_AUDIO_SILENCE_TIMEOUT_SECONDS` | `1.2` | 连续静音结束时长 |
| `GO2_AUDIO_POST_PLAYBACK_SILENCE_MS` | `500` | 播放完成到开麦的保护窗口 |
| `GO2_AUDIO_ASR_TIMEOUT_SECONDS` | `20` | ASR 超时 |
| `GO2_AUDIO_TTS_TIMEOUT_SECONDS` | `20` | TTS 超时 |
| `GO2_AUDIO_SILENCE_RMS_THRESHOLD` | `200` | PCM 静音 RMS 阈值 |

后端关闭调用 `shutdown_robot_audio_components()`；它取消活动录音/播放并释放单例。重启后重新从 idle 开始，不恢复未完成语音轮次。

## 4. 音频状态机和自对话防护

状态：

```text
IDLE
  -> PLAYING
  -> WAIT_AFTER_PLAYBACK
  -> RECORDING
  -> PROCESSING
  -> PLAYING
  -> IDLE
```

异常路径：

```text
任一状态 -> ERROR（记录 last_error）-> 停止当前 I/O -> IDLE
```

`RobotAudioService` 使用一个 session lock 串行化播放和录音。同一时间不能有两个音频 I/O。每次播放完成记录单调时钟；下一次录音必须等待 `post_playback_silence_ms` 剩余时间，因此扬声器播放期间不会开麦，也不会把机器人自己的提示音送入 ASR。

真机轮次服务另有 turn lock，使完整的“提示音→录音→处理→回复播放”不能与另一轮交叉。

## 5. WebRTC source/sink 连接方式

### 麦克风 source

- 使用 `UnitreeWebRTCConnection(WebRTCConnectionMethod.LocalSTA, ip=..., aes_128_key=...)`；
- 每次录音建立连接，读取 `connection.audio`；
- 通过 `add_track_callback()` 接收音频帧；
- `switchAudioChannel(True)` 开麦；
- 达到最大时长或连续静音时长后结束；
- `switchAudioChannel(False)` 后断开连接；
- WebRTC PCM 采集结果在进入现有文件 ASR 前封装为 WAV，保留声道、采样率和 sample width。

当前真机链路只接受 packed `pcm_s16le` / `pcm_s32le`。planar 和浮点 PCM fail closed，不进行不可靠重排。

### 扬声器 sink

- 使用同一 LocalSTA 连接方式；
- TTS 固定请求 WAV；
- base64 音频解码为 bytes，仅在系统临时目录生成短生命周期 `.wav`；
- `aiortc.contrib.media.MediaPlayer` 创建音轨；
- `peer_connection.addTrack()` 播放；
- 等待 track `ended`，随后 stop/removeTrack/disconnect；
- 临时文件无论成功、超时、取消或异常都会清理。

source/sink 仍采用“每次操作连接、完成后断开”的现有适配器策略，不维持后台常驻 WebRTC 会话。断线后下一轮会创建新连接，测试已覆盖失败后恢复。

## 6. API

### 6.1 新增真机语音轮次

```text
POST /api/v1/go2-companion/go2-voice-turn
Content-Type: application/json
```

请求示例：

```json
{
  "session_id": "elder-001",
  "voice": "Serena",
  "elder_id": "elder01_02",
  "device_mac": "53:57:08:00:00:01",
  "location_hint": "南京",
  "prompt_text": "您好，我在听，请说吧。",
  "fall_monitoring": false,
  "max_duration_s": 8,
  "silence_timeout_s": 1.2,
  "playback_timeout_s": 20
}
```

处理顺序：

```text
提示文本 TTS
-> Go2 speaker
-> 播放完成 + 静音保护窗口
-> Go2 mic
-> WAV 封装
-> ASR
-> 意图分类 + 现有 Qwen/上下文
-> TTS
-> Go2 speaker
```

响应重点字段：

```json
{
  "transcript": "陪我去散步吧",
  "reply": "……",
  "intent": "START_COMPANION",
  "intent_confidence": 0.98,
  "intent_scope": "companion",
  "intent_executed": false,
  "execution_message": "P0-2 only emits intent; P0-1 Lifecycle Service must execute it.",
  "audio_status": {
    "go2_microphone": "connected",
    "go2_speaker": "connected",
    "audio_mode": "half_duplex",
    "state": "idle",
    "recording": false,
    "playing": false,
    "last_error": null
  }
}
```

该接口不接收上传文件。原 `POST /api/v1/go2-companion/voice-turn` 的 multipart 请求、响应音频和客户端播放行为均保留。

### 6.2 状态接口

```text
GET /api/v1/go2-companion/status
```

新增/扩展字段：

- `playback_mode`: `response_only | half_duplex`
- `audio_mode`: `response_only | half_duplex`
- `go2_microphone` / `go2_speaker`: `not_configured | configured | connected | disconnected`
- `state`, `recording`, `playing`, `last_error`
- `post_playback_silence_ms`

`configured` 表示已装配但本进程尚无一次成功 I/O；`connected` 表示最近一次对应 I/O 成功；`disconnected` 表示初始化或最近 I/O 失败。source/sink 每轮结束都会主动断开底层 WebRTC，因此这里的 `connected` 是最近健康结果，不表示持久 socket 正在打开。

未增加第二个 `/robot/audio/status`，避免两个状态源互相矛盾。

### 6.3 兼容性

- 删除字段：无；
- 修改旧请求字段：无；
- 原上传 `voice-turn`：兼容；
- 前端影响：旧客户端可忽略新增状态字段；只有使用真机轮次的新客户端需要调用 JSON 接口；
- 演示流程：默认总开关关闭，不改变现有客户端返回音频演示。

## 7. 意图边界

普通陪伴范围：

```text
START_COMPANION
STOP_COMPANION
RESUME_COMPANION
REQUEST_HELP
CALL_FAMILY
CHAT
```

跌倒监护范围默认使用：

```text
I_AM_OK
NEED_HELP
NO_RESPONSE
UNCERTAIN
```

`fall_monitoring=true` 时提示固定为：

```text
检测到您可能跌倒了，请问您还好吗？
```

明确“继续走吧”优先输出 `RESUME_COMPANION`；仅“我没事”输出 `I_AM_OK`，二者不等价。所有返回均固定 `intent_executed=false`。P0-1 未接入时只由调用方保存/展示结构化意图。

## 8. 超时、取消和错误策略

| 阶段 | 策略 |
| --- | --- |
| 播放 | sink 和统一服务双层超时；超时停止 track、断开、清临时文件 |
| 录音 | 音频帧最大时长 + WebRTC 操作总超时 |
| 静音 | PCM RMS 连续低于阈值达到配置时长后结束 |
| ASR | `asyncio.wait_for(asyncio.to_thread(...))`，超时返回 504 |
| LLM | 使用现有 LLM 超时配置，超时返回 504 |
| TTS | 提示和回复分别受 TTS 超时保护，超时返回 504 |
| API 取消 | 传播 `CancelledError`，取消活动音频 I/O，状态回到 idle |
| 后端关闭 | lifespan 调用统一 cancel |
| WebRTC 断线 | 本轮 fail gracefully，记录 `last_error`；下一轮重建连接 |

同步第三方 SDK 在线程中执行。Python 无法强杀已经进入 SDK 的工作线程；API 超时/取消后不会继续启动后续音频阶段，但底层同步 SDK 调用可能在其自身网络超时前短暂存活。这是当前 SDK 边界，真机 Gate 需要核对其实际退出时间。

## 9. 测试结果

测试环境：`C:\Users\Test1\.conda\envs\health\python.exe`。pytest 临时目录显式指向工作区。

### 半双工和音频适配定向集

```text
python -m pytest \
  tests/test_go2_audio_half_duplex.py \
  tests/test_robot_audio.py \
  tests/test_go2_webrtc_audio_source.py \
  tests/test_go2_webrtc_audio_sink.py \
  tests/test_go2_companion_voice.py \
  tests/test_go2_companion_context.py -q
```

结果：`50 passed`。

### 现有智能体相关回归集

```text
python -m pytest \
  tests/test_go2_audio_half_duplex.py \
  tests/test_go2_webrtc_audio_source.py \
  tests/test_go2_webrtc_audio_sink.py \
  tests/test_go2_companion_voice.py \
  tests/test_go2_companion_context.py \
  tests/test_robot_companion_agent.py \
  tests/test_qwen_file_asr_service.py -q
```

结果：`52 passed`。

覆盖：source/sink 初始化边界、未配置硬件快速失败、播放成功、播放后开麦、播放期间不进 ASR、最大录音超时、静音结束、ASR/TTS 成功与超时、连续三轮自对话防护、guard window、取消、断线后恢复、冻结意图、`I_AM_OK != RESUME_COMPANION`、新 API，以及原上传文件 `voice-turn` 回归。

## 10. 真机 Gate

| Gate | 状态 | 说明 |
| --- | --- | --- |
| T0 WebRTC Source/Sink | `HOLD` | Go2 网络、9991 和 DPAPI key 校验通过；两套兼容运行环境均在 ICE/peer connection 阶段超时 |
| T1 Go2 扬声器播放固定 TTS | `NOT_RUN` | T0 未通过，按 Gate 停止 |
| T2 Go2 麦克风录真人语音 | `NOT_RUN` | T0 未通过，按 Gate 停止 |
| T3 Go2 Mic → ASR | `NOT_RUN` | T0 未通过，按 Gate 停止 |
| T4 Half-duplex protection | `NOT_RUN` | T0 未通过，按 Gate 停止 |
| T5 完整单轮对话 | `NOT_RUN` | T0 未通过，按 Gate 停止 |
| T6 连续 3 轮对话 | `NOT_RUN` | T0 未通过，按 Gate 停止 |
| T7 伴随意图 | `NOT_RUN` | T0 未通过，按 Gate 停止 |
| T8 跌倒监护对话 | `NOT_RUN` | T0 未通过，按 Gate 停止 |
| T9 超时/取消 | `NOT_RUN` | T0 未通过，按 Gate 停止 |

### 10.1 2026-08-24 T0 实机记录

脱敏证据：`artifacts/go2_audio_hardware_validation/T0_20260824.json`。

已通过：

- PC 有线接口 `192.168.123.222/24`；
- Go2 `192.168.123.161` ping 和 ARP 邻居通过；
- Go2 TCP/9991 可达；
- 当前用户可解密已有 DPAPI key，格式为合法 32 位十六进制，测试过程未输出或保存明文；
- 本机 8093 未运行，未发现其他本机 WebRTC bridge 进程；
- 所有 connect-only 尝试均执行 disconnect，运动调用为 0。

未通过：

- Python 3.12 / aiortc 1.15.0：AES 握手阻塞排除后，ICE 保持 `checking`、peer connection 保持 `connecting`，20 秒超时；
- Python 3.9 / aiortc 1.9.0：相同条件下 30 秒超时；
- 两次超时后的 aiortc 清理诊断显示 `remoteDescription=None`，未取得 audio channel；后续结构探针已确认机器人实际返回了合法 remote SDP answer，因此该字段不能解释为“信令没有返回 answer”；
- 当前 8000 由旧 Docker backend 提供，不含 `/api/v1/go2-companion/go2-voice-turn`，状态仍为 `response_only / not_configured`。

Gate 结论：`T0=HOLD`。必须先确认手机 Unitree Go App、远端视频客户端及其他电脑 WebRTC 会话全部关闭；经现场授权后可重启 Go2 WebRTC 服务或设备，然后重新执行 connect-only T0。T0 成功前保持 `GO2_AUDIO_ENABLED=false`，不得进入 T1。

### 10.2 2026-08-24 历史成功版本与 SDP 结构复核

脱敏证据：`artifacts/go2_audio_hardware_validation/T0_DIAG_20260824.json`。

固件结论：

- 旧文档中的 `V1.1.14` 来自更早的 Unitree App 截图，不是本轮实时读取；
- 2026-07-29 历史播放成功时和 2026-08-24 当前探针均实际得到 `data2=3`；
- 因此当前在线设备可证明为 Go2 `>=1.1.15`；上游当前把 `1.1.15` 列为 Go2 最新版，所以设备很可能就是 `1.1.15`，但在 WebRTC 未连接且未使用 App/云端账号读取前，不能把精确版本写成实测值。

历史成功版本结论：

- 本地 `unitree_webrtc_connect` 为 commit `611ea3706be3acf096d5aa00e6b75abcd011024c`、package `2.1.2`；
- 2026-07-29 Go2 扬声器真机播放 `PASS`，任务 `FINISHED`、sink `IDLE`、总耗时 `10.98 s`；
- 2026-07-30 同一 LocalSTA 链路成功连接并接收 48 kHz 双声道 PCM，失败点是上行只有约 216 Hz 嗡声，而不是 WebRTC 连接失败；
- 上述历史验收与当前使用同一 `.venv312`、同一 aiortc `1.15.0`、同一 package `2.1.2` 和同一 commit；`webrtc_driver.py` / `unitree_auth.py` 自 2026-07-18 clone 后未变化；
- 当前上游 `2.2.0` 只新增 R1 family plumbing，release note 明确说明 WebRTC transport/auth 不变，因此升级到 `2.2.0` 不是当前 T0 的针对性修复。

当前 SDP 结构结论：

- AES key 解密与 `con_notify -> con_ing_*` 加密信令完整成功；机器人返回合法、非 `reject` 的 JSON answer；
- offer 和 answer 都包含 audio、video、application 三个 media section；
- 双方 application media 均为 `UDP/DTLS/SCTP webrtc-datachannel` + `a=sctp-port` 的 RFC 8841 格式；
- 双方只声明 `sha-256` fingerprint，没有 `sha-384` / `sha-512`；因此 aiortc Issue #1338 所述 fingerprint 触发条件不适用于本次失败；
- 本地 offer 有目标 `/24` candidate，远端 answer 有目标 IP `192.168.123.161` candidate，排除“机器人 SDP 只返回错误网卡地址”；
- 信令正确完成后 ICE 仍停在 `checking`、peer connection 停在 `connecting`。剩余优先原因是机器人被其他 App/远端客户端或陈旧会话占用、机器人 WebRTC 服务卡住，或 UDP ICE 数据面没有得到响应。

本轮只做 connect-only / signal-only 诊断：音频通道未开启，播放和录音均未开始，运动调用为 0。T0 继续 `HOLD`，T1-T9 继续 `NOT_RUN`。

### 10.3 2026-08-24 19:38 单次 T0 重试

脱敏证据：`artifacts/go2_audio_hardware_validation/T0_RETRY_20260824_1938.json`。

用户要求“再试一次”后执行了唯一一次 connect-only 重试。预检确认 Go2 ping 和 TCP/9991 正常，本机 8093 未监听、没有持有 Go2 socket 的本机 WebRTC 客户端。运行时保持历史成功基线：`unitree_webrtc_connect 2.1.2 / 611ea37 / aiortc 1.15.0 / Python 3.12`。

结果：

- `con_notify:9991` 信令启动成功；
- ICE 持续 `checking`，peer connection 持续 `connecting`；
- 20 秒后 `TIMEOUT`，data channel 未打开；
- 麦克风、扬声器、播放、录音均未启用，运动调用为 0；
- disconnect 协程未在额外 5 秒内返回，但 peer/ICE/signaling 均记录到 `closed`，诊断进程以 code 0 退出；复核残留探针进程和已建立 Go2 TCP 会话均为 0；
- 超时清理期间再次出现 aiortc 后台任务的 `remoteDescription=None` 异常；结合 10.2 已取得的合法 SDP answer，该异常继续作为 teardown symptom 记录，不作为信令 answer 缺失的证据。

Gate 结论保持 `T0=HOLD`，T1-T9 未执行。下一次硬件尝试前必须确认所有手机 App 和外部 WebRTC 客户端均关闭，并经现场授权重启 Go2 或其 WebRTC 服务；否则不重复连接测试。

执行时必须按 T1→T5 顺序。任一步失败时保存后端日志、WebRTC 错误码、状态接口快照和失败音频（在取得老人授权并遵守隐私要求的前提下），停止后续 Gate，不扩大功能。

## 11. 尚未解决问题

1. 真实 Go2 T0 当前为 `HOLD`，T1–T9 未执行，因此不能宣称硬件 Gate 通过。
2. 真机 PCM format、声道布局、实际 RMS 噪声底需要现场校准；当前对不支持格式 fail closed。
3. 同步 ASR/TTS SDK 工作线程不能被 Python 强杀，只能靠 API 阶段停止和 SDK 自身网络超时收敛。
4. P0-1 Lifecycle Service 尚未在本实现中接线；结构化 START/STOP/RESUME 不会自动执行。
5. 未实现全双工、唤醒词、语音打断或自动运动执行，且不属于本阶段。
