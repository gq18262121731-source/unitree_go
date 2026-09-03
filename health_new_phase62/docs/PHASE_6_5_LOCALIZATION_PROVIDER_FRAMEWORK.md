# Phase 6.5 Localization Provider Framework

```text
base commit  b91f39c
branch       feature/localization-provider-framework-v1
```

## 1. 阶段结论

```text
Telemetry       READY（未修改）
Localization    HOLD
source          null
state           UNAVAILABLE
Map             未进入
Navigation      未进入
Motion          未进入
```

Phase 6.5 只新增未接线的定位能力框架。它不读取真实 pose，不订阅
ROS2/DDS，不发布 TF，不启动 SLAM，也不暴露 HTTP API。当前运行中的
health_new 和 Phase 6.2 Telemetry 行为不变。

## 2. 架构设计

```text
未来已验证定位源
  ├─ Go2 internal USLAM
  ├─ Point-LIO
  └─ other ROS2 localization
             |
             v
LocalizationAdmissionController
             |
             v
LocalizationProvider
  ├─ get_status()
  ├─ get_pose()
  └─ health_check()
```

当前唯一可实例化的正式实现是
`UnavailableLocalizationProvider`。它固定返回：

```json
{
  "provider": "localization",
  "state": "UNAVAILABLE",
  "available": false,
  "source": null,
  "pose": null,
  "frame": null,
  "map_id": null,
  "confidence": null,
  "timestamp": null,
  "reason": "NO_LOCALIZATION_SOURCE"
}
```

`LocalizationAdmissionController` 的 `validated_sources` 默认是空集合。候选样本
不能自行声明可信；只有未来经过独立准入并由组合根显式注入 allowlist 的来源
才能进入观察窗口。

## 3. 接口定义

`LocalizationProvider` 是只读 Protocol：

```python
class LocalizationProvider(Protocol):
    def get_status(self) -> LocalizationState: ...
    def get_pose(self) -> LocalizationPose | None: ...
    def health_check(self) -> LocalizationHealth: ...
```

接口中刻意不存在：

```text
start / stop / reset
save_map / load_map
publish_tf
move / cmd_vel / SportClient / LowCmd
```

本阶段没有新增或修改 REST、WebSocket 或前端接口。

## 4. 状态机

```text
UNAVAILABLE
     |
     | 可信 source 的完整样本通过单次 Gate
     v
OBSERVING
     |
     | 连续样本达到准入窗口
     v
READY
     |
     | stale / rollback / source、frame、map变化 / pose失败
     v
UNAVAILABLE
```

只有 `READY` 满足：

```text
available=true
get_pose()!=null
health_check().healthy=true
```

`OBSERVING` 不暴露 pose。任何 Gate 失败都会清空连续样本计数和候选身份，
恢复 `UNAVAILABLE`，后续必须重新经过完整观察窗口。

## 5. 准入条件

未来候选样本必须同时满足：

1. source 已经过独立验证，并存在于构造时显式注入的 allowlist；
2. position 和 orientation 全部为有限数值；
3. orientation 是容差内单位四元数；
4. source timestamp 带时区、无回拨、不过期且不异常超前；
5. frame 非空且连续窗口内不改变；
6. 正式 `map_id` 非空且连续窗口内不改变；
7. confidence 为有限的 `[0, 1]` 值；
8. source 在连续窗口内不改变。

框架不接受 `robot_online`、battery、Telemetry 或 `/odom` 作为输入，因此不能
从“机器人在线”或“里程计有数据”推导定位 READY。

## 6. 与 Telemetry 的边界

| 能力 | Telemetry | LocalizationProvider |
| --- | --- | --- |
| 机器人在线 | 负责 | 不推导 |
| DDS/ROS2健康 | 负责 | 不推导 |
| LiDAR/IMU/Odom在线 | 负责 | 不推导 |
| 地图中的 pose | 不负责 | 仅 READY 后提供 |
| 定位新鲜度/质量 | 不负责 | 严格 Gate |

Phase 6.5 新包未被 `backend/main.py`、现有路由或 Telemetry 服务导入。此隔离
保证 Phase 6.1/6.2 契约与 Mock 演示路径保持不变。

## 7. 未来接入点

### Go2 internal USLAM

仍只是第一调查方向。必须先获得官方接口、frame、map identity 和质量语义，
并观察到真实只读输出，才能构造 candidate。

### Point-LIO

继续 HOLD。`/utlidar/imu` 原始比力语义未通过前，不得构造可信 candidate。

### Other ROS2 localization

必须提供地图身份、可信 frame、source timestamp、pose 和有定义的
confidence。纯 `/odom` 或 `robot_localization` 局部估计不能冒充地图定位。

## 8. 验收结果

测试覆盖：

- 默认不可用；
- 非有限 pose 拒绝；
- stale timestamp 拒绝；
- frame 缺失拒绝；
- `UNAVAILABLE → OBSERVING → READY`；
- READY 后异常样本立即 fail closed；
- timestamp rollback 重置观察窗口。

安全边界：

```text
真实定位接入       NO
TF发布             NO
SLAM/Nav2          NO
Map                NO
运动接口           NO
Mock修改           NO
现有health_new接线 NO
```

Phase 6.5 完成后停止。Localization 继续：

```text
state=UNAVAILABLE
source=null
available=false
```

## 9. 验证记录

```text
Localization framework + Phase 6.2 Telemetry tests  13 passed
Robot status frontend contract                       PASS
Python compileall                                    PASS
Existing runtime imports of backend.localization     0
Control/ROS runtime calls in new code                 0
Existing health_new files modified                    0
Mock files modified                                   0
New dependencies                                      0
```

实际命令：

```powershell
C:\Users\Test1\.conda\envs\health\python.exe -m pytest `
  tests/test_localization_provider_framework.py `
  tests/test_robot_readonly_telemetry_service.py -q

C:\Users\Test1\.conda\envs\health\python.exe -m compileall -q `
  backend/localization

Set-Location frontend/vue-dashboard
npm run test:robot-status
```
