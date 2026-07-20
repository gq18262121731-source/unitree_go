# Resume Read-only Verification

Date: 2026-07-13

## Latest Re-check

```text
Windows Ethernet: 192.168.123.222/24
WSL eth0: UP, 192.168.123.222/24
Route to 192.168.123.161: dev eth0 src 192.168.123.222
Ping 192.168.123.161 via eth0: 4 received, 0% packet loss
```

The physical Ethernet link has recovered. Read-only SDK state, camera, and Real Gateway checks have been executed.

## Completed Offline Work

```text
Linux virtual environment: /home/est1/.venvs/go2-gateway
Project path: /mnt/e/笨笨狗/go2_dev/go2-gateway
unitree_sdk2py: installed
CycloneDDS: 0.10.2
OpenCV: installed
NumPy: installed
Tests: 18 passed
Motion-control safety switch: GO2_CONTROL_ENABLED=false
```

Latest test result after read-only safety and reporting updates:

```text
Tests: 19 passed
```

## Helper Script

```text
scripts/verify_readonly_real.sh
```

The script performs:

```text
1. Network preflight
2. Environment check
3. Read-only state verification
4. Optional read-only camera verification with --camera
```

It does not call stand, lie-down, move, low-level joint control, or motor control.

Gateway helper script:

```text
scripts/verify_gateway_readonly.sh
```

It starts a single-worker Real Gateway, checks `/health`, `/api/robot/status`, and `/api/robot/camera/snapshot`, then stops the Gateway process.

## Verified Results

Read-only state:

```text
online: true
stateStale: false
mode: 0
gaitType: 0
battery percentage: observed 89-98 during verification
attitude: roll / pitch / yaw available
motion velocity fields: available
```

Camera:

```text
10 consecutive snapshots captured
Output directory: /home/est1/go2-camera-test
Image size: 1920x1080
OpenCV decode: passed for all 10 images
```

Gateway Real read-only:

```text
Command: scripts/verify_gateway_readonly.sh
Mode: real
Worker count: 1
Network interface: eth0
health: passed
status: passed
snapshot: passed
Snapshot path: /home/est1/go2-gateway-snapshot.jpg
Snapshot size: 1920x1080
control.enabled in status: false
Gateway process cleanup: no uvicorn process remained
```

DDS packet capture:

```text
Not completed by Codex because tcpdump requires sudo password in this session.
Manual command:
sudo timeout 20 tcpdump -ni eth0 -vv 'src host 192.168.123.161 and (udp or igmp)' -c 50
```

## Re-run Commands

To re-run full read-only verification:

```bash
cd "/mnt/e/笨笨狗/go2_dev/go2-gateway"
./scripts/verify_readonly_real.sh --camera
./scripts/verify_gateway_readonly.sh
```

To re-run only state:

```bash
cd "/mnt/e/笨笨狗/go2_dev/go2-gateway"
./scripts/verify_readonly_real.sh
```

To re-run camera after state:

```bash
./scripts/verify_readonly_real.sh --camera
```

## Do Not Run Yet

```text
/api/robot/stand
/api/robot/lie-down
/api/robot/move
scripts/verify_motion.py
```
