# 机器狗项目部署入口

本文是仓库级部署导航。各子系统参数较多，执行到对应步骤时应继续阅读链接中的专项手册。

## 1. 拉取仓库与大文件

```powershell
git clone https://github.com/gq18262121731-source/unitree_go.git
cd unitree_go
git lfs install
git lfs pull
```

建议准备：Windows 10/11、PowerShell 7、Git LFS、Python/Conda、Node.js/npm、Docker Desktop，以及与 Go2 可互通的有线或无线网络。ROS 2 与 LiDAR/SLAM 路径建议使用 Ubuntu 22.04 和 ROS 2 Humble。

## 2. 智慧康养主系统

当前主目录为 `health_new_p04`。

```powershell
cd health_new_p04
conda create -n helth python=3.11 -y
Copy-Item .env.example .env
conda run -n helth python -m pip install -r requirements.txt
docker compose -f docker/docker-compose.yml up -d redis
conda run -n helth powershell -ExecutionPolicy Bypass -File .\scripts\start_server.ps1
```

另开终端启动前端：

```powershell
cd health_new_p04
conda run -n helth powershell -ExecutionPolicy Bypass -File .\scripts\start_frontend.ps1
```

后端默认健康检查：

```powershell
curl http://127.0.0.1:8000/healthz
```

完整环境变量、训练、接口和局域网真机说明见 [health_new_p04/README.md](health_new_p04/README.md)。摄像头运行时见 [CAMERA_PROJECT_RUNTIME.md](health_new_p04/camera_runtime_external/CAMERA_PROJECT_RUNTIME.md)，语音部署故障见 [VOICE_DEPLOYMENT_TROUBLESHOOTING.md](health_new_p04/docs/VOICE_DEPLOYMENT_TROUBLESHOOTING.md)。

## 3. Go2 实时视频桥

设备密钥不能从其他 Windows 用户或电脑直接复制。目标电脑应由最终运行用户按照文档生成 DPAPI 文件：

- [设备密钥写入与跨电脑视频部署](GO2_DEVICE_KEY_AND_CROSS_PC_VIDEO_RUNTIME_SOLUTION_2026-08-21.md)
- [Go2 到远端视觉服务器完整 Runbook](GO2_REALTIME_VIDEO_TO_VISION_SERVER_RUNBOOK_2026-08-21.md)
- [大帧、分辨率、RTSP 与 PFV2 排障](GO2_REALTIME_VIDEO_LARGE_FRAME_SOLUTION_2026-08-21.md)
- [视频桥交付包入口](handoff/go2_video_portable_runtime_2026-08-21/README_FIRST.md)

部署顺序为：网络与端口预检 → 目标电脑生成密钥 → 启动 8093 视频桥 → FFmpeg 发布 H.264 → MediaMTX 提供 RTSP → camera-service/PFV2 接入 → 30 分钟稳定性验收。

## 4. Go2 网关与只读接入

- [Go2 网关说明](go2_dev/go2-gateway/README.md)
- [Go2 只读适配器](go2_dev/go2-readonly-adapter/README.md)
- [Go2 WebRTC 连接库](go2_dev/unitree_webrtc_connect/README.md)
- [只读稳定性验收](GO2_READONLY_STABILITY_PHASE_6_1_B.md)

首次接真机时优先使用只读模式，确认网络、DDS/WebRTC 和传感器数据稳定后，再启用任何运动控制功能。

## 5. ROS 2、LiDAR 与 SLAM

- [ROS 2 Humble 虚拟机安装计划](ROS2_HUMBLE_INSTALL_PLAN_PHASE_5_2_3_VM.md)
- [Unitree ROS 2](go2_dev/unitree_ros2/README.md)
- [DDS/ROS 2 传感器桥](DDS_ROS2_BRIDGE_PHASE_5_3.md)
- [TF 坐标验证](TF_COORDINATE_VALIDATION_PHASE_5_4.md)
- [LiDAR 坐标链分析](LIDAR_COORDINATE_CHAIN_ANALYSIS_PHASE_5_4_1.md)
- [SLAM 路线选择](SLAM_ROUTE_SELECTION_PHASE_5_4_4.md)
- [Point-LIO 目标场景验证](POINT_LIO_TARGET_SCENE_PHASE_5_4_5.md)

仓库中的 `phase*` 目录包含复现实验所需的脚本、采集数据和结果。大文件由 Git LFS 管理。

## 6. 配置与安全边界

不要提交或分发以下内容：

- 任何真实 `.env` 或云服务 API Key；
- `.go2_aes_key.dpapi` 等用户/电脑绑定密钥；
- Unitree 账号密码、验证码、设备私钥；
- 本机虚拟环境、依赖缓存、系统镜像和安装器。

按模块复制 `.env.example`，只在目标机器本地填写真实配置。公开日志和验收截图前，应再次检查账号、设备序列号、内网地址和个人数据。

## 7. 最小验收

1. `git lfs pull` 完成且没有缺失对象。
2. Redis、后端和前端均可启动，`/healthz` 返回成功。
3. 视频桥状态中的帧序号持续增长，RTSP 可被 `ffprobe` 或 VLC 读取。
4. camera-service 能持续获取最新帧并输出视觉结果。
5. ROS 2 路径下所需 topic、TF 和传感器频率符合各阶段文档。
6. 连续运行 30 分钟，无持续断流、进程退出或资源失控。

