# VIRTUALBOX ENVIRONMENT REPORT — PHASE 5.2.3-VM

Updated: 2026-07-26 16:36 +08:00

## Scope

VirtualBox is being used as the available replacement for VMware. This phase
prepares an Ubuntu 22.04 VM for ROS2 Humble, CycloneDDS, and later read-only
Go2 Ethernet DDS validation.

This report does not authorize Phase 5.3, ROS2 Bridge work, DDS publishing,
robot motion control, TF, SLAM, Nav2, or changes to the frozen Mock system.

## Verified installation media and runtime

| Item | Detected value | Result |
| --- | --- | --- |
| VirtualBox installer | `E:\VirtualBox-7.2.14-174565-Win.exe` | PASS |
| VirtualBox installer SHA-256 | `5FB111F32A15763D519BF9EF23E0111153521F641CDE7460E5B8E895CA27A1D2` | PASS |
| VirtualBox version | `7.2.14r174565` | PASS |
| Oracle code signature | Oracle America, Inc.; valid | PASS |
| Bridged driver | Bound to Intel Wi-Fi and Realtek Ethernet | PASS |
| Ubuntu ISO | `E:\笨笨狗\ubuntu-22.04.5-desktop-amd64.iso` | PASS |
| Ubuntu ISO SHA-256 | `BFD1CEE02BC4F35DB939E69B934BA49A39A378797CE9AEE20F6E3E3E728FEFBF` | PASS |

## Host recovery

The earlier `VERR_NEM_MAP_PAGES_FAILED` condition was cleared by an actual
Windows restart.

| Check | Observed value | Result |
| --- | --- | --- |
| Free physical memory after restart | approximately 8.73 GiB | PASS |
| Nonpaged pool after restart | approximately 0.58 GiB | PASS |
| VM memory adjustment | 8192 MiB → 6144 MiB | PASS |

## VM definition

| Item | Configured value | Result |
| --- | --- | --- |
| Name | `Ubuntu-22.04.5-ROS2` | PASS |
| VM UUID | `022e2ae6-8ae0-4e77-b056-0aba584b907e` | PASS |
| VM directory | `E:\VirtualBox VMs\Ubuntu-22.04.5-ROS2` | PASS |
| vCPU | 4 | PASS |
| RAM | 6144 MiB | PASS |
| Disk | 100 GiB dynamic VDI | PASS |
| Firmware | EFI | PASS |
| Graphics | VMSVGA, 128 MiB VRAM | PASS |
| NIC 1 | Bridged to Intel Wi-Fi | PASS |
| NIC 2 | Bridged to Realtek Ethernet | PASS |
| NAT | Not configured | PASS |

## Ubuntu installation

The Ubiquity Live-session failure was worked around only in the temporary Live
RAM overlay. The installed system is clean, and the installation ISO has been
removed from the virtual optical drive.

| Item | Detected value | Result |
| --- | --- | --- |
| Ubuntu | 22.04.5 LTS Desktop | PASS |
| Kernel | `6.8.0-136-generic` after controlled reboot | PASS |
| Hostname | `go2-ros2-vm` | PASS |
| User | `go2`, member of `sudo` | PASS |
| Guest memory | approximately 5.8 GiB total | PASS |
| Root disk | `/dev/sda2`, approximately 99.5 GiB ext4 | PASS |
| EFI partition | `/dev/sda1`, 512 MiB vfat | PASS |
| Automatic login | disabled in GDM | PASS |
| Time zone | `Asia/Shanghai` | PASS |
| NTP | active and synchronized | PASS |

## Network report

| Path | Configuration or observation | Result |
| --- | --- | --- |
| NIC 1 / Wi-Fi bridge | `enp0s3`, `192.168.46.236/24` | PASS |
| Default route | `192.168.46.1` via `enp0s3` | PASS |
| Gateway ping | 3/3 replies, 0% loss | PASS |
| Internet ping | `www.baidu.com`, 3/3 replies, 0% loss | PASS |
| NIC 2 / Go2 bridge | `enp0s8`, `192.168.123.223/24` | CONFIGURED |
| NIC 2 default route/DNS | none; `ipv4.never-default=yes` | PASS |
| Windows Go2 Ethernet IP | `192.168.123.222/24` | CONFIGURED |
| Go2 IP | `192.168.123.161` | EXPECTED |
| Physical Windows Ethernet link | up at 1 Gbps | PASS |
| Guest-to-Go2 ping | 20/20 replies, 0% loss; avg 0.705 ms, max 2.308 ms | PASS |

The VM-side Go2 network and the physical Realtek Ethernet link are operational.
Windows uses `192.168.123.222/24`, the VM uses `192.168.123.223/24`, and Go2
uses `192.168.123.161`.

## Time stability gate

The strict 30-minute test started at approximately 2026-07-26 12:04 +08:00:

```text
PID: 2204
Script: ~/go2_validation/time_gate_30m.py
Samples: ~/go2_validation/time_gate_30m_samples.csv
Summary: ~/go2_validation/time_gate_30m_summary.json
Interval: 1 second
Target samples: 1801
```

Final result:

```text
Start: 2026-07-26 12:04:15.287066 +08:00
End: 2026-07-26 12:37:38.682997 +08:00
Samples: 1801
Wall elapsed: 2003.395718227 s
Monotonic elapsed: 2003.395711052 s
Wall-minus-monotonic drift: 0.007175 ms
Backward jumps: 0
Intervals over 2 s: 57
Minimum interval: 1000.246921 ms
Maximum interval: 9234.70699 ms
Status: PASS
```

The required no-rollback Gate passed. The long forward intervals moved wall
and monotonic time together and therefore represent VM scheduling stalls, not
clock rollback or NTP stepping. They remain a performance observation for
future real-time evaluation.

## Gate status

| Gate | Status |
| --- | --- |
| VirtualBox installed and signed | PASS |
| VM definition created | PASS |
| Ubuntu 22.04.5 VM installed | PASS |
| Bridged Wi-Fi and guest Internet | PASS |
| Go2 Ethernet interface configured | PASS |
| Physical Go2 Ethernet link | PASS |
| 30-minute no-rollback time gate | PASS |
| ROS2 Humble Desktop | PASS |
| CycloneDDS | PASS |
| Go2 ping 20/20 | PASS |
| Read-only SDK2 LowState/SportModeState | PASS |
| Phase 5.3 entry Gate | PASS; not entered |
