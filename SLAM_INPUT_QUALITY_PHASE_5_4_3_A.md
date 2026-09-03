# Phase 5.4.3-A — `cloud_base` 离线 LaserScan 输入质量报告

日期：2026-07-26  
模式：离线、只读证据分析  
结论：**转换链路 PASS；单帧 LaserScan 质量暂定 FAIL；Phase 5.4 继续 HOLD**

## 1. 安全边界

本轮只读取以下既有文件：

- `phase542_motion_capture.json`
- `phase542_motion_clouds.npz`
- `phase542_motion_analysis.json`

本轮没有：

- 连接 Go2；
- 启动 ROS2 节点；
- 发布 PointCloud2、LaserScan 或 TF；
- 启动 SLAM、Nav2；
- 调用运动接口、`SportClient`、`move()` 或 `cmd_vel`；
- 修改设备、Mock Provider 或 `health_new`。

## 2. 离线证据完整性

| 项目 | 结果 |
|---|---:|
| 采样时长 | 60.080 s |
| raw/base 同时间戳消息 | 922 / 922，匹配率 100% |
| 保存的 raw/base 点云对 | 308 |
| `cloud_base` 有限点总数 | 425,527 |
| `cloud_base` 每帧点数中位数 | 1,382 |
| 运动路径长度 | 6.323 m |
| 累计转动 | 15.535 rad |
| 报告为运动的比例 | 50.1% |
| 固定变换全体平均残差 | 0.090 µm |
| 固定变换最大残差 | 1.442 µm |

平移、转动和运动占比三项运动 Gate 均通过。因此，现有数据足以比较离线投影参数；它仍不能代替在线 RViz 验证或官方外参来源。

## 3. 输入分布

`cloud_base` 的 Z 分布中位数约为 `-0.346 m`，25%/75% 分位约为 `-0.389/-0.267 m`。大量点位于地面附近。把整个点云直接压成 LaserScan 会把地面误当成障碍物，因此参数搜索没有使用地面主带，而只测试了较高的障碍物切片。

本次捕获的 XY 距离最大约 `5.009 m`，99% 分位约 `3.330 m`。因此 `range_max=5 m` 是当前证据支持的上限；`8 m` 在这份数据中没有额外信息。

## 4. 搜索方法

对 480 组参数逐帧执行与 `pointcloud_to_laserscan` 核心行为等价的离线计算：

1. 按 Z 高度和 XY 距离筛选点；
2. 按方位角分桶；
3. 每个角度桶保留最近距离；
4. 统计有效束、空洞、最长连续空洞、相邻束距离连续性；
5. 分别统计运动和静止相邻帧的束重合率与距离变化。

搜索范围：

- 高度窗口：8 组，范围从 `[-0.25, 0.05] m` 到 `[-0.10, 0.40] m`；
- `range_min`：`0.3 / 0.4 / 0.5 m`；
- `range_max`：`5 / 8 m`；
- 角分辨率：`0.5° / 1.0°`；
- 视场：`360° / 270° / 240° / 220° / 180°`。

排序分数只用于同一份捕获内的候选比较，不是 SLAM 验收标准。

## 5. 最佳离线候选

```yaml
target_frame: base_link
min_height: -0.250
max_height: 0.050
angle_min: -1.919862177       # -110°
angle_max: 1.919862177        # +110°
angle_increment: 0.017453293  # 1°
range_min: 0.300
range_max: 5.000
```

质量结果：

| 指标 | 结果 |
|---|---:|
| 高度/距离/视场筛选后点数 | 215.60 点/帧 |
| 有效 LaserScan 束 | 53.11 束/帧 |
| 220° 视场占用率 | 24.14% |
| 空洞率 | 75.86% |
| 平均最长空洞 | 69.94° |
| 相邻有效束距离差 ≤ 25 cm | 87.27% |
| 静止帧束 Jaccard 重合率 | 6.96% |
| 静止帧重合束距离变化中位数 | 8.89 cm |
| 运动帧束 Jaccard 重合率 | 14.41% |
| 运动帧重合束距离变化中位数 | 32.35 cm |

相邻有效束的局部几何连续性尚可，但有效束太少、跨帧落入相同角度桶的稳定性很低。单帧 3D 点云切片还不能形成稳定、致密的 2D SLAM 输入。

## 6. 参数对比

| 高度窗口 | 视场 | 分辨率 | 有效束/帧 | 占用率 | 最长空洞 | 静止束重合率 |
|---|---:|---:|---:|---:|---:|---:|
| `[-0.25, 0.05] m` | 360° | 1.0° | 53.44 | 14.85% | 184.87° | 6.91% |
| `[-0.25, 0.05] m` | 220° | 1.0° | 53.11 | 24.14% | 69.94° | 6.96% |
| `[-0.25, 0.05] m` | 220° | 0.5° | 82.83 | 18.83% | 70.40° | 4.59% |
| `[-0.20, 0.30] m` | 220° | 1.0° | 48.59 | 22.09% | 70.80° | 6.97% |

把视场缩到前向 220° 能去掉没有观测价值的后方空区，但不能解决时间上的稀疏性。把分辨率提高到 0.5° 会增加有效桶数，同时让相邻帧落入同一桶的比例进一步降低。

## 7. 暂定质量 Gate

这些阈值是当前项目为了防止过早进入 SLAM 设置的工程 Gate，不是 SLAM Toolbox 官方标准。

| Gate | 阈值 | 结果 |
|---|---:|---|
| 平均占用率 | ≥ 25% | FAIL，24.14% |
| 平均最长空洞 | ≤ 60° | FAIL，69.94° |
| 静止束 Jaccard | ≥ 35% | FAIL，6.96% |
| 静止重合束距离变化 | ≤ 0.15 m | PASS，0.089 m |

总判定：**1 PASS / 3 FAIL**。

## 8. 外参产物

生成了 `hardware_observed_lidar_extrinsic.yaml`，并明确标记：

```yaml
source: observed_from_cloud_base
confidence: experimental
official_calibration: false
publish_tf: false
parent_frame: base_link
child_frame: utlidar_lidar
```

该矩阵满足：

```text
p_base = R × p_lidar + t
```

它只证明固件输出的 `cloud` 与 `cloud_base` 之间存在稳定固定变换，不是官方标定来源，禁止作为已批准 TF 发布。

## 9. 结论与下一 Gate

### 已通过

- 现有运动数据完整，可用于离线参数比较；
- `cloud_base → LaserScan` 数学转换链路可复现；
- `cloud_base` 的固定变换在运动数据中仍稳定；
- 已得到一个可供在线复验的保守参数候选。

### 未通过

- 单帧 LaserScan 稠密度和跨帧角度桶稳定性不足；
- 没有 rosbag，不能离线运行原生 ROS2 转换节点并在 RViz 中复现；
- 仍未找到可追溯的官方 `base_link → utlidar_lidar` 外参来源。

### 状态

```text
Phase 5.4.3-A offline conversion: PASS
Single-frame LaserScan quality: PROVISIONAL FAIL
Phase 5.4 HOLD: ACTIVE
Phase 5.5 SLAM: NOT AUTHORIZED
```

机器人充电后只允许进入 **Phase 5.4.3-B 在线只读复验**：

1. 使用同一候选参数运行原生只读 PointCloud2→LaserScan；
2. 在 RViz 同屏检查 `cloud_base` 与 LaserScan；
3. 区分大空洞来自 L1 单帧扫描模式、环境遮挡还是转换参数；
4. 如果仍稀疏，再设计带时间戳约束的短窗点云累积方案；禁止直接插值填洞；
5. 外参来源未解决前，不发布猜测 TF，不进入 SLAM/Nav2。

