# Phase 5.4.8 只读 IMU 语义采集规程

## 安全边界

- 不启动 Point-LIO、SLAM、Nav2。
- 不运行 `move()`、SportClient、LowCmd、`cmd_vel` 或任何程序控制。
- 只记录 `/utlidar/cloud`、`/utlidar/imu`、`/utlidar/robot_odom`。
- 倾斜保持实验必须让机器人关节保持安全、不承受强行扭转；使用稳定支撑，
  防止机器人或 L1 滑落。
- yaw 实验只能由人工遥控低速旋转，或在稳定转台上人工旋转；不得强行扭动
  站立机器人的腿或躯干。
- 动作以“从机器人后方向前看”和“从上方向下看”为统一观察约定。

## 开始前

先确认 Go2 Ethernet 已恢复、机器人周围清空、L1 正常工作，然后运行：

```bash
bash /home/go2/phase548_tools/phase548_preflight.sh
```

只有看到：

```text
go2_ping=PASS
forbidden_process_gate=PASS
/utlidar/cloud=PASS
/utlidar/imu=PASS
/utlidar/robot_odom=PASS
phase548_preflight=PASS
```

才可以继续。

## 分段采集

每段独立录制，避免依赖人工记忆时间点。

### 1. 水平静止

机器人放在后续各动作使用的同一稳定位置，尽量保持机身水平，静止 30 秒：

```bash
bash /home/go2/phase548_tools/phase548_record_segment.sh level_static 30
```

### 2. 俯仰保持

先把机器人安全支撑到指定姿态，再开始录制。录制期间保持完全静止。

机头比机尾低约 15～20°：

```bash
bash /home/go2/phase548_tools/phase548_record_segment.sh pitch_nose_down_hold 20
```

机头比机尾高约 15～20°：

```bash
bash /home/go2/phase548_tools/phase548_record_segment.sh pitch_nose_up_hold 20
```

### 3. 横滚保持

机器人左侧比右侧低约 15～20°：

```bash
bash /home/go2/phase548_tools/phase548_record_segment.sh roll_left_down_hold 20
```

机器人右侧比左侧低约 15～20°：

```bash
bash /home/go2/phase548_tools/phase548_record_segment.sh roll_right_down_hold 20
```

### 4. yaw 正反向

从上向下看，人工遥控低速旋转；建议约 10 秒旋转、其余时间静止。

逆时针：

```bash
bash /home/go2/phase548_tools/phase548_record_segment.sh yaw_ccw_manual 20
```

顺时针：

```bash
bash /home/go2/phase548_tools/phase548_record_segment.sh yaw_cw_manual 20
```

## 完成检查

```bash
ls -1 /home/go2/go2_validation/phase548
```

必须有 7 个独立 bag 目录及对应 manifest/info 文件。完成后停止桥接，不运行
Point-LIO。
