# health_new 对接合同

本文档给 `health_new` 使用，用于把 Go2 EDU 作为事件驱动的具身智能执行端接入智慧养老主系统。

## 调度原则

`health_new` 不直接调用 Unitree SDK，也不直接下发底层运动指令。推荐调度顺序：

```text
GET /api/readiness
  -> POST /api/events/fall
  -> GET /api/tasks/{task_id}/status 或等待 callback
  -> GET /api/tasks/{task_id}/result
  -> POST /api/tasks/{task_id}/voice-result
```

第一阶段只承诺固定点或短距离动作计划，不承诺 SLAM、自主路径规划、地图导航。

## 服务发现与调度前检查

```http
GET /api/capabilities
```

`health_new` 可以通过该接口发现当前支持的事件入口、任务查询 URL、任务来源字段、语音状态 URL、回调状态 URL、固定动作计划边界和未启用的导航能力。

```http
GET /api/preflight
GET /api/readiness
```

`GET /health` is the compact operations probe. It keeps the old service fields and also returns `ready`, robot online/stale/busy flags, `activeTask`, and feedback queue counters. Use it for dashboards and local troubleshooting; use `/api/readiness` as the strict dispatch gate before creating a robot task.
`GET /api/preflight` 是非运动现场预检接口，永远返回 HTTP 200，用于展示连接、readiness、相机状态、语音桥状态、回调队列和 capability URL；它不会主动抓取新的相机帧，也不替代 `/api/readiness` 的派发 gate。

成功响应：

```json
{
  "code": 0,
  "message": "Robot is ready for task dispatch.",
  "data": {
    "ready": true,
    "online": true,
    "initialized": true,
    "control_enabled": true,
    "state_stale": false,
    "busy": false,
    "active_task": null,
    "error": null,
    "last_error": null
  }
}
```

When `GO2_CONTROL_ENABLED=false`, `/api/readiness` returns HTTP 403 with `code=CONTROL_DISABLED`, and task dispatch endpoints reject the request before creating a robot task.

失败响应：

```json
{
  "code": "CONTROL_BUSY",
  "message": "Robot is not ready for task dispatch.",
  "data": {
    "ready": false,
    "busy": true,
    "active_task": {
      "task_id": "task_xxx",
      "task": "confirm_fall",
      "status": "moving"
    },
    "error": "CONTROL_BUSY",
    "last_error": null
  }
}
```

`health_new` 应把 `last_error` 展示给运维或比赛控制台，用于定位 SDK、网卡、DDS、机器人离线等问题。

`POST /api/tasks/confirm-fall`、`POST /api/events/fall`、`POST /api/tasks/target-move` 和兼容路径 `POST /api/robot/tasks/target-move` 在创建任务前也会执行同样的派发前检查。若机器人未初始化、离线、状态陈旧或控制繁忙，接口会直接返回对应错误，不会创建不可执行任务。

请求体或查询参数校验失败时，网关统一返回 HTTP 422：

```json
{
  "success": false,
  "code": "INVALID_REQUEST",
  "message": "Request validation failed.",
  "requestId": "request-id",
  "data": {
    "errors": []
  }
}
```

## 机器人状态

```http
GET /api/status
```

返回面向主系统的简洁状态：

```json
{
  "code": 0,
  "data": {
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
    "progress": null,
    "camera": true,
    "voice": null,
    "error": null,
    "last_error": null
  }
}
```

任务运行中时，`mode` 会变为 `task`，并在顶层返回 `task`、`task_id`、`status`、`revision`、`step`、`steps`、`progress`、`finished`、`voice`、`elder_id`、`location`、`location_resolution`、`confidence`、`source_event_id`、`camera_id`、`external_task_id`，便于机器人状态页直接展示。

## 跌倒事件输入

```http
POST /api/events/fall
Content-Type: application/json
```

推荐使用 snake_case：

```json
{
  "event": "fall_detected",
  "elder_id": "001",
  "location": "bedroom",
  "confidence": 0.95,
  "source_event_id": "camera-fall-001",
  "camera_id": "fixed-camera-01",
  "external_task_id": "health-task-001",
  "callback_url": "http://health-new.local/api/robot/callback"
}
```

兼容 camelCase：

```json
{
  "event": "fall_detected",
  "elderId": "001",
  "location": "bedroom",
  "confidence": 0.95,
  "sourceEventId": "camera-fall-001",
  "cameraId": "fixed-camera-01",
  "externalTaskId": "health-task-001",
  "callbackUrl": "http://health-new.local/api/robot/callback"
}
```

如果 `health_new` 已经把摄像头事件转换成机器人任务，推荐使用语义更明确的任务入口：

```http
POST /api/tasks/confirm-fall
POST /api/robot/tasks/confirm-fall
```

```json
{
  "task": "confirm_fall",
  "elder_id": "001",
  "location": "卧室",
  "confidence": 0.95,
  "source_event_id": "camera-fall-001",
  "camera_id": "fixed-camera-01",
  "taskId": "health-task-001",
  "callback_url": "http://health-new.local/api/robot/callback"
}
```

`/api/tasks/confirm-fall` 和 `/api/events/fall` 最终进入同一个 `confirm_fall` 任务管理器。`taskId` 会作为 `external_task_id` 处理，用于主系统对账、幂等重试和 `GET /api/tasks/external/{external_task_id}` 反查。

`source_event_id` 是幂等键，也兼容 camera-service 常见字段 `sourceEventId`、`event_id`、`eventId`、`camera_event_id`、`cameraEventId`。同一个摄像头跌倒事件重复上报时，网关返回已有任务，不重复触发 Go2 运动。若重放请求携带 `callback_url`，且原任务没有回调地址，网关会补挂该地址并推送一次当前任务状态。
`external_task_id` 是可选的主系统任务编号，用于对账、前端展示、幂等重试和反查内部机器人任务；网关仍会生成自己的内部 `task_id`。重复发送同一个 `external_task_id` 会返回已有机器人任务，不会重复触发 Go2 运动。

## 位置解析

网关当前使用固定点短动作计划，不做 SLAM 或复杂路径规划。`health_new` 可直接发送英文或中文位置，例如 `bedroom`、`卧室`、`卫生间`、`客厅`、`厨房`。派发前可查询：

```http
GET /api/locations
GET /api/locations/resolve?location=卧室
```

`/api/locations/resolve` 会返回归一后的固定点、是否命中已知位置、是否使用 fallback，以及将执行的短动作计划。这样主系统页面可以在派发前展示“卧室 -> bedroom -> default plan”，避免现场口径混乱。

可按摄像头事件 ID 反查接收状态：

```http
GET /api/events/fall/{source_event_id}
GET /api/robot/events/fall/{source_event_id}
GET /api/tasks/external/{external_task_id}
GET /api/robot/tasks/external/{external_task_id}
```

未接收时：

```json
{
  "data": {
    "source_event_id": "camera-fall-001",
    "received": false,
    "task_id": null,
    "task": null
  }
}
```

已接收时，`task_id` 返回对应机器人任务，`task` 返回任务摘要。

## 任务创建响应

```json
{
  "code": 0,
  "message": "Fall confirmation task accepted.",
  "data": {
    "task_id": "task_abc123",
    "taskId": "task_abc123",
    "robotId": "go2-edu-001",
    "task": "confirm_fall",
    "priority": "high",
    "status": "waiting",
    "location": "bedroom",
    "currentStep": null,
    "step": [
      "receive_event",
      "moving",
      "arrived",
      "robot_camera",
      "voice_check",
      "finished"
    ],
    "camera": "idle",
    "voice": "idle",
    "error": null
  }
}
```

## 任务状态查询

```http
GET /api/tasks/{task_id}/status
```

返回：

```json
{
  "code": 0,
  "data": {
    "task_id": "task_abc123",
    "task": "confirm_fall",
    "status": "arrived",
    "revision": 3,
    "step": "arrived",
    "progress": {
      "completed_steps": 3,
      "total_steps": 6,
      "current_index": 3,
      "percent": 50
    },
    "camera": "idle",
    "voice": "idle",
    "source": {
      "event": "fall_detected",
      "elderId": "001",
      "location": "bedroom",
      "confidence": 0.95,
      "sourceEventId": "camera-fall-001",
      "cameraId": "fixed-camera-01"
    },
    "result": {},
    "error": null
  }
}
```

任务状态取值：

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

## 任务结果查询

```http
GET /api/tasks/{task_id}/result
```

返回：

```json
{
  "code": 0,
  "data": {
    "task_id": "task_abc123",
    "task": "confirm_fall",
    "status": "finished",
    "revision": 8,
    "elder_id": "001",
    "location": "bedroom",
    "confidence": 0.95,
    "source_event_id": "camera-fall-001",
    "camera_id": "fixed-camera-01",
    "progress": {
      "completed_steps": 6,
      "total_steps": 6,
      "current_index": 6,
      "percent": 100
    },
    "camera": "ready",
    "voice": "waiting",
    "confirm": "elder_present",
    "robot_camera": {
      "streamUrl": "/api/camera/stream",
      "snapshotUrl": "/api/camera/snapshot",
      "snapshot": "available"
    },
    "voice_result": "awaiting_response",
    "voice_delivery": "mock",
    "voice_prompt_url": null,
    "voice_error": null,
    "error_code": null,
    "failure_step": null,
    "need_help": null,
    "source": {
      "event": "fall_detected",
      "elderId": "001",
      "location": "bedroom"
    },
    "error": null,
    "finished": true
  }
}
```

`GET /api/status`、`GET /api/tasks/{task_id}/status`、`GET /api/tasks/summary`、`GET /api/tasks/{task_id}/result` 和 `GET /api/tasks/{task_id}/timeline` 均会在顶层返回 `elder_id`、`location`、`location_resolution`、`confidence`、`source_event_id`、`camera_id`、`external_task_id`，便于前端和告警中心直接展示与路由。状态、摘要、timeline 和 callback 还会返回 `steps`、`progress` 与 `finished`，可直接渲染“接收事件、移动、到达、机器人摄像头、语音询问、完成”的比赛闭环，并在终态时停止轮询。`location_resolution` 会说明原始输入、归一后的固定点、是否命中已知位置、是否使用 fallback，以及短动作计划快照。

如果 Go2 二次视觉确认阶段失败，`health_new` 可以直接按结构化字段展示失败原因：

```json
{
  "code": 0,
  "data": {
    "task_id": "task_abc123",
    "task": "confirm_fall",
    "status": "failed",
    "camera": "failed",
    "confirm": "unknown",
    "robot_camera": {
      "streamUrl": "/api/camera/stream",
      "snapshotUrl": "/api/camera/snapshot",
      "snapshot": "failed"
    },
    "error_code": "CAMERA_DECODE_FAILED",
    "failure_step": "robot_camera",
    "error": "Camera returned data that is not a decodable JPEG.",
    "finished": true
  }
}
```

## 老人语音反馈写回

当 `health_new`、前端或现场人员获得老人反馈后，可写回任务：

```http
POST /api/tasks/{task_id}/voice-result
Content-Type: application/json
```

```json
{
  "voice_result": "need_help",
  "need_help": true
}
```

兼容 camelCase：

```json
{
  "voiceResult": "no_help_needed",
  "needHelp": false
}
```

语音结果只适用于 `confirm_fall` 跌倒确认任务，可以在任务运行中提前写回，也可以在任务正常 `finished` 后补录。若任务不是 `confirm_fall`，或任务已经 `cancelled` / `failed`，网关会返回 HTTP 409：

```json
{
  "success": false,
  "code": "TASK_STATE_CONFLICT",
  "message": "Voice result cannot be recorded for cancelled task: task_abc123"
}
```

## 语音播放桥

默认 `GO2_VOICE_MODE=mock` 时，网关只进入语音询问状态并记录询问文本。比赛现场如果有机器人音频服务、WebRTC 音频桥或本地播放服务，可配置：

```bash
GO2_VOICE_MODE=http
GO2_VOICE_PROMPT_URL=http://127.0.0.1:8091/api/speak
GO2_VOICE_PROMPT_TIMEOUT_SECONDS=2
GO2_VOICE_PROMPT_RETRIES=1
```

跌倒确认任务进入 `voice_check` 时，网关会向 `GO2_VOICE_PROMPT_URL` 发送：

```json
{
  "task_id": "task_abc123",
  "elder_id": "001",
  "prompt": "您好，请问您现在是否需要帮助？",
  "voice_mode": "http",
  "prompted_at": "2026-07-20T21:00:00+08:00"
}
```

播放成功时任务结果包含：

```json
{
  "voice": "waiting",
  "voice_delivery": "sent",
  "voice_prompt_url": "http://127.0.0.1:8091/api/speak",
  "result": {
    "voiceDelivery": "sent"
  }
}
```

播放失败时任务仍可查询，但语音字段会暴露故障：

```json
{
  "voice": "failed",
  "voice_delivery": "failed",
  "voice_error": "speaker offline",
  "result": {
    "voiceDelivery": "failed",
    "voiceError": "speaker offline"
  }
}
```

现场排障时先查：

```http
GET /api/voice/status
GET /api/preflight
```

这两个接口都会返回 `ready`、`delivery_mode`、`prompt_url_configured` 和 `next_action`。如果设置了 `GO2_VOICE_MODE=http` 但没有配置 `GO2_VOICE_PROMPT_URL`，语音状态会显示 `not_configured`，任务进入语音阶段时也会返回 `voice_delivery=not_configured`，避免被误判为 mock 播放成功。

## 回调 payload

如果设置全局 `HEALTH_NEW_CALLBACK_URL`，或跌倒事件里携带 `callback_url`，每次任务状态变化都会后台 POST：

```json
{
  "task_id": "task_abc123",
  "robot_id": "go2-edu-001",
  "elder_id": "001",
  "location": "bedroom",
  "confidence": 0.95,
  "source_event_id": "camera-fall-001",
  "camera_id": "fixed-camera-01",
  "task": "confirm_fall",
  "status": "checking",
  "revision": 7,
  "step": "voice_check",
  "progress": {
    "completed_steps": 5,
    "total_steps": 6,
    "current_index": 5,
    "percent": 83
  },
  "camera": "ready",
  "voice": "waiting",
  "source": {
    "sourceEventId": "camera-fall-001",
    "elderId": "001",
    "location": "bedroom"
  },
  "result": {
    "confirm": "elder_present"
  },
  "error": null,
  "finished": false,
  "updated_at": "2026-07-20T21:00:00+08:00"
}
```

`elder_id`、`location`、`location_resolution`、`confidence`、`source_event_id`、`camera_id`、`external_task_id` 会在顶层返回，便于告警中心和事件中心直接消费；完整原始来源仍保留在 `source`。

`health_new` 的回调接口建议始终返回 2xx。非 2xx 时网关会按配置重试。网关内部使用单 worker FIFO 队列发送回调；但网络抖动、重试或集成代理仍可能造成边界乱序，`health_new` 应按 `task_id + revision` 只保留最新状态，丢弃小于已处理 revision 的旧回调。若网关关闭时仍有活动任务，该任务会先变为 `cancelled`，`error` 为 `gateway_shutdown`，发送最后一次回调，并等待任务 worker 收尾后再关闭机器人连接。

## 视频接入

第一阶段使用：

```text
GET /api/camera/snapshot
GET /api/camera/stream
```

`/api/camera/stream` 是 MJPEG 流，适合比赛演示和前端嵌入。后续如果替换为 WebRTC/RTSP，可保持任务结果里的 `robot_camera.streamUrl` 合同不变。

## 推荐前端展示

机器人页面至少展示：

- 机器人在线状态、电量、当前动作。
- 当前任务状态：`status`、`step`、`camera`、`voice`。
- 最近任务摘要：`GET /api/tasks/summary?limit=50`。
- 主系统任务反查：`GET /api/tasks/external/{external_task_id}`。
- Go2 现场视频：`robot_camera.streamUrl`。
- 任务事件时间线：`GET /api/tasks/{task_id}/timeline`。
- 最近任务审计：`GET /api/tasks/audit-log?limit=50`。
- 回调投递状态：`GET /api/feedback/status`。
- 失败原因：`error` 与 `last_error`。

## 本地联调命令

启动 Mock 网关：

```bash
GO2_MODE=mock uvicorn app.main:app --host 0.0.0.0 --port 8090 --workers 1
```

用脚本模拟 health_new：

```bash
python demo/simulate_fall_event.py --base-url http://127.0.0.1:8090 --need-help --external-task-id health-task-demo-001
```

现场查看回调：

```bash
# 终端 1：模拟 health_new 回调接收端
python demo/health_new_callback_receiver.py --port 8088
# 如需查看完整 payload：
python demo/health_new_callback_receiver.py --port 8088 --dump-json

# 终端 2：启动 Go2 网关
GO2_MODE=mock uvicorn app.main:app --host 0.0.0.0 --port 8090 --workers 1

# 终端 3：发送 confirm_fall 机器人任务并携带 callback_url
python demo/simulate_fall_event.py \
  --base-url http://127.0.0.1:8090 \
  --callback-url http://127.0.0.1:8088/api/robot/callback \
  --need-help \
  --external-task-id health-task-demo-001
```

离线合同检查：

```bash
python scripts/verify_release.py
python scripts/verify_health_new_contract.py
```

`verify_release.py` 是交付前默认安全验收入口，会执行 Python 编译、`health_new` 合同脚本和全量 Mock 测试，不需要真实 Go2。

该脚本会在 Mock 模式下启动本地 callback 接收器，验证 `/health`、`/api/preflight`、`/api/capabilities`、语音状态发现、中文位置别名解析、推荐的 target-move 路径、任务摘要列表、主系统外部任务号反查、`finished=true` 的任务回调、回调投递状态、snake_case/camelCase 请求兼容、`voice=completed` 的语音结果回调、最近任务审计查询，以及 Go2 摄像头失败时任务继续完成但 `observation.camera_available=false` 的结构化结果合同。

已启动网关非运动预检：

```bash
python scripts/verify_preflight.py \
  --base-url http://127.0.0.1:8090 \
  --require-ready
```

真实 Go2 只读检查时可把 `--require-ready` 换成 `--allow-readonly`。

真实 Go2 主机启动网关前，先运行环境检查：

```bash
GO2_MODE=real python scripts/check_environment.py --strict-real
```

该命令会把 `unitree_sdk2py`、Go2 专用网卡、到机器人 IP 的路由列为严格检查项；如果现场要求 ICMP 可达，再追加 `--require-ping`。

真实机上场前也可以运行 `scripts/verify_gateway_readonly.sh`。该脚本会以 `GO2_CONTROL_ENABLED=false` 启动网关，确认 `/api/readiness`、直接运动、`/api/tasks/confirm-fall`、`/api/events/fall` 和 `/api/tasks/target-move` 都返回 `CONTROL_DISABLED`，并且不会创建任务。

已启动网关现场分级验收：

```bash
python scripts/verify_real_acceptance.py \
  --base-url http://127.0.0.1:8090 \
  --exercise-camera \
  --require-dispatch-ready
```

该脚本默认不派发运动任务，会检查 `/health`、`/api/status`、`/api/preflight`、`/api/capabilities`、位置解析、摄像头状态、语音状态和反馈状态；加 `--exercise-camera` 时只抓取一帧 JPEG 快照。只有现场确认测试区域安全后，才加 `--allow-motion --dispatch-fall` 触发跌倒确认任务闭环。现场需要验收回调链路时，再追加 `--expect-callback`，脚本会启动一个本地 `health_new` 风格 callback 接收器，并要求收到 `finished=true` 的终态回调才通过。

完整闭环示例：

```bash
python scripts/verify_real_acceptance.py \
  --base-url http://127.0.0.1:8090 \
  --allow-motion \
  --dispatch-fall \
  --expect-callback \
  --need-help \
  --external-task-id health-task-demo-001
```

已启动网关验收：

```bash
python scripts/verify_running_gateway_fall_loop.py \
  --base-url http://127.0.0.1:8090 \
  --need-help \
  --external-task-id health-task-demo-001
```

这个脚本会通过真实 HTTP 访问已经启动的网关，适合 Mock uvicorn、真实 Go2 网关和三端联调现场。它会自动启动临时 callback 接收器，验证 `/health`、`/api/preflight`、`/api/capabilities`、readiness、`confirm_fall` 任务派发、任务结果、摄像头状态、反馈投递状态、terminal callback 和语音结果回写；如需验证原始事件入口，可加 `--dispatch-mode event`。

`demo/simulate_fall_event.py` 默认会先执行 `/api/preflight`，再通过 `/api/tasks/confirm-fall` 派发任务，输出任务 revision/progress、终态 `/api/tasks/{task_id}/result`，以及可选语音回写后的 result，适合现场和 `health_new` 页面逐项对照；如需验证原始事件入口，可加 `--dispatch-mode event`，只有兼容旧网关时才使用 `--skip-preflight`。
`demo/health_new_callback_receiver.py` 会在摘要行输出 revision、external_task_id、progress、error_code 和 failure_step，方便现场排查回调顺序、主系统任务对账和失败原因。

## Confirm fall v2 contract

完整合同见 `docs/confirm_fall_contract.md`。`confirm_fall` 当前同时返回旧字段和新字段：

- 新状态字段：`status_v2`，取值 `QUEUED`、`RUNNING`、`COMPLETED`、`FAILED`、`CANCELLED`、`BLOCKED`。
- 新步骤字段：`current_step`，取值 `RECEIVED`、`PREFLIGHT`、`MOVING`、`ARRIVED`、`CAMERA_CHECK`、`VOICE_PROMPT`、`WAITING_RESPONSE`、`REPORTING`。
- 新结果字段：`result.outcome`，取值 `SAFE`、`NEED_HELP`、`NO_RESPONSE`、`UNKNOWN`。
- 新观察字段：`result.observation.camera_available`、`snapshot_url`、`voice_available`、`response_type`、`transcript`。

旧字段 `status`、`step`、`currentStep`、`result.confirm`、`result.voiceResult` 仍保留，现有页面可以继续消费；新页面建议优先使用 `status_v2/current_step/outcome/observation`。

老人响应入口：

```http
POST /api/robot/tasks/{task_id}/elder-response
```

```json
{
  "response_type": "NEED_HELP",
  "transcript": "need help"
}
```

该接口只在 `current_step=WAITING_RESPONSE` 时接受一次最终响应；同一响应重复提交按幂等处理，不同的第二次响应会被拒绝。没有真实 ASR，Mock 结果由 `GO2_MOCK_CONFIRM_FALL_OUTCOME` 控制，等待超时由 `GO2_ELDER_RESPONSE_TIMEOUT_SECONDS` 控制。

到达证据入口：

```http
GET /api/robot/tasks/{task_id}/evidence/arrival.jpg
```

任务结果只暴露 HTTP URL，不暴露本地绝对路径。相机失败不会让 `confirm_fall` 任务失败，任务会继续进入语音和等待响应流程，并返回 `camera=failed`、`robot_camera.cameraAvailable=false`、`observation.camera_available=false`。

回调投递诊断：

```http
GET /api/robot/tasks/{task_id}/callback-deliveries
POST /api/robot/tasks/{task_id}/callbacks/replay
```

回调 payload 带 `callback_id`、每任务单调递增的 `sequence`、`trace_id`、`status_v2`、`current_step`、`outcome` 和 `observation`。`health_new` 仍应按 `task_id + revision` 做最终状态去重。

新增配置：

```bash
GO2_READ_ONLY_MODE=false
GO2_TASK_EVIDENCE_DIR=data/task_evidence
GO2_ELDER_RESPONSE_TIMEOUT_SECONDS=3
GO2_MOCK_CONFIRM_FALL_OUTCOME=NO_RESPONSE
```

`GO2_CONTROL_ENABLED=false`、`GO2_READ_ONLY_MODE=true`、真实模式 DDS/运动未就绪、预检失败、真实模式未知位置或 fallback 位置都会创建 `status_v2=BLOCKED` 的任务并回调，但不会运动。

## External task direct query

`health_new` can use the task/correlation ID it supplied as `taskId`, `external_task_id`, or `externalTaskId` to query robot task state directly, without resolving the internal robot `task_id` first:

```http
GET /api/tasks/external/{external_task_id}/status
GET /api/tasks/external/{external_task_id}/result
GET /api/tasks/external/{external_task_id}/timeline
GET /api/robot/tasks/external/{external_task_id}/status
GET /api/robot/tasks/external/{external_task_id}/result
GET /api/robot/tasks/external/{external_task_id}/timeline
```

Unknown external task IDs return HTTP 404 with `code=TASK_NOT_FOUND`.

The same external task ID can be used to write elder voice feedback:

```http
POST /api/tasks/external/{external_task_id}/voice-result
POST /api/robot/tasks/external/{external_task_id}/voice-result
```

The request body is the same as `POST /api/tasks/{task_id}/voice-result`, for example `{"voice_result":"need_help","need_help":true}`.

`health_new` can also cancel an active robot task by its own task ID:

```http
POST /api/tasks/external/{external_task_id}/cancel
POST /api/robot/tasks/external/{external_task_id}/cancel
```

The request body is the same as `POST /api/tasks/{task_id}/cancel`, for example `{"reason":"health_new_cancel"}`.
