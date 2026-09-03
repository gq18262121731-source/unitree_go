# Go2 视频桥安全交付包

先阅读：

```text
docs/GO2_REALTIME_VIDEO_BLOCKER_SOLUTION_AND_ASSET_HANDOFF_2026-08-21.md
```

主路径：

```text
go2_dev/go2-wireless-camera/wireless_collector
go2_dev/unitree_webrtc_connect
```

重要规则：

1. 本包不包含 `.go2_aes_key.dpapi`、明文凭据、虚拟环境或 MediaMTX。
2. 在目标电脑使用 Python 3.12 重建 `.venv312`。
3. 由最终运行视频桥的 Windows 用户执行 `setup_wireless.ps1`。
4. 先完成 Go2 有线 IP、9991/8081 和 8093 快照验证，再配置 RTSP。
5. 不运行任何运动控制、DDS publisher 或 SportClient 示例。
6. 使用 `FILE_MANIFEST_SHA256.csv` 验证包内文件。

备用路径 `collector + unitree_sdk2_python` 只在主 WebRTC 路径失败且有 Linux/WSL SDK 维护能力时使用。
