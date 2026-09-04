# Phase 7.2-C 真实 UWB + 真实 LiDAR 连续低速控制实现报告

日期：2026-08-23

## 结论

Phase 7.2-C 的短周期闭环软件层已完成，离线和自动化验证通过。

```text
SupervisedMotionLoop implementation    PASS
UWB fail-closed integration            PASS
LiDAR fail-closed integration          PASS
External risk heartbeat integration    PASS
Manual takeover integration            PASS
Short command slicing                  PASS
Automatic resume                       DISABLED
Live continuous follow                 NOT RUN
Real motion during this implementation 0 calls
```

本轮只开发和验证软件，没有连接或控制真实 Go2。真实连续伴随仍需单独现场审批后运行。

## 实现结构

```text
SDK2 ChannelSubscriber (readers only)
    +-- rt/uwbstate
    +-- rt/utlidar/cloud_base
            |
            v
SupervisedMotionLoop (5 Hz)
    +-- UwbInputValidator
    +-- FollowTargetPlanner
    +-- FollowController
    +-- LidarSafetyGuard
    +-- MotionArbiter
            |
            v
RealFollowExecutor
    +-- vx <= 0.10 m/s
    +-- vy  = 0
    +-- |wz| <= 0.30 rad/s
    +-- each Move <= 0.10 s
```

## 新增组件

- `app/motion/supervised_loop.py`
  - 统一接收 UWB、LiDAR、外部风险和人工接管状态。
  - 每周期重新计算 Follow、LiDAR 和仲裁结果。
  - UWB/LiDAR 输入异常立即失效关闭。
  - 任意 STOP 通过执行器清除 resume 授权。
- `app/providers/unitree/phase7_input_stream.py`
  - 使用已初始化的 Unitree `ChannelFactory` 创建 UWB 和 `cloud_base` reader。
  - 不重复创建 CycloneDDS participant，避免 UDP 7400 冲突。
  - 不创建直接 DDS publisher。
- `app/providers/unitree/pointcloud_decoder.py`
  - 将 Unitree `PointCloud2` 安全解码为有限 XYZ 点。
- `tools/go2_supervised_follow_phase7_2c.py`
  - 默认仅配置检查，不初始化 SDK。
  - 真实模式需要完整环境 Gate、风险 JSONL、新会话三重确认和会话内 `RESUME`。
  - `STOP`/`EXIT` 进入人工抢占。

## 安全行为

### 每周期重新检查

控制周期固定为 0.20 秒（5 Hz），单次运动请求固定为 0.10 秒。每次发送前重新检查：

- UWB 样本及年龄；
- LiDAR 点云及年龄；
- 外部风险心跳；
- 跌倒事件锁存；
- 人工接管状态；
- 人工 resume 授权。

### 抢占优先级

沿用已冻结的 `MotionArbiter`：

```text
EMERGENCY / FALL
>
MANUAL
>
LIDAR_STOP
>
FOLLOW
```

### 不自动恢复

以下任一情况都会停车并清除 resume：

- `FALL_CONFIRMED`；
- 外部风险心跳缺失或超过 2 秒；
- 人工 `STOP`；
- LiDAR STOP、过期、未知 frame 或解码失败；
- UWB 缺失、无效或超过 1 秒；
- 执行异常或程序退出。

输入恢复后仍保持 `RESUME_REQUIRED`，必须重新输入 `RESUME`，并且当周期必须已达到 FOLLOW 安全状态，才为下一安全周期授权。

### 风险事件防重放

风险 JSONL 在程序启动前已有的内容不会刷新新会话心跳。仅接受启动后追加的事件。文件被截断、无法读取或出现非法事件时进入紧急停止。

## 固定参数

```text
UWB bearing source       orientation_est
UWB bearing unit         radians
UWB bearing sign         +1
UWB zero offset          +0.55 rad

follow back distance     1.50 m
follow right offset      0.50 m

LiDAR SLOW               1.40 m
LiDAR STOP               0.80 m
LiDAR roi_min_z          -0.25 m

control frequency        5 Hz
Move slice               0.10 s
vx max                   0.10 m/s
vy max                   0.00 m/s
|wz| max                 0.30 rad/s
```

## 验证结果

专项测试覆盖：

- CLEAR 下单周期速度发送；
- UWB stale 停车并清除 resume；
- LiDAR SLOW 限速；
- LiDAR STOP 抢占；
- `FALL_CONFIRMED` 抢占并锁存；
- 人工接管抢占；
- 风险心跳缺失关闭；
- 非法 UWB 关闭；
- 风险文件旧内容不作为新心跳；
- 风险文件截断进入 emergency；
- 三重现场确认；
- 默认入口不初始化 SDK。

执行结果：

```text
Focused tests            60 passed
Full repository tests    418 passed
Windows compileall       PASS
Ubuntu 20.04 Python 3.8  compileall PASS
Default tool invocation  SDK not initialized; real motion disabled
```

## 现场运行 Gate

本报告不批准自动开始真实连续伴随。后续现场运行必须另行确认：

1. 空旷场地、安全员和原厂遥控器就位；
2. App 伴随关闭；
3. 外部风险模块持续追加新鲜 `NON_FALL` 心跳；
4. 真实 UWB 和 `cloud_base` 均已达到安全状态；
5. 三重启动确认完成；
6. 屏幕显示 `FOLLOW + RESUME_REQUIRED` 后，人工输入 `RESUME`；
7. 初次会话限制在短时、小场地、`vx <= 0.10 m/s`。

## 当前 Gate

```text
Phase 7.2-C implementation    PASS
Phase 7.2-C live gate         READY_FOR_SEPARATE_APPROVAL
Continuous real follow        CLOSED
Automatic obstacle avoidance  NOT IMPLEMENTED
SLAM / Nav2                   FROZEN
```
