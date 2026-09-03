# Phase 5.4.10：UniLidar 官方同源数据获取

日期：2026-07-29  
状态：**环境准备 PASS；硬件 Gate BLOCKED**  
Phase 5.5：**HOLD，不得进入**

## 1. 结论

本阶段完成了主机、VirtualBox、Ubuntu VM 和官方 UniLidar SDK 的只读前置审计。

当前结果：

```text
官方 SDK 源码                READY
Ubuntu 22.04 / ROS2 Humble   BUILD PASS
SDK 可执行文件               READY
运行库依赖                   PASS

Windows L1 USB               NOT FOUND
VM L1 serial                 NOT FOUND
/unilidar/cloud              未启动
/unilidar/imu                未启动

Phase 5.4.10                 BLOCKED AT HARDWARE GATE
Phase 5.5                    HOLD
```

阻塞原因不是软件：

> 当前没有可确认属于 L1 的 USB 串口设备。Windows 上的 COM3/COM4 是
> `health_new` 的 T10 手环采集器，不能透传或占用；Ubuntu VM 因此没有
> `/dev/ttyUSB*` 或 `/dev/ttyACM*`。

没有在设备身份不明时启动 SDK，也没有把 `/utlidar/cloud` 与其他 IMU 拼接。

## 2. 安全边界

本阶段遵守：

- 未拆机、未拔 Go2 内部连接；
- 未修改 Go2 或 L1 固件；
- 未向 L1 发送工作模式、LED、网络或标定命令；
- 未运行 Point-LIO、SLAM 或 Nav2；
- 未发布 TF；
- 未调用任何运动控制接口；
- 未修改 `health_new`；
- 未把 COM3/COM4 从 Windows 交给 VM；
- 官方 ROS 2 节点只编译，未启动。

## 3. Windows 与 VirtualBox USB 审计

### 3.1 VirtualBox

```text
VirtualBox:        7.2.14r174565
VM:                Ubuntu-22.04.5-ROS2
VM state:          running
USB controller:    off
Extension Packs:   0
```

因此，当前 Ubuntu VM 不具备 USB 透传路径。

本阶段没有为了试错而关闭 VM、启用控制器或添加模糊 USB filter。真正的 L1
设备出现后，应先确认其 VID/PID/serial，再建立只针对该设备的透传规则。

### 3.2 Windows 串口

Windows 当前发现两个 `CH9102`：

| 端口 | VID:PID | USB serial | 实际用途 |
|---|---|---|---|
| COM4 | `1A86:55D4` | `588F051527` | T10 手环链路 |
| COM3 | `1A86:55D4` | `588F051539` | T10 手环链路 |

本地冻结系统已经明确：

```text
D:\health_new\shouhuan.py
  采集器芯片: CH9102
  TARGET_COM = COM3

D:\health_new\docs\WRISTBAND_DATA_TROUBLESHOOTING.md
  SERIAL_BROADCAST_PORT=COM4
  SERIAL_RESPONSE_PORT=COM3
```

所以这两个设备不是可供本阶段试用的 L1 串口。即便后续 L1 也使用相同
VID/PID，也必须依据新的 USB serial 和插拔观察区分，不能按 VID/PID 批量透传。

## 4. Ubuntu VM 设备检查

检查：

```bash
ls /dev/ttyUSB* /dev/ttyACM*
```

结果：

```text
NO_L1_SERIAL_DEVICE
```

只读预检工具结果：

```text
PHASE_5_4_10_UNILIDAR_PREFLIGHT
mode=READ_ONLY_NO_NODE_START
sdk_executable=READY
serial_device=NONE
result=BLOCKED_NO_L1_USB_SERIAL
preflight_exit=20
```

预检工具只检查设备身份和权限，不启动 SDK 节点。

## 5. 官方 UniLidar SDK 准备

来源：

```text
phase547_sources/unilidar_sdk_extract/
  unitreerobotics-unilidar_sdk-1bd7d95
```

部署位置：

```text
/home/go2/phase5410_unilidar_sdk
```

源归档：

```text
phase5410_unilidar_sdk.tar.gz
SHA-256:
68E8E2B8F2012B28F1CCC258BD7244AE2E533112E81D0C4AE145088B5E50555B
```

官方默认配置：

```text
serial port:    /dev/ttyUSB0
baud:           2000000
cloud topic:    /unilidar/cloud
cloud frame:    unilidar_lidar
IMU topic:      /unilidar/imu
IMU frame:      unilidar_imu
cloud_scan_num: 18
```

官方 ROS 2 节点直接把 SDK cloud 和 IMU 的各字段及 SDK 时间戳发布到 ROS 消息，
没有在 ROS 2 层混入 Go2 DDS 数据。

## 6. 编译验证

执行环境：

```text
Ubuntu 22.04.5
ROS2 Humble
PCL
```

编译：

```bash
cd /home/go2/phase5410_unilidar_sdk/unitree_lidar_ros2
source /opt/ros/humble/setup.bash
colcon build
```

结果：

```text
Finished <<< unitree_lidar_ros2 [25.8s]
Summary: 1 package finished [26.1s]
```

存在一条 CMake `CMP0074/PCL_ROOT` 开发警告，不影响产物生成。

验证：

```text
ros2 pkg executables:
unitree_lidar_ros2 unitree_lidar_ros2_node

ldd missing dependency count:
0
```

结论：软件环境已经准备完成。以后获得真正的 L1 串口后，不需要重新搭建 SDK。

## 7. 为什么没有启动官方节点

官方节点构造时立即尝试：

```text
open configured serial port
baud = 2000000
parse raw L1 stream
```

当前不存在可确认的 L1 串口。此时启动没有数据价值，还可能：

- 错误占用手环串口；
- 让 `health_new` 丢失 COM3/COM4；
- 产生“topic 存在但不是 L1 数据”的假阳性；
- 把设备识别问题误判为 SDK 或 ROS 2 问题。

因此节点未启动，`/unilidar/cloud` 和 `/unilidar/imu` 没有伪造输出。

## 8. A/B 状态

| 项目 | Go2 DDS | L1 USB SDK |
|---|---|---|
| cloud topic | `/utlidar/cloud` | 未获得 |
| IMU topic | `/utlidar/imu` | 未获得 |
| cloud Hz | 约 15.4 Hz | 待测 |
| IMU Hz | 约 250 Hz | 待测 |
| frame | `utlidar_*` | 待测 |
| 同源 SDK timestamp | 不可声明 | 待测 |
| 静止比力不变性 | FAIL | 待测 |
| Point-LIO | FAIL | 禁止运行 |

由于官方同源数据尚未取得，本阶段没有执行 A/B 数据分析和 Point-LIO。

## 9. Gate 判断

| 检查项 | 结果 |
|---|---|
| Windows USB 枚举 | PASS |
| 现有 COM3/COM4 身份确认 | PASS：手环设备 |
| 未影响 `health_new` | PASS |
| Ubuntu L1 串口 | **BLOCKED** |
| 官方 SDK 源码 | PASS |
| ROS2 Humble 编译 | PASS |
| SDK 运行库 | PASS |
| `/unilidar/cloud` | NOT STARTED |
| `/unilidar/imu` | NOT STARTED |
| 官方 cloud/IMU 同源性验证 | **BLOCKED** |
| Phase 5.5 准入 | **HOLD** |

## 10. 下一次继续条件

继续 Phase 5.4.10 前，只需要满足一个物理条件：

> 在不拆机、不拔内部线的前提下，把 L1 官方 USB 数据接口连接到 Windows 主机。

连接后按顺序执行：

1. Windows 记录新出现设备的 VID、PID、USB serial 和 COM 号；
2. 明确排除现有手环 serial：
   - `588F051527`
   - `588F051539`
3. 正常关闭 VM；
4. 为 VM 配置合适的 USB 控制器及仅匹配 L1 serial 的 filter；
5. 启动 VM，确认出现 `/dev/ttyUSB*` 或 `/dev/ttyACM*`；
6. 运行只读预检：

   ```bash
   bash /home/go2/phase5410_tools/phase5410_unilidar_preflight.sh
   ```

7. 人工确认设备身份后，才启动官方节点；
8. 首先只采集静止数据，验证：
   - `/unilidar/cloud` 与 `/unilidar/imu` 同时存在；
   - SDK header 时间连续；
   - 点云字段、逐点时间、频率和点数；
   - IMU 静止比力约为重力尺度；
9. 再做一个小角度俯倾 Gate；
10. 全部通过后才允许离线 Point-LIO 对照。

预检脚本：

```text
phase5410_tools/phase5410_unilidar_preflight.sh
SHA-256:
680D8ACE3D8B299536080D1CACEE4377EB856AA80CBC4200362DE6A3090801EB
```

本报告完成后停止，不进入 Phase 5.5。

