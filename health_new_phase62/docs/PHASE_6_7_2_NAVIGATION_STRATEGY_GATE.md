# Phase 6.7.2：真实导航路线决策 Gate

决策日期：2026-07-31
基线提交：`631cad5d313740e64a8593bdffccd0687a88765f`
阶段范围：**PRODUCT/TECHNICAL DECISION ONLY**
阶段状态：**COMPLETE**

## 1. 决策摘要

当前真实导航发布结论：

```text
Telemetry                      READY
Localization                   UNAVAILABLE
Localization source            null
Integrated L1 raw pair access  FAIL
Point-LIO                      HOLD
Go2 internal SLAM              NOT VALIDATED
Map                            NOT ENTERED
Navigation                     NOT ENTERED
Motion                         NOT ENTERED

real autonomous navigation     NO-GO
```

本阶段确定三类路线：

### 当前推荐路线

```text
比赛/近期交付:

真实 Go2 Telemetry + 视频/语音/应急交互
                    +
冻结的 Mock Navigation 演示
```

它是当前唯一不伪造 Localization、无需变更硬件且能够保护比赛演示稳定性的路线。

### 条件调查路线

向 Unitree/供应商取得当前 Go2 X EDU、Hardware V2.0、Firmware V1.1.15 的正式
接口和授权说明。该调查可以进行，但不改变运行状态，也不能无限期阻塞比赛交付。

### 中长期真实导航路线

如果官方不能为当前集成 L1 提供合格定位接口或同源 raw 数据，则进入独立硬件
选型 Gate，选择有公开时间、IMU、外参和定位接口闭环的官方支持方案。

当前不授权采购、拆装或代码实现。

## 2. 决策原则

真实导航准入顺序不可跳过：

```text
qualified sensor/localization source
        |
        v
LocalizationProvider READY
        |
        v
Map identity + relocalization
        |
        v
Navigation Provider
        |
        v
Motion Safety Bridge
```

禁止使用以下替代关系：

```text
robot_online              != localization
/odom                     != map localization
point cloud available     != SLAM ready
map exists                != robot knows its pose
APP product capability    != open developer API
```

没有可靠数据时，Localization 必须保持 `UNAVAILABLE`。

## 3. 当前已完成能力

### 3.1 可以发布和演示的真实能力

```text
Go2 X EDU
    |
    v
DDS / ROS2
    |
    v
UnitreeReadonlyAdapter
    |
    v
health_new Telemetry
```

已验证：

- Go2 online；
- battery；
- DDS/ROS2 transport；
- LiDAR availability/freshness；
- IMU availability，同时保持 `semantic_valid=false`；
- odometry availability/freshness；
- 30 分钟只读稳定性；
- `real_motion_enabled=false`。

### 3.2 不可发布的能力

- 地图坐标系中的可信 pose；
- map identity；
- 重启后的 relocalization；
- localization confidence/covariance；
- Navigation；
- Motion。

Telemetry 的成功不能解除这些 HOLD。

## 4. 路线一：继续争取 Go2 原生定位能力

### 4.1 当前证据

本机已有接口痕迹：

```text
/uslam/*
/slam_info
/slam_key_info
/lio_sam_ros2/mapping/odometry
/api/slam_operate/*
```

两次独立纯订阅结果：

```text
30.004 s: 目标 USLAM 输出全部 0 samples
60.009 s: 目标 USLAM/SLAM 输出全部 0 samples
```

所以：

```text
interface traces present       YES
active localization samples    NO
validated pose/frame/time      NO
validated map identity         NO
validated quality metric       NO
eligible provider              NO
```

### 4.2 官方产品能力与开发接口的区别

Unitree 官方 Go2 APP 页面说明，L1 与专属 APP 可以进行 3D 点云建图，并用于自主
巡逻和自动充电。这是“产品内部可能具备定位/导航能力”的正向证据。

但该产品页面没有定义本项目接入所需的：

- 可订阅 localization pose topic；
- frame 和 child frame；
- timestamp 语义；
- map identifier/lifecycle；
- confidence/covariance；
- 安全启动与停止协议；
- 当前硬件/固件对应的授权状态。

因此：

```text
Go2 APP navigation capability      PRODUCT EVIDENCE
LocalizationProvider eligibility   NOT ESTABLISHED
```

官方公开的 SLAM/Navigation Service 文档可以作为另一条产品路线参考，但先前审计
显示其公开支持条件描述的是 EDU + expansion dock + 官方 MID-360/XT16 服务链路。
不能把该协议直接套用到当前集成 L1 或 `/uslam/*`。

### 4.3 缺失条件

必须从 Unitree/供应商获得：

1. 当前设备序列号是否具备 L1 建图/重定位/巡逻授权；
2. Go2 X EDU V2.0、Firmware V1.1.15 对应的正式服务名称和版本；
3. `/uslam/*` 是否属于公开支持接口；
4. 安全启动方式、API ID、参数、状态机和错误码；
5. pose、frame、timestamp、map identity、quality 的正式消息语义；
6. 是否允许第三方系统只读接入 Localization；
7. 是否需要 APP、扩展坞、额外算力模块或特定 LiDAR。

### 4.4 投入决策

该路线只允许进行一次**有明确问题清单和负责人截止时间的官方确认**。

没有新书面资料、授权信息或可复现实测输出时：

- 不再重复零样本被动审计；
- 不调用 `/api/slam_operate/*`；
- 不构造 `/uslam/client_command`；
- 不实现 `Go2InternalLocalizationProvider`。

路线状态：

```text
CONDITIONAL INVESTIGATION
NOT ACTIVE IMPLEMENTATION
```

## 5. 路线二：Point-LIO

### 5.1 当前阻塞

官方 `point_lio_unilidar` 的 L1 路线要求先运行 `unilidar_sdk`，使用 L1 自身点云
和内置 IMU。当前 Go2 集成链路只有：

```text
/utlidar/cloud     available
/utlidar/imu       available but raw-specific-force semantics FAIL
```

未获得：

```text
/unilidar/cloud
/unilidar/imu
or equivalent validated same-source raw pair
```

Phase 6.7.1 已冻结：

```text
INTEGRATED_L1_RAW_PAIR_ACCESS = FAIL
```

### 5.2 是否存在解决路径

只有以下路径可以重开：

1. Unitree 提供当前集成 L1 的正式同源 raw cloud/IMU 接口；
2. 当前设备获得无需拆机、可验证的独立 L1 数据端点；
3. 更换为官方明确支持 raw cloud/IMU 的传感器硬件。

取得数据后仍需重新通过：

- IMU 原始比力与角速度语义；
- 点时间单位和单调性；
- cloud/IMU 同一时间基准；
- LiDAR/IMU 外参；
- 离线 Point-LIO 初始化、运动轨迹和地图 Gate；
- map identity 与 relocalization Gate。

### 5.3 投入决策

当前集成 L1 上：

```text
参数调优价值        NONE UNTIL INPUT GATE CHANGES
重新运行价值        NONE UNTIL INPUT GATE CHANGES
继续移植价值        NONE UNTIL INPUT GATE CHANGES
```

Point-LIO 不是永久放弃，而是**硬件/接口条件变化前停止投入**。

路线状态：

```text
STOPPED ON CURRENT INTEGRATED-L1 INPUT
CONDITIONAL FUTURE CANDIDATE
```

## 6. 路线三：硬件或官方方案调整

本阶段只做架构比较，不采购。

| 方案 | 数据/定位开放性 | 优点 | 主要风险 | 当前状态 |
|---|---|---|---|---|
| 官方扩展坞 + 官方支持 LiDAR/SLAM 服务 | 有公开服务文档，但需确认当前 Go2 X EDU 兼容性 | 厂商链路完整、接口和支持责任更明确 | 黑盒、授权/版本依赖、成本未知 | **首选真实导航硬件调查** |
| 独立 UniLidar L1 开发套件 + Point-LIO | 官方 SDK 提供同源 cloud/IMU | 算法和数据链路可控，可复用已完成 Point-LIO 经验 | 新安装外参、供电、网络、算力、地图重定位仍需建设 | 备选 |
| 其他开放 ROS2 3D LiDAR/IMU | 取决于具体型号 | 可选择更完整 ROS2/LIO 生态 | 重新选型、标定、驱动和安全验证工作量最大 | 备选 |
| 继续只用集成 L1 `/utlidar/*` | raw pair 不合格 | 无额外硬件 | 无合法 LIO 输入 | **停止** |

### 6.1 硬件准入条件

任何候选采购前必须书面确认：

1. 与 Go2 X EDU V2.0 的机械、电气和网络兼容；
2. Ubuntu 22.04/ROS2 或官方服务支持范围；
3. 同源点云/IMU或正式 Localization 输出；
4. timestamp、frame、extrinsic 和质量字段；
5. map 保存、加载、版本和重定位；
6. SDK/服务许可与售后支持；
7. 计算资源、带宽、重量和供电；
8. 是否需要改变现有冻结比赛系统；
9. 总成本和交付周期。

未完成准入矩阵前，不授权购买。

## 7. 路线四：比赛演示保底

### 7.1 推荐原因

当前项目已经具备：

- 真实 Go2 在线和传感器 Telemetry；
- health_new 真实只读状态展示；
- 冻结且可恢复的 Mock Navigation；
- 明确的 `real_motion_enabled=false` 安全边界。

因此可以诚实展示：

```text
真实硬件:
  online / battery / DDS / LiDAR / IMU / odometry / video / interaction

模拟链路:
  map / localization / navigation / task movement
```

前端和答辩材料必须明确标识数据来源，不能让观众误以为 Mock Navigation 是真实
Go2 自主导航。

### 7.2 当前发布决策

```text
real telemetry demo       GO
mock navigation demo      GO
real localization demo    NO-GO
real navigation demo      NO-GO
real motion demo          NO-GO
```

该路线不是技术失败，而是在硬件接口缺口未关闭时保护交付和安全。

## 8. 决策矩阵

评分为当前项目条件下的相对判断，不代表采购报价。

| 路线 | 当前证据 | 可立即交付 | 真实导航潜力 | 工程风险 | 决策 |
|---|---|---:|---:|---:|---|
| Go2 集成 L1 原生定位 | APP 有产品能力证据，但无合格开发输出 | 否 | 条件性 | 高：接口/授权黑盒 | **限时官方确认** |
| 当前 `/utlidar/*` + Point-LIO | raw IMU Gate FAIL | 否 | 当前无 | 极高 | **停止投入** |
| 官方扩展坞 + 支持 LiDAR/SLAM | 有公开官方服务方向，兼容性待确认 | 否 | 高 | 中：厂商依赖 | **首选硬件调查** |
| 独立 L1 + Point-LIO | 官方 raw pair + 算法链路成立 | 否 | 高 | 中高：集成/重定位 | 备选 |
| 其他开放 ROS2 定位硬件 | 尚未选型 | 否 | 条件性 | 高：重新集成 | 备选 |
| 真实 Telemetry + Mock Navigation | 当前系统已具备 | **是** | 不提供真实导航 | 低 | **当前推荐发布路线** |

## 9. 最终推荐、备选与停止路线

### 推荐路线

```text
当前比赛/演示:
真实 Telemetry + Mock Navigation

并行非代码动作:
向 Unitree/供应商提交正式接口确认清单
```

### 真实导航首选后续路线

如果官方确认当前集成 L1 有合格公开接口：

```text
重新进入 Localization Source Gate
        |
        v
只读样本准入
        |
        v
LocalizationProvider Adapter
```

如果官方明确不开放，或在项目负责人设定的截止时间内没有可验证答复：

```text
进入 Hardware Selection Gate
        |
        v
优先评估官方扩展坞 + 官方支持 LiDAR/SLAM
        |
        +--> 独立 L1 + Point-LIO 作为备选
```

任何硬件变化都必须从传感器/定位源 Gate 重新开始，不能直接进入 Map 或 Navigation。

### 停止路线

立即停止：

- 在当前 `/utlidar/imu` 上继续 Point-LIO 调参；
- `/utlidar/cloud + rt/lowstate IMU` 拼接；
- 用 `/odom`、`robot_pose` 或 cloud frame 伪造地图定位；
- 重复观察无新证据的零输出 `/uslam/*`；
- 猜测 `/api/slam_operate/*` 或 `/uslam/client_command`；
- 在 Localization READY 前实现 Map、Nav2 或 Motion。

## 10. 决策触发器

| 新证据/事件 | 允许的下一步 |
|---|---|
| 官方提供当前集成 L1 raw pair 文档 | 重开 Phase 6.7.1，只读实测 |
| 官方提供可订阅 Localization 服务 | 重开 Localization Source Adapter Gate |
| 官方确认 APP 能力不开放给开发者 | 关闭原生接口路线，进入硬件选型 |
| 官方确认扩展坞/指定 LiDAR 与当前设备兼容 | 进入采购前 Hardware Selection Gate |
| 比赛临近但无新定位证据 | 冻结真实 Telemetry + Mock Navigation |
| 只有营销描述、topic 名称或 `/odom` | 不改变 HOLD |

## 11. 风险分析

### 11.1 产品风险

- 把 APP 功能误写为开放 SDK 能力；
- 对外承诺尚未获得的真实导航；
- 硬件采购后才发现型号、固件或授权不兼容。

### 11.2 技术风险

- raw IMU/time/extrinsic 不完整导致 LIO 发散；
- 只有 odometry，没有跨会话 map localization；
- 黑盒定位没有质量指标，无法 fail closed；
- 新传感器增加计算、带宽、供电和标定负担。

### 11.3 演示风险

- Mock 与真实数据来源未明确标识；
- 为追求真实导航破坏冻结比赛系统；
- 未经过运动安全 Gate 就启用真实控制。

控制措施：

- 保留独立分支和冻结标签；
- 所有新定位源先只读准入；
- UI/答辩明确标记 Real Telemetry 与 Mock Navigation；
- 硬件采购、运动控制和 API 调用分别授权。

## 12. 状态冻结

```json
{
  "telemetry": "READY",
  "localization": {
    "state": "UNAVAILABLE",
    "available": false,
    "source": null
  },
  "integrated_l1_raw_pair": "FAIL",
  "point_lio": "HOLD",
  "go2_internal_slam": "NOT_VALIDATED",
  "map": "NOT_ENTERED",
  "navigation": "NOT_ENTERED",
  "motion": "NOT_ENTERED",
  "release_strategy": {
    "real_telemetry": true,
    "mock_navigation": true,
    "real_navigation": false
  }
}
```

## 13. 可追溯依据

### 本项目决策报告

```text
docs/PHASE_6_7_LOCALIZATION_STRATEGY_DECISION.md
SHA-256:
90477D1FE06DD02CBDD561C2DB9F4387CCF988AE42F211F3073022BCB79A9AD1

docs/PHASE_6_7_1_L1_RAW_DATA_ACCESS_GATE.md
SHA-256:
142136287718671B3047B1F2BB7AAE57E1732D83E6B861211A29AD02355439FC
```

### Unitree 官方资料

- [Go2 APP：3D LiDAR Mapping](https://www.unitree.com/app/go2/)
- [Unitree SLAM and Navigation Services Interface](https://support.unitree.com/home/en/developer/SLAM%20and%20Navigation_service)
- [Unitree UniLidar SDK](https://github.com/unitreerobotics/unilidar_sdk)
- [Unitree Point-LIO for UniLidar](https://github.com/unitreerobotics/point_lio_unilidar)
- [Unitree ROS2](https://github.com/unitreerobotics/unitree_ros2)

## 14. 验收

| 验收项 | 结果 |
|---|---|
| 推荐路线明确 | PASS |
| 备选路线明确 | PASS |
| 停止路线明确 | PASS |
| 未伪造 Localization | PASS |
| 未实现 Map/Navigation/Motion | PASS |
| 未修改 Mock/Telemetry/准入规则 | PASS |
| 未调用 SLAM/运动 API | PASS |
| 未授权硬件采购 | PASS |
| Localization 保持 UNAVAILABLE | PASS |
| source 保持 null | PASS |

本阶段完成后停止。不要进入 Map Provider、Navigation Provider、Nav2 或 Motion，
等待项目负责人对“比赛发布路线”和“真实导航硬件调查路线”的下一步确认。
