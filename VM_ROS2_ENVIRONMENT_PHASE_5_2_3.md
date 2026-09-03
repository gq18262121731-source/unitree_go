# VM ROS2 ENVIRONMENT — PHASE 5.2.3

Updated: 2026-07-26 16:36 +08:00

Overall status: PASS  
Phase 5.3 entry Gate: PASS; Phase 5.3 has not been entered

## Environment

| Required item | Verified value | Status |
| --- | --- | --- |
| Hypervisor | Oracle VirtualBox `7.2.14r174565` | PASS |
| Ubuntu | Ubuntu 22.04.5 LTS Desktop amd64 | PASS |
| Kernel | `6.8.0-136-generic` after controlled reboot | PASS |
| vCPU / Memory | 4 vCPU / 6144 MiB configured | PASS |
| Disk | 100 GiB dynamic VDI; 99.5 GiB root partition | PASS |
| Network mode | Bridged only; no NAT | PASS |
| Ubuntu Internet IP | `192.168.46.236/24` on `enp0s3` | PASS |
| Ubuntu Go2 IP | `192.168.123.223/24` on `enp0s8` | PASS |
| Go2 IP | `192.168.123.161` | EXPECTED |

## Network validation

| Test | Result | Status |
| --- | --- | --- |
| Guest → Wi-Fi gateway | 3/3 replies, 0% loss | PASS |
| Guest → Internet | 3/3 replies, 0% loss | PASS |
| Guest → Go2 | 20/20 replies, 0% loss; avg 0.705 ms, max 2.308 ms | PASS |
| Physical Realtek Ethernet | link up at 1 Gbps | PASS |

The physical Ethernet carrier is active. Windows uses `192.168.123.222/24`,
the Ubuntu VM uses `192.168.123.223/24`, and Go2 uses `192.168.123.161`.

## Time stability

| Check | Result | Status |
| --- | --- | --- |
| Time zone | `Asia/Shanghai` | PASS |
| NTP service | active | PASS |
| System clock synchronized | yes | PASS |
| 30-minute sample process | 1801 samples; 2003.395718227 s wall elapsed | PASS |
| Backward jumps | 0 | PASS |
| Wall-minus-monotonic drift | 0.007175 ms | PASS |
| Forward scheduling stalls | 57 intervals over 2 s; maximum 9234.70699 ms | OBSERVATION |

Artifacts inside the VM:

```text
~/go2_validation/time_gate_30m.py
~/go2_validation/time_gate_30m_samples.csv
~/go2_validation/time_gate_30m_summary.json
~/go2_validation/time_gate_30m.out
```

## ROS 2 and DDS

| Check | Result | Status |
| --- | --- | --- |
| ROS 2 Humble Desktop | `ros-humble-desktop 0.10.0-1jammy.20260612.213429` | PASS |
| ROS 2 CLI | `/opt/ros/humble/bin/ros2`; `ros2 node list` exit code 0 | PASS |
| RViz2 / tf2 / rosbag2 packages | installed through the Desktop metapackage | PASS |
| CycloneDDS RMW | `ros-humble-rmw-cyclonedds-cpp 1.3.4-1jammy.20260605.121029` | PASS |
| `RMW_IMPLEMENTATION` | `rmw_cyclonedds_cpp`, automatically loaded from `~/.bashrc` | PASS |
| `ros2 doctor --report` | exit code 0; middleware name `rmw_cyclonedds_cpp` | PASS |
| Post-reboot NTP | `NTP=yes`; `NTPSynchronized=yes` | PASS |
| Temporary installation proxy | guest apt configuration removed; host proxy stopped | PASS |

## SDK2 read-only validation

| DDS topic | Samples | Observed frequency | Status |
| --- | ---: | ---: | --- |
| `rt/lowstate` | 4900 over 10.001 s | 489.956 Hz | PASS |
| `rt/sportmodestate` | 2944 over 10.001 s | 294.373 Hz | PASS |

Validation used the official Unitree SDK2 repository at commit `7740f8b`.
The validation executable contains only two `ChannelSubscriber` instances and
reported `publisher_count=0` and exit code 0. No DDS publisher, SportClient,
or robot motion API was used.

Artifacts inside the VM:

```text
~/go2_validation/sdk2_readonly_validation/
~/go2_validation/sdk2_readonly_result.txt
```

## Phase 5.3 entry Gate

- [x] Ubuntu 22.04 VM runs normally
- [x] 30 minutes with no clock rollback
- [x] ROS 2 Humble Desktop operates normally
- [x] CycloneDDS operates normally
- [x] Ubuntu VM reaches Go2 over physical Ethernet
- [x] LowState read-only subscriber receives samples
- [x] SportModeState read-only subscriber receives samples

Phase 5.2.3-VM is complete. Work stops here; DDS → ROS2 Bridge has not been
started.
