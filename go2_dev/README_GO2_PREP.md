# Unitree Go2 development preparation

This workspace is for safe first-stage Go2 development. The current stage is
read-only: verify the PC, SDK, network, and state subscription before running
any command that can move or reconfigure the robot.

## What is ready

- Official SDK repositories have been cloned locally:
  - `unitree_sdk2` at `7740f8b`
  - `unitree_sdk2_python` at `37116c5`
  - `unitree_ros2` at `668d1ec`
- Python SDK is installed in the Ubuntu user environment:
  - `unitree_sdk2py`
  - `cyclonedds==0.10.2`
  - `numpy==1.24.4`
  - `opencv-python==5.0.0.93`
- Local read-only helper:
  - `tools/go2_read_only_status.py`

## Current blockers before talking to Go2

- Windows Ethernet is up and configured on the Go2 subnet.
- WSL2 mirrored networking is enabled, and Ubuntu sees `eth0` on
  `192.168.123.x`.
- Go2 responds to ping at `192.168.123.161`.
- Read-only DDS discovery is sent from the PC to `192.168.123.161`, but Go2 has
  not replied with DDS/UDP traffic yet.

Do not run robot control examples until read-only DDS status subscription
receives data.

## Safe network checklist

1. Power on Go2 and connect the cable to the robot Ethernet port and PC Ethernet
   port.
2. On Windows, confirm `以太网` changes from `Disconnected` to `Up`.
3. Configure the PC Ethernet IPv4 address manually, for example:
   - IP: `192.168.123.222`
   - Mask: `255.255.255.0`
   - Gateway: leave blank
   Or open PowerShell as Administrator and run:
   `.\tools\configure_go2_ethernet_admin.ps1`
   You can also double-click:
   `tools\run_configure_go2_ethernet_as_admin.cmd`
4. Test common Go2 addresses from Windows first:
   - `ping 192.168.123.161`
   - `ping 192.168.123.18`
5. Make Ubuntu see the robot network. Preferred options:
   - WSL mirrored networking is currently configured in `C:\Users\Test1\.wslconfig`.
   - Use native Ubuntu or an Ubuntu live USB if WSL DDS discovery remains blocked.
   - Use a bridged VM attached to the Realtek Ethernet adapter.
6. If ping works but DDS returns zero messages, confirm Go2 has completed App
   activation and that the robot-side SDK/DDS service is enabled.
   See `tools/ROBOT_SIDE_CHECKLIST.md`.

## Allowed first-stage commands

Run only after the network checklist passes and replace `<iface>` with the
actual robot Ethernet interface name:

```bash
cd /mnt/e/笨笨狗/go2_dev
python3 tools/go2_read_only_status.py <iface>
```

The helper subscribes to Go2 status topics only. It does not create publishers
or service clients.

If the robot is reachable but DDS is silent, capture evidence without sending
commands:

```bash
sudo tcpdump -ni eth0 udp
python3 tools/go2_read_only_status.py eth0 --seconds 10
```

Expected for a working SDK connection: UDP packets from Go2 back to the PC and
non-zero `lowstate_messages` or `sportstate_messages`.

## Do not run yet

Avoid these examples in the first stage because they can move the robot or
change robot-side state:

- `unitree_sdk2_python/example/go2/high_level/go2_sport_client.py`
- `unitree_sdk2_python/example/go2/low_level/go2_stand_example.py`
- `unitree_sdk2_python/example/go2/high_level/go2_utlidar_switch.py`
- `unitree_sdk2_python/example/obstacles_avoid/*`
- `unitree_sdk2_python/example/vui_client/vui_client_example.py`
- `unitree_sdk2_python/example/motionSwitcher/motion_switcher_example.py`
- Any C++ or ROS2 sample that publishes `cmd`, `lowcmd`, velocity, posture,
  sport mode, obstacle-avoidance switch, light, volume, or motion commands.

## Next stage after read-only status succeeds

1. Save the working interface name and Go2 IP.
2. Record a short successful status log.
3. Install ROS2 only if a ROS workflow is needed.
4. Review each motion example line by line before running it, with the robot in
   an open area and manual control ready.
