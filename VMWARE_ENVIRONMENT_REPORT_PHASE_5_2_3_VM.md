# VMWARE ENVIRONMENT REPORT — PHASE 5.2.3-VM

Generated: 2026-07-25 19:56:19 +08:00

## Scope

Prepare a Windows-hosted VMware Ubuntu 22.04 virtual machine for ROS2 Humble,
CycloneDDS, and later read-only Go2 Ethernet DDS validation.

This report does not authorize Phase 5.3, ROS2 Bridge work, DDS publishing, or
robot motion control. The existing competition Mock system remains frozen.

## Windows host

| Item | Detected value | Result |
| --- | --- | --- |
| Host | LENOVO 82JD | PASS |
| OS | Microsoft Windows 11 Home China, 10.0.26200 (build 26200) | INFO |
| CPU | Intel Core i7-11800H | PASS |
| Physical cores | 8 | PASS |
| Logical processors | 16 | PASS |
| Installed memory | 15.84 GiB | PASS |
| Free memory at inspection | 7.40 GiB | INFO |
| E: free space | 428.53 GiB | PASS |

## Virtualization

| Item | Detected value | Result |
| --- | --- | --- |
| Windows hypervisor present | Yes | PASS: firmware virtualization is active |
| `hvhost` service | Running | INFO |
| `vmcompute` service | Running | INFO |
| Virtual Machine Platform | Enabled (`InstallState=1`) | INFO |
| Windows Subsystem for Linux feature | Enabled (`InstallState=1`) | INFO |
| Windows Hypervisor Platform | Disabled (`InstallState=2`) | INFO |

`Win32_Processor` reports the raw VT-x/SLAT flags as false while a Windows
hypervisor is active. This is expected masking behavior and is not treated as
evidence that BIOS virtualization is disabled. VMware runtime compatibility and
performance must still be verified after VMware is installed.

## VMware

| Item | Detected value | Result |
| --- | --- | --- |
| VMware Workstation/Player installation | Not detected | BLOCKED |
| VMware version | Not available | PENDING |
| `vmrun` | Not detected | PENDING |
| Existing `.vmx` in scoped locations | None found | INFO |
| Target release | VMware Workstation Pro 26H1 for Windows (64-bit) | SELECTED |
| Official download | Broadcom Support Portal login and Terms acceptance required | USER ACTION |

Scoped VM search locations:

- `E:\笨笨狗`
- `%USERPROFILE%\Documents\Virtual Machines`

## Ubuntu installation media

| Item | Detected value | Result |
| --- | --- | --- |
| ISO | `E:\笨笨狗\ubuntu-22.04.5-desktop-amd64.iso` | PASS |
| Size | 4,762,707,968 bytes | PASS |
| SHA-256 | `BFD1CEE02BC4F35DB939E69B934BA49A39A378797CE9AEE20F6E3E3E728FEFBF` | PASS (verified previously) |

## Proposed VM allocation

The host has 16 GiB RAM, so allocating 12 GiB would leave too little headroom
for Windows, Codex, and the frozen Mock environment. Use this initial profile:

| Resource | Allocation |
| --- | --- |
| vCPU | 4 processors/cores total |
| RAM | 8 GiB |
| Virtual disk | 100 GiB, thin provisioned, stored on E: |
| Firmware | UEFI |
| Guest OS | Ubuntu 64-bit |
| Install media | Ubuntu 22.04.5 Desktop amd64 |
| Initial network | Bridged; NAT is not an acceptance configuration |

RAM may be raised to 10 GiB only after measuring host pressure with the Mock
environment running.

## Step 1 verdict

Hardware capacity: **PASS**

Firmware virtualization evidence: **PASS**

VMware software: **BLOCKED — not installed**

Overall Step 1: **CONDITIONAL PASS**. Install a supported VMware Workstation
release, record its exact version, and confirm that a 64-bit Ubuntu VM can start
before proceeding to Ubuntu installation.

Official release checks performed on 2026-07-25:

- VMware announced Workstation Pro 26H1 as generally available on 2026-05-14.
- Workstation Pro 26H1 is free for commercial, educational, and personal use.
- The Windows build is now a 64-bit application.
- Broadcom requires an authenticated Support Portal session and explicit Terms
  acceptance before activating the installer download.
