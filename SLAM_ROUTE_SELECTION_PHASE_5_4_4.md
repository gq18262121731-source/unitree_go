# Phase 5.4.4 — Go2 X + L1 SLAM 路线选型验证

日期：2026-07-27  
模式：在线只读接口审计 + 本地证据分析 + 官方公开资料核对  
结论：**选择 L1 原生 LiDAR-inertial 路线作为主方向，以 Unitree `point_lio_unilidar` 作为首个离线算法基线；不启动 SLAM，Phase 5.4 HOLD 继续生效。**

## 1. 安全边界

本轮只执行：

- 30 秒 `/uslam/*` 与 `/utlidar/*` 只读订阅；
- ROS2/DDS graph 的 writer/reader 查询；
- 已有点云文件的字段、时间和坐标分析；
- 官方公开文档与官方仓库核对。

本轮没有：

- 向 `/uslam/client_command` 或 `/utlidar/*command` 发送消息；
- 启动 USLAM、SLAM Toolbox、Cartographer、Point-LIO、FAST-LIO 或 Nav2；
- 发布新 TF；
- 发布 `cmd_vel`、LowCmd 或任何运动控制；
- 修改现有 bridge、Mock、`health_new` 或 go2-gateway 业务逻辑。

只读探针明确创建了 0 个 publisher。

## 2. 当前阶段状态

```text
Phase 5.4 TF/坐标链路:
  HOLD
  原因: base_link -> utlidar_lidar 官方外参来源未确认

cloud_base:
  PASS WITH OBSERVATION

Phase 5.4.4 路线选择:
  L1-native LiDAR-inertial route SELECTED
  Unitree point_lio_unilidar OFFLINE BASELINE SELECTED

Phase 5.5 SLAM:
  NOT ENTERED / NOT AUTHORIZED
```

## 3. 在线 USLAM 只读审计

测试时长：30.09 秒。

### 3.1 参考链路正常

| Topic | 样本 | Hz | Frame |
|---|---:|---:|---|
| `/utlidar/cloud_base` | 462 | 15.398 | `base_link` |
| `/utlidar/cloud_deskewed` | 460 | 15.331 | `odom` |
| `/utlidar/robot_odom` | 4,478 | 149.246 | `odom -> base_link` |

三者时间戳回拨均为 0，说明本次 USLAM 静默不是网络、DDS 或 LiDAR 链路掉线造成的。

### 3.2 USLAM 输出全部静默

| Topic | 类型 | 30 秒样本 | 当前 writer |
|---|---|---:|---|
| `/uslam/cloud_map` | `PointCloud2` | 0 | 有，静默 |
| `/uslam/frontend/cloud_world_ds` | `PointCloud2` | 0 | 无活动 writer |
| `/uslam/frontend/odom` | `Odometry` | 0 | 无活动 writer |
| `/uslam/localization/cloud_world` | `PointCloud2` | 0 | 无活动 writer |
| `/uslam/localization/odom` | `Odometry` | 0 | 无活动 writer |
| `/uslam/map_file_pub` | `PointCloud2` | 0 | 有，静默 |
| `/uslam/map_file_sub` | `PointCloud2` | 0 | 输入 reader |
| `/uslam/navigation/global_path` | `PointCloud2` | 0 | 无活动 writer |
| `/uslam/server_log` | `String` | 0 | 有，静默 |

DDS graph 中存在 `/uslam/client_command` reader，说明固件中有命令入口；本轮没有订阅其数据内容，也没有向它发布。所有固件端点都显示为 `_CREATED_BY_BARE_DDS_APP_`，ROS2 graph 无法提供真实进程名或组件版本。

判定：

> USLAM 接口驻留，但默认没有运行 frontend、localization 或 map 输出。没有官方命令契约、状态机、frame 定义和地图格式前，不能通过试发字符串来“探测”启动方式。

## 4. 当前 L1 输入契约

### 4.1 原始点云

```text
topic: /utlidar/cloud
type: sensor_msgs/PointCloud2
frame: utlidar_lidar
rate: about 15.4 Hz
fields:
  x, y, z: float32
  intensity: float32
  ring: uint16
  time: float32
```

抽样帧：

- 原始点数：4,154；
- `time`：0 至 0.0628646 秒；
- 4,154 个点具有 4,154 个不同的逐点时间；
- `ring` 全帧恒为 1；
- intensity：40 至 249。

因此原始 L1 点云具备逐点相对时间，适合运动畸变处理；但 `ring=1` 不能直接满足依赖机械多线 ring 排列的算法。

### 4.2 `cloud_base`

```text
topic: /utlidar/cloud_base
type: sensor_msgs/PointCloud2
frame: base_link
rate: about 15.4 Hz
fields:
  x, y, z, intensity, ring, time
```

同一抽样帧：

- 点数：1,435；
- 逐点 `time` 与 raw 保持；
- 通过固件固定变换与过滤生成；
- 在线 raw/base 时间戳匹配率 100%；
- 运动固定变换残差 P95 为 0.432 μm。

它是可信的 base-frame 点云候选，但代表经过过滤后的产品，不等同于完整 raw scan。

### 4.3 IMU 与 odom

```text
/utlidar/imu
  frame: utlidar_imu
  about 248–251 Hz

/utlidar/robot_odom
  frame: odom
  child: base_link
  about 149 Hz
```

Unitree L1 官方公开的 LiDAR/内置 IMU 几何关系为：

```text
IMU origin in lidar frame:
[-0.007698, -0.014655, 0.00667] m
axes parallel
```

该关系只解决 L1 内部 `utlidar_lidar <-> utlidar_imu`，不解决 Go2 安装关系 `base_link <-> utlidar_lidar`。

## 5. 路线矩阵

| 路线 | 推荐输入 | 算法核心所需外参 | 当前可行性 | 判定 |
|---|---|---|---|---|
| Unitree Point-LIO L1 | raw cloud + L1 IMU | L1 内部 LiDAR/IMU 外参，官方已有 | 硬件最匹配；公开实现为 ROS1 Noetic | **主路线、离线基线** |
| FAST-LIO2/L1 适配 | raw cloud + L1 IMU | LiDAR/IMU 外参 | 字段和频率匹配；需 L1 与 ROS2 适配 | 高价值备选 |
| `cloud_base` + Cartographer 3D | cloud_base + IMU + 可选 odom | `base_link <-> IMU` / tracking frame | cloud 可用，但 IMU TF 仍未官方确认；VM 资源偏紧 | 次级候选 |
| `cloud_base` + LiDAR-only 3D matching | cloud_base + odom prior | 可不依赖 raw LiDAR frame | 会失去 raw 点与官方 L1 LIO 路线；deskew 责任不清 | 研究备选 |
| Unitree USLAM | 固件内部输入 | 固件内部 | 接口存在但默认静默；命令/输出契约未知 | 暂不选择 |
| SLAM Toolbox 2D | LaserScan | 标准 base/sensor TF | 单层 LaserScan 仅 1/4 Gate 通过 | 降级路线 |

## 6. 为什么主路线不是直接 `cloud_base + LIO`

`cloud_base` 已通过在线稳定性验证，但 LIO 同时需要 IMU。当前：

```text
cloud_base frame = base_link
L1 IMU frame     = utlidar_imu
```

若把 `cloud_base` 直接与 L1 IMU送入 LIO，仍需要可信的：

```text
base_link <-> utlidar_imu
```

它等价于：

```text
base_link <-> utlidar_lidar
          +
官方 lidar <-> imu
```

所以 `cloud_base` 并没有自动消除 LiDAR-inertial 算法的全部外参需求。

相反，官方 `point_lio_unilidar` 直接使用 raw L1 点云与 L1 内置 IMU，配置中已给出逐点时间单位、IMU 频率和 L1 内部外参。它可以先验证 LIO/建图算法本身；只有将算法位姿接入机器人 `base_link`、TF 和后续导航时，Go2 安装外参才重新成为硬 Gate。

## 7. 候选算法判断

### 7.1 Unitree `point_lio_unilidar`

Unitree 官方仓库明确针对 L1/L2 的 360°×90°非重复扫描，并定位于低速移动机器人。L1 配置使用：

```yaml
lid_topic: /unilidar/cloud
imu_topic: /unilidar/imu
timestamp_unit: 0       # seconds
imu_time_inte: 0.004    # 250 Hz
extrinsic_T: [0.007698, 0.014655, -0.00667]
extrinsic_R: identity
```

当前实机 raw 点云的 `time` 单位、IMU 频率和官方配置吻合。

限制：

- 官方测试环境为 Ubuntu 20.04 + ROS Noetic；
- 当前 VM 为 Ubuntu 22.04 + ROS2 Humble；
- 不能把 ROS1/catkin 依赖直接混入已验收的 ROS2 VM；
- 应先使用官方示例 bag 建立可重复基线，再在隔离环境验证项目 bag。

### 7.2 FAST-LIO2

FAST-LIO 强调 LiDAR/IMU 同步、逐点时间和准确外参。当前 L1 数据具备这些输入基础，但没有 Unitree L1 官方适配层，因此排在 Point-LIO 基线之后。

### 7.3 LIO-SAM

原版 LIO-SAM 依赖逐点 `time` 与多线 `ring`，官方说明当前主要支持机械 LiDAR。实机 L1 虽有 `ring` 字段，但抽样帧全为 `ring=1`，不能直接用于原版的多线矩阵组织。

结论：不作为 L1 首选。

### 7.4 Cartographer 3D

Cartographer 3D 接收 `PointCloud2`，但 3D 模式要求 IMU，并依赖正确 tracking frame/传感器 TF。`cloud_base` 本身通过 Gate，不代表 `base_link <-> utlidar_imu` 已解决。

此外当前 VM 只有 4 vCPU / 6 GiB RAM，应在离线数据上先测 CPU、内存和实时倍率。

### 7.5 Unitree USLAM

本轮证明 USLAM 默认静默，不能获得 frame、timestamp、地图分辨率、定位重载和生命周期语义。没有官方命令与状态机契约前，禁止向 `/uslam/client_command` 试发消息。

USLAM 保留为次级路线，只有 Unitree 官方资料或支持渠道确认以下内容后才重新评估：

1. 固件版本兼容性；
2. start/stop/save/load 命令 schema；
3. 输出 frame 与时间定义；
4. 地图格式及导出/重载契约；
5. 是否会触发机器人运动或修改持久状态。

## 8. 时间与 TF 要求

### 主路线算法 Gate

```text
raw cloud sensor stamp:
  preserve

per-point time:
  preserve, seconds

L1 IMU sensor stamp:
  preserve

fixed time offset:
  forbidden

LiDAR/IMU drift and nearest-time distribution:
  must be measured from rosbag
```

Point-LIO 算法基线只需要 L1 内部 LiDAR/IMU 关系；该关系有官方来源。

### 机器人集成 Gate

以下操作仍然需要解决 `base_link <-> utlidar_lidar`：

- 把 LIO 位姿声明为机器人 base pose；
- 发布标准 `map/odom/base_link/sensor` TF；
- 将地图与 Go2 控制、Nav2 或定位融合；
- 用 raw cloud 在 base frame 做可维护的可视化。

因此 Phase 5.4 HOLD 不能解除。

## 9. 推荐路线

```text
首选算法族:
  L1-native LiDAR-inertial odometry/mapping

首个算法基线:
  Unitree point_lio_unilidar

首选算法输入:
  /utlidar/cloud
  /utlidar/imu

验证与对照输入:
  /utlidar/cloud_base
  /utlidar/robot_odom
  /utlidar/lidar_state

禁止作为算法输入:
  /utlidar/cloud_deskewed
  原因: frame=odom，包含固件运动补偿/里程计链，存在循环依赖风险

USLAM:
  保留为次级只读审计路线

2D SLAM:
  降级
```

## 10. 下一道 Gate：Phase 5.4.5

下一步仍不运行在线 SLAM，建议进入：

```text
Phase 5.4.5 — L1 LIO 离线输入契约与数据集准备
```

任务：

1. 录制 10 分钟只读 rosbag：
   - `/utlidar/cloud`
   - `/utlidar/cloud_base`
   - `/utlidar/imu`
   - `/utlidar/robot_odom`
   - `/utlidar/lidar_state`
2. 覆盖静止、低速直线、旋转和停止；
3. 统计 timestamp、逐点 time、点数、intensity、`dirty_percentage` 与丢包窗口；
4. 在隔离的 ROS1 Noetic 环境先跑 Unitree 官方示例 bag；
5. 再离线适配项目 bag，测地图连续性、漂移、CPU、RAM 与处理倍率；
6. 不发布机器人 TF，不连接 Nav2，不向机器人发送控制。

通过离线数据和性能 Gate 后，才能申请 Phase 5.5 的受控 SLAM 试运行。

## 11. 官方参考

- [Unitree point_lio_unilidar](https://github.com/unitreerobotics/point_lio_unilidar)
- [Unitree Point-LIO L1 配置](https://github.com/unitreerobotics/point_lio_unilidar/blob/main/config/unilidar_l1.yaml)
- [Unitree L1 SDK 与坐标定义](https://github.com/unitreerobotics/unilidar_sdk)
- [Unitree ROS2](https://github.com/unitreerobotics/unitree_ros2)
- [Unitree SLAM and Navigation service 文档入口](https://support.unitree.com/home/en/developer/SLAM%20and%20Navigation_service)
- [FAST-LIO](https://github.com/hku-mars/FAST_LIO)
- [LIO-SAM](https://github.com/TixiaoShan/LIO-SAM)
- [Cartographer ROS documentation](https://google-cartographer-ros.readthedocs.io/en/latest/)

## 12. 证据文件

- `phase544_uslam_probe.json`
- `phase544_uslam_graph.json`
- `phase54_tools/phase544_uslam_probe.py`
- `phase54_tools/phase544_run_probe.sh`
- `phase543b_online_analysis.json`
- `CLOUD_BASE_ONLINE_VALIDATION_PHASE_5_4_3_B.md`
- `SLAM_ROUTE_ASSESSMENT_PHASE_5_4_3_A_1.md`

停止条件：

```text
Phase 5.4.4 COMPLETE
Phase 5.4 HOLD ACTIVE
Phase 5.5 NOT ENTERED
```
