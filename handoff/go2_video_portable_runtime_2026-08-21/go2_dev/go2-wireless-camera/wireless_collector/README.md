# Go2 本地视频桥（便携交付版）

本目录只负责把目标 Go2 的 WebRTC 画面转换为本机 HTTP 接口：

- `http://127.0.0.1:8093/status`
- `http://127.0.0.1:8093/snapshot`
- `http://127.0.0.1:8093/stream.mjpg`

## 首次配置

必须使用最终运行视频桥的 Windows 用户打开 PowerShell。

方式 A：通过绑定目标 Go2 的 Unitree 账号在本机获取设备密钥：

```powershell
.\setup_go2_device_key.ps1 -Region cn
```

方式 B：设备负责人已通过安全渠道取得 32 位十六进制设备 AES Key：

```powershell
.\write_go2_device_key_dpapi.ps1
```

两种方式都会在本目录生成仅限当前 Windows 用户、本机可解密的：

```text
.go2_aes_key.dpapi
```

该文件不能复制到另一台电脑，也不能由管理员账号生成后交给普通运行账号使用。

## 启动与验证

```powershell
.\test_go2_device_key_dpapi.ps1
.\start_go2_bridge.ps1 -RobotIp "192.168.8.251" -NoOpenBrowser
Invoke-RestMethod http://127.0.0.1:8093/status | ConvertTo-Json -Depth 8
```

只有 `data.hasFrame=true` 且 `data.sequence` 持续增长才表示收到真实画面。

完整安装、FFmpeg、MediaMTX、camera-service、PFV2 和验收步骤见交付包 `docs` 目录。
