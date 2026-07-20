# Unitree Go2 开发能力与当前环境说明

编写日期：2026-07-09

本文档用于回答两个问题：

1. 这台 Unitree Go2 当前有哪些功能可以被我们开发或调用。
2. 在这台电脑当前环境下，哪些能力已经准备好，哪些还需要先解决通信问题。

本文档只做开发准备和能力说明，不要求立即执行任何会改变机器狗状态的操作。

## 1. 当前结论

当前电脑侧开发环境已经基本准备好：

- 本地已有官方 SDK 仓库：
  - `unitree_sdk2`
  - `unitree_sdk2_python`
  - `unitree_ros2`
- Ubuntu/WSL 环境已经安装 Python SDK 所需依赖：
  - `unitree_sdk2py`
  - `cyclonedds==0.10.2`
  - `numpy`
  - `opencv-python`
- Windows 以太网已经配置到 Go2 常用网段。
- Go2 当前可在 `192.168.123.161` ping 通。

当前主要阻塞点：

- 电脑可以向 Go2 发出 DDS 发现数据，但目前还没有收到 Go2 返回的 DDS/UDP 数据。
- 因此，SDK 层面的状态订阅、摄像头、ROS2 话题等能力目前还没有在本机验证成功。
- 在 SDK/DDS 数据未通之前，不应运行运动控制、低层电机控制、避障控制、灯光音量设置、雷达开关等示例。

换句话说：**Go2 的 SDK 能力是明确存在的，电脑侧环境也已经准备到位；但当前还需要先解决 Go2 侧 DDS/SDK 服务响应问题，才能安全进入实际开发验证。**

## 2. 资料来源

本文档依据以下资料整理：

- 宇树官方 Go2 开发文档入口：<https://support.unitree.com/home/zh/developer/about_Go2>
- 宇树官方 SDK2 Python 仓库：<https://github.com/unitreerobotics/unitree_sdk2_python>
- 宇树官方 SDK2 仓库：<https://github.com/unitreerobotics/unitree_sdk2>
- 宇树官方 ROS2 支持仓库：<https://github.com/unitreerobotics/unitree_ros2>
- 本地说明书目录：`E:\笨笨狗\说明书`
- 本地 SDK 目录：`E:\笨笨狗\go2_dev`

官方 ROS2 文档说明 Unitree SDK2 使用 CycloneDDS 通信，Go2、B2、H1 等机器人底层可以与 ROS2 兼容；官方 Python SDK README 和示例提供了状态读取、高层运动、低层控制、遥控器读取、前置摄像头、避障、VUI 灯光音量等能力入口。

## 3. 安全等级说明

后续开发前，请先按下面的风险等级理解各功能：

| 等级 | 含义 | 是否建议当前执行 |
| --- | --- | --- |
| 绿色 | 只读取数据，不改变机器狗状态 | DDS 通信恢复后可以优先验证 |
| 黄色 | 可能改变配置、模式、灯光、音量或开关状态 | 暂不执行，先读代码再决定 |
| 红色 | 可能让机器狗运动、站立、翻滚或输出电机力矩 | 当前不要执行 |

第一阶段只建议做绿色功能，并且只在确认 DDS 数据能正常返回后进行。

## 4. 功能开发矩阵

| 功能 | 可开发/可使用内容 | 本地入口 | 风险 | 当前状态 |
| --- | --- | --- | --- | --- |
| 网络连通 | ping Go2、确认网卡 IP、确认接口名 | `tools/check_go2_network.ps1` | 绿色 | 已基本可用，Go2 `192.168.123.161` 可达 |
| 只读状态检查 | 订阅低层状态和运动状态，确认 DDS 是否通 | `tools/go2_read_only_status.py` | 绿色 | 当前未收到 DDS 数据 |
| 运动状态读取 | 读取姿态、位置、速度、步态、足端位置、障碍距离等 | ROS2 `read_motion_state`，Python/C++ topic 订阅 | 绿色 | DDS 通后可用 |
| 低层状态读取 | 读取 IMU、电机状态、电池、足端力、遥控器原始数据等 | ROS2 `read_low_state`，Python lowstate 订阅 | 绿色 | DDS 通后可用 |
| 遥控器状态读取 | 解析手柄摇杆、按键状态 | `example/wireless_controller/wireless_controller.py` | 绿色 | 需先修正示例为 Go2 消息类型，DDS 通后可用 |
| 前置摄像头 | 电脑调用 Go2 前置摄像头图像，用 OpenCV 显示或保存 | `example/go2/front_camera/camera_opencv.py` | 绿色 | SDK 支持，当前尚未验证成功 |
| 截图保存 | 获取一帧前置摄像头图片并保存到电脑本地 | `example/go2/front_camera/capture_image.py` | 绿色 | SDK 支持，当前尚未验证成功 |
| ROS2 话题 | 用 `ros2 topic list/echo` 读取 Go2 DDS/ROS2 话题 | `unitree_ros2` | 绿色 | 需先解决 DDS 响应 |
| rosbag 记录 | 记录状态话题，便于离线分析 | `unitree_ros2/example/src/src/record_bag.cpp` | 绿色 | DDS 通后可用 |
| 高层运动控制 | 站立、趴下、停止、速度控制、平衡站立、恢复站立、特殊动作等 | `example/go2/high_level/go2_sport_client.py` | 红色 | 当前不要运行 |
| 低层电机控制 | 直接向关节电机发送底层控制命令 | `example/go2/low_level/go2_stand_example.py` | 红色 | 当前不要运行 |
| 避障开关 | 查询/开启/关闭避障服务 | `example/obstacles_avoid/obstacles_avoid_switch.py` | 黄色 | 当前不要运行 |
| 避障移动 | 让避障模块接管 API 运动命令并移动 | `example/obstacles_avoid/obstacles_avoid_move.py` | 红色 | 当前不要运行 |
| VUI 灯光/音量 | 获取和设置亮度、音量 | `example/vui_client/vui_client_example.py` | 黄色 | 当前不要运行 |
| 运动模式切换 | 切换 `ai`、`normal`、`advanced` 等模式 | `example/motionSwitcher/motion_switcher_example.py` | 黄色 | 当前不要运行 |
| UTLidar 开关 | 向 `rt/utlidar/switch` 发布 ON/OFF | `example/go2/high_level/go2_utlidar_switch.py` | 黄色 | 当前不要运行 |

## 5. 摄像头能否被电脑调用

结论：**可以，但当前还需要先让 SDK/DDS 通信恢复正常。**

Go2 Python SDK 中有两个前置摄像头示例：

- `E:\笨笨狗\go2_dev\unitree_sdk2_python\example\go2\front_camera\camera_opencv.py`
- `E:\笨笨狗\go2_dev\unitree_sdk2_python\example\go2\front_camera\capture_image.py`

它们使用的核心方式是：

```python
from unitree_sdk2py.go2.video.video_client import VideoClient

client = VideoClient()
client.SetTimeout(3.0)
client.Init()
code, data = client.GetImageSample()
```

`camera_opencv.py` 会把 `GetImageSample()` 得到的图像数据转成 `numpy` 数组，再用 OpenCV 解码和显示：

```python
image_data = np.frombuffer(bytes(data), dtype=np.uint8)
image = cv2.imdecode(image_data, cv2.IMREAD_COLOR)
cv2.imshow("front_camera", image)
```

这说明电脑侧程序可以通过 SDK 拉取 Go2 前置摄像头画面，而不是必须依赖网页或 App。

不过它依赖 SDK 通信正常。当前本机状态是：

- Go2 IP 可达。
- DDS 发现包已从电脑发出。
- 尚未收到 Go2 的 DDS/UDP 回包。

所以摄像头功能目前应记录为：**官方 SDK 支持，本地环境已安装所需 OpenCV 依赖，但还没有在当前连接状态下验证成功。**

## 6. 推荐开发路线

### 阶段 0：只确认连接，不控制机器狗

目标是证明网络和 DDS 都能通信。

可做：

```powershell
cd E:\笨笨狗\go2_dev
.\tools\check_go2_network.ps1
```

Ubuntu 中只运行只读检查：

```bash
cd /mnt/e/笨笨狗/go2_dev
python3 tools/go2_read_only_status.py eth0 --seconds 15
```

成功标准：

- 能收到 `lowstate_messages` 或 `sportstate_messages`。
- 能看到 Go2 发回 UDP/DDS 数据。

如果仍然收不到，需要优先检查：

- Go2 是否已完成 App 激活。
- Go2 侧 SDK/DDS 服务是否开启。
- 当前机器狗型号/系统版本是否允许 SDK 连接。
- 网线是否插在机器狗正确的以太网口。
- Ubuntu/WSL 是否确实使用了 Go2 所在的 `192.168.123.x` 网卡。

### 阶段 1：只读状态开发

在 DDS 通信恢复后，优先开发这些内容：

- 读取 Go2 当前姿态、速度、步态。
- 读取 IMU、电池、电机状态。
- 读取足端力和足端位置。
- 读取遥控器按键和摇杆。
- 将状态数据保存为日志，便于分析。

推荐先使用本地只读脚本，再参考：

- `unitree_ros2/example/src/src/read_motion_state.cpp`
- `unitree_ros2/example/src/src/read_low_state.cpp`
- `unitree_sdk2_python/example/wireless_controller/wireless_controller.py`

注意：`wireless_controller.py` 当前默认导入的是 G1/H1 消息类型，Go2 使用前需要改为文件顶部注释中提示的 Go2 消息类型。

### 阶段 2：摄像头开发

DDS 通后，再验证摄像头：

```bash
cd /mnt/e/笨笨狗/go2_dev/unitree_sdk2_python
python3 example/go2/front_camera/camera_opencv.py eth0
```

预期现象：

- 弹出 OpenCV 窗口。
- 窗口名为 `front_camera`。
- 按 `ESC` 退出。

如果只想保存一张图片，可参考：

```bash
python3 example/go2/front_camera/capture_image.py eth0
```

该示例会在电脑本地保存图片，不会修改机器狗状态。

### 阶段 3：ROS2 集成

当 Python SDK 的只读状态和摄像头都正常后，再考虑 ROS2：

- 配置 ROS2 使用 CycloneDDS。
- 设置 `CYCLONEDDS_URI` 指向连接 Go2 的网卡。
- 用 `ros2 topic list` 确认话题。
- 用 `ros2 topic echo /sportmodestate` 或对应低频话题查看数据。
- 用 rosbag 记录状态，后续可做可视化、建模或控制算法调试。

ROS2 适合后续做：

- 多节点系统。
- 状态可视化。
- 数据记录与回放。
- 与导航、感知、算法模块集成。

### 阶段 4：谨慎进入控制开发

只有满足以下条件后，才考虑控制类开发：

- 只读状态稳定。
- 摄像头或状态话题稳定。
- 已理解每个控制示例的代码。
- 机器狗在空旷地面。
- 遥控器在手边，可以随时急停或接管。
- 电量充足，地面防滑，无人靠近。

仍然不建议一开始运行特殊动作，例如翻滚、跳跃、倒立、交叉步等。

## 7. 可开发方向详解

### 7.1 状态监控系统

这是最适合当前阶段的方向。

可开发内容：

- 实时显示姿态、速度、步态。
- 显示电池电压、电流、剩余状态。
- 显示 IMU 数据。
- 显示每个关节电机状态。
- 显示足端接触力。
- 显示遥控器输入。
- 保存 CSV 或日志。

价值：

- 不改变机器狗状态。
- 能验证 SDK 通信链路。
- 后续所有控制算法都需要这些状态数据。

### 7.2 摄像头画面采集

可开发内容：

- 实时显示前置摄像头。
- 保存图片或视频。
- 接入 OpenCV 做目标识别、颜色识别、二维码识别。
- 与状态数据同步保存。
- 后续接入视觉导航或远程监控界面。

当前限制：

- 依赖 `VideoClient.GetImageSample()` 能成功返回。
- 当前 DDS 尚未收到 Go2 回包，因此还不能确认图像通道已经可用。

### 7.3 ROS2 数据系统

可开发内容：

- 读取 Go2 ROS2/DDS 话题。
- 自建 ROS2 节点处理 Go2 状态。
- 使用 rosbag 记录实验数据。
- 后续接入 RViz、导航、SLAM、算法节点。

适合场景：

- 项目规模变大。
- 希望多个模块协作。
- 希望以后接入机器人生态工具。

### 7.4 高层运动控制

SDK 示例显示 Go2 可通过高层接口执行：

- 阻尼模式。
- 站立。
- 趴下。
- 前进。
- 横移。
- 旋转。
- 停止运动。
- 平衡站立。
- 恢复站立。
- 自由行走。
- 避障行走。
- 倒立、翻滚、跳跃等特殊动作。

本地入口：

- `E:\笨笨狗\go2_dev\unitree_sdk2_python\example\go2\high_level\go2_sport_client.py`

风险：

- 会直接让机器狗运动。
- 示例中包含翻滚、跳跃、倒立等危险动作。
- 当前阶段不要运行。

### 7.5 低层电机控制

低层控制可以直接面向关节电机发送控制命令。

本地入口：

- `E:\笨笨狗\go2_dev\unitree_sdk2_python\example\go2\low_level\go2_stand_example.py`

风险：

- 这是最高风险能力之一。
- 可能导致机器狗突然输出力矩、跌倒或损坏。
- 不建议在初期开发中使用。

建议：

- 先完成只读状态、摄像头和 ROS2 数据链路。
- 后续如果确实需要低层控制，应单独制定安全测试方案。

### 7.6 避障与导航相关能力

本地 Python SDK 示例包含避障客户端：

- `example/obstacles_avoid/obstacles_avoid_switch.py`
- `example/obstacles_avoid/obstacles_avoid_move.py`

可开发内容：

- 查询避障服务 API 版本。
- 查询避障开关状态。
- 设置避障开关。
- 通过避障模块控制移动。

风险：

- `obstacles_avoid_switch.py` 会开关避障状态。
- `obstacles_avoid_move.py` 会让机器狗移动。
- 当前不要执行。

### 7.7 VUI 灯光与音量

本地示例：

- `example/vui_client/vui_client_example.py`

可开发内容：

- 获取亮度。
- 设置亮度。
- 获取音量。
- 设置音量。

风险：

- 不会让机器狗走动，但会改变机器狗配置。
- 当前阶段不建议运行，除非先单独改成只读取版本。

### 7.8 UTLidar 开关

本地示例：

- `example/go2/high_level/go2_utlidar_switch.py`

该示例会向 `rt/utlidar/switch` 发布字符串 `ON` 或 `OFF`。

风险：

- 会改变雷达相关状态。
- 当前不要运行。

### 7.9 运动模式切换

本地示例：

- `example/motionSwitcher/motion_switcher_example.py`

示例中默认选择：

```python
selectMode = "ai"
```

可选模式注释中还包括：

- `normal`
- `advanced`
- `ai-w`

风险：

- 会改变机器狗运动模式。
- 当前不要运行。

## 8. 当前最重要的排障点

现在不是先写控制程序，而是先让 SDK 数据通起来。

建议按顺序排查：

1. 确认机器狗已完成官方 App 激活。
2. 确认机器狗当前系统支持 SDK2 连接。
3. 确认机器狗侧 SDK/DDS 服务处于启用状态。
4. 确认网线插在机器狗用于开发的以太网口。
5. 确认电脑网卡仍在 `192.168.123.x` 网段。
6. 确认 Ubuntu 内看到的接口是当前 Go2 网线接口。
7. 如果 WSL 仍收不到 DDS，考虑使用原生 Ubuntu、Ubuntu Live USB 或桥接虚拟机测试。

当前推荐的安全验证命令仍然是：

```bash
cd /mnt/e/笨笨狗/go2_dev
python3 tools/go2_read_only_status.py eth0 --seconds 15
```

不要用运动示例来“测试是否连通”。这很危险，也会让问题更难判断。

## 9. 近期推荐任务

按优先级排序：

1. 解决 DDS 无回包问题。
2. 让 `go2_read_only_status.py` 收到非零状态消息。
3. 保存一份成功的状态读取日志。
4. 验证前置摄像头 `camera_opencv.py`。
5. 把摄像头图像和状态数据整合成一个只读监控程序。
6. 再决定是否安装/编译 ROS2 工作流。
7. 最后才进入运动控制实验。

## 10. 禁止当前直接运行的示例

在只读状态没有成功前，不要运行：

- `unitree_sdk2_python/example/go2/high_level/go2_sport_client.py`
- `unitree_sdk2_python/example/go2/low_level/go2_stand_example.py`
- `unitree_sdk2_python/example/go2/high_level/go2_utlidar_switch.py`
- `unitree_sdk2_python/example/obstacles_avoid/obstacles_avoid_switch.py`
- `unitree_sdk2_python/example/obstacles_avoid/obstacles_avoid_move.py`
- `unitree_sdk2_python/example/vui_client/vui_client_example.py`
- `unitree_sdk2_python/example/motionSwitcher/motion_switcher_example.py`
- 任何会发布 `cmd`、`lowcmd`、速度、姿态、运动模式、避障开关、灯光音量、雷达开关的 C++ 或 ROS2 示例。

## 11. 一句话总结

这台 Go2 可以开发状态读取、摄像头画面、遥控器输入、ROS2 话题、数据记录、高层运动、低层电机、避障、灯光音量、雷达开关等能力；其中最适合当前阶段的是只读状态和摄像头。但在当前电脑环境下，必须先解决 SDK/DDS 没有收到 Go2 回包的问题。通信恢复之前，只做网络和只读排查，不做任何控制机器狗的实验。
