# Phase 7.2-D 有效步态速度策略冻结报告

日期：2026-08-24

## 结论

Phase 7.2-D 的软件准备已通过，真机 UWB 连续伴随尚未执行。

官方级运动基线已经证明：以 5 Hz 连续刷新 `Move(0.30, 0, 0)` 时，Go2 能正常抬脚、向前行走、及时 `StopMove()`，且原厂遥控器可以接管。因此，先前原地扭动的主要原因已收敛为伴随控制器输出低于稳定步态速度区间。

## 冻结策略

真实 UWB 伴随控制器单独启用以下策略；默认 Mock 控制行为保持不变：

```text
期望位姿附近（forward_error < 0.25 m）
→ vx = 0

达到启动阈值（forward_error >= 0.25 m）
→ 启动步态
→ minimum_walking_vx = 0.20 m/s

步态已启动且 forward_error > 0.10 m
→ 保持 0.20～0.30 m/s

进入停止阈值（forward_error <= 0.10 m）
→ vx = 0
```

转向和横移限制：

```text
vy = 0
|wz| <= 0.30 rad/s
```

启动和停止采用双阈值滞回，避免 UWB 噪声使控制器在临界点反复启停。策略不生成主动后退命令。

## 安全优先级

步态速度下限不会绕过安全链：

- UWB 超时、规划器停止、非 FOLLOW 控制权：立即输出零并清除步态状态；
- LiDAR `SLOW`：在步态速度计算后继续降速；
- LiDAR `STOP`、外部风险、人工接管：最终运动为零；
- 安全恢复后不会自动继续，仍需人工重新授权；
- 真实执行继续采用 5 Hz 速度刷新，异常时才调用 `StopMove()`；
- RobotService watchdog 继续保留。

## 独立真机门禁

普通 C1 仍限制为最多 5 个成功刷新周期。只有额外确认：

```text
UWB_FOLLOW_LIVE_GATE
```

才允许 Phase 7.2-D 最多 15 个成功刷新周期（5 Hz 下最多约 3 秒）。固定速度诊断与 UWB 连续伴随不能同时启用。

建议首次只批准 10 个成功周期，即约 2 秒；到达上限后强制进入人工接管并停车。

## 验证结果

```text
定向控制/门禁/执行器测试：PASS（76 tests）
全量 Python 测试：          PASS
Ubuntu 20.04 Python 3.8：   py_compile PASS
真实 Move 调用：            0（本次软件变更后）
Phase 7.2-D Live Gate：     READY，未执行
```

覆盖项包括：

- 小正向误差保持零速度；
- 达到启动门槛后直接进入 `0.20 m/s`；
- 远距离速度增长且不超过 `0.30 m/s`；
- 启停滞回；
- UWB 超时清除步态状态；
- 期望右后方位姿保持零命令；
- LiDAR、风险、人工接管、resume 和 executor 安全回归；
- Mock 默认行为未启用步态速度下限。

## 当前 Gate

```text
SportClient 官方运动基线     PASS
有效步态速度策略              PASS
UWB/LiDAR/风险软件闭环        PASS
真实 UWB 连续伴随             READY / NOT RUN
连续自主伴随                  CLOSED
```

下一步仅在现场再次确认空场、安全员持原厂遥控器、标签约 2 m、LiDAR CLEAR、风险心跳新鲜后，单独审批 2 秒真实 UWB 闭环。
