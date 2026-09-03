# Phase 7.1-C LiDAR 静态距离标定报告

日期：2026-08-23  
状态：`FUNCTIONAL_TRANSITIONS_PASS_ABSOLUTE_DISTANCE_HOLD`  
模式：真实 Go2 + L1，只读；机器人不运动

## 1. 结论

`rt/utlidar/cloud_base` 点云链路、解码、帧名、频率和 SafetyGuard 的状态转换均已通过真机验证：

- 空场可稳定输出 `CLEAR`；
- 0.80 m 连续测试可稳定输出 `SLOW`；
- 障碍接近时可由 `CLEAR -> SLOW -> STOP`；
- `STOP` 立即生效并锁存；
- 障碍真实移除后可恢复 `CLEAR`；
- 全程 `motion_calls=0`。

但是，独立人工摆放距离与点云最近距离仍存在系统性正偏差，且 1.20 m 物体在单点测试中被判为 `CLEAR`，0.50 m 物体的中位测距误差达到 `+0.1718 m`。因此绝对距离标定没有通过，不能据此开放真实运动。

```text
LiDAR 数据链路                  PASS
CLEAR/SLOW/STOP 功能转换        PASS
直接 STOP 与恢复                PASS
绝对物理距离标定                HOLD
Phase 7.2 真实运动              CLOSED
```

## 2. 安全边界

本轮始终保持：

```text
PHASE7_MOTION_EXECUTION_ENABLED=false
FOLLOW_EXECUTION_ENABLED=false
GO2_CONTROL_ENABLED=false
```

探针只创建 DDS reader；没有创建 publisher，没有调用 `SportClient`、`RobotService`、`Move` 或 `StopMove`。所有正式产物均记录 `read_only=true`、`motion_calls=0`。

## 3. 点云基线

| 项目 | 结果 |
|---|---:|
| Go2 地址 | `192.168.123.161` |
| 主机地址 | `192.168.123.222` |
| DDS Domain | `0` |
| Topic | `rt/utlidar/cloud_base` |
| Frame | `base_link` |
| 频率 | 约 `15.34–15.44 Hz` |
| 每帧点数中位数 | 约 `1544–1573` |
| 解码错误 | `0` |

该结果证明点云能够稳定读取，但不等同于物理距离标定通过。

## 4. 正式单点测量

单点测量使用 `roi_min_z=-0.30 m`。启动时 SafetyGuard 处于 fail-closed 状态，因此前两帧常见 `clearance_confirmation_pending`。

| 人工摆放 | 预期 | 稳态输出 | 最近距离中位数 | 误差 | 判定 |
|---:|---|---|---:|---:|---|
| 2.00 m | CLEAR | CLEAR | 无 ROI 点 | 不适用 | 功能通过；物体位于 `roi_max_x=2.00 m` 边界 |
| 1.50 m | CLEAR | CLEAR | 1.6518 m | +0.1518 m | 分类通过；误差临界/超差 |
| 1.20 m | SLOW | CLEAR | 1.3166 m | +0.1166 m | `HOLD_LEVEL` |
| 0.80 m | SLOW | STOP 锁存 | 0.9105 m | +0.1105 m | 冷启动锁存样本，不作为 SLOW 分类证据 |

0.80 m 单点测试在障碍已存在时冷启动，SafetyGuard 未先获得三帧空场确认，故 76 帧均保持 fail-closed `STOP`。它证明了安全锁存，不证明稳态 SLOW 分类。

## 5. ROI 下边界 A/B

空场诊断发现：

- `roi_min_z=-0.35 m` 会将接近地面的点纳入 ROI，产生假 STOP；
- `roi_min_z=-0.30 m` 空场仍有间歇性假 SLOW；
- 运行时使用 `roi_min_z=-0.25 m` 时，空场基线为 77/77 `CLEAR`。

`-0.25 m` 仅是本轮候选参数，没有写回生产默认。当前 `LidarSafetyConfig.roi_min_z` 仍为 `-0.35 m`。在缺少低矮障碍覆盖测试前，不应冻结候选值。

## 6. 连续状态与滞回测试

连续会话使用候选 `roi_min_z=-0.25 m`，保持同一 SafetyGuard 实例，避免把冷启动锁存误判为距离分类。

| 测试段 | 样本 | 稳态 | 关键证据 | 结果 |
|---|---:|---|---|---|
| 空场基线 | 77 | CLEAR | 77/77 CLEAR | PASS |
| 0.80 m | 76 | SLOW | 65 帧直接 SLOW，11 帧 SLOW 释放确认 | PASS |
| 0.65 m | 77 | STOP | 全程锁存；中位距离 0.7886 m | 功能 PASS，绝对距离 HOLD |
| 0.50 m | 77 | STOP | 全程锁存；中位距离 0.6718 m | 功能 PASS，距离误差 +0.1718 m |

SafetyGuard 已增加 SLOW 释放滞回：进入 SLOW 后必须连续三帧空场才恢复 CLEAR。STOP 仍保持立即抢占和 fail-closed 行为。

## 7. 直接停车与恢复

40 秒直接停车测试共 616 帧：

```text
CLEAR 466
SLOW  104
STOP   46
```

其中：

- `obstacle_in_slow_zone`: 96 帧；
- `slow_clearance_confirmation_pending`: 8 帧；
- `obstacle_in_stop_zone`: 2 帧；
- `clearance_confirmation_pending`: 44 帧；
- 最小距离：`0.6037 m`；
- 最后 30 帧稳定为 `STOP`。

第一次“恢复”记录中障碍物实际上尚未移除，308 帧持续 STOP；这是现场操作无效段，不用于判定恢复失败。障碍物确认移除后的重试为 616/616 `CLEAR`，恢复通过。

## 8. 绝对距离问题

有效物体样本均表现为点云距离大于人工摆放距离：

```text
1.50 m -> 1.6518 m  (+0.1518 m)
1.20 m -> 1.3166 m  (+0.1166 m)
0.80 m -> 0.9074 m  (+0.1074 m)
0.65 m -> 0.7886 m  (+0.1386 m)
0.50 m -> 0.6718 m  (+0.1718 m)
```

这些误差尚不能简单作为固定 offset 写入软件。现场操作者已经确认人工测距起点采用前后髋轴中心线纵向中点的地面投影，并非机身前壳。SafetyGuard 使用的 `hypot(x,y)` 相对前向 `x` 只增加约 6–12 mm，也解释不了现有偏差。下一步须用宽平刚性参考面复验障碍物有效反射面，并判断 `cloud_base` 是否存在固定物理原点偏置。现阶段禁止通过放宽 STOP/SLOW 阈值来掩盖来源不明的几何偏差。

## 9. 软件验证

相关定向测试结果：`27 passed`。

完整 pytest 收集 400 项，运行到 100% 时出现 1 项与本轮 LiDAR 无关的异步 Mock 审计时序失败：

```text
tests/test_robot_tasks.py::test_task_audit_log_endpoint_returns_recent_entries
```

该测试单独重跑通过。不能据此宣称完整测试套件 400/400 全通过，但不影响本轮 SafetyGuard 定向测试结论。

## 10. Gate 决策

Phase 7.1-C 不是完整 PASS，而是：

```text
FUNCTIONAL_TRANSITIONS_PASS
ABSOLUTE_DISTANCE_HOLD
```

解除 HOLD 前至少需要：

1. 保留已确认的前后髋轴中点地面投影基准；
2. 使用宽平刚性参考面和可靠测距工具重测 1.20 m、0.80 m、0.65 m、0.50 m；
3. 验证候选 `roi_min_z=-0.25 m` 不会漏检低矮障碍；
4. 冻结并复测生产 ROI/阈值；
5. 再次证明 `motion_calls=0` 后，单独审批 Phase 7.2。

## 11. 主要证据

- `artifacts/phase7_1c_formal_lidar_2p00m_20260823.json`
- `artifacts/phase7_1c_formal_lidar_1p50m_20260823.json`
- `artifacts/phase7_1c_formal_lidar_1p20m_20260823.json`
- `artifacts/phase7_1c_formal_lidar_0p80m_20260823.json`
- `artifacts/phase7_1c_hysteresis_roi_n0p25_20260823.json`
