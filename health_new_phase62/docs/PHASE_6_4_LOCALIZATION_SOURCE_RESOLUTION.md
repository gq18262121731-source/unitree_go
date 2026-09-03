# Phase 6.4：Localization Source Resolution

日期：2026-07-31

设备：Go2 X EDU，Hardware V2.0，Firmware V1.1.15

基线提交：`b51e2cd`

状态：**DECISION COMPLETE / ACTIVE SOURCE REMAINS NULL**

## 1. 当前定位问题总结

Phase 6.1 和 Phase 6.2 已证明真实只读链路能够稳定提供：

```text
robot_online
battery
DDS / ROS2 health
LiDAR
IMU
Odometry
```

这些数据只能回答机器人和传感器是否在线，不能回答机器人在某张地图中的
位置。

当前必须保持：

```json
{
  "provider": "unitree_localization",
  "available": false,
  "source": null,
  "map_id": null,
  "pose": null,
  "confidence": null,
  "timestamp": null,
  "reason": "NO_VALIDATED_LOCALIZATION_SOURCE"
}
```

禁止使用以下捷径：

```text
robot_online => localization
battery      => localization
odometry     => localization
/odom        => localization
```

`/odom` 是局部里程计。没有地图身份、坐标原点、重定位语义和质量证明时，
不能将其声明为 LocalizationProvider 的可用输出。

## 2. Go2 内部 SLAM 二次只读审计

### 2.1 当前环境复核

本阶段开始时：

| 项目 | 结果 |
| --- | --- |
| Git 分支 | `feature/health-real-readonly-integration-v1` |
| 基线提交 | `b51e2cd` |
| Windows → Go2 `192.168.123.161` | PASS |
| Ubuntu VM | 原状态为 poweroff |
| Windows ROS2 / CycloneDDS Python | 未安装 |

VM 可以启动且 `192.168.123.223` 可达，但来宾 SSH 未开放。为了保持本阶段
只读边界，没有：

- 开启 SSH；
- 猜测来宾凭据；
- 安装新的 Windows DDS/ROS2 运行时；
- 修改 VM 或 Go2；
- 调用任何 SLAM API。

VM 在复核后已恢复为：

```text
VMState="poweroff"
```

### 2.2 两轮独立被动证据

同一 Go2 X EDU、同一硬件和固件已经有两轮独立、只订阅的正式证据。

第一轮：

```text
file:
E:\笨笨狗\phase544_uslam_probe.json

duration:
30.004198787006317 s

SHA-256:
AB6BBA0DA8396AFBF65D4C8D7DE81DFB74A679323CDD8D2B1FB88FB1A38B8085
```

第二轮：

```text
file:
E:\笨笨狗\phase5411_internal_slam_probe.json

duration:
60.00948037998751 s

SHA-256:
93738DAEC3D07209D8B44EAC1DA483D3CE99064A9CFF41053D28F03C8B0F7D88
```

第二轮安全记录：

```text
publishers_created:       0
request_topics_published: []
motion_control:           NOT_USED
slam_started:             false
tf_published:             false
```

本报告不把上述历史窗口伪装成本次新采样。它们是已校验、可追溯且足以进行
来源决策的两次独立观察；当前只补充了网络和环境可达性复核。

### 2.3 Topic 结果

| Topic | 类型 | 30 s samples | 60 s samples | Hz | frame / pose / quality |
| --- | --- | ---: | ---: | ---: | --- |
| `/lio_sam_ros2/mapping/odometry` | `nav_msgs/msg/Odometry` | 未纳入首轮 | 0 | 0 | 无法验证 |
| `/slam_info` | `std_msgs/msg/String` | 未纳入首轮 | 0 | 0 | 无法验证 |
| `/slam_key_info` | `std_msgs/msg/String` | 未纳入首轮 | 0 | 0 | 无法验证 |
| `/uslam/cloud_map` | `sensor_msgs/msg/PointCloud2` | 0 | 0 | 0 | 无法验证 |
| `/uslam/frontend/cloud_world_ds` | `sensor_msgs/msg/PointCloud2` | 0 | 0 | 0 | 无法验证 |
| `/uslam/frontend/odom` | `nav_msgs/msg/Odometry` | 0 | 0 | 0 | 无法验证 |
| `/uslam/localization/cloud_world` | `sensor_msgs/msg/PointCloud2` | 0 | 0 | 0 | 无法验证 |
| `/uslam/localization/odom` | `nav_msgs/msg/Odometry` | 0 | 0 | 0 | 无法验证 |
| `/uslam/map_file_pub` | `sensor_msgs/msg/PointCloud2` | 0 | 0 | 0 | 无法验证 |
| `/uslam/navigation/global_path` | `sensor_msgs/msg/PointCloud2` | 0 | 0 | 0 | 无法验证 |
| `/uslam/server_log` | `std_msgs/msg/String` | 0 | 0 | 0 | 无法验证 |
| `/api/slam_operate/response` | `unitree_api/msg/Response` | 未纳入首轮 | 0 | 0 | 无响应 |

由于样本为零，无法获得：

```text
frame_id
child_frame_id
timestamp
pose
map identity
confidence
covariance
定位连续性
重启后坐标原点
```

Graph 中出现 bare-DDS endpoint 只证明接口或占位存在，不证明定位服务已经
运行，也不证明可以公开调用。

两轮审计结论一致：

```text
USLAM interface footprint         PRESENT
default localization samples     0
default map samples              0
validated localization source    NONE
```

本阶段没有调用：

```text
/api/slam_operate/*
/uslam/client_command
/utlidar/mapping_cmd
/utlidar/switch
```

## 3. Point-LIO 当前阻塞分析

Point-LIO 继续保持 HOLD。本阶段没有重新运行或调参。

### 3.1 已排除

此前 A/B 已排除：

- ROS2 Bridge 修改 payload；
- ROS1 bag 转换污染；
- Point-LIO 二进制完全不可运行；
- 普通 header timestamp 回拨；
- 单纯 ROS1/ROS2 移植差异。

官方 L1 示例包能够运行并生成合理尺度轨迹与 PCD，而同一 Go2 数据在
ROS2 和官方 ROS1 Point-LIO 中均发散。

### 3.2 当前硬阻塞

`/utlidar/imu.linear_acceleration` 未通过原始比力语义 Gate：

```text
水平静止模长          9.8098 m/s²
低头保持模长         10.4495 m/s²
抬头保持模长         10.3675 m/s²
左侧降低保持模长     12.7412 m/s²
右侧降低保持模长     15.5575 m/s²
```

同时，Z 分量在不同静止姿态下持续保持约 `9.8065 m/s²`，X 分量随姿态
出现近似 `g·tan(pitch)` 的变化。该字段不是已经验证的原始 IMU 比力，
不能直接送入 Point-LIO 传播模型。

其他观察项：

- Go2 L1 点云约 15.4 Hz，官方示例约 9.9 Hz；
- Go2 单帧逐点时间约 62.2 ms，官方示例约 97.3 ms；
- Go2 `ring` 恒为 1；
- 部分帧有小幅逐点时间逆序；
- Point-LIO 内部 LiDAR↔IMU 外参消费方向已经审计，但缺少合法同源原始
  IMU 仍是首要阻塞。

### 3.3 Point-LIO 重新准入的前置条件

必须先得到完整同源链路：

```text
official raw L1 cloud
official raw L1 IMU
shared timestamp
documented LiDAR↔IMU extrinsic
```

并重新通过：

1. 多姿态静止比力模长 Gate；
2. gyro 轴向和符号 Gate；
3. cloud/IMU 时间同步 Gate；
4. 离线固定 rosbag 轨迹与地图 Gate。

在此之前：

```text
Point-LIO source eligibility = false
```

## 4. 候选方案比较

| 候选 | 传感器匹配 | 当前输出 | 可解释性 | 主要风险 | 当前准入 |
| --- | --- | --- | --- | --- | --- |
| Go2 内部 USLAM | 最可能使用集成 L1、内部 IMU和标定 | 接口存在，默认 0 samples | 黑盒，frame/地图/质量未知 | 未知启动协议和副作用 | **NO** |
| Point-LIO | 算法方向与 L1 匹配 | Go2 数据运行发散 | 源码可审计 | 原始 IMU语义不合法 | **NO** |
| FAST-LIO 类 | 理论适配 PointCloud2+IMU | 未建立 | 源码可审计 | 同样依赖原始 IMU、时间和外参 | **NO** |
| Cartographer 3D | 支持3D点云 | 未建立 | 较成熟 | 需要可信 IMU、tracking frame和TF | **NO** |
| `robot_localization` | 可融合 odom/IMU | 未建立 | 成熟 | 只能形成局部状态估计，不是地图定位 | **NOT A LOCALIZATION SOURCE** |
| AMCL / 2D定位 | 标准地图定位 | 未建立 | 成熟 | 需要地图和合格 LaserScan；2D Gate失败 | **NO** |
| 3D地图匹配 / ICP | 可使用 `cloud_base` | 未建立 | 可设计 | 需要正式地图、重定位和质量模型 | **FUTURE RESEARCH** |
| 纯 `/odom` | 已有约149 Hz数据 | 可用 | 语义明确为里程计 | 漂移、无 map_id、无重定位 | **FORBIDDEN AS LOCALIZATION** |

## 5. 推荐 LocalizationProvider 来源

### 5.1 当前活动来源

当前唯一正确决策：

```text
active_source = null
localization.available = false
status = UNAVAILABLE
```

这不是“未做决定”，而是经过两轮零输出审计和 Point-LIO 输入语义审计后的
正式 fail-closed 决策。

### 5.2 未来来源优先级

未来调查优先级：

```text
P1  Go2内部USLAM（条件候选）
P2  官方同源raw L1 + LIO（条件候选）
P3  已有正式3D地图后的点云地图匹配
```

选择 Go2 内部 USLAM 作为**第一调查方向**，原因是它最可能已经掌握：

- 集成 L1 的原始数据；
- 内部 IMU；
- LiDAR↔IMU 和传感器↔机身标定；
- 固件统一时间基准。

但必须区分：

```text
preferred investigation source = go2_uslam
validated active source         = null
```

只有在 Unitree 官方确认接口语义，并且不调用未知命令即可获得稳定只读输出
时，USLAM 才能进入 `OBSERVING`。当前不能写成：

```text
source=go2_uslam
available=true
```

Point-LIO 是第二条件候选。只有获得官方同源 raw L1 cloud/IMU 链路并通过
输入语义 Gate 后才重新评估，禁止使用 LowState IMU 或其他 IMU 进行未经
标定的拼接。

## 6. LocalizationProvider 准入条件

### 6.1 状态机

```text
UNAVAILABLE
     |
     | 已配置可信source，收到只读样本
     v
OBSERVING
     |
     | 连续窗口内全部Gate通过
     v
READY
     |
     | stale / rollback / frame变化 / map不匹配 / 质量失败
     v
DEGRADED 或 UNAVAILABLE
```

只有 `READY` 可以设置：

```text
available=true
```

### 6.2 强制 Gate

候选 source 必须同时满足：

1. **有效 pose**
   - position 和 orientation 均为有限数值；
   - 四元数归一化且坐标方向已验证；
   - 不能使用默认零值假装有效。

2. **时间新鲜**
   - source timestamp 连续；
   - 无 backward jump；
   - `sample_age_ms` 在明确阈值内；
   - 禁止固定 offset 和 `now()` 重打传感器时间。

3. **frame 明确**
   - `frame_id` 和 `child_frame_id` 有可追溯定义；
   - 运行期间不可静默改变；
   - 不发布猜测 TF。

4. **地图身份明确**
   - map localization 必须有正式 `map_id`；
   - 地图版本、原点和重载语义必须稳定；
   - 纯 odom source 不能填充虚假 `map_id`。

5. **质量指标存在**
   - covariance、confidence 或有正式定义的替代指标；
   - 指标含义和阈值必须可解释；
   - 不允许固定写入 `confidence=1.0`。

6. **来源可信**
   - 官方文档、可审计源码或可复现实验至少满足一种；
   - 已证明输入、时间、frame 和生命周期；
   - 未调用未知 SLAM 或运动接口。

7. **稳定性**
   - 静止、平移、旋转和回到起点连续；
   - 断流与 provider 重启后 fail-closed；
   - 不因 `robot_online=true` 自动恢复定位。

### 6.3 推荐状态契约

```json
{
  "schema_version": "1.0",
  "provider": "unitree_localization",
  "real_motion_enabled": false,
  "available": false,
  "status": "UNAVAILABLE",
  "source": null,
  "preferred_investigation_source": "go2_uslam",
  "scope": null,
  "map_id": null,
  "frame_id": null,
  "child_frame_id": null,
  "pose": null,
  "covariance": null,
  "confidence": null,
  "timestamp": null,
  "received_at": null,
  "sample_age_ms": null,
  "timestamp_rollback_count": 0,
  "quality": {
    "fresh": false,
    "pose_valid": false,
    "frame_valid": false,
    "map_identity_valid": false,
    "quality_metric_valid": false,
    "source_semantic_valid": false
  },
  "reason": "NO_VALIDATED_LOCALIZATION_SOURCE"
}
```

`preferred_investigation_source` 是研发路线，不是运行能力，前端不得将它显示
为定位在线。

## 7. 下一阶段路线

Phase 6.4 完成后继续停止。

进入任何下一阶段前，需要一次新的明确授权。推荐先补充官方资料，而不是
直接进入 Map Provider：

1. 向 Unitree 官方确认 Go2 X EDU V1.1.15 的 USLAM：
   - 支持条件；
   - 是否需要 App 中的只读模式；
   - 输出 topic、frame、timestamp、map identity；
   - start/stop/load 的正式协议及副作用；
   - confidence/covariance 定义。
2. 如果官方提供安全、已知的启用条件，单独审批后执行新的 60–120 秒
   只读观察。
3. 如果 USLAM 不开放，转入官方同源 raw L1 cloud/IMU 获取可行性决策。
4. 只有一个 source 完成 `OBSERVING → READY`，才允许实现
   LocalizationProvider。

当前禁止进入：

```text
Phase 6.5 Map Provider
Phase 6.6 Navigation Provider
Nav2
地图加载
TF发布
运动控制
```

## 8. Phase 6.4 验收

```text
定位能力伪造                         NO
运动接口调用                         NO
Nav2启动                             NO
地图加载                             NO
TF发布                               NO
未知SLAM API调用                     NO
Mock系统修改                         NO
Telemetry语义修改                    NO
robot_online推导localization         NO
/odom直接作为localization            NO

两轮内部SLAM被动证据                 COMPLETE
Point-LIO阻塞分析                    COMPLETE
候选来源比较                         COMPLETE
当前活动source                       null
未来第一调查方向                     go2_uslam (CONDITIONAL)
Localization fail-closed             PASS
```

最终判定：

```text
Phase 6.4                       PASS
Localization Source Resolution DECIDED: NONE CURRENTLY VALIDATED
Localization HOLD              KEEP
Map Provider                   NOT ENTERED
Navigation Provider            NOT ENTERED
Motion                         NOT ENTERED
```
