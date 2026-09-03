# Phase 6.6 Localization Source Adapter

```text
base commit  4f8dd7e64a1b19624c4b041aab794794de53b48c
branch       feature/localization-source-adapter-v1
audit time   2026-07-31T12:04:41+08:00
```

## 1. 阶段结论

```text
Telemetry                READY
Localization framework   READY
Localization source      NONE VALIDATED
Localization state       UNAVAILABLE
Localization source      null
Map                      NOT ENTERED
Navigation               NOT ENTERED
Motion                   NOT ENTERED
```

Phase 6.6 完成的是定位来源 Adapter 的**评估与接入准备**，不是定位接入。
当前没有一个候选 source 满足 Phase 6.5 allowlist 的准入条件，因此：

- 不新增 `Go2InternalLocalizationProvider` 运行实现；
- 不把任何 source 加入 `validated_sources`；
- 不修改 Phase 6.5 核心；
- 不解除 Localization HOLD。

创建一个没有真实消息、frame、地图身份和质量语义支撑的 source-specific 类，
会让“调查方向”看起来像“已接入能力”。本阶段拒绝这种伪实现。

## 2. Go2 内部定位源审计

### 2.1 历史独立被动证据

两份证据均来自纯订阅探针。探针创建 publisher 数量为 0，没有发送
`/api/slam_operate/request` 或 `/uslam/client_command`。

| 证据 | 时长 | 结果 | SHA-256 |
| --- | ---: | --- | --- |
| `phase544_uslam_probe.json` | 30.004 s | 所有 USLAM 输出 0 samples | `AB6BBA0DA8396AFBF65D4C8D7DE81DFB74A679323CDD8D2B1FB88FB1A38B8085` |
| `phase5411_internal_slam_probe.json` | 60.009 s | 所有 USLAM/SLAM 输出 0 samples | `93738DAEC3D07209D8B44EAC1DA483D3CE99064A9CFF41053D28F03C8B0F7D88` |

第二次审计结果：

| Topic | Graph type | Samples | Hz |
| --- | --- | ---: | ---: |
| `/lio_sam_ros2/mapping/odometry` | `nav_msgs/msg/Odometry` | 0 | 0 |
| `/slam_info` | `std_msgs/msg/String` | 0 | 0 |
| `/slam_key_info` | `std_msgs/msg/String` | 0 | 0 |
| `/uslam/cloud_map` | `sensor_msgs/msg/PointCloud2` | 0 | 0 |
| `/uslam/frontend/cloud_world_ds` | `sensor_msgs/msg/PointCloud2` | 0 | 0 |
| `/uslam/frontend/odom` | `nav_msgs/msg/Odometry` | 0 | 0 |
| `/uslam/localization/cloud_world` | `sensor_msgs/msg/PointCloud2` | 0 | 0 |
| `/uslam/localization/odom` | `nav_msgs/msg/Odometry` | 0 | 0 |
| `/uslam/map_file_pub` | `sensor_msgs/msg/PointCloud2` | 0 | 0 |
| `/uslam/navigation/global_path` | `sensor_msgs/msg/PointCloud2` | 0 | 0 |
| `/uslam/server_log` | `std_msgs/msg/String` | 0 | 0 |

部分 topic 在 DDS graph 中存在 endpoint，但 endpoint 存在不等于消息存在。
零样本意味着无法验证：

- frame 和 child frame；
- source timestamp；
- pose 连续性；
- 地图身份；
- confidence/covariance；
- 重启和断流语义。

### 2.2 本次在线条件复核

```text
Go2 192.168.123.161          reachable
Ubuntu VM 192.168.123.223    reachable
VM SSH port 22               closed
unknown credentials          not guessed
SSH/service/config changes   none
new probe sample             not claimed
```

本次启动既有 Ubuntu VM 后确认网络可达，但来宾未开放 SSH。没有猜测凭据、
开启 SSH、安装服务或改动 Go2/VM。由于没有安全的来宾执行入口，本报告不把
历史结果包装成第三次实时采样。

## 3. 候选来源评估

| 候选 | 可观察输出 | 语义完整性 | 当前决定 |
| --- | --- | --- | --- |
| Go2 internal USLAM | 接口存在，连续两轮 0 samples | frame/map/quality 均未知 | `CONDITIONAL_CANDIDATE` |
| Point-LIO | 失败数据与官方对照证据存在 | `/utlidar/imu` 原始比力语义未通过 | `HOLD` |
| Other ROS2 localization | 无已部署来源 | 无 map identity、pose、quality | `NOT_AVAILABLE` |
| `/odom` / `robot_localization` | 局部状态估计可存在 | 不是地图定位 | `REJECT_AS_MAP_LOCALIZATION` |

当前决策：

```text
preferred_investigation_source = go2_uslam
validated_active_source        = null
validated_sources allowlist    = empty
```

Go2 internal USLAM 保留第一调查优先级，但不再无条件重复零输出审计。只有以下
前置条件发生变化后才重新采样：

1. Unitree 提供 Go2 X EDU V1.1.15 的正式 USLAM 接口和状态机说明；
2. 官方说明安全、已知的只读启用条件；
3. 已知操作使被动 topic 开始发布，但不要求猜测 command/API；
4. 可以在 Ubuntu VM 中通过已授权的执行入口运行探针。

## 4. Go2 Internal Adapter 设计

未来 Adapter 的职责只能是把**已验证的现成输出**转换成 Phase 6.5
`LocalizationCandidate`：

```text
validated Go2 USLAM message
            |
            v
Go2InternalLocalizationAdapter
  - verify message/topic contract
  - preserve source timestamp
  - map pose without axis guessing
  - attach official frame/map identity
  - map defined quality metric
            |
            v
LocalizationAdmissionController
            |
     OBSERVING -> READY
```

建议未来接口：

```python
class Go2InternalLocalizationAdapter:
    source = LocalizationSource.GO2_USLAM

    def to_candidate(
        self,
        message: ValidatedGo2LocalizationMessage,
        map_identity: ValidatedMapIdentity,
    ) -> LocalizationCandidate:
        ...
```

当前**不创建此类**，因为以下类型尚不存在且不得猜测：

- `ValidatedGo2LocalizationMessage`；
- `ValidatedMapIdentity`；
- 官方 confidence/covariance 映射；
- frame 和 child frame 的正式语义。

### 4.1 字段映射 Gate

| Candidate 字段 | 未来允许来源 | 当前 |
| --- | --- | --- |
| `source` | 固定 `go2_uslam`，仅在官方语义确认后 | 未准入 |
| `pose` | 官方定位输出 pose | 无样本 |
| `timestamp` | 原消息 header stamp | 无样本 |
| `frame` | 官方定义的地图定位 frame | 未知 |
| `map_id` | 正式地图生命周期提供的身份 | 未知 |
| `confidence` | 官方定义质量指标的可解释映射 | 未知 |

禁止 Adapter：

- 使用 receive time 替换 source timestamp；
- 把 `/odom` 当 map pose；
- 固定写入 `map_id="map"`；
- 固定写入 `confidence=1.0`；
- 重命名 frame 或发布补偿 TF；
- 启动/停止 SLAM；
- 调用 `/api/slam_operate/*`；
- 发布 `/uslam/client_command`；
- 静默切换到 Point-LIO 或其他来源。

## 5. Phase 6.5 准入关系

Phase 6.5 核心不需要修改。未来 source adapter 必须：

1. 独立完成消息和坐标语义验证；
2. 由组合根显式加入 `validated_sources` allowlist；
3. 先进入 `OBSERVING`；
4. 连续通过 pose、timestamp、frame、map identity、confidence Gate；
5. 任何 stale、rollback、frame/map/source 改变立即回到
   `UNAVAILABLE`。

在 allowlist 仍为空时，即使构造出格式正确的 synthetic candidate，也不能
成为 READY。

## 6. 路线决策

### 6.1 工程路线

```text
P1  等待/获取 Unitree 官方 USLAM 语义与安全启用条件
P2  若 USLAM 不开放，获取官方同源 raw L1 cloud + raw IMU
P3  评估具备正式地图身份的外部 3D localization
```

Point-LIO 不因 Phase 6.6 重新开放。只有 L1 同源原始 IMU 问题解决后才允许
重新验证。

### 6.2 进度压力下的安全产品路线

如果定位来源在演示冻结日前仍未通过准入，允许保持：

```text
真实 Go2 Telemetry     READY
真实 Localization      UNAVAILABLE
真实 Navigation        DISABLED
Mock Navigation        保持冻结演示能力
```

不得把 Mock pose 或 `/odom` 填入真实 LocalizationProvider。

## 7. 是否解除 Localization HOLD

```text
Go2 USLAM pose samples          0
Go2 USLAM frame                 unknown
Go2 USLAM map identity          unknown
Go2 USLAM quality               unknown
Point-LIO IMU semantics         FAIL/HOLD
Other validated source          none

Localization source             null
Localization state              UNAVAILABLE
Localization HOLD               KEEP
```

结论：**不解除 Localization HOLD。**

## 8. 安全与兼容性

```text
Phase 6.5 core modified         0
Telemetry code modified         0
Mock modified                   0
REST/WebSocket modified         0
TF published                    0
SLAM command/API called         0
Map/Nav2 entered                NO
Motion interface called         0
New dependency                  0
```

Phase 6.6 完成后停止，不进入 Phase 6.7 Map Provider 或 Phase 6.8
Navigation。
