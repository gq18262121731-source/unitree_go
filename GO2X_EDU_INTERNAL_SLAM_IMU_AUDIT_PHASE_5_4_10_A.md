# Phase 5.4.10-A：Go2 X EDU 内部 IMU / SLAM 接口离线审计

审计日期：2026-07-29  
设备：Unitree Go2 X EDU，Hardware V2.0，Firmware V1.1.15  
审计状态：**COMPLETE — Phase 5.5 继续 HOLD**

## 1. 结论

Go2 X EDU 上的 L1 是机身集成链路。当前没有证据支持把它当作独立 UniLidar L1 USB 开发套件使用，因此：

- 不拆 Go2；
- 不拆 L1；
- 不寻找隐藏 USB；
- 不把 Windows 的 CH9102 串口设备透传给 Ubuntu；
- 不拼接 `/utlidar/cloud + /unilidar/imu`；
- 不继续在已判定不适配的输入上运行或调试 Point-LIO。

基于已保存的 DDS/ROS2 图谱和本地代码，当前固件公开给外部 ROS2 的独立 L1 IMU 仍只有：

```text
/utlidar/imu
```

没有发现第二个可作为 Point-LIO 原始 IMU 的 topic。`rt/lowstate` 中的机身 IMU 虽然物理表现合理，但与 L1 点云缺少同源时间、坐标和外参闭环，不能直接替代。

与此同时，保存的 ROS2 图谱证明 Go2 内部存在一组定位/建图接口：

```text
/api/slam_operate/request
/api/slam_operate/response
/lio_sam_ros2/mapping/odometry
/slam_info
/slam_key_info
/uslam/*
```

这些接口值得在下次开机后做**零发布、纯订阅观察**。但其请求协议、API ID、状态机和启动方式没有在本次审计到的 Unitree 官方公开仓库中找到，因此不得猜测命令或主动发布请求。

## 2. 审计边界

本阶段只使用：

- 已保存的 topic/DDS 图谱；
- 既有只读探针结果；
- 本地 Unitree 官方 SDK/消息定义；
- Unitree 官方公开文档和仓库。

本阶段未执行：

- 机器人开机；
- USB 透传；
- DDS/ROS2 发布；
- SLAM 启动；
- Point-LIO；
- TF 修改；
- Nav2；
- 任何运动控制。

## 3. 已保存图谱中的传感器接口

既有 DDS discovery 记录：

```text
Participants:       25
Publications:       130
Unique rt/* topics: 95
```

已发现的 L1 相关输出包括：

```text
rt/utlidar/cloud
rt/utlidar/cloud_base
rt/utlidar/cloud_deskewed
rt/utlidar/imu
rt/utlidar/lidar_state
rt/utlidar/robot_odom
rt/utlidar/robot_pose
rt/utlidar/grid_map
rt/utlidar/range_info
```

在保存的 topic 名称中按 `imu|inertial|sensor|lidar` 审计后：

- `/utlidar/imu` 是唯一独立的 L1 IMU topic；
- `/sensor/imu` 是 Phase 5.3 桥接输出，不是新的原始数据源；
- LowState/SportModeState 内含机身状态，但不是与 L1 配套的独立原始 IMU；
- 未发现 `/unilidar/imu`、`/imu_raw`、`/lidar/imu_raw` 等第二数据源。

因此，Phase 5.4.9 对 `/utlidar/imu` 的语义 FAIL 结论没有被新的离线证据推翻。

## 4. 内部 SLAM / 定位接口审计

### 4.1 保存图谱中存在的接口

```text
/api/slam_operate/request
/api/slam_operate/response
/lio_sam_ros2/mapping/odometry
/slam_info
/slam_key_info
/uslam/client_command
/uslam/cloud_map
/uslam/frontend/cloud_world_ds
/uslam/frontend/odom
/uslam/localization/cloud_world
/uslam/localization/odom
/uslam/map_file_pub
/uslam/map_file_sub
/uslam/navigation/global_path
/uslam/server_log
```

这说明固件镜像中至少包含内部定位/建图组件或接口占位，但 **topic 存在不等于服务可公开调用，也不等于当前正在运行**。

### 4.2 既有被动探针结果

此前 30.09 秒纯订阅观察中：

- `/utlidar/cloud_base`、`/utlidar/cloud_deskewed`、`/utlidar/robot_odom` 有数据；
- 所有被观察的 `/uslam/*` 输出均为 0 samples；
- `/uslam/client_command` 存在 reader；
- `cloud_map`、`map_file_pub`、`server_log` 可见 publisher endpoint，但未输出样本；
- `frontend`、`localization` 输出未观察到有效 writer/data。

结论：

```text
内部组件或接口存在：YES
默认可用的 USLAM 输出：NOT PROVEN
公开、可追溯的启动协议：NOT FOUND
允许猜测命令：NO
```

### 4.3 API 消息定义的能力边界

本地消息仅表明 `/api/slam_operate/*` 使用通用 Unitree API 包装：

```text
Request:
  RequestHeader header
  string parameter
  uint8[] binary

Response:
  ResponseHeader header
  string data
  int8[] binary
```

消息定义没有给出：

- SLAM 对应的 API ID；
- `parameter` 格式；
- 启动/停止状态机；
- 地图保存协议；
- 错误码语义。

因此不能从通用消息结构反推控制命令。

## 5. 官方公开资料交叉检查

审计到的 Unitree 官方公开资料显示：

- `unitree_ros2` 公开说明 ROS2 与 Unitree DDS 的连接，并演示 `/utlidar/cloud`；
- `unitree_sdk2` 与 `unitree_sdk2_python` 的公开代码中未找到上述 USLAM 请求协议或使用示例；
- `unilidar_sdk` 面向独立 L1 数据链路，可获取该传感器自身的点云和 IMU；
- `point_lio_unilidar` 依赖 L1 的配套点云与内置 IMU。

官方资料：

- [Unitree Developer 文档入口](https://support.unitree.com/home/en/developer/)
- [Unitree ROS2 官方仓库](https://github.com/unitreerobotics/unitree_ros2)
- [Unitree SDK2 官方仓库](https://github.com/unitreerobotics/unitree_sdk2)
- [UniLidar SDK 官方仓库](https://github.com/unitreerobotics/unilidar_sdk)
- [Point-LIO for UniLidar 官方仓库](https://github.com/unitreerobotics/point_lio_unilidar)

“未找到”仅表示本次审计覆盖的官方公开资料中未发现相应协议，不代表 Unitree 内部不存在私有或需授权的 EDU 接口。

## 6. 下次开机的零发布探针

已准备：

```text
E:\笨笨狗\phase5410a_tools\phase5410a_internal_slam_probe.py
```

SHA-256：

```text
663123932A79B943BE972C8D6EC332FF89B196F0F731F33CA3B13F2E9D66CF2C
```

静态校验：

```text
python -m py_compile: PASS
```

安全属性写入探针结果：

```text
publishers_created: 0
request_topics_published: []
motion_control: NOT_USED
slam_started: false
tf_published: false
```

探针只订阅以下输出：

```text
/api/slam_operate/response
/lio_sam_ros2/mapping/odometry
/slam_info
/slam_key_info
/uslam/cloud_map
/uslam/frontend/cloud_world_ds
/uslam/frontend/odom
/uslam/localization/cloud_world
/uslam/localization/odom
/uslam/map_file_pub
/uslam/navigation/global_path
/uslam/server_log
```

以下请求/控制 topic 只检查 graph endpoint，绝不发布：

```text
/api/slam_operate/request
/uslam/client_command
/utlidar/client_command
/utlidar/mapping_cmd
/utlidar/switch
```

当前机器人关机，Go2 Ethernet 路径不可用，因此脚本尚未部署到 Ubuntu VM，也没有把空文件/无输出误记为运行成功。下次连接恢复后，应先核对传输文件的大小和 SHA-256，再在已经正确 source ROS2 与 Unitree 消息环境的终端中运行：

```bash
python3 phase5410a_internal_slam_probe.py 60 \
  ~/go2_validation/phase5410a_internal_slam_probe.json
```

此命令仅运行 60 秒订阅探针。

## 7. 下次开机 Gate

### 情况 A：内部输出默认活跃

如果以下任一 topic 有样本：

```text
/lio_sam_ros2/mapping/odometry
/slam_info
/slam_key_info
/uslam/frontend/odom
/uslam/localization/odom
```

下一步仍只做只读表征：

- publisher 节点；
- message type；
- frame/child_frame；
- timestamp 连续性与回拨；
- Hz；
- 静止漂移；
- 是否伴随 map/cloud 输出。

不得因此直接进入 Nav2。

### 情况 B：所有内部输出仍沉默

如果 60 秒被动观察仍为 0 samples：

- 不猜 API ID；
- 不构造 `/api/slam_operate/request`；
- 不发布 `/uslam/client_command`；
- 不尝试社区来源的未验证命令。

后续应向 Unitree EDU 官方支持确认：

1. Go2 X EDU V2.0 / V1.1.15 是否公开支持内部 USLAM；
2. 对应授权、服务和启动协议；
3. `/lio_sam_ros2/mapping/odometry`、`/slam_info`、`/slam_key_info` 的正式接口定义；
4. 是否有与集成 L1 同源的原始 IMU 接口；
5. `/utlidar/imu` 的物理量语义、坐标定义和处理链。

## 8. Gate 判定

```text
Go2 X EDU 集成 L1 路线识别           PASS
禁止拆机/隐藏 USB 假设                PASS
保存 topic / DDS 证据审计             PASS
第二个原始 L1 IMU 数据源              NOT FOUND
/utlidar/imu Point-LIO 适配            FAIL（沿用 Phase 5.4.9）
内部 SLAM 接口存在性                  OBSERVED
内部 SLAM 默认有效输出                NOT PROVEN
官方公开启动协议                      NOT FOUND
零发布在线探针                        READY ON HOST
Phase 5.5                             HOLD
```

最终结论：

> 当前最安全、信息增益最高的下一步，是机器人下次开机后运行一次 60 秒零发布探针，重点观察内部 LIO/SLAM 输出；不是再次做大范围 topic 枚举，不是拆机找 USB，也不是继续调 Point-LIO。

