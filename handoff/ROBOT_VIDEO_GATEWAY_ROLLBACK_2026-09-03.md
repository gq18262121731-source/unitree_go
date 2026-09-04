# 机器狗视频网关回退记录（2026-09-03）

## 当前启用版本

日常入口 `go2_dev/go2-gateway/scripts/Start-RobotVideoGateway.ps1` 已回退为轻量直连视频桥：

- 机器狗电脑地址：`192.168.8.254`
- Go2 地址：`192.168.8.245`
- 视频：`http://192.168.8.254:8093/stream.mjpg`
- 状态：`http://192.168.8.254:8093/status`
- 不启动 mDNS、新版视频 watchdog、运动、Follow、UWB 或语音模块。

启动命令：

```powershell
cd "E:\笨笨狗\go2_dev\go2-gateway"
.\scripts\Start-RobotVideoGateway.ps1 -ConfigureFirewall
```

首次放行防火墙后，日常启动可以省略 `-ConfigureFirewall`。

## 已保留的后续修复版

- 完整源码压缩包：`E:\笨笨狗\handoff\robot_video_gateway_unified_watchdog_2026-09-03.zip`
- 原启动器副本：`E:\笨笨狗\go2_dev\go2-gateway\scripts\Start-RobotVideoGateway-UnifiedWatchdog.ps1`
- SHA-256：`6DD4637227C0734D2CF69E093B98774BBB81F41E57A4EC20A2C58DF80EE93BB2`

需要继续修新版时，不要覆盖上述压缩包。

## 使用限制

回退版会独占 Go2 WebRTC。运行它时不要同时启动统一 Runtime、Unitree App 实时视频或其他 Go2 WebRTC 视频客户端，否则可能抢占连接、黑屏或卡顿。

该回退版固定要求机器狗电脑实际拥有 `192.168.8.254`。如果地址不对，启动脚本会直接报错，不会把错误地址交给移动端或主系统。
