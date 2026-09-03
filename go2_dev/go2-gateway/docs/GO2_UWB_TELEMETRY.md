# Go2 UWB伴随实时监测

这是比赛现场使用的只读工程界面。Dashboard 只通过 HTTP `GET` 读取现有
Companion Runtime 的状态，不初始化第二套控制 Runtime，不调用运动接口。

## 数据链

```text
rt/uwbstate + LiDAR
        ↓
Companion Runtime / Supervisor / Arbiter / Executor
        ↓
GET /api/v1/robot/companion/status
        ↓
Go2 UWB伴随实时监测
```

- 当前距离：同一个无线 Runtime 最新 `rt/uwbstate.distance_est` 只读快照。
- 方位角：同一帧 `orientation_est` 经现有 Runtime 校准链得到，不在 Dashboard
  内重新计算另一套控制输入。
- 目标距离：当前 Companion YAML 配置，由状态接口返回；相对位置图的期望点
  位于真实当前方位的目标距离处，因此会随 UWB 方位变化。
- `vx/wz`：无线伴随控制循环经过对齐状态与最终限幅后实际交给
  `refresh_velocity()` 的值。状态快照按每个控制周期更新；当前无线配置为 4 Hz，
  Dashboard 为 5 Hz 读取。
- LiDAR：Runtime 的 LiDAR 安全状态；默认界面不显示点云。

## 安装

```powershell
python -m pip install -r requirements.txt
```

## Mock 模式

```powershell
python tools/go2_uwb_telemetry.py --mock
```

## 真机模式

### 无线 WebRTC（当前比赛链路）

先在第一个 PowerShell 窗口启动现有无线 Runtime：

```powershell
.\scripts\Start-Go2WirelessRuntime.ps1 -RobotIp 192.168.8.252
```

再在第二个 PowerShell 窗口启动只读 Dashboard：

```powershell
python tools/go2_uwb_telemetry.py --wireless
```

只有在无线 Runtime 控制台执行 `START`、状态进入 `FOLLOWING` 后，最终
`vx/wz` 才会显示真实非零伴随输出。未启动伴随时仍可读取真实 UWB 距离和方位，
但运动输出应如实为 `0.00`。

无线 Runtime 状态接口为
`http://127.0.0.1:8093/api/v1/robot/companion/status`。当前无线伴随链是
UWB-only，页面会如实显示 `LiDAR ● 不可用`，不会伪造 LiDAR 正常状态。

### 有线 SDK2 DDS

先在第一个 PowerShell 窗口启动唯一的 REST Gateway Runtime：

```powershell
.\scripts\Start-Go2CompanionReal.ps1 -RestGateway
```

该进程启动后保持 `IDLE`，不会自动开始运动。不要同时启动不带
`-RestGateway` 的 Console Runtime。

再在第二个 PowerShell 窗口运行只读 Dashboard：

```powershell
python tools/go2_uwb_telemetry.py
```

如果 Gateway 不在默认的 `http://127.0.0.1:8090`：

```powershell
python tools/go2_uwb_telemetry.py `
  --status-url http://127.0.0.1:8090/api/v1/robot/companion/status
```

浏览器地址：`http://127.0.0.1:8050`

`--interface` 仅在 debug 信息中记录实际 Gateway/DDS 所用网卡；DDS 的生命周期
仍由唯一的 Companion Runtime 管理，Dashboard 不会自行订阅或发布 DDS topic。

## Debug 模式

```powershell
python tools/go2_uwb_telemetry.py --mock --debug
```

Debug 面板额外显示 topic、采样率、数据年龄、原始 `orientation_est`、校准后
`bearing`、最终 `vx/wz` 和执行状态。比赛默认模式隐藏这些信息。

无线真机诊断命令：

```powershell
python tools/go2_uwb_telemetry.py --wireless --debug
Invoke-RestMethod -Uri "http://127.0.0.1:8093/api/v1/robot/companion/status"
```

## 只读约束

`app/telemetry/` 与 `tools/go2_uwb_telemetry.py` 不允许导入或调用
`SportClient.Move()`、`SportClient.StopMove()`、Companion START/STOP/RESUME
接口，也不允许创建 DDS publisher。运动 Writer 仍只有现有 Companion Runtime。
