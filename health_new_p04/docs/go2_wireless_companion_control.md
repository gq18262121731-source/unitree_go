# Flutter 老人端 Go2 无线伴随控制

## 目标与边界

老人登录 Flutter 后，可以在自己的首页进入“Go2 UWB 伴随”页面，查看状态并点击“开始伴随”或“停止伴随”。Flutter 不连接 Go2、不发送速度，也不建立第二条 WebRTC 连接。

正式调用链为：

```text
Flutter 老人端
  -> health_new 老人范围 API
  -> 老人身份、Go2 绑定、风险锁检查
  -> Go2 统一无线 Runtime（8093）
  -> 已有 WirelessUwbFollowSession
  -> 已有 WebRTC DataChannel 运动写入器
```

视频、SportState、UWB 和伴随运动继续共用同一个 Go2 WebRTC PeerConnection。

## 权限

- 老人账号只能读取和控制与自身账号 ID 相同的伴随资源。
- 社区和管理员账号保留现场运维权限。
- 家属账号不能发起或停止机器狗运动。
- 即使接口身份验证通过，未绑定 Go2、UWB 不可用、风险锁存在或运动控制忙时，后端仍会拒绝启动。

## health_new API

以下接口都需要正常登录令牌：

```http
GET  /api/v1/elders/{elder_id}/robot-companion/status
POST /api/v1/elders/{elder_id}/robot-companion/start
POST /api/v1/elders/{elder_id}/robot-companion/stop
```

Flutter 只使用这三个高层生命周期接口。请求体不包含 `vx`、`vy` 或 `wz`。

## 配置

在 `health_new` 的 `.env` 中设置：

```dotenv
COMPANION_BOUND_ELDER_ID=elder01_02
COMPANION_GATEWAY_BASE_URL=http://127.0.0.1:8093
```

`COMPANION_BOUND_ELDER_ID` 必须与登录老人账号的真实 ID 一致。若 `health_new` 与 Go2 Runtime 不在同一台电脑，将 `127.0.0.1` 改成运行 Go2 Runtime 的电脑 A 地址，并开放相应 TCP 端口。

## 启动顺序

### 1. 电脑 A：启动唯一 Go2 无线 Runtime

```powershell
cd "E:\笨笨狗\go2_dev\go2-gateway"
.\scripts\Start-Go2WirelessRuntimeWithFollowTarget.ps1
```

等待看到 WebRTC、DataChannel、SportState 和 Video Track 均为 READY。不要同时启动旧的独立无线视频客户端或第二个 WebRTC 客户端。

### 2. 启动 health_new

```powershell
cd "E:\笨笨狗\health_new_p04"
& "C:\Users\Test1\.conda\envs\health\python.exe" -m uvicorn backend.main:app --host 0.0.0.0 --port 8765
```

### 3. Flutter 老人端

1. 将服务器地址设为电脑 A 的 `http://<电脑A-IP>:8765/api/v1/`。
2. 使用绑定老人账号登录。
3. 在老人首页点击“Go2 UWB 伴随”。
4. 确认 Go2 在线、UWB 状态和距离/方向正常。
5. 确保场地开阔、原厂遥控器可立即停车后，再点击“开始伴随”。

## 状态与安全

- “开始伴随”只请求 Lifecycle START。Runtime 在真正运动前重新检查 WebRTC、UWB 新鲜度与运动控制权。
- “停止伴随”触发 Runtime 的安全停车并等待状态回到 IDLE。
- UWB stale、连接异常或控制冲突不会由 Flutter 绕过。
- 跌倒风险锁存在时，`health_new` 拒绝 START。
- Flutter 离开页面只停止状态轮询，不会伪造或直接修改机器人状态。

## 验证

只读检查 Go2 Runtime：

```powershell
Invoke-RestMethod "http://127.0.0.1:8093/api/v1/robot/companion/status" |
  ConvertTo-Json -Depth 8
```

正式真机点击测试前，应先确认返回内容中的 Runtime、机器人和 UWB 状态符合现场安全要求。测试结束后点击“停止伴随”，并确认状态为 `IDLE`、机器狗不再运动、遥控器仍可接管。
