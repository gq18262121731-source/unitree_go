# PC 社区端真实 Go2 伴随模式试运行 Gate

## 本轮范围

只验收 `IDLE → START → FOLLOWING → STOP → IDLE`。跌倒恢复、`WAIT_RESUME`、VLM、视频和事件时间线都不作为 START Gate。

PC 只能调用 health_new：

```text
GET  /api/v1/elders/{elder_id}/robot-companion/status
POST /api/v1/elders/{elder_id}/robot-companion/start
POST /api/v1/elders/{elder_id}/robot-companion/stop
```

health_new 再代理到 `go2-gateway :8090` 的 Companion Lifecycle API。PC 不得直连 8090、`SportClient` 或 `Move`。

## 启动前配置

health_new 必须显式配置：

```dotenv
ROBOT_GATEWAY_BASE_URL=http://127.0.0.1:8090
COMPANION_BOUND_ELDER_ID=<现场老人 ID>
COMPANION_ROBOT_ID=go2_edu_01
COMPANION_ROBOT_NAME=小康01
COMPANION_ROBOT_MODEL=Go2 EDU
```

go2-gateway 真机配置必须由现场负责人复核，不能沿用 `.env.example` 的 mock/禁用默认值：

```dotenv
GO2_MODE=real
GO2_CONTROL_ENABLED=true
GO2_READ_ONLY_MODE=false
GO2_MAX_VX=0.30
GO2_MAX_WZ=0.30
FOLLOW_SIMULATION=false
FOLLOW_EXECUTION_ENABLED=true
PHASE7_MOTION_EXECUTION_ENABLED=true
PHASE7_REQUIRE_EXTERNAL_RISK_FEED=true
GO2_COMPANION_RISK_EVENTS_PATH=<health_new 风险事件 JSONL 的绝对路径>
```

伴随配置同时保持：`walk_min_mps=0.20`、`vx_max_mps=0.30`、`wz_max_radps=0.30`、`vy=0`。若状态接口的 `configuration.motion_limits_aligned` 不是 `true`，禁止点击 START。

## G0–G9

| Gate | 验收条件 |
|---|---|
| G0 | PC 经 health_new 读取真实 Companion status |
| G1 | 页面显示正确老人、Go2 名称和绑定状态 |
| G2 | Go2、DDS、UWB、LiDAR、风险锁、接管、控制权和速度检查通过 |
| G3 | PC START 只请求 health_new，按钮保持 loading |
| G4 | Gateway 明确返回 `FOLLOWING` 后页面才显示“正在陪伴” |
| G5 | 老人慢走 2–3 步时 Go2 正常建立步态 |
| G6 | 页面按真实 motion output 显示“正在移动/已停稳” |
| G7 | PC STOP 请求 health_new，允许安全重复 |
| G8 | Gateway 执行 StopMove，真实 Go2 及时停稳 |
| G9 | Gateway 和页面均回到 `IDLE` |

## 现场顺序

在约 3×3m 空旷区域，老人携带 UWB 目标，初始距离 1.5–2m。先确认 STOP 操作员和急停手段就位，再做十几秒短测：START、前行 2–3 步、停止、再前行 2–3 步、PC STOP。随后在 `FOLLOWING` 时单独尝试一次旧 Move/动作接口，预期为 `409 CONTROL_BUSY`。

任何检查失败、状态不同步或 STOP 未迅速回到 `IDLE`，都立即终止本轮，不进入跌倒恢复测试。
