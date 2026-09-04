# Go2 真实硬件接入架构与安全边界

更新时间：2026-07-25  
文档状态：**ARCHITECTURE BASELINE / NO IMPLEMENTATION AUTHORIZATION**  
当前阶段：**Phase 5.2.3 WAITING_FOR_PHYSICAL_HOST**  
Phase 5.3：**BLOCKED / NOT STARTED**

## 1. 文档目的

本文固化 Mock 演示、真实传感器只读链路、未来 ROS2 Adapter 和未来运动能力
之间的边界，防止真实硬件实验污染已经冻结的比赛系统。

本文只描述架构和阶段闸门，不授权：

- 修改 Mock Provider、Mock API 或 Mock WebSocket；
- 将真实 DDS 数据接入 `health_new`；
- 实现 DDS → ROS2 Bridge；
- 创建 TF、SLAM 或 Nav2 节点；
- 创建 DDS Publisher；
- 调用任何运动接口。

## 2. 当前能力状态

```text
Phase 5.1 真实硬件基础通信
├── 网络连通             PASS
├── SDK2 DDS             PASS
└── 官方 Ethernet 链路   PASS

Phase 5.2 传感器只读
├── L1 LiDAR             PASS
├── IMU                  PASS
├── Odometry             PASS
└── Pose                 PASS

Phase 5.2.1 时间同步
├── WSL2 时间稳定性      FAIL
└── 固定 offset 补偿     REJECTED

Phase 5.2.2 ROS2 开发环境
├── Ubuntu 22.04 WSL2    PASS
├── ROS2 Humble          PASS
├── CycloneDDS           PASS_STATIC
└── 真实运行时钟         FAIL

Phase 5.2.3 物理 ROS2 主机
└── WAITING_FOR_PHYSICAL_HOST

Phase 5.3 DDS → ROS2 Bridge
└── BLOCKED
```

已确认数据链路和传感器本身可用。当前阻塞项是可提供连续、可信时间基准的
Ubuntu 22.04 物理运行环境。

## 3. 总体原则

1. Mock 保留，不删除、不替换、不降级。
2. 真实只读能力作为独立 Provider 新增，不修改 Mock 实现。
3. 真实读取不等于真实控制。
4. 传感器数据路径与运动命令路径必须物理和代码双重隔离。
5. ROS2 Adapter 只负责数据适配，不能拥有运动权限。
6. Navigation Provider 不能直接持有 Unitree SDK 运动客户端。
7. 真实运动必须在后续独立阶段、独立 Provider 和新的安全闸门下设计。
8. 默认配置始终保持比赛安全状态。

## 4. 目标分层

```text
health_new
    |
    | 现有稳定 Mock 合同
    v
Robot Gateway
    |
    +---------------- Mock 数据平面 ----------------+
    |                                               |
    |  MockNavigationProvider                       |
    |  provider=mock                                |
    |  real_motion_enabled=false                    |
    |                                               |
    +--------------- 真实只读数据平面 --------------+
                                                    |
                                           UnitreeReadonlyProvider
                                                    |
                                           Unitree DDS Readers
                                                    |
                                      LowState / SportModeState
                                      L1 / IMU / Odom / Pose
                                                    |
                                      Go2 官方 Ethernet SDK2 DDS

未来且当前冻结：

UnitreeReadonlyProvider
    |
    v
ROS2 Sensor Adapter
    |
    +-- PointCloud2
    +-- Imu
    +-- Odometry
    +-- Pose
    |
    X  不存在反向运动命令路径

更后续且当前禁止：

ROS2 Sensor Topics
    -> TF
    -> SLAM
    -> Nav2
    -> RealNavigationProvider
    -> Motion Safety Gate
    -> Unitree Motion Adapter
```

图中的未来层不代表已经实现，也不允许在 Phase 5.3 解冻前提前创建。

## 5. 各层职责

### 5.1 MockNavigationProvider

职责：

- 维持比赛演示的状态、路径、地图和导航模拟；
- 维持现有 HTTP API 与 WebSocket 合同；
- 在无真实硬件时提供完全可重复的演示数据。

不可变化：

- 默认 Provider 仍为 `mock`；
- `real_motion_enabled=false`；
- Mock 状态转换不能路由到真实硬件 Adapter；
- 真实硬件字段不能破坏 Mock 响应结构；
- 不因为新增真实能力而修改比赛页面行为。

现有文件：

```text
app/navigation/mock_provider.py
```

该文件在当前真实硬件阶段保持冻结。

### 5.2 UnitreeReadonlyProvider

这是目标架构中的真实数据入口。它只负责：

- 初始化指定物理 Ethernet 接口上的 SDK2 DDS Subscriber；
- 发现并读取真实状态和传感器 Topic；
- 将设备端样本归一为内部只读 Observation；
- 输出连接、样本频率、时间戳和数据质量状态；
- 在任何异常时 fail closed。

它不得：

- 实现 Navigation Provider 的运动方法；
- 创建 DDS Publisher 或 DataWriter；
- 初始化 `SportClient`；
- 调用 `move()`、velocity、locomotion API 或 `cmd_vel`；
- 接受或转发导航目标；
- 自动启动 ROS2、TF、SLAM 或 Nav2。

当前仓库已有 Phase 5.1 诊断类：

```text
app/providers/unitree/real_provider.py
class RealGo2Provider
```

该类是只读硬件探针，不是 Navigation Provider。未来如采用
`UnitreeReadonlyProvider` 名称，应通过新增和明确迁移完成，不将
`RealGo2Provider` 扩展为运动 Provider。

### 5.3 Unitree DDS Readers

建议保持按职责拆分：

```text
app/providers/unitree_readonly/
├── dds_reader.py
├── state_reader.py
├── lidar_reader.py
├── imu_reader.py
├── odometry_reader.py
└── readonly_provider.py
```

这是后续实现建议，不是当前创建目录或代码的授权。

Reader 只应暴露：

```text
connect_readonly()
discover_topics()
read_state()
read_lidar()
read_imu()
read_odometry()
get_data_quality()
close()
```

接口中不出现：

```text
move
stand
sit
velocity
cmd_vel
navigate
patrol
return_home
```

### 5.4 ROS2 Sensor Adapter

Phase 5.3 解冻后才允许设计。职责仅限：

- 将内部只读 Observation 转换为 ROS2 标准消息；
- 保留原始 sensor timestamp 和接收时间元数据；
- 对设备端 IDL 差异做兼容解析；
- 暴露数据质量和时间同步状态。

第一步只允许候选输出：

```text
PointCloud2
Imu
Odometry
PoseStamped
LidarState diagnostics
```

Phase 5.3 初始实现仍不包括：

- TF 广播；
- LaserScan 转换；
- 地图构建；
- Nav2；
- DDS/ROS2 控制方向的反向通路。

### 5.5 RealNavigationProvider

这是未来运动阶段的独立组件，当前不存在、禁止实现。

即使未来创建，也必须满足：

- 与 `UnitreeReadonlyProvider` 分离；
- 默认不注册；
- 必须经过单独的显式配置和硬件安全授权；
- 必须具备速度、区域、姿态、急停、watchdog 和人工确认闸门；
- 不能复用 Mock 状态转换作为真实运动授权；
- 不能因为收到 Nav2 goal 就直接调用 Unitree 运动接口。

## 6. 单向数据流约束

Phase 5.2 和未来 Phase 5.3 的合法方向只有：

```text
Go2 / L1
    |
    | SDK2 DDS Subscriber
    v
UnitreeReadonlyProvider
    |
    | immutable/read-only observations
    v
ROS2 Sensor Adapter
    |
    | sensor topics
    v
ROS2 consumers
```

必须不存在：

```text
ROS2 consumer
    -> UnitreeReadonlyProvider
    -> Go2 command topic
```

代码审查时，应将以下任何内容视为阻断项：

- Publisher、DataWriter、RPC client；
- `SportClient`；
- `move`、`cmd_vel`、velocity、locomotion；
- control topic 名称；
- 对运动 Adapter 的依赖注入；
- 从 Web/API/ROS2 goal 到 SDK2 的反向调用路径。

## 7. 配置矩阵

当前安全默认值：

```env
ROBOT_PROVIDER=mock
REAL_MOTION_ENABLED=false
GO2_CONTROL_ENABLED=false
```

真实只读诊断的候选配置：

```env
ROBOT_PROVIDER=unitree_readonly
REAL_MOTION_ENABLED=false
GO2_CONTROL_ENABLED=false
UNITREE_DOMAIN_ID=0
UNITREE_NETWORK_INTERFACE=<physical_ethernet_interface>
UNITREE_ROBOT_IP=192.168.123.161
```

当前 Phase 5.1 探针实际使用过 `ROBOT_PROVIDER=unitree_real`。在进入正式
Provider 实现前，应统一配置命名；命名调整不能改变“只读且无运动权限”的
语义。

配置验收矩阵：

| Provider | Motion flag | 允许状态 |
| --- | --- | --- |
| `mock` | `false` | PASS / 默认 |
| `unitree_readonly` | `false` | 仅物理机 Gate 全部通过后 |
| `unitree_readonly` | `true` | REJECT |
| 未知 Provider | 任意 | REJECT |
| 未来真实运动 Provider | 任意 | 当前阶段 REJECT |

## 8. 部署边界

| 环境 | 用途 | 禁止 |
| --- | --- | --- |
| Windows | `health_new`、Mock、前端、开发工具 | 真实 SLAM/Nav2 运行 |
| WSL Ubuntu 20.04 | SDK2 实验和历史诊断 | 真实时间敏感运行 |
| WSL Ubuntu 22.04 | ROS2 编译、消息、launch、开发 | LiDAR/IMU/TF/SLAM/Nav2 真实运行 |
| 物理 Ubuntu 22.04 | 通过 Gate 后的真实 ROS2 运行 | Gate 未通过时接入 Bridge/导航 |

真实 SDK2 DDS 链路固定使用 Go2 官方 Ethernet。WLAN/Go2 AP 可以继续用于
App 和视频，但不作为当前已验证的 SDK2 DDS 运行链路。

## 9. 阶段闸门

```text
Phase 5.2.3 Gate A
物理 Ubuntu 22.04 + NTP synchronized
        |
        v
Gate B
30 分钟：0 回拨、0 个 >20 ms 突变、span error <= 5 ms
        |
        v
Gate C
ROS2 Humble + rmw_cyclonedds_cpp 静态验收
        |
        v
Gate D
Go2 Ethernet 网络
        |
        v
Gate E
LowState / SportModeState 官方只读订阅
        |
        v
Gate F
L1 / IMU / Odom / Pose 只读复验
        |
        v
Gate G
跨设备时间偏差与漂移基线
        |
        v
Phase 5.2.3 PASS
        |
        v
等待人工明确授权 Phase 5.3
```

Gate 通过不自动推进下一 Phase。Phase 5.3 必须由人工明确解冻。

ROS2 CLI 验收使用：

```bash
printenv ROS_DISTRO
ros2 --help
dpkg-query -W ros-humble-ros-base
```

不依赖 `ros2 --version`，因为 ROS2 CLI 不保证提供该参数。

## 10. Mock 合同保护

后续真实硬件工作不得修改：

```text
app/navigation/mock_provider.py
docs/go2_navigation_mock_contract.md
现有 Mock API 响应
现有 Mock WebSocket 事件
比赛演示页面
```

如果真实数据需要新增字段或接口，必须：

1. 使用新增命名空间或显式 Provider 能力；
2. 保持现有 Mock 客户端无需修改；
3. 默认仍返回 Mock 安全语义；
4. 对不可用真实数据返回明确的 unavailable，不使用伪造值冒充真实值；
5. 不允许读取失败触发任何运动恢复动作。

## 11. 源代码与分支保护

`health_new` 当前实测：

```text
branch: feature/go2-real-hardware-phase1
HEAD tag: robot-mock-demo-v1.1
```

工作区已有用户修改和未跟踪文件；本文没有修改、清理或提交这些内容。

该分支在 Phase 5.3 解冻前：

- 不新增 ROS2 安装脚本；
- 不接入真实 Provider；
- 不修改 Mock 合同；
- 不提交与物理主机准备无关的实验文件。

当前 `go2-gateway` 目录没有 `.git` 元数据，因此不能在此目录验证或声明
Git 分支。不得为了制造分支状态而重新初始化仓库。

## 12. Phase 5.3 解冻条件

全部满足后，才可以提交 Phase 5.3 进入申请：

- Ubuntu 22.04 物理主机验收 PASS；
- 30 分钟系统时间连续性 PASS；
- NTP 状态稳定；
- ROS2 Humble / CycloneDDS PASS；
- Go2 Ethernet PASS；
- LowState / SportModeState PASS；
- L1 / IMU / Odometry / Pose PASS；
- 跨设备时间偏差和漂移已记录；
- 无 Publisher、无运动客户端、无控制调用；
- `LidarState` IDL 兼容策略已形成；
- Mock 合同保持不变；
- 获得人工明确授权。

Phase 5.3 的第一目标只能是只读 DDS → ROS2 sensor topic 适配，不是 TF、
SLAM、Nav2 或运动控制。

## 13. 当前停止点

```json
{
  "mock_contract": "FROZEN",
  "mock_default": true,
  "real_readonly_data": "VALIDATED_ON_ETHERNET",
  "wsl_ros2_role": "DEVELOPMENT_ONLY",
  "physical_ros2_host": "WAITING",
  "dds_to_ros2_bridge": "BLOCKED",
  "tf": "NOT_STARTED",
  "slam": "NOT_STARTED",
  "nav2": "NOT_STARTED",
  "real_motion": "PROHIBITED"
}
```

在物理主机通过 Phase 5.2.3 且人工明确解冻前，到此停止。
