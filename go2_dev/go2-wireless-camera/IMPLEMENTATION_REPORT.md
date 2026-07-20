# Go2 Wireless Camera Verification Implementation Report

Date: 2026-07-14

## Scope

Created an independent verification project for Go2 wireless camera capture and simulated upload:

```text
go2-wireless-camera/
  collector/
  mock_receiver/
```

This project does not modify:

```text
camera_79
selection-contest-dev
go2-gateway
```

It does not call robot motion APIs.

## Observed Network Context

```text
Windows WLAN adapter: WLAN
Windows WLAN IPv4: 192.168.8.253/24
WSL wireless-side interface: eth1 / 192.168.8.253/24
Windows wired Go2 adapter: 192.168.123.222/24
WSL wired Go2 interface: eth0 / 192.168.123.222/24
Go2 wired IP: 192.168.123.161
Go2 wireless IP: not confirmed
```

Real wireless camera verification was not executed because the Go2 wireless IP has not been provided or discovered from the Unitree App/network configuration. No broad subnet scan was performed.

## Collector

Path:

```text
collector/
```

Implemented:

```text
Single background capture thread
Single Unitree VideoClient instance
ChannelFactoryInitialize(0, network_interface)
VideoClient.SetTimeout / Init / GetImageSample
Latest JPEG frame cache
Frame metadata: seq, capturedAt, width, height, size, SDK code, latency, FPS
No-frame detection
SDK error statistics
Reconnect with exponential backoff
HTTP snapshot
MJPEG preview
Upload start / stop / status
Sampled JPEG upload
Heartbeat upload
```

Collector endpoints:

```text
GET  /health
GET  /status
GET  /snapshot
GET  /stream.mjpg
POST /upload/start
POST /upload/stop
GET  /upload/status
```

## Mock Receiver

Path:

```text
mock_receiver/
```

Implemented:

```text
POST /api/video/frame
POST /api/video/heartbeat
GET  /latest.jpg
GET  /stream.mjpg
GET  /status
```

The receiver validates:

```text
X-Upload-Token
robot_id
camera_id
frame_seq
captured_at
JPEG format
frame size
```

It stores only:

```text
latest JPEG frame
latest 100 metadata records
latest heartbeat
```

## Scripts

```text
collector/scripts/check_wireless_network.py
collector/scripts/capture_one_frame.py
collector/scripts/soak_test_camera.py
```

Real-device order:

```bash
cd "/mnt/e/笨笨狗/go2_dev/go2-wireless-camera/collector"
source ~/.venvs/go2-gateway/bin/activate

export GO2_NETWORK_INTERFACE=eth1
export GO2_WIRELESS_IP=<go2-wireless-ip>

python scripts/check_wireless_network.py --interface "$GO2_NETWORK_INTERFACE" --ip "$GO2_WIRELESS_IP"
python scripts/capture_one_frame.py --interface "$GO2_NETWORK_INTERFACE"
uvicorn app.main:app --host 127.0.0.1 --port 8091 --workers 1
python scripts/soak_test_camera.py --duration-seconds 600
```

## Tests

Commands run:

```bash
cd "/mnt/e/笨笨狗/go2_dev/go2-wireless-camera/collector"
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q

cd "/mnt/e/笨笨狗/go2_dev/go2-wireless-camera/mock_receiver"
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q
```

Results:

```text
collector: 13 passed
mock_receiver: 7 passed
total: 20 passed
```

Script/module syntax check:

```text
passed
```

## Known Limits

```text
Go2 wireless IP is still unknown.
Real wireless single-frame capture was not executed.
10-minute real wireless soak test was not executed.
Wi-Fi disconnect/recovery test was not executed.
No camera_79 integration was attempted.
No selection-contest-dev integration was attempted.
```

## Next Manual Inputs Needed

Provide the Go2 wireless IP from the Unitree App or robot network configuration, then run the real-device verification commands above.

