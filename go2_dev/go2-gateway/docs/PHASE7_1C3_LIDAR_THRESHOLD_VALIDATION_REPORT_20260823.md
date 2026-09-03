# Phase 7.1-C3 LiDAR 候选阈值只读验证报告

日期：2026-08-23  
状态：`OPERATOR_ACCEPTED_REFERENCE_BOARD_PASS_WITH_WAIVERS`  
真实运动：`CLOSED`

## 1. 候选参数

本轮只通过连续只读探针注入：

```text
SLOW = 1.40 m
STOP = 0.80 m
roi_min_z = -0.25 m
```

生产 `LidarSafetyConfig` 默认值没有修改。

## 2. 连续参考板结果

同一个 SafetyGuard 实例贯穿全部七段：

| 工况 | 样本 | 全段状态 | 最后 60 帧 | 结果 |
|---|---:|---|---|---|
| 空场基线 | 154 | 154 CLEAR | CLEAR | PASS |
| 1.50 m | 154 | 154 CLEAR | CLEAR | PASS |
| 1.20 m | 154 | 154 SLOW | SLOW | PASS |
| 0.80 m | 154 | 154 SLOW | SLOW | 边界 PASS：无 CLEAR |
| 0.65 m | 154 | 154 STOP | STOP | PASS；143 帧直接 STOP |
| 0.50 m | 154 | 154 STOP | STOP | PASS；154 帧直接 STOP |
| 板移除后空场 | 154 | 154 CLEAR | CLEAR | 最终恢复 PASS |

关键距离：

```text
1.50 m -> median 1.6581 m
1.20 m -> median 1.3586 m
0.80 m -> median 0.9536 m
0.65 m -> median 0.7715 m
0.50 m -> median 0.6205 m
```

1.50 m 和 1.20 m 的偏差约 0.158 m，略超原 0.15 m 记录容差；操作者明确接受当前测距误差并要求停止进一步测试。

## 3. 0.80 m 边界解释

工具按照 `distance <= stop_distance` 的严格规则，把物理 0.80 m 自动预期为 STOP，因此该段自动 verdict 为 `HOLD_LEVEL`。C3 事先定义的人工 Gate 允许 0.80 m 为 SLOW/STOP 边界，但禁止 CLEAR。实测 154/154 SLOW、0 CLEAR，故人工边界 Gate 通过，行为冻结为“物理 0.80 m 稳定 SLOW”。

## 4. 已接受但未完成的项目

操作者停止后续复测，以下事项没有取得新的现场证据：

1. STOP 后前三帧恢复转场没有被采集；板移除后开始的窗口只证明最终稳定 CLEAR。三帧逻辑已有单元测试和既往连续测试证据，但本轮同步现场转场为 waived；
2. `roi_min_z=-0.25 m` 的低矮障碍覆盖没有执行；
3. 候选参数尚未写入生产配置；
4. Phase 7.2 真实运动未获本报告自动授权。

关闭会话时 reader 收到一次不含 `fields` 的 `InvalidSample` 并退出。该事件发生在七段报告全部保存后，不影响已有结果，但应作为只读探针健壮性事项保留。

## 5. 安全证明

```text
read_only = true
motion_calls = 0
DDS publisher = 0
SportClient / Move / StopMove = 0
```

会话已经关闭，没有残留 LiDAR 探针进程。

## 6. Gate 决策

基于操作者对距离误差的明确接受，参考板阈值状态链记为：

```text
C3_REFERENCE_BOARD_THRESHOLDS = OPERATOR_ACCEPTED
LOW_OBSTACLE_COVERAGE         = NOT_RUN_WAIVED_FOR_NOW
PRODUCTION_CONFIG_WRITE       = NOT_DONE
PHASE_7_2_REAL_MOTION         = CLOSED
```

如后续要写入生产配置或开放一次性低速 Move，必须单独明确授权；本次“接受误差”不自动扩大为真实运动授权。

## 7. 证据

- `artifacts/phase7_1c3_candidate_thresholds_20260823.json`
- SHA-256: `DB584A46712B750F7A38C581189C7744FEEEBFB37DDA4000DF9745E140D4AFDC`

