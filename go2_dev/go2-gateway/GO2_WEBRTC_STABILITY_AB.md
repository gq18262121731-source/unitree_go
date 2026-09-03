# Go2 WebRTC 稳定性诊断与四组 A/B

该诊断只建立 WebRTC 连接、订阅状态并按组开关媒体轨道；不会启动
`FollowController`，不会发送运动指令，也不会调用 AudioHub。

## 四组矩阵

| 组 | Video | UWB | Sport | LowState | MultipleState | AudioHub |
|---|---:|---:|---:|---:|---:|---:|
| A | ON | ON | ON | ON | ON | OFF |
| B | ON | ON | ON | OFF | ON | OFF |
| C | OFF | ON | ON | OFF | ON | OFF |
| D | ON | OFF | ON | OFF | ON | OFF |

`Sport` 始终开启，因为它既提供 readiness，也作为 DataChannel 健康信号。
`MultipleState` 保留，用于观察 UWB switch；高频 `LowState` 只在 A 组开启。

## 正式运行

在机器人处于静止、Unitree App 已关闭且电脑连接 Go2 STA 网络时执行：

```powershell
cd E:\笨笨狗\go2_dev\go2-gateway
.\scripts\Start-Go2WebRTCStabilityAB.ps1
```

启动器会使用 `unitree_webrtc_connect\.venv312`，设置本地 SDK 路径，并从
现有 DPAPI 文件加载设备密钥。不要使用 Conda `(base)` 环境中的 `python`
直接运行该工具。

默认每组 600 秒、组间冷却 5 秒。先做短烟测可用：

```powershell
.\scripts\Start-Go2WebRTCStabilityAB.ps1 -DurationSeconds 30
```

也可只运行指定组：

```powershell
.\scripts\Start-Go2WebRTCStabilityAB.ps1 -TestGroups B,C
```

每次运行会在 `data/webrtc_stability/<时间>/` 生成：

- `summary.json`：四组对照总表；
- `group_X_result.json`：该组的重连、consent、stale 和最长在线指标；
- `group_X_samples.jsonl`：每 0.5 秒一条的原始健康快照。

重点比较 `reconnectCount`、`consentExpiredCount`、
`sportStateStaleEpisodes`、`videoStaleEpisodes`、`uwbStaleEpisodes` 和
`longestContinuousOnlineSeconds`。B 明显优于 A 指向 LowState/DataChannel 负载；
C 明显优于 B 指向视频并发；D 优于 B 则提示 UWB 订阅参与了退化。

## Runtime 策略

生产 Runtime 默认只在 PeerConnection、ICE 或 DataChannel 明确失败时重连。
媒体或 sport state stale 会进入 `DEGRADED`，不会自行拆掉仍为 connected 的
transport。硬失败后的新 PeerConnection 按 2、4、6、8、10 秒冷却退避。

如必须临时恢复“双信号 stale 后重连”的旧行为：

```text
GO2_WEBRTC_RECONNECT_ON_STALE=true
GO2_WEBRTC_STALE_GRACE_SECONDS=10
```

可以用 `GO2_WEBRTC_ENABLE_LOW_STATE=false` 做长期低负载运行；其余订阅和
音频轨道也有对应的 `GO2_WEBRTC_ENABLE_*` 开关，详见 `.env.example`。
