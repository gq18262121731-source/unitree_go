# Phase 7 验收总报告

日期：2026-08-23  
总体状态：`PHASE_7_1B_PASS_PHASE_7_1C_HOLD_PHASE_7_2_CLOSED`

## 1. 当前结论

机器人侧 Phase 7 的只读输入与离线安全闭环已完成主要验收：UWB 真实数据、掉线超时、方向映射、右后方伴随几何、LiDAR 功能状态转换和风险锁存均已有真机证据。绝对 LiDAR 距离标定尚未通过，因此没有开放任何真实运动。

| 阶段 | 结果 |
|---|---|
| Phase 7.0 范围与安全架构 | PASS |
| Phase 7.1 UWB 真实输入 | PASS |
| Phase 7.1-B 真实数据 dry-run | PASS |
| Phase 7.1-C LiDAR 功能转换 | PASS |
| Phase 7.1-C LiDAR 绝对距离 | HOLD |
| Phase 7.1-C2 几何重复性 | PASS |
| Phase 7.1-C2 物理阈值映射 | HOLD |
| Phase 7.1-C3 参考板候选阈值 | OPERATOR ACCEPTED |
| Phase 7.1-C3 低矮障碍覆盖 | NOT RUN / WAIVED FOR NOW |
| 候选参数写入生产配置 | NOT DONE |
| Phase 7.2-A 离线控制映射 | PASS |
| Phase 7.2-A Live UWB dry-run | WAITING_FOR_ROBOT |
| Phase 7.2 单次极低速运动 | CLOSED |

## 2. UWB 真机验收

### 掉线与恢复

同步掉电采集显示：

```text
关机前最后样本：10.750 s
恢复后首个样本：32.907 s
连续空窗：       22.157 s
```

LowState 在空窗期间继续正常，证明是 UWB 标签断连，而非机器人 DDS 整体中断。控制安全逻辑以最后一次 UWB 样本的新鲜度为准；超过 2 秒进入 `uwb_stale`、输出零速度并清除 resume。标签恢复后不能自动恢复运动。

### 方向与单位

真机标定确认：

```text
source: orientation_est
unit: radians
left: positive
right: negative
zero_offset: +0.55 rad
```

254 个样本全部有效，观测频率约 5.08 Hz，最大非掉线接收间隔约 0.969 s。`yaw_est` 不应作为目标 bearing。

## 3. Phase 7.1-B dry-run

真实采集数据经过：

```text
JSONL -> UwbInputValidator -> FollowTargetPlanner
      -> FollowController -> MotionArbiter
      -> RealFollowExecutor (disabled)
```

掉电回放 85/85 样本有效；22.157 秒掉线触发 `uwb_stale` 并输出零运动。方向回放 254/254 样本有效：

| 目标位置 | 平均 bearing | 平均 wz | 判断 |
|---|---:|---:|---|
| 正前方 | +0.0037 rad | -0.3906 | 符合右后方伴随修正 |
| 左侧 | +0.6634 rad | +0.2637 | 正确 |
| 右侧 | -0.4434 rad | -0.5000 | 正确 |

期望右后方相对位姿为：

```text
back_distance = 1.5 m
right_offset  = 0.5 m
distance      = 1.5811 m
bearing       = 0.3218 rad
```

在该期望位姿下 `target_x=0`、`target_y=0`、`vx=0`、`wz=0`。因此“目标正前方时 wz 必须接近零”不是本项目的正确 Gate。

## 4. LiDAR 真机验收

点云 `rt/utlidar/cloud_base`、`base_link` frame 稳定，约 15.34–15.44 Hz，解码错误为 0。

候选运行参数 `roi_min_z=-0.25 m` 下完成连续测试：

- 空场 77/77 CLEAR；
- 0.80 m 76/76 SLOW；
- 直接接近产生 CLEAR -> SLOW -> STOP；
- STOP 立即生效并锁存；
- 障碍物实际移除后 616/616 CLEAR；
- 全程 `motion_calls=0`。

但绝对测距存在约 +0.11 至 +0.17 m 的正偏差，1.20 m 单点测试稳态为 CLEAR，0.50 m 中位距离误差为 +0.1718 m。因此只给“功能转换 PASS”，不给“绝对标定 PASS”。`roi_min_z=-0.25 m` 也仍是候选值，生产默认尚未修改。

## 5. 本轮软件收口

- 新增连续只读 LiDAR 会话探针：`tools/probe_lidar_safety_phase7_1c_session.py`；
- SafetyGuard 增加 SLOW 释放滞回，连续三帧空场后才解除；
- STOP 保持立即、fail-closed；
- 新增连续会话和滞回测试；
- 相关定向测试：27 passed；
- 完整套件有 1 个无关异步 Mock 审计时序测试偶发失败，单独重跑通过。

## 6. 运动安全证明

所有本轮真机采集和回放均满足：

```text
read_only = true
motion_calls = 0
RealFollowExecutor = disabled
DDS publishers = 0
SportClient calls = 0
```

没有执行 Move、StopMove、cmd_vel、Follow mode switch、SLAM 或 Nav2。

## 7. 下一步

现场已确认人工距离是从前后髋轴中心线纵向中点的地面投影开始测量，
因此前壳起量假设已排除。目前只允许按
`docs/LIDAR_GEOMETRIC_DATUM_RECHECK_PHASE_7_1_C2.md` 使用宽平参考面继续做
LiDAR 几何基准与低矮障碍复验。Phase 7.2 必须继续关闭，直到绝对距离与
生产 ROI 参数完成复核并由人工单独批准。

### C2 更新

宽平参考板四点复测确认距离偏差高度可重复：径向拟合为
`observed=1.042654*physical+0.075784 m`，`R²=0.997020`。但是物理 1.20 m
仍为 CLEAR，物理 0.65 m 没有直接 STOP 区点；只有物理 0.50 m 可靠触发
直接 STOP。详见 `docs/PHASE7_1C2_LIDAR_GEOMETRIC_DATUM_REPORT_20260823.md`。

下一 Gate 已定义为
`docs/LIDAR_THRESHOLD_READONLY_VALIDATION_PHASE_7_1_C3.md`。候选阈值只通过
只读探针运行时注入，生产默认未修改；参考板序列通过后仍需低矮障碍验证。

### C3 操作者收口

连续参考板序列已证明 1.50 m CLEAR、1.20 m SLOW、0.80 m 稳定 SLOW 且无
CLEAR、0.65 m 和 0.50 m 直接 STOP、板移除后最终 CLEAR。操作者明确接受
当前测距误差并停止进一步测试。同步三帧恢复转场和低矮障碍覆盖未取得新
现场证据，候选参数未写入生产配置，真实运动仍关闭。详见
`docs/PHASE7_1C3_LIDAR_THRESHOLD_VALIDATION_REPORT_20260823.md`。

### Phase 7.2-A 离线映射

931 个真实历史 UWB 样本已通过统一 Validator→Planner→Controller→Arbiter→
SafetyGuard→disabled Executor 链，形成 936 个时间点。真实远距离、过近、
左右方向、22.157 秒掉线、LiDAR三态和 FALL_CONFIRMED 锁存均通过，所有
Executor 状态为 DISABLED，`motion_calls=0`、DDS publisher=0。Live 工具只
完成源码与编译准备，没有运行。详见
`docs/PHASE7_2A_OFFLINE_MOVE_MAPPING_REPORT_20260823.md`。
