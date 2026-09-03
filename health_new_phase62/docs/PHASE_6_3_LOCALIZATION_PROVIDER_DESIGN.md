# Phase 6.3：Localization Provider 设计与 Go2 内部定位能力只读评估

日期：2026-07-31

设备：Go2 X EDU，Hardware V2.0，Firmware V1.1.15

基线提交：`e69e40f`

状态：**DESIGN PASS / LOCALIZATION HOLD**

## 1. 当前定位能力状态

当前系统已经能够可信回答：

```text
机器人是否在线
传感器是否在线
DDS / ROS2 链路是否健康
LiDAR、IMU、Odometry 是否有新鲜数据
```

当前系统不能可信回答：

```text
机器人在某张已知地图中的位置
当前定位置信度
定位是否满足后续导航条件
```

因此 Phase 6.3 的冻结状态为：

```json
{
  "provider": "unitree_localization",
  "real_motion_enabled": false,
  "available": false,
  "status": "UNAVAILABLE",
  "source": null,
  "map_id": null,
  "pose": null,
  "confidence": null,
  "timestamp": null,
  "reason": "NO_VALIDATED_LOCALIZATION_SOURCE"
}
```

`/odom` 可用不改变该结论。它是局部里程计观测，不是带地图身份和质量
证明的定位结果。

## 2. Go2 内部 SLAM 接口审计结果

### 2.1 本阶段复核

2026-07-31 复核：

| 项目 | 结果 |
| --- | --- |
| Windows → Go2 `192.168.123.161` | 可达 |
| Ubuntu VM `Ubuntu-22.04.5-ROS2` | 已启动、`192.168.123.223` 可达 |
| Ubuntu SSH | 未开放 |
| Windows ROS2 / CycloneDDS Python | 未安装 |

本阶段没有为了重复实验而开启 SSH、安装新运行时、修改 VM、修改 Go2 或
调用固件 API。复核使用同一设备、硬件和固件在 2026-07-29 已完成的正式
60 秒只读证据：

```text
E:\笨笨狗\phase5411_internal_slam_probe.json
duration:
60.00948037998751 s

SHA-256:
93738DAEC3D07209D8B44EAC1DA483D3CE99064A9CFF41053D28F03C8B0F7D88
```

该探针只创建 subscriptions，记录为：

```text
publishers_created:       0
request_topics_published: []
motion_control:           NOT_USED
slam_started:             false
tf_published:             false
```

### 2.2 Topic 结果

| Topic | ROS2 类型 | Samples | Hz | frame / timestamp / pose |
| --- | --- | ---: | ---: | --- |
| `/lio_sam_ros2/mapping/odometry` | `nav_msgs/msg/Odometry` | 0 | 0 | 无样本，无法验证 |
| `/slam_info` | `std_msgs/msg/String` | 0 | 0 | 无样本 |
| `/slam_key_info` | `std_msgs/msg/String` | 0 | 0 | 无样本 |
| `/uslam/cloud_map` | `sensor_msgs/msg/PointCloud2` | 0 | 0 | 无样本，无法验证 |
| `/uslam/frontend/cloud_world_ds` | `sensor_msgs/msg/PointCloud2` | 0 | 0 | 无样本，无法验证 |
| `/uslam/frontend/odom` | `nav_msgs/msg/Odometry` | 0 | 0 | 无样本，无法验证 |
| `/uslam/localization/cloud_world` | `sensor_msgs/msg/PointCloud2` | 0 | 0 | 无样本，无法验证 |
| `/uslam/localization/odom` | `nav_msgs/msg/Odometry` | 0 | 0 | 无样本，无法验证 |
| `/uslam/map_file_pub` | `sensor_msgs/msg/PointCloud2` | 0 | 0 | 无样本，无法验证 |
| `/uslam/navigation/global_path` | `sensor_msgs/msg/PointCloud2` | 0 | 0 | 无样本，无法验证 |
| `/uslam/server_log` | `std_msgs/msg/String` | 0 | 0 | 无样本 |
| `/api/slam_operate/response` | `unitree_api/msg/Response` | 0 | 0 | 无响应 |

DDS graph 中：

- `/uslam/cloud_map`、`/uslam/map_file_pub` 和 `/uslam/server_log` 可见
  bare-DDS endpoint，但 60 秒内没有样本；
- `/uslam/frontend/odom`、`/uslam/localization/odom`、
  `/lio_sam_ros2/mapping/odometry` 没有可用 publisher；
- `/uslam/client_command` 和 `/api/slam_operate/request` 存在接口痕迹，
  但协议、生命周期和副作用未知；
- graph endpoint 的存在不能证明定位服务正在运行，也不能证明接口公开
  可用。

由于没有样本，以下项目全部为 **NOT PROVEN**：

```text
frame_id
child_frame_id
sensor timestamp
timestamp rollback
pose语义
map identity
confidence / covariance
定位连续性
重启后的坐标原点
```

### 2.3 审计结论

```text
内部接口存在                 YES
默认内部定位输出             NO
可用定位 source             NO
允许调用未知启动接口         NO
```

本阶段没有调用：

```text
/api/slam_operate/*
/uslam/client_command
/utlidar/mapping_cmd
/utlidar/switch
```

## 3. 候选定位来源比较

| 候选 | 当前证据 | 关键缺口 | Phase 6.3 判定 |
| --- | --- | --- | --- |
| Go2 内部 USLAM | Topic/type 和部分 bare-DDS endpoint 存在 | 默认无输出；启动、地图、frame、时间、置信度契约未知 | **HOLD，产品形态最匹配但不可接入** |
| Point-LIO | 官方示例可运行；Go2 数据输入和时间链可读取 | `/utlidar/imu` 不是已验证的原始比力；Go2 包在 ROS1/ROS2 实现均发散 | **HOLD，不重新运行或调参** |
| `robot_localization` EKF/UKF | 可融合 odom/IMU | 只能形成局部状态估计；当前 IMU 还有语义 HOLD；不能生成地图定位 | **不是当前 Localization source** |
| AMCL / 2D ROS2 定位 | 标准 ROS2 方案 | 需要已验证地图和 LaserScan；单层 LaserScan 质量 Gate 失败 | **不进入，且 Phase 6.4 尚未授权** |
| 3D 点云地图匹配 / ICP | `cloud_base` 已通过在线稳定性检查 | 需要可信地图、重定位策略、质量指标和坐标链 | **研究候选，当前不可用** |
| 视觉定位 | 当前系统有摄像头能力 | 无相机标定、地图和定位质量证据 | **未评估，不伪造** |

本阶段不选择 A、B 或 C 中的任何一个作为活动 source。

## 4. LocalizationProvider 接口设计

### 4.1 设计原则

LocalizationProvider 只包装**已经运行且已经验证**的定位输出。它不得：

- 启动或停止 SLAM；
- 保存、加载或删除地图；
- 发布 TF；
- 发布控制 topic；
- 调用 Unitree SLAM command/API；
- 把 odometry 自动升级为 localization；
- 在 source 失败时静默切换到未经验证的来源。

建议只暴露：

```python
class LocalizationProvider(Protocol):
    provider_name: str
    real_motion_enabled: Literal[False]

    def snapshot(self) -> LocalizationState:
        ...
```

不应出现：

```text
start()
stop()
reset()
load_map()
save_map()
publish_tf()
move()
```

### 4.2 统一状态模型

```json
{
  "schema_version": "1.0",
  "provider": "unitree_localization",
  "real_motion_enabled": false,

  "available": false,
  "status": "UNAVAILABLE",
  "source": null,
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
    "frame_valid": false,
    "map_identity_valid": false,
    "pose_finite": false,
    "orientation_normalized": false,
    "quality_metric_valid": false
  },

  "reason": "NO_VALIDATED_LOCALIZATION_SOURCE"
}
```

字段约束：

- `status`：`UNAVAILABLE | OBSERVING | READY | STALE | DEGRADED`；
- `source`：`null | go2_uslam | point_lio | ros2_localization`；
- `scope`：`null | local_odometry | map_localization`；
- `pose` 必须包含 position 和单位四元数 orientation；
- `covariance` 若存在，必须是 36 个有限数值；
- `confidence` 若存在，范围为 `[0, 1]`，不得凭经验伪造；
- `map_id` 只能来自正式地图生命周期，不得使用固定字符串占位；
- 所有时间必须来自 source，禁止固定 offset 或 `now()` 重打传感器时间。

### 4.3 Fail-closed 状态机

```text
UNAVAILABLE
    |
    | source已配置且只读样本出现
    v
OBSERVING
    |
    | 时间、frame、pose、地图身份、质量 Gate 全部通过
    v
READY  <-------------------+
    |                      |
    | stale / rollback /   | 连续重新验证通过
    | frame变化 / 非有限值  |
    v                      |
STALE 或 DEGRADED ---------+
```

仅当 `status=READY` 时：

```text
available=true
```

以下任一情况必须立即：

```text
available=false
```

- source 无样本或断流；
- 时间回拨；
- frame 或 child frame 改变；
- map identity 缺失或改变；
- pose 出现 NaN/Inf；
- 四元数不合法；
- source 没有可解释的 covariance/confidence；
- provider 重启后坐标原点语义未知。

### 4.4 Source 准入 Gate

一个候选 source 进入 `READY` 前至少需要：

1. 官方或可追溯的消息与坐标语义；
2. 连续只读样本和稳定频率；
3. sensor timestamp 无回拨；
4. frame/child frame 固定且坐标方向已验证；
5. 地图身份或参考坐标原点可追溯；
6. 静止、平移、旋转和回到起点的定位连续性证据；
7. covariance、confidence 或替代质量指标有明确语义；
8. 断流、重启、地图不匹配时 fail-closed；
9. 不调用运动、导航或未知 SLAM API；
10. 独立回归证明不会改变 Phase 6.1/6.2 Telemetry。

## 5. 与 Readonly Telemetry 的边界

| 问题 | Readonly Telemetry | LocalizationProvider |
| --- | --- | --- |
| 机器人是否在线 | 是 | 不负责 |
| DDS / ROS2 是否健康 | 是 | 只记录自身 source 健康 |
| LiDAR / IMU / Odom 是否有数据 | 是 | 不负责 |
| `/utlidar/imu` 语义是否验证 | 是，保持 `false` | 不覆盖该结论 |
| 局部 odom 是否有数据 | 是 | 不等于定位 |
| 机器人位于哪张地图 | 不负责 | 是 |
| 地图坐标中的 pose | 不负责 | 是 |
| 定位置信度和新鲜度 | 不负责 | 是 |
| 导航是否可用 | 始终不由传感器在线推导 | 也不能单独授权导航 |

禁止推导：

```text
robot_online=true
        =>
localization.available=true
```

也禁止：

```text
odometry.available=true
        =>
localization.available=true
```

建议未来保持两个独立只读端点：

```text
GET /api/v1/robot/telemetry
GET /api/v1/robot/localization
```

Phase 6.3 只冻结第二个端点的设计，不实现或暴露它。

## 6. 是否解除定位 HOLD

```text
Go2 USLAM 默认定位输出           FAIL
Point-LIO 输入语义               FAIL
其他地图定位 source              NOT VALIDATED
可信 map_id                      NOT AVAILABLE
可信 map pose                    NOT AVAILABLE

localization.available           false
Localization HOLD                KEEP
```

结论：**不解除定位 HOLD。**

## 7. 下一阶段建议

本阶段完成后停止，不进入 Phase 6.4 或 Phase 6.5。

推荐的下一次工作仍是只读证据补齐，且需要单独授权：

1. 向 Unitree 官方确认 Go2 X EDU V1.1.15 的 USLAM 支持范围、输出
   frame、地图身份、状态机和只读启动条件；
2. 若官方能在不发送未知命令的情况下启用并解释输出，重新执行 60 秒被动
   观察；
3. 对出现的候选 odometry/localization topic 先执行时间、frame、pose、
   covariance 和断流 Gate；
4. 只有 source 达到 `READY`，才实现独立
   `LocalizationProvider`；实现仍不得连接导航或运动。

继续禁止：

```text
Phase 6.4 地图
Phase 6.5 导航
Nav2
未知 SLAM 服务
猜测 TF
/api/slam_operate/*
/uslam/client_command
运动控制
```

## 8. Phase 6.3 验收

```text
Mock 冻结基线修改                 NO
Phase 6.1/6.2 readonly 语义修改   NO
运动接口调用                      NO
Nav2 启动                         NO
SLAM 服务启动                     NO
猜测 TF 发布                      NO
未知 Unitree API 调用             NO
定位能力伪造                      NO
无可靠 source 时 fail-closed      YES

Phase 6.3-A 被动能力审计           COMPLETE / NO DEFAULT OUTPUT
Phase 6.3-B Provider 设计          PASS
Phase 6.3-C Telemetry 边界         PASS
Localization HOLD                 KEEP
```
