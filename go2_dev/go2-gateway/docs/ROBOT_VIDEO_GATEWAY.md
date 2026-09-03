# Robot Video Gateway 交接文档

## 1. 交付结论

机器狗电脑是机器狗系统唯一的视频边缘网关。Go2 IP、Unitree SDK、WebRTC、视频解码、最新帧缓存和断线重连均由机器狗电脑负责，其他系统只消费标准 HTTP 视频接口。

机器狗电脑使用 DHCP，局域网 IP 可能变化，因此业务系统不得保存 `192.168.x.x` 形式的地址。正式交付前，网络负责人必须为机器狗电脑配置唯一的局域网 DNS 名称 `robot-gateway`。正式服务基地址为：

```text
http://robot-gateway:8093
```

跌倒检测电脑的配置只写一个稳定基地址：

```yaml
robot_video:
  gateway_url: "http://robot-gateway:8093"
```

当前机器狗电脑的 Windows 主机名是 `TEST`，但实测它会解析到 WLAN、虚拟网卡和多个 IPv6 地址，`http://TEST:8093` 访问可能超时。因此 `TEST` 只能用于排查，不是正式交付地址。`robot-gateway` 别名未配置并从跌倒检测电脑验收通过之前，网络交付状态为 `BLOCKED`。

## 2. 为什么不能交付固定 IP

DHCP 可能在机器狗电脑重启、路由器重启或租约更新后分配新地址。固定写入以下地址是不合格的交接方式：

```text
http://192.168.8.254:8093/stream.mjpg
```

局域网 DNS 应把 `robot-gateway` 映射到机器狗电脑的有效 WLAN 地址；当 DHCP 地址变化时，只更新 DNS/DHCP 映射，业务配置保持不变。跌倒检测电脑先通过稳定名称访问发现接口，再使用接口返回的 `stream_url`：

```text
http://robot-gateway:8093/api/v1/robot/video
```

只要请求使用 `robot-gateway`，返回的 `stream_url` 也会保持该主机名形式，不会把当前 DHCP 地址写入调用方配置。

## 3. 系统边界

```text
Go2 Camera
    -> 机器狗电脑共享 WebRTC Runtime
    -> 容量为 1 的最新帧缓冲
    -> JPEG 编码
    -> Robot Video Gateway :8093
    -> 跌倒检测电脑 / 调试前端 / 手机
```

原始帧编码队列容量固定为 1。新帧到达且队列已满时覆盖旧帧；消费方处理速度低于采集速度时允许丢帧，但不通过排队累积历史画面延迟。

调用方不需要也不得依赖：

- Go2 本体 IP；
- Unitree SDK 或 DDS；
- WebRTC、密钥和视频解码细节；
- 机器狗电脑当前通过 DHCP 获得的 IP。

## 4. 正式接口

| 用途 | 方法和路径 | 判定方式 |
|---|---|---|
| 网关进程存活 | `GET /healthz` | HTTP 200 且 `status=ok` |
| 视频实时状态 | `GET /api/v1/video/status` | `streaming=true` 且 `last_frame_age_ms < 1000` |
| 视频地址发现 | `GET /api/v1/robot/video` | 读取 `video.stream_url` |
| MJPEG 视频流 | `GET /stream.mjpg` | 持续收到更新的 JPEG 帧 |

完整地址：

```text
http://robot-gateway:8093/healthz
http://robot-gateway:8093/api/v1/video/status
http://robot-gateway:8093/api/v1/robot/video
http://robot-gateway:8093/stream.mjpg
```

`GET /api/v1/robot/video/stream` 是同一视频流的版本化别名。旧 `/status` 和 `/snapshot` 仅供本机诊断及兼容已有工具，新业务系统不得依赖其中的 Unitree/WebRTC 字段。

`/healthz` 返回 200 只代表 HTTP 进程存活，不代表机器狗视频正常。视频可用性必须由 `/api/v1/video/status` 判定。

视频状态示例：

```json
{
  "robot_id": "go2-01",
  "status": "online",
  "robot_connected": true,
  "video_connected": true,
  "streaming": true,
  "fps": 14.8,
  "width": 1280,
  "height": 720,
  "last_frame_age_ms": 63.0,
  "frame_count": 12034,
  "dropped_frame_count": 87,
  "clients": 2,
  "timestamp": "2026-09-01T11:00:00+08:00"
}
```

## 5. 机器狗电脑启动与网络要求

在 `E:\笨笨狗\go2_dev\go2-gateway` 中启动，并监听所有网卡：

```powershell
.\scripts\Start-Go2WirelessRuntime.ps1 -ListenHost 0.0.0.0 -VideoPort 8093
```

机器狗电脑必须满足：

- 局域网 DNS 名称 `robot-gateway` 唯一指向机器狗电脑的有效 WLAN 地址；
- 推荐在路由器上同时为机器狗电脑的 WLAN MAC 配置 DHCP 地址保留，减少 DNS 映射变化；
- Windows 防火墙允许局域网入站 TCP 8093；
- 8093 只启动一个共享 Runtime，不允许再启动独立 WebRTC 视频客户端；
- 更换路由器或网络后，先恢复 `robot-gateway` 名称解析，不向业务方重新分发临时 IP。

## 6. 跌倒检测电脑接入流程

跌倒检测电脑每次启动跌倒检测时执行：

1. 请求 `http://robot-gateway:8093/healthz`；
2. 请求 `http://robot-gateway:8093/api/v1/video/status`；
3. 仅当 `streaming=true` 且 `last_frame_age_ms < 1000` 时判定视频实时；
4. 请求 `http://robot-gateway:8093/api/v1/robot/video`；
5. 读取 `video.stream_url` 并建立视频连接；
6. 读取失败时释放连接并自动重连，不让检测进程永久退出；
7. 检测线程只处理最新帧，不在跌倒检测电脑再建立无限帧队列。

`gateway_url` 可以放入配置文件或环境变量，但不得把 `/stream.mjpg` 的某个临时 IP 散落写入代码。

## 7. 主机名解析验收

以下命令必须在跌倒检测电脑上执行，而不是只在机器狗电脑本机执行：

```powershell
Resolve-DnsName robot-gateway -ErrorAction Stop
Test-NetConnection robot-gateway -Port 8093
Invoke-RestMethod "http://robot-gateway:8093/healthz"
```

验收标准：

- `Test-NetConnection` 的 `TcpTestSucceeded=True`；
- `/healthz` 返回 `status=ok`；
- 解析结果只包含机器狗电脑当前有效的局域网地址，不包含虚拟网卡地址；
- 机器狗电脑的 DHCP 地址变化并更新映射后，上述地址仍可访问。

如果跌倒检测电脑无法解析 `robot-gateway`，不要把当前 IP 写回业务代码，也不要宣布交接完成。按优先顺序处理：

1. 在路由器或局域网 DNS 中创建或修复 `robot-gateway` 记录；
2. 为机器狗电脑的 WLAN MAC 配置 DHCP 地址保留，并让 DNS 指向该地址；
3. 临时联调才允许使用当前 IP，并明确标注为临时地址，不得作为正式交付配置。

## 8. 视频功能验收

在跌倒检测电脑上执行：

```powershell
$Gateway = "http://robot-gateway:8093"
$Health = Invoke-RestMethod "$Gateway/healthz"
$Status1 = Invoke-RestMethod "$Gateway/api/v1/video/status"
Start-Sleep -Seconds 2
$Status2 = Invoke-RestMethod "$Gateway/api/v1/video/status"
$Discovery = Invoke-RestMethod "$Gateway/api/v1/robot/video"

$Health
$Status1
$Status2
$Discovery
```

正式通过条件：

- `streaming=true`；
- `last_frame_age_ms < 1000`；
- 第二次的 `frame_count` 大于第一次；
- `video.stream_url` 使用 `robot-gateway:8093`，而不是固定 IPv4 地址；
- 跌倒检测电脑能连续读取视频至少 10 分钟；
- 网络短暂断开并恢复后，跌倒检测电脑能自动重新连接；
- 同时连接跌倒检测和一个调试页面时，实时性仍满足要求。

## 9. 责任划分

机器狗团队负责：Go2 连接、WebRTC、解码、最新帧缓存、MJPEG 输出、网关重连、主机名可达性、8093 防火墙及视频健康状态。

跌倒检测团队负责：保存单一 `gateway_url` 配置、调用状态与发现接口、读取最新帧、输入超时、断线重连、模型推理和跌倒事件上报。

网络负责人负责：提供并维护唯一的 `robot-gateway` DNS/DHCP 映射，避免虚拟网卡地址进入解析结果。机器狗电脑 IP 变化不应触发业务代码修改。

## 10. 交付给调用方的最小信息

```text
服务名称：Robot Video Gateway
稳定基地址：http://robot-gateway:8093
状态接口：http://robot-gateway:8093/api/v1/video/status
发现接口：http://robot-gateway:8093/api/v1/robot/video
视频接口：http://robot-gateway:8093/stream.mjpg
健康标准：streaming=true 且 last_frame_age_ms < 1000
注意：禁止在业务代码中保存机器狗电脑当前IP；断线后必须自动重连。
```
