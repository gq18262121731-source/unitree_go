# DDS Discovery Diagnosis — Phase 5.1.2-A

采集日期：2026-07-24

范围：WLAN DDS discovery 只读诊断

结论：

```text
LOCAL_DDS_DOMAIN_CONFIG_OK
LOCAL_DDS_INTERFACE_BINDING_OK
LOCAL_MULTICAST_JOIN_OK
REMOTE_GO2_DISCOVERY_ABSENT
ROOT_CAUSE_NOT_YET_DISTINGUISHED
WLAN_MULTICAST_AB_TEST_PENDING
```

测试路线更正：

- 不切换到 Go2 自身 AP。
- 保持 PC 与 Go2 同时连接外部 WLAN。
- A/B 变量改为 E5576 multicast 策略，或换用另一个允许客户端 multicast
  的简单路由器。
- 本文第 9 节原“Go2 热点模式”方案已被
  `WLAN_DDS_MULTICAST_AB_TEST_PHASE_5_1_2_B.md` 取代，不应继续执行。

## 1. 安全边界

本阶段只读取环境变量、CycloneDDS XML、网卡属性、multicast/IGMP 状态，
并在已有只读 Subscriber 运行期间观察本机组播加入情况。

- 未修改业务代码。
- 未修改 `health_new`、Mock Provider、导航代码或安全门禁。
- 未创建 DDS 应用 Publisher 或 DataWriter。
- 未创建 SportClient。
- 未调用 `move()`、velocity、`cmd_vel` 或 locomotion API。
- 未启动 ROS2、Nav2、SLAM、L1、IMU 或导航。
- 未修改 Windows、WSL、路由器或 Go2 网络配置。

## 2. Domain 与环境变量

检查结果：

| 变量 | 结果 |
| --- | --- |
| `ROS_DOMAIN_ID` | 未设置 |
| `CYCLONEDDS_URI` | 未设置 |
| `RMW_*` | 未发现 |
| 其他包含 DDS 的环境变量 | 未发现 |
| 探针显式 Domain ID | `0` |

判定：

- 本机没有环境变量把探针覆盖到其他 Domain。
- 本次探针明确使用 Domain `0`。
- Go2 远端当前实际使用的 Domain 无法从现有 discovery 结果中反查，因此
  “本机 Domain 0 正确配置”不等于“远端一定也是 Domain 0”。

## 3. CycloneDDS 有效配置

当前 SDK 工作树的 `ChannelConfigHasInterface` 包含接口占位符和固定 Peer。
只读探针在进程内将接口与 Peer 改写为本次实际值。等效配置核心如下：

```xml
<Domain Id="any">
  <General>
    <Interfaces>
      <NetworkInterface
        name="eth1"
        priority="default"
        multicast="default"/>
    </Interfaces>
  </General>
  <Discovery>
    <Peers>
      <Peer Address="192.168.8.250"/>
    </Peers>
  </Discovery>
</Domain>
```

实际调用：

```text
ChannelFactoryInitialize(domain_id=0, networkInterface=eth1)
```

说明：

- 没有使用 hostname 自动选择；hostname 解析结果为 `127.0.1.1`，不适合
  作为 WLAN DDS 绑定依据。
- 显式接口是当前 WSL mirrored WLAN 接口 `eth1`。
- Peer 是 Go2 当前在线地址 `192.168.8.250`。
- multicast 保持 CycloneDDS 的 `default`，没有被 XML 禁用。

SDK 仓库中固定 Peer 的本地补丁默认仍写着 `192.168.123.161`，但诊断脚本
会在 DDS 初始化前于进程内改写为 `192.168.8.250`。本次有效配置不是旧
有线地址。

## 4. `eth1` 接口能力

接口状态：

```text
eth1: BROADCAST,MULTICAST,UP,LOWER_UP
IPv4: 192.168.8.254/24
MAC: 0c:9a:3c:ad:c0:9b
```

相关内核值：

| 项目 | 值 |
| --- | --- |
| `net.ipv4.conf.eth1.rp_filter` | `0` |
| `net.ipv4.conf.eth1.mc_forwarding` | `0` |
| `net.ipv4.icmp_echo_ignore_broadcasts` | `1` |

`mc_forwarding=0` 表示主机不充当 multicast 路由器，不妨碍本机作为 DDS
参与者收发接口上的 multicast。`rp_filter=0` 不会因反向路径过滤丢弃该
接口的合法返回流量。

## 5. Multicast / IGMP

空闲状态下，`eth1` 包含基础组：

```text
224.0.0.1
```

在 10 秒只读 DDS Subscriber 生命周期内，动态观测到：

```text
inet 239.255.0.1 users 2
link 01:00:5e:7f:00:01
```

`/proc/net/igmp` 同时显示：

```text
eth1
0100FFEF users=2
```

`0100FFEF` 是内核表中的字节序表示，对应 `239.255.0.1`。

判定：

- CycloneDDS 确实在正确接口 `eth1` 上加入了 discovery multicast 组。
- 本机接口支持 multicast。
- “CycloneDDS 绑定到 eth0/lo/docker0”不符合实测。
- “本机没有加入 discovery multicast 组”不符合实测。

## 6. 远端 Discovery 结果

即使本机正确加入 `239.255.0.1`：

- LowState 候选 Subscriber：0 样本；
- SportModeState 候选 Subscriber：0 样本；
- Built-in Discovery：只看到本机 DCPS 元数据端点；
- 未发现 Go2 远端 participant/publication；
- 未发现远端 `rt/*` topic。

因此当前断点位于：

```text
Local CycloneDDS participant
        |
        X  remote discovery absent
        |
Go2 SDK2 DDS participant
```

## 7. WLAN AP 隔离判断

已经通过的证据：

- PC 与 Go2 单播 ping 正常；
- ARP/邻居解析正常；
- Windows 与 WSL 均可直接访问 Go2；
- 本机 CycloneDDS 正确加入 multicast。

这使“完全客户端隔离”可能性降低，因为单播和二层邻居通信已通过；但不能
排除 AP 只过滤或抑制 WLAN 客户端之间的 UDP multicast/broadcast。

当前仍可能：

1. WLAN AP 不转发客户端 multicast；
2. Go2 `wlan0` 网络模式不发布 SDK2 DDS；
3. Go2 DDS 服务未运行或未绑定 `wlan0`；
4. Go2 使用不同 Domain；
5. Go2 发出 RTPS，但中间链路或主机防火墙丢弃。

现有证据不能在这些分支中做唯一归因。

## 8. 抓包能力

WSL 已安装：

```text
/usr/sbin/tcpdump
```

但当前非 root 用户没有捕获 `eth1` 的权限：

```text
You don't have permission to capture on that device
(socket: Operation not permitted)
```

本次没有请求 sudo 密码、没有修改 capability，也没有调整防火墙。因而
尚无包级证据证明 Go2 是否从 `192.168.8.250` 发出 RTPS/UDP。

## 9. WLAN multicast A/B 测试

固定 PC 与 Go2 同接外部 WLAN，只改变 E5576 multicast 策略，或更换为
明确允许客户端 multicast 的简单路由器。完整步骤与判定矩阵见：

```text
WLAN_DDS_MULTICAST_AB_TEST_PHASE_5_1_2_B.md
```

不切换 Go2 自身 AP，不改变 SDK、Domain、Topic、QoS 或安全门禁。

## 10. 阶段门禁

当前状态：

```text
Network: PASS
Local DDS initialization: PASS
Local interface binding: PASS
Local multicast join: PASS
Remote DDS discovery: FAIL
WLAN multicast A/B: PENDING
```

Phase 5.1.2-A 未闭环，继续停止：

- 不进入 L1；
- 不进入 IMU/里程计；
- 不进入 ROS2 Bridge；
- 不修改 Provider 或业务代码；
- 不开放运动权限。
