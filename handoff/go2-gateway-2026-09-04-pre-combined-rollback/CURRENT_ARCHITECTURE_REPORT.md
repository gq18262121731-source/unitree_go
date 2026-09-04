# Go2 机器狗应用端当前架构报告

更新时间：2026-07-20

## 项目定位

`go2-gateway` 当前已经从单纯的机器人控制网关，推进为可被智慧养老主系统 `health_new` 调度的 Go2 EDU 执行服务。

核心链路：

```text
camera-service
  -> health_new
  -> go2-gateway /api/events/fall
  -> task_manager 生成 confirm_fall
  -> Go2 移动、摄像头取证、语音询问状态
  -> health_new 查询或接收回调
```

当前没有优先开发 SLAM、复杂路径规划或机器人算法研究。移动策略仍是第一阶段固定点短距离动作计划。

## 当前代码结构

```text
app/gateway/       Go2Gateway，统一封装底层 adapter/SDK 能力
app/adapters/      MockGo2Adapter 与 UnitreeGo2Adapter
app/services/      状态、运动、摄像头、语音、任务、回调服务
app/task_manager/  任务接收、查询、状态摘要边界
app/event/         health_new 事件入口
app/api/           FastAPI 路由注册
demo/              跌倒事件演示脚本和 health_new 回调接收演示脚本
tests/             Mock 模式自动化测试
```

## 已实现的 Go2 能力

- 连接管理：服务启动时通过 `Go2Gateway.connect()` 初始化 adapter/SDK，并支持 HTTP 触发重连；真实 adapter 关闭时会清理 SDK client、视频 client、状态缓存和 subscriber 引用。
- 连接失败可观测性：`GET /api/connection` 和 `GET /api/readiness` 返回 `last_error`，重连失败走结构化 `SDK_NOT_INITIALIZED` 响应。
- 请求校验错误：FastAPI/Pydantic 校验失败统一返回 `INVALID_REQUEST`，并携带 `requestId` 与 `data.errors` 字段。
- 状态读取：`GET /api/status`、`GET /api/robot/status`、`GET /api/connection`；简洁状态会在任务运行时顶层返回 `task_id`、`status`、`revision`、`step`、`progress`、`voice`、`elder_id`、`location`、`confidence`、`source_event_id`、`camera_id`、`external_task_id`。
- 调度前检查：`GET /api/readiness`，用于判断机器人是否可接新任务；跌倒事件和目标移动入口也会在创建任务前执行同样的就绪保护。
- 运动控制：stand、sit、lie-down、stop、emergency-stop、短距离 move。
- 摄像头：JPEG 快照与 MJPEG 流。
- 语音：跌倒确认任务会进入语音询问状态，并记录默认询问语和老人反馈结果；支持通过 `GO2_VOICE_PROMPT_URL` 对接 HTTP 语音播放桥；`/api/voice/status` 和 `/api/preflight` 会暴露语音桥是否 ready、当前模式和下一步配置建议。
- 事件任务：`POST /api/events/fall` 接收跌倒事件并生成 `confirm_fall` 任务；`POST /api/tasks/confirm-fall` 供 `health_new` 按机器人任务语义直接派发同一类任务；摄像头事件 ID 支持 `source_event_id`、`sourceEventId`、`event_id`、`eventId`、`camera_event_id`、`cameraEventId` 并统一归一到 `source_event_id`；`GET /api/events/fall/{source_event_id}` 可按摄像头事件 ID 反查接收状态和映射任务；重复事件可后补 `callback_url` 并推送当前任务状态。
- 外部任务号：`external_task_id` 可作为 `health_new` 幂等/对账键；重复发送同一外部任务号会返回已有机器人任务，`GET /api/tasks/external/{external_task_id}` 可从主系统任务号反查内部任务。
- 状态反馈：任务可被查询，也可通过 `HEALTH_NEW_CALLBACK_URL` 或事件级 `callback_url` 回传给 `health_new`。
- 回调有序处理：任务状态、结果、timeline 和 callback payload 均包含单调递增 `revision`；回调 payload 顶层包含 `elder_id`、`location`、`confidence`、`source_event_id`、`camera_id` 和 `external_task_id`；回调发送使用单 worker FIFO 队列，便于 `health_new` 抵抗异步回调乱序。
- 回调投递状态：`/api/feedback/status` 和 `/api/robot/feedback/status` 可查询回调配置、队列积压、成功/失败/丢弃计数和最近错误。
- 回调补发：`POST /api/tasks/{task_id}/feedback/replay`、`/api/tasks/external/{external_task_id}/feedback/replay` 及对应 `/api/robot/...` 兼容路径可将当前任务快照重新入队发送到任务级 callback、全局 callback 或请求体指定的 `callback_url`，用于 `health_new` 回调短暂不可用后的状态恢复。
- 关闭保护：网关关闭时会先把活动任务标记为 `cancelled` / `gateway_shutdown`、flush 最终回调、等待任务 worker 收尾，再关闭机器人连接。
- 语音反馈保护：`voice-result` 仅允许写入 `confirm_fall`；支持运行中提前写回和正常完成后补录，但会拒绝非跌倒确认、`cancelled` / `failed` 任务，返回 `TASK_STATE_CONFLICT`。
- 结果保存：任务生命周期会追加写入 JSONL 审计日志，默认路径 `logs/task-events.jsonl`，并可通过 `/api/tasks/audit-log` 查询最近审计。
- 任务列表摘要：`/api/tasks/summary` 和 `/api/robot/tasks/summary` 返回适合 `health_new` 事件中心/机器人页直接展示的轻量任务列表，原始 `/api/tasks` 仍保留完整任务结构。
- 位置移动：支持默认固定点动作计划，也支持通过 `GO2_LOCATION_MOTION_PLANS_JSON` 配置现场动作；`/api/locations/resolve` 可把 `卧室`、`卫生间`、`客厅`、`厨房` 等中文位置解析为固定点。
- 任务中断：运行中的任务可以取消，取消时会触发机器人 stop 并记录审计日志。
- 当前任务：支持查询当前运行任务摘要，便于 `health_new` 前端和调度器快速判断。
- 运行健康摘要：`GET /health` 返回 dispatch ready、机器人在线/陈旧/忙碌标记、当前任务摘要和回调队列计数，便于比赛现场快速排障。
- 非运动预检：`GET /api/preflight` 汇总连接、readiness、相机状态、语音桥状态、回调队列和 capability URL；该接口不主动抓取相机帧、不触发运动，适合真实 Go2 上场前检查。
- 任务时间线：支持查询步骤和事件日志，供前端展示机器人响应过程。
- 任务进度：任务状态、结果、timeline、`/api/status` 和 callback payload 均返回 `progress`，并在顶层返回 `elder_id`、`location`、`location_resolution`、`confidence`、`source_event_id`、`camera_id`、`external_task_id`，便于前端 stepper/progress bar 和告警上下文展示。
- 任务结果：支持轻量查询确认结果、机器人视频地址、语音反馈、语音播放投递状态、来源事件和结构化失败字段。
- 能力发现：支持查询当前网关、控制、视频、语音、事件入口、任务 URL、来源字段、反馈状态和导航边界。
- 位置发现：支持查询默认和配置化固定点动作计划，供 `health_new` 派发任务前选择 location。
- 主系统对接：新增 `HEALTH_NEW_INTEGRATION.md`，明确 readiness、跌倒事件、任务查询、回调、语音反馈和视频合同。
- 离线合同验证：`scripts/verify_health_new_contract.py` 可在 Mock 模式下验证主调度链路，并启动本地 HTTP 接收器验证 `/health`、`/api/preflight`、`/api/capabilities`、语音状态发现、中文位置别名解析、推荐 target-move 路径、terminal 回调、反馈投递状态、snake_case/camelCase 请求兼容、语音结果回调、最近审计查询和 Go2 摄像头失败合同。
- 综合安全验收：`scripts/verify_release.py` 会执行 Python 编译、`health_new` 合同脚本和全量 Mock 测试；可选 `--running-base-url` 对已启动网关追加非运动预检。
- 非运动 HTTP 预检：`scripts/verify_preflight.py` 可对已启动网关执行跨平台预检；`--require-ready` 用于派发前检查，`--allow-readonly` 用于真实 Go2 只读检查。
- 运行中网关验收：`scripts/verify_running_gateway_fall_loop.py` 可对已启动的 Mock 或真实 Go2 网关执行 HTTP 端到端跌倒闭环检查，默认覆盖 `/api/tasks/confirm-fall` 任务派发，也可通过 `--dispatch-mode event` 覆盖 `/api/events/fall`；同时检查 `/health`、`/api/preflight`、`/api/capabilities`、摄像头状态、反馈状态和 callback。

## 已封装的 SDK 接口

业务代码不直接调用 Unitree SDK，统一经过：

```text
Go2Gateway
  -> RobotAdapter
  -> MockGo2Adapter / UnitreeGo2Adapter
```

稳定网关方法：

```text
connect()
get_status()
stand()
sit()
lie_down()
stop()
move()
get_camera()
```

真实 SDK 相关调用集中在 `app/adapters/unitree_adapter.py`，包括：

- `ChannelFactoryInitialize`
- `SportClient`
- `VideoClient`
- 高层动作与移动命令
- 前置摄像头 JPEG 获取

## 当前通信方式

- 对 `health_new`：HTTP JSON API。
- 对前端：HTTP JSON、JPEG、MJPEG。
- 对 Go2：真实模式走 Unitree SDK DDS/RPC；Mock 模式走本地模拟 adapter。
- 对 `health_new` 状态回传：后台线程 HTTP POST，支持 bearer token、超时、重试和事件级 callback URL。

## 当前控制能力

已支持：

- `POST /api/robot/stand`
- `POST /api/robot/sit`
- `POST /api/robot/lie-down`
- `POST /api/robot/stop`
- `POST /api/robot/emergency-stop`
- `POST /api/robot/move`
- `GET /api/capabilities`
- `GET /api/robot/capabilities`
- `GET /api/locations`
- `GET /api/robot/locations`
- `POST /api/connection/reconnect`
- `POST /api/robot/connection/reconnect`
- `POST /api/tasks/target-move`
- `POST /api/robot/tasks/target-move`
- `GET /api/events/fall/{source_event_id}`
- `GET /api/robot/events/fall/{source_event_id}`
- `GET /api/tasks/summary`
- `GET /api/robot/tasks/summary`
- `GET /api/tasks/external/{external_task_id}`
- `GET /api/robot/tasks/external/{external_task_id}`
- `GET /api/tasks/active`
- `GET /api/robot/tasks/active`
- `GET /api/tasks/{task_id}/status`
- `GET /api/robot/tasks/{task_id}/status`
- `GET /api/tasks/{task_id}/timeline`
- `GET /api/robot/tasks/{task_id}/timeline`
- `GET /api/tasks/audit-log`
- `GET /api/robot/tasks/audit-log`
- `GET /api/feedback/status`
- `GET /api/robot/feedback/status`
- `GET /api/tasks/{task_id}/result`
- `GET /api/robot/tasks/{task_id}/result`
- `POST /api/tasks/{task_id}/cancel`
- `POST /api/robot/tasks/{task_id}/cancel`

保护机制：

- 速度和时长上限。
- control lock 防止并发运动。
- watchdog。
- shutdown stop。
- 离线、状态陈旧、SDK 未初始化时拒绝运动。
- `GO2_CONTROL_ENABLED=false` 只读保护；直接运动命令、`/api/readiness`、`/api/tasks/confirm-fall`、`/api/events/fall` 和 `/api/tasks/target-move` 均返回 `CONTROL_DISABLED`，不会创建运动任务。
- `GO2_TASK_AUDIT_ENABLED=true` 时保存任务生命周期日志。

## 当前视频能力

已支持：

- `GET /api/camera/status`
- `GET /api/camera/snapshot`
- `GET /api/robot/camera/snapshot`
- `GET /api/camera/stream`
- `GET /api/robot/camera/stream`

`/api/camera/stream` 是第一阶段 MJPEG 流，底层仍通过 gateway 获取 Go2 JPEG 帧。后续如果接入 WebRTC/RTSP，只需要替换 `GO2_CAMERA_STREAM_URL` 或摄像头服务实现。

## 当前状态读取能力

`GET /api/status` 面向 `health_new` 的简洁状态：

```json
{
  "robot_id": "go2-edu-001",
  "online": true,
  "ip": "192.168.123.161",
  "battery": 78,
  "mode": "idle",
  "action": "mock-locomotion",
  "task": null,
  "task_id": null,
  "status": null,
  "revision": null,
  "step": null,
  "camera": true,
  "voice": null,
  "error": null,
  "last_error": null
}
```

`GET /api/readiness` 面向主系统调度：

```json
{
  "ready": true,
  "online": true,
  "initialized": true,
  "state_stale": false,
  "busy": false,
  "active_task": null,
  "error": null,
  "last_error": null
}
```

## 跌倒确认任务

入口：

```text
POST /api/events/fall
POST /api/robot/events/fall
```

任务步骤：

```text
receive_event
moving
arrived
robot_camera
voice_check
finished
```

任务状态：

```text
waiting
running
moving
arrived
checking
finished
failed
cancelled
```

已支持 `source_event_id` 幂等。同一个摄像头事件重复上报时返回已有任务，不重复触发机器人运动；如果重放请求提供了新的 `callback_url`，且原任务没有事件级回调地址，网关会绑定该地址并发送当前任务状态。

## 与目标架构的差异

已对齐：

- `gateway`：已有统一 Go2Gateway，业务层不直接调用 SDK。
- `robot_service`：状态、运动、摄像头、语音服务已分层。
- `task_manager`：已有跌倒确认任务、目标移动任务、任务查询与状态摘要。
- `event`：已有 health_new 跌倒事件接收层。
- `api`：已有 HTTP 对外接口。
- `demo`：已有跌倒事件演示脚本和 `health_new` callback 接收演示脚本；跌倒演示会输出进度、终态 result 和语音回写后的 result，回调接收器会输出 progress 与失败字段。

仍有缺口：

- 真实 Go2 硬件环境未完成本地验证。
- 语音识别仍未实现；语音播放已预留 HTTP 播放桥，真实机器人扬声器或音频桥仍需现场联调。
- MJPEG 是第一阶段视频输出，非低延迟 WebRTC 视频。
- 跟随任务和巡检任务目前只保留接口合同，返回 `TASK_NOT_SUPPORTED`。
- 目标移动仍是固定动作计划或配置化短动作计划，不是自主导航。

## 当前修改计划

P0：

- 保持 `/api/readiness`、`/api/events/fall`、任务状态查询、状态回调稳定。
- 对接 `health_new` 时优先使用 `/api/tasks/{task_id}/result` 或回调 payload 里的 `result`、`step`、`finished` 字段。
- 在真实 Go2 网络恢复后按只读、摄像头、站立/停止、小步移动、运行中跌倒闭环脚本顺序验收。
- 让 `health_new` 联调方先按 `HEALTH_NEW_INTEGRATION.md` 和 `scripts/verify_health_new_contract.py` 固定接口合同。

P1：

- 在真实机器人或音频桥上验证 `GO2_VOICE_PROMPT_URL` 播放链路。
- 将固定位置动作计划外置为现场配置。
- 给 `health_new` 前端提供机器人任务状态与 MJPEG 视频展示说明。

P2：

- 跟随任务、巡检任务、自主导航、地图能力。

## 当前验证结果

```text
pytest -q
105 passed
```

```text
python -m py_compile app\services\robot_service.py app\services\status_service.py tests\test_status.py
passed
```

```text
python scripts\verify_health_new_contract.py
health_new contract verification passed
```

```text
GO2_MODE=mock python -m uvicorn app.main:app --host 127.0.0.1 --port 8090 --workers 1
python scripts\verify_running_gateway_fall_loop.py --base-url http://127.0.0.1:8090 --need-help --external-task-id health-task-demo-001 --timeout-seconds 20 --poll-seconds 0.2
running gateway fall loop verification passed
```

真实 Go2 验收仍需要物理网络和机器人在线环境，不能仅凭 Mock 测试宣称完成。
