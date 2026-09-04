# Phase 7.1-C3 LiDAR 候选阈值只读验证

状态：`OPERATOR_ACCEPTED_REFERENCE_BOARD_PASS_WITH_WAIVERS`  
模式：单一连续 SafetyGuard 会话、真实点云、零运动

## 候选参数

仅通过探针命令行注入：

```text
SLOW = 1.40 m
STOP = 0.80 m
roi_min_z = -0.25 m
```

生产 `LidarSafetyConfig` 默认值保持：

```text
SLOW = 1.20 m
STOP = 0.65 m
roi_min_z = -0.35 m
```

本轮结果不得自动写入生产配置，不得开放 Phase 7.2。

## 安全约束

```text
PHASE7_MOTION_EXECUTION_ENABLED=false
FOLLOW_EXECUTION_ENABLED=false
GO2_CONTROL_ENABLED=false
motion_calls=0
```

禁止 DDS publisher、SportClient、Move、StopMove、cmd_vel、SLAM 和 Nav2。

## 连续会话

必须保持一个 SafetyGuard 实例贯穿全部位置，避免将冷启动 fail-closed
锁存误认为真实 STOP：

```bash
python3 tools/probe_lidar_safety_phase7_1c_session.py \
  --peer 192.168.123.161 \
  --local-address 192.168.123.222 \
  --stop-distance 0.80 \
  --slow-distance 1.40 \
  --roi-min-z -0.25 \
  --capture-seconds 10 \
  --minimum-samples 60 \
  --output artifacts/phase7_1c3_candidate_thresholds_20260823.json
```

## 顺序

```text
空场          -> CLEAR
参考板 1.50 m -> CLEAR
参考板 1.20 m -> SLOW
参考板 0.80 m -> SLOW/STOP 边界，但禁止 CLEAR
参考板 0.65 m -> STOP
参考板 0.50 m -> STOP
移除参考板    -> 三帧确认后 CLEAR
```

每段至少采集 60 帧，并记录整段计数及最后 60 帧稳态。

## Gate

- 空场最后 60 帧全部 CLEAR，无持续假 SLOW/STOP；
- 1.50 m 最后 60 帧 CLEAR；
- 1.20 m 最后 60 帧 SLOW；
- 0.80 m 不得出现危险 CLEAR，边界行为须记录；
- 0.65 m 最后 60 帧 STOP，且必须出现直接 `obstacle_in_stop_zone`；
- 0.50 m 最后 60 帧 STOP，且必须出现直接 `obstacle_in_stop_zone`；
- 板移除后必须经过三帧确认才 CLEAR；
- frame、频率和解码正常；
- 全程 `motion_calls=0`。

## 低矮障碍

参考板序列只验证距离阈值。生产参数冻结前仍须单独完成低矮障碍覆盖：

```text
低矮障碍 @ 0.80 m -> 不得 CLEAR
低矮障碍 @ 0.50 m -> 必须 STOP
```

因此 C3 参考板序列通过后，状态最多为
`CANDIDATE_THRESHOLDS_PASS_LOW_OBSTACLE_PENDING`，不能直接开启真实运动。

## 实测收口

参考板连续序列已经完成。1.50 m CLEAR、1.20 m SLOW、0.80 m SLOW 且无
CLEAR、0.65 m 和 0.50 m 直接 STOP、最终空场 CLEAR。操作者接受当前距离
误差并停止进一步测试。

三帧现场恢复转场与低矮障碍覆盖没有完成，生产配置未写入，Phase 7.2
仍关闭。详见 `docs/PHASE7_1C3_LIDAR_THRESHOLD_VALIDATION_REPORT_20260823.md`。
