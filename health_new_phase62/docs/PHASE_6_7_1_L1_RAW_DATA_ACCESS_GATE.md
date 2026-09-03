# Phase 6.7.1：集成 L1 同源原始数据可获得性 Gate

审计日期：2026-07-31
基线提交：`0ce08d6d157e01c799ed1fdc064ff8045901cbc0`
设备：Unitree Go2 X EDU，Hardware V2.0，Firmware V1.1.15
阶段状态：**COMPLETE**
raw 数据 Gate：**FAIL — CURRENT DOCUMENTED/VALIDATED INTERFACES DO NOT PROVIDE A QUALIFIED PAIR**
Localization：**UNAVAILABLE**
Localization source：`null`

## 1. 执行摘要

本阶段只回答一个问题：

> 当前 Go2 X EDU 集成 L1，能否通过公开且已验证的接口获得 Point-LIO 所需的同源
> raw cloud、raw IMU、传感器时间和外参闭环？

结论：

```text
/utlidar/cloud                       可读取
/utlidar/imu                         可读取，但原始比力语义 FAIL
/unilidar/cloud                      当前 Go2 ROS2/DDS 图谱中不存在
/unilidar/imu                        当前 Go2 ROS2/DDS 图谱中不存在
等价 raw L1 IMU topic                未发现
Go2 body IMU                         不是可直接替换的 L1 内置 IMU
集成 L1 USB/serial 直连              未暴露、未验证
集成 L1 raw UDP endpoint             未公开、未验证

同源 raw cloud + raw IMU 配对         FAIL
Point-LIO 输入准入                    HOLD
Localization                         UNAVAILABLE
source                               null
```

该结论不是“L1 硬件内部没有 raw 数据”，也不是“Unitree 永远不会开放接口”。准确含义是：

> 截至本次审计，在当前设备、固件、外部网络和 Unitree 公开 SDK/文档范围内，没有找到
> 可追溯且已经验证的集成 L1 同源 raw 数据访问路径。

因此不能运行 Point-LIO，也不能进入 Map、Navigation 或 Motion。

## 2. 审计边界

本阶段使用：

- 已保存的 Go2 DDS/ROS2 discovery 和只读采样结果；
- Phase 5.4.8、5.4.9、5.4.10 的受控实验与硬件预检；
- 本地保存的 Unitree 官方仓库快照；
- Unitree 当前公开官方仓库和开发文档；
- 本次 Go2 Ethernet 只读可达性复核。

本阶段未执行：

- Point-LIO、SLAM、Nav2；
- Map Provider、Navigation Provider；
- DDS/ROS2/TF 发布；
- `/api/slam_operate/*` 或 `/uslam/client_command` 调用；
- `cmd_vel`、`SportClient`、`Move()`、`LowCmd`；
- L1 工作模式、IP、端口、LED 或固件配置命令；
- 拆机、拔内部线、寻找隐藏 USB；
- Ubuntu VM 启动或环境修改。

本次状态复核：

```text
Go2 192.168.123.161: 4/4 ICMP reachable
Ubuntu VM:            poweroff
```

网络可达不等于 raw L1 接口存在。本阶段没有把历史 DDS 审计冒充成本次新采样。

## 3. 当前硬件条件

### 3.1 集成形态

用户现场确认：

- 设备为 Go2 X EDU；
- L1 为机身集成版本；
- 外部未看到独立 L1 USB 数据接口；
- 本阶段禁止拆机。

因此当前可验证的外部链路是：

```text
Go2 X EDU integrated L1
        |
        | internal processing / transport
        v
Go2 controller
        |
        | DDS over Ethernet
        v
/utlidar/*
```

不能把它直接等同于：

```text
standalone Unitree L1
        |
        | USB serial or dedicated UDP
        v
unilidar_sdk
        |
        +-- /unilidar/cloud
        +-- /unilidar/imu
```

### 3.2 主机访问条件

Phase 5.4.10 已确认：

- Windows 未枚举出可确认属于 L1 的新 USB 串口；
- COM3/COM4 是现有 T10 手环 CH9102 链路，禁止占用；
- Ubuntu VM 中不存在 L1 对应 `/dev/ttyUSB*` 或 `/dev/ttyACM*`；
- 官方 ROS2 UniLidar 节点已编译通过，但因无合格设备而未启动。

所以独立 L1 的 USB/serial 路径在当前硬件形态下不可达。

## 4. 官方 SDK 与支持范围审计

审计日期为 2026-07-31。“未找到”只表示公开资料范围内没有发现，不代表 Unitree
内部不存在私有、授权或尚未发布的接口。

| 官方组件 | 公开能力 | 与当前集成 L1 的关系 | Gate 结论 |
|---|---|---|---|
| `unitree_sdk2` | Go2/B2/H 系列 DDS 通信与机器人服务 | 本地官方源码未发现集成 L1 raw cloud/IMU 获取接口或示例 | 不构成 raw 配对证据 |
| `unitree_ros2` | 通过 CycloneDDS 使用机器人 DDS；官方 README 展示 `/utlidar/cloud` | 没有公开声明 `/utlidar/imu` 是 Point-LIO raw specific force，也没有展示 `/unilidar/*` | 只证明 Go2 点云可读 |
| `unilidar_sdk` | 从独立 L1 硬件数据流解析同一传感器的 pointcloud 与 IMU；给出两坐标系关系 | 默认 ROS2 驱动使用 `/dev/ttyUSB0`；SDK 也有需明确 LiDAR IP/端口的 UDP 初始化 | SDK 能力成立，但当前集成链路未接通 |
| `point_lio_unilidar` | 官方 L1 Point-LIO 使用 `unilidar_sdk` 的配套 cloud 与内置 IMU | 官方运行步骤先启动 UniLidar 驱动，再启动 L1 mapping launch | 当前缺少其前置输入 |
| Go2 SLAM/Navigation Service | 当前官方文档描述 EDU + 扩展坞 + 官方 MID-360/XT16 的服务链路 | 没有证明 Go2 X EDU 集成 L1 raw 接入 | 不是当前 raw L1 Gate 的替代证据 |

### 4.1 独立 L1 SDK 能力

Unitree 官方 `unilidar_sdk` 明确说明：

- 可从 L1 获得 pointcloud 与 LiDAR 内置 IMU；
- LiDAR 与 IMU 轴方向平行；
- IMU 原点在 LiDAR frame 中为
  `[-0.007698, -0.014655, 0.00667] m`；
- ROS/ROS2 默认输出 `unilidar/cloud` 与 `unilidar/imu`。

本地官方快照进一步表明：

```text
serial port: /dev/ttyUSB0
baud:        2000000
cloud:       unilidar/cloud
imu:         unilidar/imu
```

SDK 也包含 `initializeUDP(...)`。但示例需要一个独立 LiDAR IP、主机 IP和 UDP
端口，并会向传感器发送 STANDBY/NORMAL 工作模式命令。当前没有官方资料给出
Go2 X EDU 集成 L1 的可访问 IP、端口或“只读旁路”协议，因此本阶段没有尝试。

Go2 控制器地址 `192.168.123.161` 不能被推断为 L1 raw UDP endpoint。

### 4.2 官方 Point-LIO 输入前提

官方 `point_lio_unilidar` 的 L1 流程是：

```text
unilidar_sdk
  /unilidar/cloud
  /unilidar/imu
        |
        v
mapping_unilidar_l1.launch
```

它不是：

```text
/utlidar/cloud
        +
任意其他 IMU
```

因此当前不能用 Go2 body IMU 或未验证的 `/utlidar/imu` 替换官方同源 IMU。

## 5. ROS2/DDS 只读审计结果

保存的完整 DDS discovery：

```text
Participants:       25
Publications:       130
Unique rt/* topics: 95
```

发现的 L1 相关输出：

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

未发现：

```text
/unilidar/cloud
/unilidar/imu
/imu_raw
/lidar/imu_raw
其他有官方 raw L1 语义说明的 IMU topic
```

`/sensor/lidar`、`/sensor/imu` 是 Phase 5.3 对现有 `/utlidar/*` 的透传桥接，不是
新的传感器来源，不能改变原始语义。

## 6. 当前可获得数据源

| 数据 | Topic/来源 | 频率/字段 | 语义结论 | Point-LIO |
|---|---|---|---|---|
| L1 点云 | `/utlidar/cloud` | 约 15.4 Hz；`x,y,z,intensity,ring,time` | 数据可用；但配套 raw IMU 缺失 | 单独不足 |
| 集成链路 IMU | `/utlidar/imu` | 约 250 Hz；ROS `sensor_msgs/Imu` | 原始比力语义 FAIL | **拒绝** |
| L1 SDK 点云 | `/unilidar/cloud` | 官方驱动候选 | 当前设备未获得 | 未准入 |
| L1 SDK IMU | `/unilidar/imu` | 官方驱动候选 | 当前设备未获得 | 未准入 |
| Go2 body IMU | `rt/lowstate.imu_state` | 机身 IMU | 比力形态较合理，但不同传感器、时间和外参未闭环 | **拒绝直接拼接** |
| 固件处理点云 | `/utlidar/cloud_base` 等 | 已变换/处理 | 可用于只读观察，不等于 Point-LIO raw 配对 | 不替代 raw pair |

### 6.1 `/utlidar/imu` 的阻塞证据

Phase 5.4.9 同步实验结果：

```text
水平:
  /utlidar/imu |a| = 10.3926 m/s²

前低后高:
  /utlidar/imu |a| = 9.8205 m/s²

模长变化: -5.505%
az 变化:   -0.00199 m/s²
```

在约 `18.7°` 俯倾时，Z 分量几乎不随重力投影变化，不能按 Point-LIO 所需原始
specific force 使用。

相同实验中的 Go2 body IMU 具有较合理重力投影，但它不是 L1 内置 IMU，并缺少：

- 与 L1 逐点时间同源的传感器时间；
- 可追溯的 LiDAR 到 body IMU 六自由度外参；
- 同一硬件链路的同步保证。

所以不能通过更换 topic 名称完成修复。

## 7. 同源 raw 数据 Gate

准入要求：

1. cloud 和 IMU 来自同一 L1 或有官方声明的同步链路；
2. 点云含可解释、连续的逐点时间；
3. IMU 输出符合原始比力和原始角速度语义；
4. cloud 与 IMU 使用同一可追溯时间基准；
5. LiDAR/IMU frame 与外参方向有官方或实测可维护依据；
6. 只读采样可以同时稳定获得两路数据；
7. 不需要拆机、猜协议或改变传感器工作模式。

| Gate 项 | 结果 |
|---|---|
| raw cloud 可获得 | **PARTIAL**：`/utlidar/cloud` 可读，但尚未证明等价于官方 UniLidar raw stream |
| raw IMU 可获得 | **FAIL** |
| cloud/IMU 同源性 | **FAIL** |
| 共同传感器时间 | **NOT PROVEN** |
| 官方 LiDAR/IMU 外参适用于当前 pair | **NOT APPLICABLE：pair 未建立** |
| 非拆机访问路径 | **NOT FOUND** |
| 官方集成 L1 USB/UDP 接口说明 | **NOT FOUND** |
| Point-LIO 输入闭环 | **FAIL** |

最终 Gate：

```text
PHASE_6_7_1_AUDIT                 COMPLETE
INTEGRATED_L1_RAW_PAIR_ACCESS     FAIL
POINT_LIO                         HOLD
LOCALIZATION                      UNAVAILABLE
SOURCE                            null
```

## 8. Point-LIO 可行性

### 当前设备、当前公开接口

```text
NOT READY
```

阻塞不是 Point-LIO 参数，而是输入资格。不得重新运行或调参。

### 条件可行

只有取得以下任一类新证据，才允许重开 Gate：

1. Unitree 对当前 Go2 X EDU 硬件/固件给出正式的集成 L1 raw cloud + raw IMU
   接口、topic/transport、时间语义和外参说明；
2. 官方支持提供无需拆机的访问方法，并能只读同时采集两路原始数据；
3. 更换为官方明确支持原始 cloud/IMU 输出的 LiDAR 硬件链路。

取得接口说明仍不等于 Gate 自动通过，还必须完成静止、倾斜、旋转、时间连续性、
点时间和同源性实测。

## 9. 下一步路线

### 路线 A：向 Unitree/供应商取得书面接口确认（首选，非本阶段执行）

需要明确询问：

1. Go2 X EDU V2.0、Firmware V1.1.15 的集成 L1 是否公开 raw IMU；
2. 是否有不拆机的 USB、serial、UDP 或 DDS raw 接口；
3. raw cloud 与 raw IMU 的正式 topic/type/transport；
4. 两路数据的时间基准、点时间单位和同步保证；
5. LiDAR 到内置 IMU 的外参方向及适用硬件版本；
6. 是否官方支持 `point_lio_unilidar` 直接用于该集成版本；
7. 若需要授权、扩展坞或专用转接硬件，其正式型号与支持范围。

没有书面或可复现实测证据前，保持 HOLD。

### 路线 B：官方不开放或无法验证时

停止在当前集成 L1 上继续投入 Point-LIO，重新选择：

- 官方明确支持的扩展坞 + LiDAR/定位方案；
- 独立、可直接访问原始 cloud/IMU 的 L1 开发套件；
- 其他具备完整时间、IMU、外参闭环的 ROS2 3D LiDAR；
- 若项目不扩展硬件：保留真实 Telemetry，Navigation 继续使用 Mock。

硬件采购、拆装、固件修改均需独立授权，不属于本阶段。

## 10. 状态冻结

```json
{
  "telemetry": "READY",
  "localization": {
    "state": "UNAVAILABLE",
    "available": false,
    "source": null,
    "reason": "NO_VALIDATED_SAME_SOURCE_RAW_L1_CLOUD_IMU_PAIR"
  },
  "map": "NOT_ENTERED",
  "navigation": "NOT_ENTERED",
  "motion": "NOT_ENTERED"
}
```

禁止从以下数据推导 Localization：

- `robot_online=true`；
- `/odom`；
- `/utlidar/robot_pose`；
- `/utlidar/cloud_base`；
- topic 存在但无合格语义；
- 独立 L1 SDK 的能力说明。

## 11. 可追溯证据

### 本地实验报告

```text
E:\笨笨狗\IMU_SOURCE_COMPARISON_PHASE_5_4_9.md
SHA-256:
7D20C201EBA183032D8522FF87829DD961B3AC17D4FCF4743EEE80B88AE48C8C

E:\笨笨狗\GO2X_EDU_INTERNAL_SLAM_IMU_AUDIT_PHASE_5_4_10_A.md
SHA-256:
AEFC3F5252E9205149C0D8A3220438B17A9B203197B132DF86750F177093EA19

E:\笨笨狗\UNILIDAR_RAW_SOURCE_VALIDATION_PHASE_5_4_10.md
SHA-256:
DBC3C5594C391196BC55CACF68E41A8FC079637A8199A3F45BE2F3EEBF1011AE

E:\笨笨狗\phase547_sources\unilidar_sdk_official.zip
SHA-256:
E24AF75DECF2C96598ACBDB1497841F078ECBFB8CB9CF3399360245A6BD63C91
```

### Unitree 官方公开资料

- [Unitree UniLidar SDK](https://github.com/unitreerobotics/unilidar_sdk)
- [Unitree Point-LIO for UniLidar](https://github.com/unitreerobotics/point_lio_unilidar)
- [Unitree ROS2](https://github.com/unitreerobotics/unitree_ros2)
- [Unitree SDK2](https://github.com/unitreerobotics/unitree_sdk2)
- [Unitree SLAM and Navigation Services Interface](https://support.unitree.com/home/en/developer/SLAM%20and%20Navigation_service)

## 12. 验收

| 验收项 | 结果 |
|---|---|
| 官方 SDK/接口审计 | PASS |
| ROS2/DDS 既有完整图谱审计 | PASS |
| 硬件访问条件确认 | PASS |
| 未把独立 L1 能力误写为集成 L1 能力 | PASS |
| 未伪造 `/unilidar/*` | PASS |
| 未运行 Point-LIO/SLAM/Nav2 | PASS |
| 未发布 TF/DDS/ROS2 | PASS |
| 未调用运动或未知 SLAM API | PASS |
| Localization 保持 UNAVAILABLE | PASS |
| source 保持 null | PASS |
| Map/Navigation/Motion 未进入 | PASS |

本阶段完成后停止，不进入 Phase 6.8 Map Provider 或 Phase 6.9 Navigation。
