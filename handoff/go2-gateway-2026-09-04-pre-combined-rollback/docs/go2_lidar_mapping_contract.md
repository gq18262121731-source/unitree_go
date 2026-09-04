# Go2 LiDAR Mapping Readiness Contract

本合同冻结 Go2 EDU 雷达、建图、定位、导航能力的第一阶段接口边界。

第一阶段目标不是证明 Go2 已经具备自主导航能力，而是建立一条可信、可观测、可诊断的只读链路：

```text
雷达硬件可能存在
        ↓
通信通道是否可初始化
        ↓
相关话题是否被发现
        ↓
是否持续收到真实样本
        ↓
样本是否新鲜、稳定、可用于后续建图定位验证
```

## Scope

本阶段所有接口均为只读诊断接口。

允许：

- 查询 DDS / 网络 / 接口诊断状态。
- 查询候选雷达话题。
- 查询 `rt/utlidar/lidar_state` 最近样本状态。
- 判断雷达数据链路是否满足后续人工建图验证的最低技术前置条件。

禁止：

1. 调用机器人运动指令。
2. 切换机器人运动模式。
3. 启动或停止雷达设备。
4. 启动或停止建图。
5. 加载、保存或覆盖地图。
6. 启动定位。
7. 启动路径规划。
8. 发送目标点。
9. 启动巡航或自主导航。
10. 通过健康事件直接触发机器人移动。

`mappingPrerequisitesReady=true` 只是进入下一阶段人工验证的必要条件，不是自动放行条件。它不代表：

- 地图可以正常建立。
- 定位已经稳定。
- Nav2 可以运行。
- 机器人可以自主移动。
- 现场导航可以投入使用。

## APIs

### Raw diagnostic API

```http
GET /api/lidar/status
```

该接口面向 `go2-gateway` 运维、联调和机器人开发。接口执行成功时固定返回 HTTP 200，通过业务字段表达雷达是否就绪。

返回字段：

```json
{
  "deviceDetected": null,
  "transportInitialized": true,
  "topicDiscovered": false,
  "sampleReceived": false,
  "dataFresh": false,
  "mappingPrerequisitesReady": false,
  "transport": "dds",
  "topic": null,
  "candidateTopics": [
    "rt/utlidar/lidar_state",
    "rt/utlidar/voxel_map",
    "rt/utlidar/voxel_map_compressed",
    "/utlidar/cloud",
    "rt/uslam/frontend/cloud_world_ds",
    "rt/uslam/frontend/odom",
    "rt/uslam/cloud_map",
    "rt/uslam/localization/odom",
    "rt/uslam/navigation/global_path"
  ],
  "frequencyHz": null,
  "minFrequencyHz": 1.0,
  "packetLossRate": null,
  "maxPacketLossRate": 0.2,
  "sampleAgeMs": null,
  "staleAfterMs": 2000,
  "lastSampleAt": null,
  "enumerationStatus": "OK",
  "enumerationReliable": true,
  "errorCode": "LIDAR_DATA_UNAVAILABLE",
  "message": "DDS 基础链路尚未收到机器人真实状态样本，雷达硬件状态未知。",
  "blockedBy": [
    "ROBOT_DDS_NO_STATE_SAMPLES"
  ],
  "checkedAt": "2026-07-21T23:00:00+08:00"
}
```

### Main-system adapter API

```http
GET /api/robot/lidar/status
```

该接口面向健康监护主系统，只返回稳定、简化的业务语义。它必须调用同一个 `LidarStatusService`，不得复制一套检测逻辑。

返回字段：

```json
{
  "available": false,
  "status": "unavailable",
  "mappingReady": false,
  "reason": "LIDAR_DATA_UNAVAILABLE",
  "updatedAt": "2026-07-21T23:00:00+08:00",
  "deviceDetected": null,
  "sampleReceived": false,
  "dataFresh": false,
  "blockedBy": [
    "ROBOT_DDS_NO_STATE_SAMPLES"
  ]
}
```

## State Semantics

接口必须区分：

- 未检测到雷达。
- 接口或话题枚举失败。
- 通信初始化失败。
- 话题存在但没有样本。
- 有样本但频率过低。
- 有样本但数据过期。
- 有样本但丢包率异常。
- 数据稳定，具备进入建图验证阶段的前置条件。

当 DDS 基础链路尚未收到任何机器人真实状态样本时：

```json
{
  "deviceDetected": null,
  "transportInitialized": true,
  "topicDiscovered": false,
  "sampleReceived": false,
  "mappingPrerequisitesReady": false,
  "errorCode": "LIDAR_DATA_UNAVAILABLE",
  "blockedBy": [
    "ROBOT_DDS_NO_STATE_SAMPLES"
  ]
}
```

此状态只能说明雷达数据不可用，不能推断：

- 雷达不存在。
- 雷达硬件故障。
- Go2 不支持雷达。

## Error Codes

机器可读错误码：

- `ROBOT_NETWORK_UNREACHABLE`
- `ROBOT_DDS_NOT_INITIALIZED`
- `LIDAR_DATA_UNAVAILABLE`
- `LIDAR_INTERFACE_ENUMERATION_UNRELIABLE`
- `LIDAR_TOPIC_NOT_DISCOVERED`
- `DDS_NO_LIDAR_SAMPLES`
- `LIDAR_DATA_STALE`
- `LIDAR_FREQUENCY_TOO_LOW`
- `LIDAR_PACKET_LOSS_HIGH`

## HTTP Semantics

- 接口本身执行成功：HTTP 200。
- 雷达尚未就绪：HTTP 200，通过 `errorCode`、`message`、`mappingPrerequisitesReady` 表达。
- 服务内部不可恢复异常：HTTP 503。
- 请求参数错误：HTTP 4xx。

不要因为雷达未就绪就返回 HTTP 503，避免主系统状态页把“设备未就绪”和“网关服务故障”混为一谈。

## Acceptance Criteria

- 合同文档已经冻结。
- 两个 API 使用同一个状态服务。
- 没有新增运动、建图、导航调用。
- 没有修改现有运动控制链路。
- 无雷达样本时不会误报设备故障。
- 枚举超时时不会误报雷达不存在。
- 状态接口具有稳定 JSON Schema。
- 所有关键错误均有机器可读 `errorCode`。
- Mock 测试覆盖主要降级路径。
- 现有 `go2-gateway` 测试全部通过。
