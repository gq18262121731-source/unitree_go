# 机器狗视频网关交接说明（9.2 回退版）

> 当前启用版本：2026-09-02 08:33 冻结 Runtime  
> 当前网络：Go2 为 `192.168.8.245`，机器狗电脑为 `192.168.8.254`

## 启动

先关闭 Unitree App 实时视频，并确认没有其他 8093 视频桥或 Go2 WebRTC 程序正在运行。

```powershell
cd "E:\笨笨狗\go2_dev\go2-gateway"
.\scripts\Start-RobotVideoGateway.ps1
```

首次需要放行防火墙时，在管理员 PowerShell 中执行：

```powershell
.\scripts\Start-RobotVideoGateway.ps1 -ConfigureFirewall
```

启动窗口必须保持运行。正常停止请在该窗口按 `Ctrl+C`。

## 视频地址

主系统和 Flutter 移动端统一使用机器狗电脑地址：

```text
网关：http://192.168.8.254:8093
视频：http://192.168.8.254:8093/stream.mjpg
状态：http://192.168.8.254:8093/status
快照：http://192.168.8.254:8093/snapshot
```

Go2 的 `192.168.8.245` 只供机器狗电脑连接，主系统和移动端不要直接连接 Go2。

本回退版不使用 `robot-gateway.local` 或 mDNS 搜索，客户端暂时手动配置 `http://192.168.8.254:8093`。

## 验收

机器狗电脑本机执行：

```powershell
Invoke-RestMethod "http://127.0.0.1:8093/status" | ConvertTo-Json -Depth 8
```

确认：

```text
data.connected = true
data.hasFrame = true
data.latestFrame.sequence 持续增加
```

然后在主系统电脑和手机上分别访问：

```text
http://192.168.8.254:8093/stream.mjpg
```

## 版本说明

9.2 原始 Python 源文件在 9.3 被覆盖，但本机保留了 9.2 当天实际生成的 CPython 3.9 缓存。当前启动器使用这个冻结文件，并在启动前校验 SHA-256：

```text
源时间：2026-09-02 08:33:00
源大小：159700 bytes
SHA-256：05BAB7C9A5766B8F3B80896DF03F6E217A6785FD6166B6FFA40D8C05BA92BFCF
```

冻结文件：

```text
E:\笨笨狗\go2_dev\go2-gateway\snapshots\2026-09-02\app\webrtc\go2_wireless_runtime.pyc
```

9.3 两个版本都已保留：

```text
统一 watchdog 版：
E:\笨笨狗\handoff\robot_video_gateway_unified_watchdog_2026-09-03.zip

轻量直连版：
E:\笨笨狗\handoff\robot_video_gateway_direct_2026-09-03.zip
SHA-256：8B34964DB7E17AB762EB13CB2B1BECD774DB658F5DCE13AA2A6957421FC566F4
```

不要同时启动多个 Go2 WebRTC 程序。9.2 Runtime 不会自动执行运动演示（`AutoDemo=none`），但它仍是统一 Runtime，会占用机器狗连接。

