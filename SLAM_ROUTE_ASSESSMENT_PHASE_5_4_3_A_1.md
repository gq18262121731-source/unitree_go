# Phase 5.4.3-A.1 — Go2 X + L1 SLAM 路线评估

日期：2026-07-26  
模式：只读架构评估，不安装、不编译、不运行 SLAM  
结论：**单层 LaserScan 路线降级；优先评估 L1 原生 LiDAR-inertial 路线；机器人充电完成前停止。**

## 1. 当前阶段状态

```text
Phase 5.1 网络/DDS                 PASS
Phase 5.2 传感器读取                PASS
Phase 5.3 DDS → ROS2 Bridge         PASS

Phase 5.4 TF                        HOLD
  - base_link → utlidar_lidar 缺少官方可追溯来源
  - cloud_base 稳定，但单层 2D SLAM 输入质量未通过

Phase 5.4.3-A 离线质量评估           COMPLETE
Phase 5.4.3-A.1 SLAM路线评估         COMPLETE
Phase 5.4.3-B 在线只读复验           WAITING FOR CHARGE

Phase 5.5 SLAM                       NOT ENTERED
Phase 5.6 Nav2                       NOT ENTERED
```

## 2. 已有证据

`cloud_base` 本身不是主要故障点：

- raw/base 时间戳一致；
- 每点 `time/ring/intensity` 保留；
- 固定变换在静止和人工运动状态下均稳定；
- 运动状态未发现 gross frame jump。

单层 `PointCloud2 → LaserScan` 最佳离线候选仍只有：

| 指标 | 结果 | Gate |
|---|---:|---|
| 平均有效束 | 53.11 | — |
| 220° FOV 占用率 | 24.14% | FAIL |
| 平均最长空洞 | 69.94° | FAIL |
| 静止帧 beam Jaccard | 6.96% | FAIL |
| 静止重合束距离变化中位数 | 8.89 cm | PASS |

这表明当前主要矛盾是 L1 非重复 3D 扫描向单帧二维平面的降维损失，而不是固定安装关系漂移。

## 3. 路线比较

| 路线 | 与 L1 匹配度 | 当前工程状态 | 判断 |
|---|---|---|---|
| SLAM Toolbox + 单层 LaserScan | 低 | 1/4 质量 Gate 通过 | 不作为主路线 |
| 多层/短窗口 LaserScan | 中 | 尚未设计时间约束融合 | 仅保留为 2D 兼容备选 |
| Cartographer 3D | 中高 | 支持 PointCloud2 + IMU，但 TF、时间和 ROS2 集成未验收 | 次级研究候选 |
| LIO-SAM | 中低 | 原版要求机械 LiDAR、ring/time、可靠高频 IMU 和精确外参 | 不作为 L1 首选 |
| FAST-LIO2 | 高 | 支持固态 LiDAR与逐点时间，但官方主线为 ROS1，需 L1/ROS2 适配 | 高价值候选 |
| Unitree `point_lio_unilidar` | 很高 | Unitree 官方针对 L1/L2；当前公开流程为 Ubuntu 20.04 + ROS Noetic | 首选离线算法基线 |
| Go2 固件 USLAM | 未知 | 仅发现 topic/type，语义、频率、地图格式、支持边界未验证 | 只读审计候选 |

## 4. 各路线判断

### 4.1 SLAM Toolbox

SLAM Toolbox 是 2D SLAM，输入是 `sensor_msgs/LaserScan`。Phase 5.4.3-A 已证明单帧高度切片的稠密度和时间稳定性不足，因此继续微调 `height/angle/range` 的收益预计有限。

除非后续引入：

- 有明确时间窗口的多帧累积；
- 多高度层融合；
- 或独立平面 LiDAR；

否则不把 SLAM Toolbox 作为 Go2 X + L1 主路线。

### 4.2 Cartographer 3D

Cartographer 3D 可以使用 PointCloud2，并要求 IMU 作为 3D SLAM 初始姿态信息。它在输入模型上比单层 LaserScan 更匹配 L1。

风险：

- 仍需要可信的 tracking frame / published frame / odom frame 关系；
- 需要验证 IMU 轴向、重力方向和 LiDAR-IMU 外参；
- 需要评估 VMware 下的 CPU 与时延；
- 工程维护和 ROS2 集成成本高于只读 PointCloud bridge。

因此保留为次级研究候选，不在充电期间安装或运行。

### 4.3 LIO-SAM

LIO-SAM 要求逐点相对时间和 ring，并用 IMU 做 deskew；当前 L1 数据确实具备 `time/ring`，IMU 频率也超过 200 Hz。

但官方说明原版主要支持机械 LiDAR，并要求可靠的 LiDAR-IMU 外参和 IMU 坐标对齐。L1 是非重复扫描固态 LiDAR，因此“PointCloud2 + IMU”并不自动意味着 LIO-SAM 就是最佳适配。

结论：不作为首选。

### 4.4 FAST-LIO2

FAST-LIO2 直接使用原始点，适合固态 LiDAR，强调逐点时间、LiDAR/IMU 同步和外参。这与当前 L1 的 `time` 字段和约 248 Hz IMU 在算法需求上较匹配。

阻塞：

- 官方主线以 ROS1/catkin 为主；
- L1 消息格式、时间单位和扫描模式需适配；
- 仍需明确 LiDAR-IMU 外参来源；
- ROS2 Humble 部署路径不能依赖未经审计的第三方 fork。

结论：高价值候选，但先做离线格式兼容审计。

### 4.5 Unitree `point_lio_unilidar`

Unitree 官方提供 `point_lio_unilidar`，明确适配 L1/L2 的 360°×90°非重复扫描，并说明适用于低速移动机器人。它可直接利用 L1 点云与内置 IMU，是当前最贴合硬件的公开参考实现。

限制：

- 官方测试环境是 Ubuntu 20.04 + ROS Noetic；
- 当前主环境是 Ubuntu 22.04 + ROS2 Humble；
- 不能直接把 ROS1 安装步骤混入已经验收的 ROS2 VM；
- 需要先用官方示例 rosbag 建立算法基线，再设计隔离的 ROS1 容器或 ROS2 适配层。

结论：**首选离线算法基线，不等于立即部署方案。**

### 4.6 Unitree 固件 USLAM

已经发现以下 topic/type：

```text
/uslam/cloud_map                     PointCloud2
/uslam/frontend/cloud_world_ds       PointCloud2
/uslam/frontend/odom                 Odometry
/uslam/localization/cloud_world      PointCloud2
/uslam/localization/odom             Odometry
/uslam/map_file_pub                  PointCloud2
/uslam/map_file_sub                  PointCloud2
/uslam/navigation/global_path        PointCloud2
```

当前只确认 topic 名称和类型，没有确认：

- 是否实际持续发布；
- frame_id 和时间戳；
- frontend/localization 的算法语义；
- 地图导出与重载契约；
- 固件版本兼容和官方支持边界；
- 是否依赖内部控制或专有状态。

因此 USLAM 只允许在 Phase 5.4.3-B 后进行只读接口审计，禁止发送 `/uslam/client_command`。

## 5. 推荐顺序

机器人充电完成后：

1. **Phase 5.4.3-B**：原生 `cloud_base` 与候选 LaserScan 在线 RViz 同屏，只读验证拖影、空洞、时间和连续性；
2. 同一窗口只读枚举 USLAM 的实际发布频率、frame 和 timestamp，不发送 command；
3. 录制只读 rosbag，覆盖 raw cloud、cloud_base、IMU、robot_odom；
4. 使用 Unitree 官方 `point_lio_unilidar` 示例 bag 建立离线基线；
5. 对项目 bag 做格式兼容性审计，再决定 Point-LIO/FAST-LIO2/Cartographer 3D；
6. 只有算法路线与 TF/时间 Gate 均通过后，才申请进入 Phase 5.5。

## 6. 当前决策

```text
主路线:
  Unitree point_lio_unilidar 作为 L1 离线算法基线

高价值备选:
  FAST-LIO2

次级研究:
  Cartographer 3D
  Unitree USLAM 只读接口审计

降级路线:
  单层 LaserScan + SLAM Toolbox

仍然禁止:
  发布 experimental extrinsic TF
  运行 SLAM/Nav2
  运动控制
  修改 Mock/health_new/go2-gateway 业务逻辑
```

## 7. 官方参考

- [Unitree `point_lio_unilidar`](https://github.com/unitreerobotics/point_lio_unilidar)
- [FAST-LIO 官方仓库](https://github.com/hku-mars/FAST_LIO)
- [LIO-SAM 官方仓库](https://github.com/TixiaoShan/LIO-SAM)
- [Cartographer ROS 文档](https://google-cartographer-ros.readthedocs.io/en/latest/)
- [SLAM Toolbox 官方仓库](https://github.com/SteveMacenski/slam_toolbox)

**停止条件：本阶段到此结束。等待机器人充电完成，不进入在线复验或 SLAM。**

