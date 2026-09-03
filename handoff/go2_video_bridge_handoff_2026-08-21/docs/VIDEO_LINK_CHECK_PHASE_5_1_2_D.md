# Phase 5.1.2-D：视频链路回归验证报告

测试日期：2026-07-25  
当前状态：完成；现有 8093/WebRTC 视频链路 PASS  
范围：Go2 视频链路只读回归

## 1. 安全边界

本轮未修改：

- `health_new`、Mock Provider、go2-gateway 业务代码；
- Unitree SDK、CycloneDDS、Domain 或 DDS XML；
- Go2 网络配置或固件。

本轮未启动 ROS2、Nav2、SLAM 或 L1，也没有创建 DDS publisher、
`SportClient` 或调用任何运动接口。

## 2. 当前网络模式

| 项目 | 实测值 |
| --- | --- |
| 网络模式 | Go2 自身 AP |
| SSID | `Go2_57838_ea9717f3` |
| AP BSSID | `96:ba:06:f8:e5:1f` |
| Go2 IP | `192.168.12.1` |
| Windows IP | `192.168.12.62` |
| 信号 | 93%，RSSI `-41 dBm` |
| Go2 ping | 4/4，0% 丢包，平均 1 ms |
| WebRTC 信令 | `192.168.12.1:9991` TCP 可达 |

Go2 IP 的 ARP MAC 与当前 AP BSSID 一致。

## 3. Unitree App 视频

| 项目 | 结果 |
| --- | --- |
| Go2 在线 | 本轮不再单独复测；网络与现有 WebRTC 桥已证明在线 |
| 实时视频 | 本轮不再单独复测 |
| 延迟 | 本轮不再单独复测 |
| 离线/黑屏/超时提示 | 不适用 |

为了避免 Go2 单视频会话造成假失败，完成 8093 验证后已停止本机视频桥并
释放会话。App 结果必须在 8093 停止后观察。

## 4. 8093 现有视频桥

测试前 8093 没有监听。复用已有视频桥、已有 Python 环境和 DPAPI 加密设备
密钥，以当前实际 Go2 IP 建立 WebRTC 视频会话；没有新增实现或改动配置。

启动后 `/status`：

```text
serviceId: go2-wireless-camera
serviceState: running
videoState: ready
robotIp: 192.168.12.1
connected: true
hasFrame: true
lastFrameAt: 2026-07-25T09:03:37.646619+08:00
frameAgeMs: 109
captureFps: 8.19
resolution: 1280x720
errorCount: 0
reconnectCount: 0
lastErrorCode: null
```

5 秒采样：

```text
sequence: 504 -> 548
frames advanced: 44
observed rate: about 8.8 FPS
reported captureFps: 8.19
```

HTTP 读取：

| 接口 | 结果 |
| --- | --- |
| `/status` | HTTP 200 |
| `/snapshot` | HTTP 200，`image/jpeg`，105858 bytes，约 21 ms |
| `/stream.mjpg` | HTTP 200，MJPEG；5 秒收到约 4.86 MB |

运行日志存在间歇性 H.264 包解码警告，但有效帧持续更新，`hasFrame` 保持
为 true，因此不判定为链路失败。

现有启动器使用 `LocalSTA(ip=192.168.12.1)` 代码路径，所以状态标签显示
`Go2 STA / WebRTC`；物理网络仍是 Go2 自身 AP。库中的 `LocalAP` 也是把 IP
固定为 `192.168.12.1` 后调用相同的 WebRTC 初始化。

验证结束后已停止 8093，避免占用手机 App 视频会话。

## 5. 页面验证

### 现有 8093 视频测试页面

浏览器实测：

```text
状态: 无线画面在线
机器人: 192.168.12.1
画面: 1280x720
帧率: 约 8.4 FPS
错误: 无
```

MJPEG `<img>` 元素：

```text
src: /stream.mjpg
complete: true
natural size: 1280x720
rendered size: 916x630
```

判定：PASS。

### RobotFollowPage

`health_new` 的现有构建产物可以加载，但在本轮隔离环境中没有启动
`health_new` 后端，页面停留在登录入口。为遵守“不修改/不启动 Mock 业务”
边界，没有绕过登录或启动 Mock 后端。

判定：本轮未完成 RobotFollowPage 端到端登录态验证；现有 8093 视频测试页
已完成真实画面验证。

## 6. 与历史视频成功状态对比

历史 RobotFollow 验收记录：

```text
resolution: 1280x720
about 8.4 FPS
frameAgeMs: 171
```

本轮：

```text
resolution: 1280x720
captureFps: 8.19
frameAgeMs: 109
```

分辨率、帧率和帧龄处于相同量级，本机 WebRTC 视频桥已恢复到历史成功状态。

## 7. 与 DDS 诊断的关系

当前自动验证支持以下分层结论：

```text
Go2 AP IP network: PASS
Go2 WebRTC signaling: PASS
Go2 video stream: PASS
SDK2 DDS remote discovery: FAIL
```

视频使用 `192.168.12.1:9991` 信令和 WebRTC 媒体链路；SDK2 状态读取使用
DDS/RTPS discovery。视频成功不能证明 DDS 正常，但证明 Go2 网络、视频服务
和有效双向应用通信正常。

本阶段按现有 8093/WebRTC 实测判定为情况 A：视频链路正常，DDS 问题继续
定位到 SDK2 DDS 服务、状态发布、运行模式或固件行为。用户确认不再追加
Unitree App 视频复测，进入 Phase 5.1.3。

## 8. 下一步建议

1. Phase 5.1.2-D 已完成并停止；
2. 本轮没有修改 DDS、Domain、SDK 或业务代码；
3. 后续进入 Phase 5.1.3，仅诊断 SDK2 DDS 状态发布。
