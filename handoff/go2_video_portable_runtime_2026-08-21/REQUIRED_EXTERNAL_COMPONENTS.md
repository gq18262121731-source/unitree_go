# 另一台电脑仍需自行准备的组件

本包不包含以下外部组件，目标电脑必须自行安装或从项目正式交付源取得：

| 组件 | 目标位置示例 | 要求 |
|---|---|---|
| Python 3.12 x64 | 系统安装 | 能创建 `.venv312` |
| Python 虚拟环境 | `go2_dev\unitree_webrtc_connect\.venv312` | 必须在目标电脑重建，不能复制现有虚拟环境 |
| FFmpeg | `tools\ffmpeg\bin\ffmpeg.exe` 或 PATH | 必须支持 `libx264` 和 `mpjpeg` |
| MediaMTX | `runtime\mediamtx\mediamtx.exe` | 使用目标电脑自己的配置和 RTSP 凭据 |
| camera-service | `vision\camera-service` | 必须是正式完整工程，含其依赖、配置、启动入口 |
| PFV2 模型 | 由 camera-service 配置指定 | 模型文件、推理依赖、设备驱动必须完整 |

以下内容必须在每台目标电脑现场生成或填写，交付包不会携带：

- `.go2_aes_key.dpapi`；
- Unitree 账号、App 密码、目标 Go2 序列号、明文 AES Key；
- 正式 `mediamtx.yml` 内的 RTSP 用户名和密码；
- camera-service 的生产配置和业务密钥；
- 日志、截图和运行时缓存。

完整操作见 `docs\GO2_DEVICE_KEY_AND_CROSS_PC_VIDEO_RUNTIME_SOLUTION_2026-08-21.md`。
