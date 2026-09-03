# Go2 Companion Follow V1 实现说明（2026-08-24）

## 结论

Go2 EDU 完整伴随监督框架已经接入现有 Phase 7 控制链，并完成离线自动化验证。
本次没有初始化 Unitree SDK、没有调用 `Move()`、没有开放新的真机权限。

现有 UWB、规划器、LiDAR、风险锁存、仲裁、执行器和连续刷新机制均被保留；
新增层只生成行为状态并治理候选速度，不能直接向机器人发送命令。

## 控制链

```text
official UWB remote
  -> UwbInputValidator
  -> FollowTargetPlanner
  -> FollowController + FollowProfile
  -> CompanionSupervisor
  -> MotionArbiter
  -> LidarSafetyGuard decision
  -> RealFollowExecutor
  -> RobotService / SportClient

external fall events
  -> MotionArbiter fall latch
  -> CompanionSupervisor fall state
```

`CompanionSupervisor` 与 `MotionArbiter` 是两道不同的门：前者决定伴随行为，后者
继续执行 `EMERGENCY > MANUAL > LIDAR_STOP > FOLLOW` 的最终运动权限仲裁。

## 状态机

```text
IDLE -> FOLLOWING

FOLLOWING -> PERSON_STOPPED -> VIEW_ADJUST -> HOLD
FOLLOWING -> TARGET_LOST -> SAFE_STOP
FOLLOWING -> OBSTACLE_STOP
FOLLOWING -> FALL_SUSPECTED

FALL_SUSPECTED -> EMERGENCY_STOP -> MONITORING
MONITORING -> RECOVERING -> WAIT_RESUME
WAIT_RESUME -- explicit RESUME --> FOLLOWING
```

主要约束：

- 目标静止约 1.5 秒后停止前进；观察角度不合适时只允许低速原地转向。
- UWB 丢失和 LiDAR STOP 都会拒绝候选速度，由既有执行器执行 `StopMove` 并清除
  resume 授权。
- `FALL_CONFIRMED` 立即进入锁存的 `EMERGENCY_STOP`。
- 新鲜 `NON_FALL` 只能推动监护状态进入恢复流程，不能自动恢复跟随。
- 只有稳定恢复、风险锁清除并到达 `WAIT_RESUME` 后，人工 `RESUME` 才能重新跟随。

## 第一版 FollowProfile

```text
back_distance                 1.50 m
right_offset                  0.50 m
hard safe distance range      1.00..2.50 m
follow start distance         1.80 m
follow stop distance          1.70 m
bearing dead band             12 deg
minimum effective walk vx     0.20 m/s
maximum vx                    0.30 m/s
maximum |wz|                  0.30 rad/s
view-adjust maximum |wz|      0.15 rad/s
stationary window             1.50 s
```

1.80/1.70 m 是带滞回的启停阈值：尚未行走时距离达到 1.80 m 才启动；已在行走时
进入 1.70 m 才停止。正向速度一旦启动便进入 Go2 已验证的有效步态区，且不产生
主动倒退命令。目标方向误差在 12 度内时角速度为零。

`FollowProfile.control_frequency_hz` 记录 V1 的 10 Hz 监督目标。当前已审批的真实
执行器安全门仍硬限制为最多 5 Hz，现场审批前不提高真实 `Move()` 刷新频率。

## 代码位置

```text
app/companion/config.py         CompanionConfig / FollowProfile
app/companion/events.py         统一行为事件
app/companion/models.py         状态、快照、运动模式
app/companion/state_machine.py  确定性状态转移
app/companion/supervisor.py     UWB 静止检测与候选速度治理
app/motion/supervised_loop.py   现有监督循环集成点
app/follow/controller.py        距离滞回、有效步速、方向死区
```

## 验证范围

新增测试覆盖：

- 完整状态图的主要分支；
- 目标静止进入 HOLD、重新移动返回 FOLLOWING；
- 1.80/1.70 m 距离滞回；
- 0.20 m/s 有效步态下限；
- 12 度方向死区；
- 跌倒确认、监护、恢复、`WAIT_RESUME` 和人工恢复；
- `SupervisedMotionLoop` 中 supervisor 与 risk latch 的同步；
- live UWB builder 自动装配 CompanionSupervisor，固定速度诊断保持原路径。

全项目测试已通过。验证只覆盖软件与离线行为，以下仍需现场集中完成：远离、靠近、
左右转、人停下/重走、LiDAR 障碍、UWB 掉线，以及参数冻结。

## 真机边界

本实现不改变以下约束：

- 默认配置不允许真实运动；
- 真实执行必须显式通过 Phase 7 环境开关、风险心跳、现场安全员和多次键入确认；
- 固定速度诊断与 live UWB follow 不能同时启用；
- 当前 live UWB gate 仍受成功刷新周期上限和 5 Hz 执行器上限约束；
- 本报告不构成下一轮真机测试授权。
