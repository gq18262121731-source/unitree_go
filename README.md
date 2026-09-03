# Unitree Go2 综合项目

本仓库汇总 Unitree Go2 机器狗相关的开发、联调和验证材料，包含 Go2 SDK/ROS 接入、视频桥、网关、LiDAR/SLAM 实验、智慧康养应用、模型与现场证据。

## 主要目录

- `go2_dev/`：Go2 SDK、ROS/ROS 2、WebRTC、视频采集与网关代码。
- `health_new_p04/`：当前智慧康养主系统版本，包含 FastAPI 后端、Vue 前端、Flutter 客户端、视觉与跌倒检测模块。
- `camera-service/`：独立视觉服务。
- `phase*`：各阶段 ROS、LiDAR、SLAM、只读接入和稳定性验证的源码、数据与证据。
- `handoff/`：Go2 视频桥跨电脑交付包和清单。
- `说明书/`、`docs/`、根目录 `*.md`：设备资料、实施记录和技术文档。

## 开始部署

统一入口见 [DEPLOYMENT.md](DEPLOYMENT.md)。当前主系统的完整运行说明见 [health_new_p04/README.md](health_new_p04/README.md)。

Go2 实时视频链路建议按以下顺序阅读：

1. [Go2 设备密钥写入与跨电脑部署](GO2_DEVICE_KEY_AND_CROSS_PC_VIDEO_RUNTIME_SOLUTION_2026-08-21.md)
2. [Go2 实时画面接入视觉服务器实施手册](GO2_REALTIME_VIDEO_TO_VISION_SERVER_RUNBOOK_2026-08-21.md)
3. [实时画面与大画面问题解决方案](GO2_REALTIME_VIDEO_LARGE_FRAME_SOLUTION_2026-08-21.md)
4. [有线视频桥运行手册](go2_dev/go2-wireless-camera/WIRED_BRIDGE_RUNBOOK.md)

## 大文件

ROS bag、SQLite/ROS 2 bag、点云、模型与压缩归档使用 Git LFS。首次克隆后执行：

```powershell
git lfs install
git lfs pull
```

## 安全说明

仓库不包含本机 `.env`、云服务 API Key、Go2 DPAPI 密钥文件、现场语音缓存/麦克风录音、操作系统镜像、第三方安装器、虚拟环境、`node_modules` 和构建缓存。展开后的便携运行包不重复提交，使用同目录下的 ZIP 发布包即可。请从各模块的 `.env.example` 创建本机配置，并按部署文档在目标机器上重新生成机器绑定密钥。
