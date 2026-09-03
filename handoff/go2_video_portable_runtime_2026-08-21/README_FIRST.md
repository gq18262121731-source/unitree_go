# Go2 可移植视频运行包

本包不包含任何真实账号、密码、设备序列号、AES Key、DPAPI 文件、RTSP 密码或虚拟环境。

每台目标电脑都必须：

1. 使用 Python 3.12 重建 `.venv312`。
2. 由最终运行视频桥的 Windows 用户配置本机 DPAPI 文件。
3. 使用目标 Go2 的当前 IP 启动 `start_go2_bridge.ps1`。
4. 以 `8093/status` 的 `hasFrame=true` 和持续增长的序列号作为成功依据。

本机获取并保存 Key：

```powershell
.\setup_go2_device_key.ps1
```

设备负责人已通过安全渠道掌握 Key 时，本机安全写入：

```powershell
.\write_go2_device_key_dpapi.ps1
```

禁止复制另一台电脑生成的 `.go2_aes_key.dpapi`。

完整说明见：

```text
docs/GO2_DEVICE_KEY_AND_CROSS_PC_VIDEO_RUNTIME_SOLUTION_2026-08-21.md
```
