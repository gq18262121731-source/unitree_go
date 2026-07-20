# Go2 无线视频采集

该服务通过 Go2 AP 热点和 WebRTC 读取 Go2 EDU 内置摄像头，不使用狗背网线。

## 使用

1. 将电脑 Wi-Fi 连接到 `Go2_57838_34ab40aa`。
2. 关闭手机 Unitree Go App 的实时视频，避免单会话占用。
3. 首次使用时运行一次安全配置。脚本会在本机提示输入账号和密码，并用 Windows 当前用户加密保存设备密钥：

```powershell
cd "E:\笨笨狗\go2_dev\go2-wireless-camera\wireless_collector"
.\setup_wireless.ps1
```

4. 在 PowerShell 运行服务：

```powershell
cd "E:\笨笨狗\go2_dev\go2-wireless-camera\wireless_collector"
.\start_wireless.ps1
```

5. 浏览器打开 `http://127.0.0.1:8093/`。

开发自检时也可经有线地址验证 WebRTC：

```powershell
$env:GO2_WEBRTC_MODE="sta"
$env:GO2_WEBRTC_IP="192.168.123.161"
..\..\unitree_webrtc_connect\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8093
```

接口：

- `GET /status`
- `GET /snapshot`
- `GET /stream.mjpg`

`GET /status` 为向后兼容的 v1 状态契约。顶层包含：

```text
success / ok / apiVersion / serviceVersion / serviceId / timestamp
```

`data` 在保留 `connected`、`hasFrame`、`captureFps`、`latestFrame` 等旧字段的基础上，增加：

```text
serviceState / videoState / lastFrameAt / frameAgeMs
resolution / source / clientCount / lastErrorCode / error
```

当前 `serviceId` 为 `go2-wireless-camera`，`apiVersion` 为 `1`。`hasFrame=true` 表示最近 `GO2_FRAME_STALE_SECONDS` 秒内仍收到新帧，默认阈值为 3 秒。

通过 Go2 AP 取流时，电脑的单块 Wi-Fi 被机器人热点占用。若还要把视频推到公网服务端，需要第二块上网网卡，或让 Go2 和电脑连接同一个独立路由器后改用 STA-L。

## 当前 STA-L 配置

- 路由器：`E5576-822_D7E5`
- 电脑：`192.168.8.251`
- Go2：`192.168.8.248`
- 本地视频：`http://127.0.0.1:8093/`

以后可直接双击 `start_wireless_video.cmd` 启动，双击 `stop_wireless_video.cmd` 停止。

## 社区端本机启动器模式

社区端 `#/robot-follow` 页面通过当前用户级 Windows 协议 `go2bridge://start` 调用本目录的 `start_sta_wireless.ps1`。启动器固定传入：

```powershell
.\start_sta_wireless.ps1 -NoOpenBrowser
```

`-NoOpenBrowser` 只关闭脚本原有的独立预览页打开动作，不改变 Go2 连通性检查、密钥读取、WebRTC 连接或 `8093` 服务启动逻辑。工作人员点击社区端按钮后会留在当前页面；页面轮询：

```text
GET http://127.0.0.1:8093/status
```

当 `data.hasFrame=true` 时，社区端在当前页面重新加载：

```text
http://127.0.0.1:8093/stream.mjpg
```

为支持浏览器读取 `/status`，服务只允许以下开发环境 Origin 跨域 GET：

- `http://127.0.0.1:5173`
- `http://localhost:5173`

若社区端部署到其他协议、主机或端口，必须在 `app.py` 中显式增加该 Origin；不要使用任意来源通配符。

## 状态契约测试

```powershell
cd "E:\笨笨狗\go2_dev\go2-wireless-camera\wireless_collector"
& "E:\笨笨狗\go2_dev\unitree_webrtc_connect\.venv312\Scripts\python.exe" `
  -m unittest discover -s tests -v
```
