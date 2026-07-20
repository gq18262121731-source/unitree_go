# Running the safe read-only Go2 check

Use this after the physical Ethernet link is up.

## 1. Check Windows Ethernet

From PowerShell opened in this `tools` folder:

```powershell
.\check_go2_network.ps1
```

If the adapter is up but not on `192.168.123.x`, open PowerShell as
Administrator in this `tools` folder and run:

```powershell
.\configure_go2_ethernet_admin.ps1
```

You can also double-click `run_configure_go2_ethernet_as_admin.cmd`.

Passing result:

- Ethernet status is `Up`
- Adapter has an address like `192.168.123.222`
- At least one Go2 address responds

## 2. Run Ubuntu read-only subscriber

The current Ubuntu install is WSL2 NAT. If WSL cannot see the robot Ethernet
interface, run this from native Ubuntu, Ubuntu Live USB, bridged VM, or WSL with
mirrored networking.

```bash
cd /mnt/e/*/go2_dev
ip -br addr
python3 tools/go2_read_only_status.py <robot_ethernet_interface> --seconds 15
```

The script only subscribes to `rt/lowstate` and `rt/lf/sportmodestate`.
