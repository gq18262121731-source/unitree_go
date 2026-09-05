# Unitree Go2 私有资产迁移清单

审计日期：2026-09-05

适用仓库：`gq18262121731-source/unitree_go`（当前为公开仓库）

本文只记录文件类型、目标位置和恢复方式，不包含任何密钥值、业务数据或语音内容。

## 1. 是否适合上传到当前 GitHub 仓库

| 资产 | 当前状态 | 是否上传公开仓库 | 处理结论 |
|---|---|---:|---|
| `health_new_p04/.env` | 本机存在，包含云端语音、Qwen、天气等真实凭据 | 否 | 仅放入离线补充包；跨电脑前建议轮换密钥，并通过加密介质传输 |
| `.go2_aes_key.dpapi` | 本机存在，Windows DPAPI 加密；原始 Go2 AES Key 已确认另有安全保存 | 否 | 旧 DPAPI 仅留作原机恢复证据；新电脑使用现成 AES Key 重新生成自己的 DPAPI |
| `health_new_p04/data/app.db` | 本机存在，含设备、传感器和告警业务数据 | 否 | 已用 SQLite 在线备份方式生成一致副本，放入离线补充包 |
| 跌倒检测 `.pt` 模型 | 找到完整模型包 | 暂不上传 | 自训练/私有数据模型的权属和隐私许可尚未确认；先离线保存 |
| 完整独立 PFV2/生产视觉服务 | 未找到 | 无法上传 | 当前只找到 `camera-service` 文档目录和仓库内的部分 camera runtime，不足以还原完整独立服务 |
| 静态健康模型与 scaler | 未找到 | 无法上传 | `static_health_model.pt`、`feature_scaler.joblib` 及训练源数据均未找到 |
| 语音缓存和最近录音 | 本机存在 | 否 | 可能含个人声音、指令和合成内容，只放离线补充包；不属于启动必需资产 |
| 日志和任务证据 | 本机存在 | 否 | 可能含设备标识、地址、路径、时间线或业务内容，只放离线补充包 |
| 脱敏复现手册和本清单 | 已生成 | 是 | 适合公开，可随代码版本管理 |

## 2. 离线补充包目录约定

补充包根目录下包含：

```text
private_config/health_new_p04/.env
machine_bound/go2-wireless-camera/wireless_collector/.go2_aes_key.dpapi
database/app.db
database/chroma/
database/fall_events/
models/fall_detection_model_bundle.zip
runtime/go2_voice/
runtime/task_evidence/
runtime/health_new_p04/
README.md
DO_NOT_UPLOAD_PUBLICLY.txt
SHA256SUMS.txt
FILE_INVENTORY.csv
```

`SHA256SUMS.txt` 和 `FILE_INVENTORY.csv` 用于确认复制后文件是否完整。补充包本身没有加密；复制到 U 盘、网盘或新电脑前，应先放入带强密码的加密容器，并与密码分开传递。

## 3. 新电脑恢复顺序

先按照仓库根目录的 `REPRODUCTION.md` 完成代码、依赖和 Mock 环境复现，再按需恢复私有资产。

### 3.1 恢复主系统 `.env`

停止主系统后端，把补充包中的文件复制为：

```text
<repo>/health_new_p04/.env
```

不要把该文件加入 Git。由于密钥已在另一台电脑上迁移，建议先在服务商控制台轮换 Qwen/DashScope、天气和其他云端凭据，再更新新电脑的 `.env`。

### 3.2 Go2 无线密钥必须在新电脑重新生成

不要把旧的 `.go2_aes_key.dpapi` 当作新电脑密钥。DPAPI 密文通常只能由创建它的 Windows 电脑和用户解密。旧文件只用于原机灾难恢复或取证。

在新电脑进入：

```text
<repo>/go2_dev/go2-wireless-camera/wireless_collector
```

然后按 `REPRODUCTION.md` 运行 `setup_wireless.ps1`，重新输入 Go2 无线 WebRTC 密钥并生成新电脑自己的 DPAPI 文件。

### 3.3 恢复业务数据库

1. 停止主系统后端和所有可能访问 SQLite 的进程。
2. 备份新电脑已有的 `health_new_p04/data/app.db`。
3. 将补充包的 `database/app.db` 复制到该位置。
4. 启动前执行 `PRAGMA integrity_check;`，结果应为 `ok`。

本次离线副本的完整性检查结果为 `ok`。数据库包含当前业务状态，不应发布到公开仓库。

### 3.4 恢复跌倒检测模型

补充包内模型 ZIP 的 SHA-256：

```text
0B224FF91208D52696F27FD60F1D38550F259793096C55FF2E71A98099B30C47
```

不要用 ZIP 中的旧代码覆盖 GitHub 上的新代码。应解压到临时目录，只把所需的 `.pt` 文件按原相对路径复制到：

```text
<repo>/health_new_p04/fall_detection_model_bundle/
```

模型注册表位于：

```text
health_new_p04/fall_detection_model_bundle/configs/model_registry.yaml
```

恢复后逐项确认注册表引用的权重均存在。只有在确认模型版权、训练数据授权和隐私风险后，才能考虑通过 Git LFS 发布；公开仓库不应先上传再排查。

### 3.5 日志、语音缓存和任务证据

这些文件主要用于故障复盘，不是正常启动必需项。默认不要恢复到运行目录。需要排错时，只复制与问题时间段相关的最小文件集，并在共享前清除个人声音、设备标识、IP、令牌和业务记录。

## 4. 本次复刻不要求的部分

本次目标是复刻旧电脑当前实际状态。以下内容在旧电脑上本来就不存在，因此不作为复刻失败项：

- 没有找到完整独立的 PFV2/生产视觉服务源码、镜像或部署包；新电脑保持与旧电脑相同的视觉能力边界。
- 没有找到 `static_health_model.pt`、`feature_scaler.joblib`、`feature_columns.json` 及训练源数据；新电脑保持与旧电脑相同的静态健康模型缺失状态。
- Docker daemon 在审计时不可用，因此没有核实或导出可能存在的 Docker volumes。
- 没有找到移动端正式签名密钥；如需生成与原发行版同签名的安装包，必须从原签名保管处取得密钥。

无线 Go2 不再属于缺失资产：用户已确认原始 AES Key 有现成的安全副本。新电脑不得使用旧 DPAPI，而应运行 `setup_wireless.ps1` 并交互输入现成 AES Key。

## 5. 安全底线

- 不要把 `.env`、DPAPI 文件、业务数据库、原始录音、完整日志或任务证据提交到公开仓库。
- 即使误提交后删除，Git 历史仍可能保留内容；发生误传时应立即轮换凭据，并清理 Git 历史。
- 私有仓库也不是密钥管理器。真实凭据应使用密码管理器、CI Secrets 或专用密钥服务。
- 离线补充包当前是明文目录。离开本机前必须额外加密。
