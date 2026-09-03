# Phase 5.4.11：Go2 内部定位 / SLAM 被动观察

日期：2026-07-29  
设备：Go2 X EDU，Hardware V2.0，Firmware V1.1.15  
状态：**COMPLETE — CASE B / NO DEFAULT LOCALIZATION OUTPUT**

## 安全边界

本阶段只创建 ROS2 subscriptions，并检查 DDS graph endpoint。

```text
publishers_created:       0
request_topics_published: []
motion_control:           NOT_USED
slam_started:             false
tf_published:             false
```

未启动或调用：

- `/api/slam_operate/request`；
- `/uslam/client_command`；
- `/utlidar/mapping_cmd`；
- `/utlidar/switch`；
- SLAM、Point-LIO、Nav2、TF；
- `cmd_vel`、SportClient、LowCmd 或其他运动接口。

## 环境预检

| 项目 | 结果 |
| --- | --- |
| Windows → Go2 `192.168.123.161` | 4/4，0% 丢包，<1 ms |
| Ubuntu VM | `Ubuntu-22.04.5-ROS2` 运行中 |
| Ubuntu Go2 NIC | `enp0s8`, `192.168.123.223/24` |
| RMW | `rmw_cyclonedds_cpp` |
| CycloneDDS interface | `enp0s8` 配置 |
| `unitree_api/msg/Response` | PASS |
| 探针 SHA-256 | `663123932A79B943BE972C8D6EC332FF89B196F0F731F33CA3B13F2E9D66CF2C` |

为了只解析 `/api/slam_operate/response`，本阶段在独立
`~/phase5411_msg_ws` 中编译了 Unitree 官方 `unitree_api` 消息定义。
原始 CMake 依赖当前 VM 未安装的 `rosidl_generator_dds_idl`，因此独立
观察工作区使用标准 `rosidl_default_generators` 生成 ROS2 消息。没有
安装、启动或调用任何 SLAM 服务。

## 60 秒观察结果

实际观察时长：

```text
60.00948037998751 s
```

| Topic | Samples | Hz | 时间回拨 |
| --- | ---: | ---: | ---: |
| `/api/slam_operate/response` | 0 | 0 | 0 |
| `/lio_sam_ros2/mapping/odometry` | 0 | 0 | 0 |
| `/slam_info` | 0 | 0 | 0 |
| `/slam_key_info` | 0 | 0 | 0 |
| `/uslam/cloud_map` | 0 | 0 | 0 |
| `/uslam/frontend/cloud_world_ds` | 0 | 0 | 0 |
| `/uslam/frontend/odom` | 0 | 0 | 0 |
| `/uslam/localization/cloud_world` | 0 | 0 | 0 |
| `/uslam/localization/odom` | 0 | 0 | 0 |
| `/uslam/map_file_pub` | 0 | 0 | 0 |
| `/uslam/navigation/global_path` | 0 | 0 | 0 |
| `/uslam/server_log` | 0 | 0 | 0 |

## Graph endpoint 解释

观察结束时：

- `/lio_sam_ros2/mapping/odometry`：无 publisher；存在 bare-DDS
  subscription 和本探针 subscription；
- `/slam_info`、`/slam_key_info`：无 publisher；
- `/uslam/frontend/odom`、`/uslam/localization/odom`：无 publisher；
- `/uslam/cloud_map`、`/uslam/map_file_pub`：可见 bare-DDS publisher，
  但 60 秒内没有任何样本；
- `/uslam/server_log`：可见 bare-DDS endpoint，但没有样本；
- `/uslam/client_command`：存在多个 bare-DDS endpoint，协议未知；
- `/api/slam_operate/request`：存在 bare-DDS endpoint，方向和协议均不足以
  授权调用；
- `/api/slam_operate/response`：无 publisher。

bare-DDS endpoint 只能证明固件图谱中存在接口或占位，不能证明服务已启动，
也不能证明公开可用。

## 结果证据

主结果：

```text
E:\笨笨狗\phase5411_internal_slam_probe.json
SHA-256:
93738DAEC3D07209D8B44EAC1DA483D3CE99064A9CFF41053D28F03C8B0F7D88
```

## Gate 判定

```text
内部定位 topic 可见                 YES
默认内部定位输出                   NO
默认地图输出                       NO
公开启动协议                       NOT FOUND
允许猜测 API/command               NO
Phase 5.4 标准 TF                  HOLD
Phase 5.5 SLAM                     HOLD
Phase 6.1-B 只读稳定性验证          READY
```

最终结论：

> Go2 X EDU V1.1.15 当前没有默认输出可直接接入的内部定位结果。保持
> `localization.available=false`，不调用未公开的 USLAM/SLAM API。此结果
> 不阻塞独立 UnitreeReadonlyAdapter 的真实只读稳定性验证。

