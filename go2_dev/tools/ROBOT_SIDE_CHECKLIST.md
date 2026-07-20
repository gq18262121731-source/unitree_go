# Go2 robot-side checklist for silent DDS

Current PC-side evidence:

- Windows Ethernet is `Up` at 1 Gbps.
- PC addresses on the robot subnet: `192.168.123.99` and `192.168.123.222`.
- Ubuntu WSL mirrored interface `eth0` is on `192.168.123.x`.
- Go2 responds to ICMP ping at `192.168.123.161`.
- PC sends CycloneDDS discovery to multicast `239.255.0.1:7400` and unicast
  `192.168.123.161:7410/7412/...`.
- Go2 does not send DDS/UDP replies, so read-only status subscription receives
  zero messages.
- Latest recheck produced the same result: `lowstate_messages=0` and
  `sportstate_messages=0`, while tcpdump showed PC-to-Go2 discovery packets and
  no Go2-to-PC DDS packets.
- `192.168.123.18` is not reachable on this setup.
- TCP `192.168.123.161:80` is open but returns no useful SDK status page.

## Check on the Unitree Go App

1. Confirm the robot has completed first-use activation/binding in Unitree Go.
2. Confirm the app can connect to the robot and show live robot status.
3. Look for a developer, SDK, wired development, Ethernet, or debugging mode.
4. If there is such a setting, enable only the development/SDK communication
   setting. Do not trigger stand, walk, dance, gesture, or remote-control tests.
5. After changing any app setting, reboot Go2 once, keep Ethernet connected, and
   rerun:

```powershell
Set-Location <your go2_dev folder>
.\tools\check_go2_network.ps1
```

Then in Ubuntu:

```bash
cd <your go2_dev folder as mounted in Ubuntu>
python3 tools/go2_read_only_status.py eth0 --seconds 20
```

## If the app shows no SDK option

- Check whether your Go2 package includes the expansion dock / onboard Robot PC.
  Some documentation refers to `192.168.123.18` as the Robot PC address, but it
  is not reachable in the current setup.
- If a Robot PC / expansion dock is installed, verify its power and Ethernet
  connection.
- If the unit is Go2 EDU without a separate Robot PC, ask Unitree support which
  firmware/app setting enables SDK2 DDS status topics on wired Ethernet.

## Still safe to run

These checks are read-only:

- `ping 192.168.123.161`
- `.\tools\check_go2_network.ps1`
- `python3 tools/go2_read_only_status.py eth0 --seconds 20`
- `sudo tcpdump -ni eth0 udp`

Do not run:

- `go2_sport_client.py`
- `go2_stand_example.py`
- obstacle-avoidance switch examples
- VUI/light/volume examples
- any script that imports `ChannelPublisher`, `SportClient`, `LowCmd`, or calls
  `Move`, `Stand`, `Set`, or `Write`
