# Phase 7.1-C2 LiDAR 几何基准复核报告

日期：2026-08-23  
状态：`GEOMETRY_REPEATABLE_THRESHOLD_CALIBRATION_HOLD`  
模式：真实 Go2 + L1、静止、只读

## 1. 结论

操作者确认所有人工距离均从前后髋轴中心线纵向中点的地面投影，量到同一块宽平参考板的迎机器人表面。因此“从前壳或 LiDAR 外壳起量”已排除。

四点复测表明 `cloud_base` 距离相对物理距离存在高度可重复的仿射关系，而非随机噪声：

```text
observed_x      = 1.034456 × physical + 0.067427 m   R²=0.993225
observed_radial = 1.042654 × physical + 0.075784 m   R²=0.997020
```

现有 SafetyGuard 使用水平径向距离 `hypot(x,y)`。物理 0.65 m 参考板没有任何点进入当前 `<=0.65 m` STOP 区；物理 1.20 m 参考板也稳定为 CLEAR。因此绝对距离本身已满足单点 `0.15 m` 容差，但现有软件阈值并未映射到要求的物理边界，真实运动仍不得开放。

```text
参考面测距重复性            PASS
偏差模型可拟合              PASS
物理 0.50 m 直接 STOP       PASS
物理 0.65 m 直接 STOP       FAIL
物理 1.20 m SLOW            FAIL
空场恢复                    PASS
Phase 7.2                   CLOSED
```

## 2. 安全证明

全程保持：

```text
PHASE7_MOTION_EXECUTION_ENABLED=false
FOLLOW_EXECUTION_ENABLED=false
GO2_CONTROL_ENABLED=false
```

五段采集全部为 DDS reader-only，所有产物记录 `motion_calls=0`；没有 publisher、SportClient、Move、StopMove、cmd_vel、SLAM 或 Nav2。

## 3. 四点结果

候选运行 ROI 为 `roi_min_z=-0.25 m`，生产默认仍为 `-0.35 m`。

| 物理距离 | 最近 x 中位数 | x 偏差 | 径向中位数 | 径向偏差 | 直接状态证据 |
|---:|---:|---:|---:|---:|---|
| 1.20 m | 1.3190 m | +0.1190 m | 1.3338 m | +0.1338 m | 152 CLEAR；0 SLOW |
| 0.80 m | 0.8865 m | +0.0865 m | 0.9043 m | +0.1043 m | 冷启动锁存；不能作为 SLOW 证据 |
| 0.65 m | 0.7089 m | +0.0589 m | 0.7328 m | +0.0828 m | 0 个 STOP 区点；仅冷启动锁存 |
| 0.50 m | 0.6138 m | +0.1138 m | 0.6166 m | +0.1166 m | 130 帧直接 STOP，4484 个 STOP 区点 |

各段约 153–154 帧，频率约 15.37–15.40 Hz，frame 均为 `base_link`，解码错误均为 0。

## 4. 为什么不是距离公式导致

SafetyGuard 使用：

```python
math.hypot(x, y)
```

没有把高度 `z` 计入距离。四点中水平径向距离相对前向 `x` 增量约为 3–24 mm；它会增加一小部分保守偏差，但不是 0.11–0.17 m 历史偏差的主要来源。

历史 raw cloud 与 `cloud_base` 同帧分析得到固定刚体变换，平移约 `[0.282160, 0, 0] m`，内部残差小于 1.5 μm。该结果证明固件转换稳定，但没有外部标定溯源，不能证明固件声明的 `base_link` 原点与实体基准绝对一致。

## 5. 阈值 Gate

当前阈值：

```text
SLOW <= 1.20 m
STOP <= 0.65 m
```

在实际点云测量域中：

- 物理 1.20 m 的观测范围最高约 1.3671 m，当前阈值会判 CLEAR；
- 物理 0.65 m 的观测范围约 0.7091–0.7532 m，当前阈值不会直接 STOP；
- 物理 0.50 m 已有 130 帧直接 STOP。

拟合结果可用于设计下一轮“候选阈值验证”，但不得直接作为生产 offset 写入。若采用圆整数值进行只读候选测试，可优先验证：

```text
candidate SLOW threshold: 1.40 m
candidate STOP threshold: 0.80 m
```

它们只是覆盖本次参考板观测范围的测试起点，不是冻结参数。必须通过空场误报、低矮障碍、边界滞回和连续 CLEAR→SLOW→STOP→CLEAR 后才能提出冻结申请。

## 6. 空场恢复

参考板移除后的独立空场采集：

```text
154 frames
2 STOP  (startup fail-closed)
152 CLEAR
last 60 frames: stable CLEAR
ROI points: 0
decode errors: 0
motion_calls: 0
```

候选 `roi_min_z=-0.25 m` 在本次空场继续无误报，但低矮障碍覆盖仍未完成。

## 7. 最终决策

C2 已经回答“偏差是否可重复”：是。它没有证明当前阈值安全映射到物理边界。

下一阶段只能是只读的阈值/低矮障碍验证，不是 Phase 7.2 运动：

1. 保持生产配置不变；
2. 在探针运行参数中验证候选 SLOW/STOP 阈值；
3. 验证低矮障碍不会因 `roi_min_z=-0.25 m` 被漏掉；
4. 重做连续 CLEAR→SLOW→STOP→CLEAR；
5. 通过后另行审批参数冻结和单次低速运动 Gate。

## 8. 证据

- `artifacts/phase7_1c2_reference_plane_1p20m_20260823.json`
- `artifacts/phase7_1c2_reference_plane_0p80m_20260823.json`
- `artifacts/phase7_1c2_reference_plane_0p65m_20260823.json`
- `artifacts/phase7_1c2_reference_plane_0p50m_20260823.json`
- `artifacts/phase7_1c2_clear_recovery_20260823.json`
- `artifacts/phase7_1c2_geometric_fit_20260823.json`

