# Phase 7.2-A 离线 Move 参数映射报告

日期：2026-08-23  
运行条件：Go2 EDU 未开机；仅文件回放  
结论：`PASS_OFFLINE_CONTROL_MAPPING`

## 1. Gate

```text
Offline control mapping          PASS
Live UWB control dry-run         WAITING_FOR_ROBOT
Real motion                      CLOSED
motion_calls                     0
DDS publishers                   0
```

本轮没有连接 Go2、没有初始化 DDS、没有运行 live 工具，也没有调用任何机器人服务。

## 2. 统一离线链路

新增工具：

```text
tools/replay_phase7_2a_control_chain.py
```

实际执行链：

```text
真实历史 UWB JSONL
→ UwbInputValidator
→ FollowTargetPlanner
→ FollowController
→ MotionArbiter
→ LidarSafetyGuard（离线三态 fixture）
→ RealFollowExecutor(execution_enabled=false)
→ 候选 final command + executed zero
```

每个时间点均输出：

```text
timestamp
uwb_distance / uwb_bearing / uwb_valid
follow_target_x / follow_target_y
candidate_vx / candidate_vy / candidate_wz
lidar_state
arbiter_authority
final_vx / final_vy / final_wz
execution_enabled
executor_status
executed_vx / executed_vy / executed_wz
resume_authorized
risk_state
reason
```

`final_*` 是 MotionArbiter 的候选输出；`executed_*` 始终为零。

## 3. 输入证据

使用四份真实历史 UWB 数据：

| 文件 | 作用 |
|---|---|
| `phase7_1_uwb_capture_elevated_20260822.jsonl` | 真实远距离与左向样本 |
| `phase7_1_uwb_capture_20260822_1351.jsonl` | 真实过近样本 |
| `phase7_1_uwb_yaw_calibration_20260822_141937.jsonl` | 左右方向标定样本 |
| `phase7_1_uwb_powercycle_synced_20260822_141408.jsonl` | 22.157 秒真实掉线 |

总计：

```text
真实 UWB 样本              931
时间线记录                 936
真实掉线超时插入点           1
文件结束 liveness 检查点      4
Executor DISABLED          936/936
```

## 4. UWB → 候选 Move 映射

### 真实远距离

选中的真实样本：

```text
distance        2.6562 m
bearing         0.3794 rad
target_x        0.9673 m
target_y        0.4837 m
candidate_vx    +0.1935 m/s
candidate_wz    +0.3869 rad/s
authority       FOLLOW
```

满足 `far → vx > 0`。

### 期望右后方位姿

```text
distance = 1.5811388301 m
bearing  = 0.3217505544 rad
target_x = 0
target_y = 0
vx = vy = wz = 0
```

满足零误差目标。没有把“人在正前方”错误地当成零转向基准。

### 真实过近

```text
distance        0.9452 m
candidate_vx    0
candidate_wz    0
reason          follow_safety_stop
```

小于 `min_distance=1.0 m` 时禁止继续前进。控制器在安全停止区输出零，而不是主动倒退。

### 左右方向

```text
真实左向 bearing +0.5278 rad → candidate_wz +0.0562
真实右向 bearing -0.4044 rad → candidate_wz -0.5000
```

方向与 Phase 7.1 标定一致，没有重新猜测映射。

## 5. 真实 UWB 掉线

真实掉电采集中的连续空窗：

```text
receive gap = 22.157 s
planner timeout check = 2.0 s
reason = uwb_stale
final_vx = final_vy = final_wz = 0
execution_enabled = false
resume_authorized = false
```

LowState 与 UWB 原始证据的既有结论保持不变：这是标签掉线，不是机器人 DDS 整体中断。

## 6. LiDAR 仲裁

LiDAR 使用保存证据确认过的候选阈值配置，并以已有离线 point fixture 驱动真实 `LidarSafetyGuard`：

```text
SLOW = 1.40 m
STOP = 0.80 m
roi_min_z = -0.25 m
slow scale = 0.35
```

同一真实远距离 Follow 候选命令的仲裁结果：

| LiDAR | authority | final vx | final wz | 结果 |
|---|---|---:|---:|---|
| CLEAR | FOLLOW | 0.1935 | 0.3869 | 原候选通过 |
| SLOW | FOLLOW | 0.0677 | 0.1354 | 按 0.35 限速 |
| STOP | LIDAR_STOP | 0 | 0 | 零运动 |

这只是离线仲裁验收，不构成新一轮 LiDAR 真机验收。

## 7. FALL_CONFIRMED 抢占

使用正式外部事件契约注入：

```text
FALL_CONFIRMED
incident_id = phase7-2a-fall-001
confidence = 0.93
```

结果：

```text
before authority       FOLLOW
fall authority         EMERGENCY
risk_state             PAUSED_BY_FALL
fall final             [0, 0, 0]
continued UWB final    [0, 0, 0]
executor               DISABLED
resume_authorized      false
```

跌倒事件保持锁存；后续 UWB 即使继续要求前进，仍由 EMERGENCY 输出零运动。

## 8. 未来 live 工具

已准备但没有运行：

```text
tools/live_uwb_move_dryrun_phase7_2a.py
```

启动时固定显示：

```text
PHASE 7.2-A LIVE DRY-RUN
REAL MOTION DISABLED
DDS READERS ONLY
```

工具需要未来显式传入 `--confirm-readonly-live` 才会启动 DDS reader，并会在环境变量 `PHASE7_MOTION_EXECUTION_ENABLED=true` 时拒绝运行。静态 AST 检查确认不存在 DDS writer/publisher、机器人运动客户端或运动调用。

当前状态：`WAITING_FOR_ROBOT`，没有伪造 live 结果。

## 9. 测试

```text
Python compile check                         PASS
Phase 7.2-A + UWB replay + motion safety    30 tests PASS
```

覆盖：

- far → positive vx；
- desired pose → near-zero command；
- too-close → zero forward；
- left/right wz sign；
- real 22.157 s dropout → zero；
- LiDAR CLEAR/SLOW/STOP；
- FALL_CONFIRMED 抢占与锁存；
- resume authorization false；
- Executor 936/936 disabled；
- move/safe-stop count zero；
- live 工具静态只读边界。

## 10. 停止结论

Phase 7.2-A 离线控制映射通过后按要求停止：

```text
Phase 7.2-A offline mapping    PASS
Live UWB dry-run               WAITING_FOR_ROBOT
Phase 7.2-B real Move          NOT ENTERED
Continuous follow             NOT ENTERED
SLAM / Nav2                    NOT ENTERED
```

Go2 充电并重新开机后，也必须先执行 live UWB→Move 参数实时 dry-run；本报告不授权真实 Move。

## 11. 机器证据

- `artifacts/phase7_2a_offline_control_mapping_20260823.json`
- SHA-256: `38FBBB64EBA0576D38303B21113A2BFD80BF2F05CCEFEDD4C1971488B9F28DFE`

