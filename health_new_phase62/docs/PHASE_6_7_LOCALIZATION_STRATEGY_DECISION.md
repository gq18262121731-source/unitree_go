# Phase 6.7 Localization Strategy Decision

```text
date         2026-07-31
base commit  995262ca85f85ff26f44f756979baab878544392
branch       docs/localization-strategy-decision-v1
scope        TECHNICAL DECISION ONLY
```

## 1. 决策摘要

本阶段完成路线决策，不实现定位。

```text
runtime active source       null
Localization state          UNAVAILABLE
validated allowlist         empty

selected target strategy:
self-managed 3D LIO + map relocalization

first estimator candidate:
Point-LIO using official same-source raw L1 cloud + raw L1 IMU
```

这三个结论必须同时成立：

1. 当前运行能力仍然是 `source=null`；
2. Go2 集成 `/uslam/*` 不再作为默认第一调查路线；
3. Point-LIO 只是所选自主管理路线的首个 LIO 候选，不因本决策自动解除 HOLD。

如果无法从当前集成 L1 获得官方同源 raw cloud/IMU，则现有硬件配置没有可进入
READY 的自主定位路线。届时必须选择硬件范围变更，或保持“真实 Telemetry +
Mock Navigation”，不得退回 `/odom` 或伪 pose。

## 2. 当前机器人能力状态

```text
Go2 network / SDK2 DDS        PASS
ROS2 Bridge                   PASS
real Telemetry                READY
Localization framework       PASS
Localization source adapter  NONE IMPLEMENTED
Localization                 UNAVAILABLE
Map                          NOT ENTERED
Navigation                   NOT ENTERED
Motion                       NOT ENTERED
```

Telemetry 已经可靠回答：

- 机器人是否在线；
- DDS/ROS2 是否正常；
- LiDAR、IMU、odometry 是否有数据。

它不能回答：

- 机器人位于哪张地图；
- 机器人在地图中的可信 pose；
- 定位质量是否满足导航；
- 重启后是否能够重定位。

## 3. Localization 缺口分析

LocalizationProvider READY 必须同时具备：

| Gate | 当前证据 |
| --- | --- |
| 来源明确 | 无活动来源 |
| pose 可信 | 无地图定位 pose |
| frame 明确 | 无已验证 localization frame |
| timestamp 有效 | 无定位样本可验证 |
| map identity | 无正式 map lifecycle |
| quality metric | 无 confidence/covariance 语义 |
| 重定位能力 | 未建立 |

`/odom` 和 `/utlidar/robot_odom` 仅提供局部里程计。即使频率稳定，也不包含
地图身份和跨会话重定位语义，不能填入 LocalizationProvider。

## 4. 方案一：Go2 内部定位

### 4.1 本机事实

两次独立、只订阅审计：

| 证据 | 时长 | 结果 | SHA-256 |
| --- | ---: | --- | --- |
| `phase544_uslam_probe.json` | 30.004 s | `/uslam/*` 目标输出全部 0 samples | `AB6BBA0DA8396AFBF65D4C8D7DE81DFB74A679323CDD8D2B1FB88FB1A38B8085` |
| `phase5411_internal_slam_probe.json` | 60.009 s | USLAM/SLAM 目标输出全部 0 samples | `93738DAEC3D07209D8B44EAC1DA483D3CE99064A9CFF41053D28F03C8B0F7D88` |

接口痕迹存在：

```text
/uslam/*
/slam_info
/slam_key_info
/lio_sam_ros2/mapping/odometry
/api/slam_operate/*
```

但是没有样本，因此无法验证 frame、timestamp、pose、map identity 和 quality。

### 4.2 官方公开能力边界

Unitree 官方文档中心确实公开了
[SLAM and Navigation Services Interface](https://support.unitree.com/home/en/developer/SLAM%20and%20Navigation_service)。
截至本次核查，官方目录显示该页更新于 `2026-05-13`。

该官方页面明确限定：

- EDU 机器人；
- expansion dock；
- Unitree 官方 MID-360 或 XT16；
- `unitree_slam` 模块和对应 lidar driver；
- 对外 topic 为 `rt/unitree/slam_mapping/*`、
  `rt/unitree/slam_relocation/*`、`rt/slam_info`、`rt/slam_key_info`；
- 服务为 `slam_operate`，包含 mapping、初始化、pose navigation 等已定义 API。

当前硬件已经确认的是 Go2 X EDU 集成 L1。没有证据证明当前设备具备该官方文档
要求的 MID-360/XT16 配置，也没有证据证明 `/uslam/*` 与上述公开
`unitree_slam` 服务是同一个、版本匹配的接口。

结论：

```text
public Unitree SLAM startup method   EXISTS
applicable to current integrated L1  NOT ESTABLISHED
current /uslam output                0 samples
Go2 internal source eligibility      false
```

不得把官方 MID-360/XT16 启动参数套用到集成 L1，也不得猜测
`/api/slam_operate/*` 或 `/uslam/client_command`。

### 4.3 是否继续投入

停止重复被动零输出审计。只有出现以下任一新证据才重新开放：

1. Unitree 对 Go2 X EDU 集成 L1 提供型号和固件匹配的 USLAM 文档；
2. 官方确认 `/uslam/*` 的安全启用方式、消息、frame、map 和质量语义；
3. 设备获得官方支持的 expansion dock + MID-360/XT16 配置；
4. 官方技术支持确认当前序列号具备相应服务授权和安装包。

在此之前：

```text
Go2 internal USLAM = NOT SELECTED FOR CURRENT HARDWARE
```

## 5. 方案二：Point-LIO / 自主管理 3D LIO

### 5.1 官方适配能力

Unitree 官方 [Unilidar SDK](https://github.com/unitreerobotics/unilidar_sdk)
说明 L1 SDK 可同时获取 LiDAR 测得的 point cloud 和内置 IMU 数据，并给出两者
坐标定义及固定几何关系。

Unitree 官方
[point_lio_unilidar](https://github.com/unitreerobotics/point_lio_unilidar)
明确使用 UniLidar SDK 的 L1 数据运行 Point-LIO，支持实时 L1 和官方 rosbag
示例。官方示例在本项目环境中已经成功产生合理尺度轨迹和 PCD。

这证明：

```text
Unitree L1 raw cloud + built-in raw IMU
                    |
                    v
             Point-LIO

是官方支持的传感器/算法组合。
```

它不能证明 Go2 固件发布的 `/utlidar/cloud + /utlidar/imu` 与独立
UniLidar SDK 原始输出语义相同。

### 5.2 当前硬阻塞

Phase 5.4.8 已验证 `/utlidar/imu.linear_acceleration`：

- 水平静止模长约 `9.81 m/s²`；
- 不同静止倾斜姿态模长增大到最高约 `15.56 m/s²`；
- Z 轴持续接近重力，水平分量随倾角产生未文档化变化；
- 不能作为已经验证的原始比力进入 Point-LIO。

报告：

```text
POINT_LIO_IMU_SEMANTIC_VALIDATION_PHASE_5_4_8.md
SHA-256:
3C79CCD93F336C80210D593D34755FF05DE15F2953C6A9A52F190AB78E390E52
```

已排除：

- ROS2 Bridge payload 污染；
- ROS1 bag 转换污染；
- Point-LIO 二进制完全不可用；
- 单纯 ROS1/ROS2 移植差异。

### 5.3 Point-LIO 的能力边界

Point-LIO 首先提供 LIO 状态估计和地图构建。它本身不能自动满足
LocalizationProvider 的全部跨会话要求：

- 必须建立正式 map identity；
- 必须定义重启后的初始 pose；
- 必须增加或验证 3D scan-to-map relocalization；
- 必须提供可解释的 covariance/confidence；
- 必须在地图不匹配和重定位失败时 fail closed。

所以最终路线不是简单写成：

```text
Point-LIO => Localization READY
```

而是：

```text
official same-source raw L1 cloud + raw L1 IMU
                    |
                    v
                Point-LIO
                    |
                    v
        versioned 3D map + relocalization
                    |
                    v
         LocalizationAdmissionController
                    |
              OBSERVING -> READY
```

### 5.4 继续投入价值

选择该路线作为当前硬件的目标路线，原因：

- 与集成 L1 的 3D 非重复扫描特性匹配；
- 算法和数据路径可审计；
- 不依赖未知 `/uslam` command；
- 可在现有 LocalizationProvider Gate 后接入；
- 失败时可以准确定位到数据、外参、地图或重定位层。

但第一任务不是继续调 Point-LIO，而是向 Unitree 获取**当前集成 L1 的官方同源
raw cloud/IMU 访问路径**。

## 6. 方案三：其他 ROS2 定位

Unitree 官方 [unitree_ros2](https://github.com/unitreerobotics/unitree_ros2)
证明当前 ROS2/DDS 通信基础有效，并公开 `/utlidar/cloud` 的读取和 RViz
显示方法，但没有为当前集成 L1 提供一个可直接声明为地图定位的 source。

当前评估：

| 方案 | 输入条件 | 当前缺口 | 结论 |
| --- | --- | --- | --- |
| FAST-LIO 类 | raw PointCloud2 + raw IMU + 外参 | 与 Point-LIO 相同的 IMU 硬阻塞 | 不构成替代 |
| Cartographer 3D | 3D点云、可信 IMU、tracking frame | IMU/TF/map lifecycle 未满足 | HOLD |
| AMCL/SLAM Toolbox | 合格 LaserScan + 2D map | L1→2D Gate 已失败 | 不推荐 |
| `robot_localization` | odom/IMU | 仅局部融合，无 map identity | 不是最终 Localization |
| 3D scan-to-map | 版本化 3D map + 初始 pose | 地图和重定位器尚未建立 | Point-LIO 后续组件 |
| 外部视觉/动捕 | 标定后的绝对 pose + quality | 当前没有已部署输入 | 应急条件候选 |

不存在一个只换 ROS2 package 就能绕过原始 IMU、TF、外参和地图身份问题的
方案。

## 7. 决策矩阵

| 方案 | 当前状态 | 技术适配 | 可维护性 | 完整重定位 | 决策 |
| --- | --- | --- | --- | --- | --- |
| Go2 内部 SLAM/USLAM | 两轮 0 samples；公开服务硬件边界不匹配 | 未验证 | 黑盒/版本相关 | 理论存在，当前不可证 | **不选作当前硬件默认路线** |
| Point-LIO + 3D relocalization | raw IMU 阻塞 | 与 L1 最匹配 | 源码和数据可审计 | 需后续建立 | **选定目标路线，继续 HOLD** |
| 其他 ROS2 定位 | 无已部署来源 | 条件不足 | 视方案而定 | 未建立 | **仅保留条件候选** |

## 8. 推荐路线

### 8.1 战略选择

```text
selected_strategy:
self_managed_l1_lio_and_3d_relocalization

first_estimator:
point_lio

required_source:
official_same_source_raw_l1_cloud_and_imu

runtime_active_source:
null
```

这是路线选择，不是能力声明。不得修改：

```text
Localization state       UNAVAILABLE
Localization source      null
validated allowlist      empty
```

### 8.2 路线退出条件

下一次只允许执行一个单独授权的“L1 同源原始数据可获得性 Gate”，向 Unitree
官方/供应商确认：

1. 集成 L1 是否能通过不拆机方式输出 UniLidar SDK 同源 raw cloud/IMU；
2. 是否有 Go2 X EDU V1.1.15 匹配的内部接口；
3. timestamp、坐标系、LiDAR↔IMU 外参是否与独立 L1 SDK一致；
4. 使用该接口是否影响机器人固件、保修或 App 功能。

结果分支：

```text
A. 官方同源 raw L1 可获得
   -> 重新执行 IMU物理语义 Gate
   -> 固定 rosbag Point-LIO Gate
   -> 3D map identity / relocalization 设计

B. 官方确认不可获得
   -> 停止当前集成L1自建LIO路线
   -> 评估官方支持的 expansion dock + MID-360/XT16
      或维持真实Telemetry + Mock Navigation
```

未经硬件范围变更授权，不采购、不安装外部 LiDAR 或 expansion dock。

## 9. 风险分析

| 风险 | 影响 | 控制措施 |
| --- | --- | --- |
| 将公开 MID-360/XT16 文档套用到 L1 | 错误启动、错误外参、设备风险 | 明确型号/固件/授权 Gate |
| 继续使用 `/utlidar/imu` | 运动后 LIO 发散 | 保持 Point-LIO HOLD |
| 把 LIO odom 当地图定位 | 重启后坐标失效 | 强制 map identity + relocalization |
| 固定 confidence/map_id | 伪造 READY | Phase 6.5 严格准入 |
| 无限重复 USLAM 零输出审计 | 消耗时间，无新增证据 | 只在前置条件变化后重开 |
| 为赶进度使用 `/odom` | 导航漂移且无法恢复 | 明确禁止 |
| 新增外部硬件 | 成本、集成时间、比赛变更 | 必须单独审批 |

## 10. 下一阶段建议

不进入 Map Provider 或 Navigation Provider。

建议下一项独立决策工作：

```text
Phase 6.7.1
Integrated L1 Same-Source Raw Data Availability Gate
```

它只做官方资料/供应商确认和接口可获得性审计，不运行 SLAM、不修改机器人、
不拆机、不发布 TF、不接运动。

在新的真实 source 完成 Phase 6.5：

```text
UNAVAILABLE -> OBSERVING -> READY
```

之前，Map、Navigation、Motion 全部保持未进入。

## 11. 事实来源

官方公开来源：

- [Unitree SLAM and Navigation Services Interface](https://support.unitree.com/home/en/developer/SLAM%20and%20Navigation_service)
- [Unitree Unilidar SDK for L1](https://github.com/unitreerobotics/unilidar_sdk)
- [Unitree point_lio_unilidar](https://github.com/unitreerobotics/point_lio_unilidar)
- [Unitree ROS2](https://github.com/unitreerobotics/unitree_ros2)

本地可复现证据：

- `phase544_uslam_probe.json`
- `phase5411_internal_slam_probe.json`
- `POINT_LIO_IMU_SEMANTIC_VALIDATION_PHASE_5_4_8.md`
- `PHASE_6_6_LOCALIZATION_SOURCE_ADAPTER.md`

未使用第三方教程或逆向命令作为决策依据。

## 12. 验收结论

```text
code modified                    0
Mock modified                    0
Telemetry contract modified     0
Localization Gate modified      0
fake pose/source created        0
SLAM/Nav2/Map started           0
TF published                    0
motion interface called         0

strategy decision               COMPLETE
target strategy                 Point-LIO + 3D relocalization
runtime source                  null
Localization HOLD              KEEP
```

Phase 6.7 完成后停止，不进入 Phase 6.8 Map Provider 或 Phase 6.9
Navigation。

## 13. 验证记录

```text
required document sections          PASS
official Unitree SLAM scope check   PASS
official catalog update             2026-05-13 19:43:54
changed files                       1 document
code files modified                 0
Phase 6.5 + Telemetry tests         13 passed
```

官方事实核对直接使用：

```text
https://robot-api.unitree.com/doc?space=developer&locale=en
https://doc-cdn.unitree.com/6/111/en/6_111_en
```

没有使用第三方镜像补充官方页面内容。
