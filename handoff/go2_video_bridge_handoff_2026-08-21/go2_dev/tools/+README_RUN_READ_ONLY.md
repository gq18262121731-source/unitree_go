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

## Read-only UWB probe

`go2_uwb_readonly_probe.py` validates the complete Stage 1 UWB input chain
without starting Follow mode or publishing any DDS data. It checks:

- `rt/lowstate` samples as the DDS communication baseline;
- discovery of the robot-side `rt/uwbstate` writer;
- `rt/uwbstate` samples and their key measurement fields;
- `rt/uwbswitch` samples, if any;
- `uwbSwitch` reported by `rt/multiplestate`.

From the repository root, with `unitree_sdk2py` and `cyclonedds` available:

```bash
export PYTHONPATH="$PWD/go2_dev/unitree_sdk2_python"
python3 go2_dev/tools/go2_uwb_readonly_probe.py \
  --peer 192.168.123.161 \
  --interface enp0s8 \
  --seconds 30
```

On Windows, select the Go2-facing adapter by its IPv4 address:

```powershell
$env:PYTHONPATH = (Resolve-Path 'go2_dev\unitree_sdk2_python').Path
python go2_dev\tools\go2_uwb_readonly_probe.py `
  --peer 192.168.123.161 `
  --local-address 192.168.123.222 `
  --seconds 30
```

The final JSON event is `probe_result`. Important verdicts are:

- `UWB_SAMPLES_RECEIVED`: proceed to coordinate and unit calibration;
- `UWB_WRITER_PRESENT_NO_SAMPLES`: check Tracking Module power, pairing, and
  live Follow state;
- `DDS_BASELINE_FAILED`: fix the network, interface, or DDS domain before
  investigating UWB.

The probe creates DDS readers only. It contains no publisher, API client,
SportClient, or motion command.
