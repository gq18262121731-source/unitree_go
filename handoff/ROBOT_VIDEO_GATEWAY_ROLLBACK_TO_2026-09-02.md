# 回退结果

日常入口已切换到 `2026-09-02 08:33` 的冻结 Runtime。

- Go2 当前地址：`192.168.8.245`
- 机器狗电脑：`192.168.8.254`
- 视频：`http://192.168.8.254:8093/stream.mjpg`
- 启动：`E:\笨笨狗\go2_dev\go2-gateway\scripts\Start-RobotVideoGateway.ps1`
- 9.2 冻结说明：`E:\笨笨狗\go2_dev\go2-gateway\snapshots\2026-09-02\README.md`

切换时必须先在原视频桥窗口按 `Ctrl+C`，确认 8093 已释放，再重新运行日常入口。

9.3 轻量直连版已经保存为：

`E:\笨笨狗\handoff\robot_video_gateway_direct_2026-09-03.zip`

恢复后的 9.2 运行包：

`E:\笨笨狗\handoff\robot_video_gateway_recovered_2026-09-02.zip`

SHA-256：`7806D7FB8AC54BA923EEFAE26B793764E112135068D37FA3723EC8EF00DC52D9`

## 现场验证（2026-09-03 18:02）

- WebRTC：CONNECTED
- DataChannel：READY
- Video Track：READY
- `data.connected=true`
- `data.hasFrame=true`
- 4 秒内帧序号从 96 增长到 112（增加 16 帧）
- 状态帧率约 5.12 fps
- 两帧 MJPEG 请求：HTTP 200，共 197793 bytes

随后现场视频源开始持续返回损坏的 H.264 PPS/NAL 数据。9.2 Runtime
执行了 6 次恢复/重连仍未恢复连续新帧，因此已通过 `EXIT` 正常停止并释放
WebRTC 和 8093。该现象发生在解码输入端，不影响 9.2 回退文件本身；再次验收前
应关闭 Unitree App 实时视频并重启 Go2，再运行启动脚本。
