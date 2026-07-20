# Go2 Wireless Camera Collector

Independent read-only verification service for Unitree Go2 wireless camera capture and simulated upload.

This project does not call robot motion APIs. It does not modify `camera_79`, `selection-contest-dev`, or `go2-gateway`.

## Ports

```text
collector: 8091
mock receiver: 8092
```

## Required Real-Device Configuration

Do not assume the wireless interface or Go2 wireless IP.

For the currently verified wired bridge route, use:

```bash
export GO2_NETWORK_INTERFACE=eth0
export GO2_CAPTURE_FPS=15
export GO2_MJPEG_FPS=15
```

Set:

```bash
export GO2_NETWORK_INTERFACE=<wsl-wireless-interface>
export GO2_WIRELESS_IP=<go2-wireless-ip>
```

Check network first:

```bash
python scripts/check_wireless_network.py --interface "$GO2_NETWORK_INTERFACE" --ip "$GO2_WIRELESS_IP"
```

## Run Collector

```bash
source ~/.venvs/go2-gateway/bin/activate
cd "/mnt/e/笨笨狗/go2_dev/go2-wireless-camera/collector"
python -m pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8091 --workers 1
```

Endpoints:

```text
GET  /health
GET  /status
GET  /snapshot
GET  /stream.mjpg
POST /upload/start
POST /upload/stop
GET  /upload/status
```

For RTMP forwarding to a media server, see:

```text
../WIRED_BRIDGE_RUNBOOK.md
scripts/push_mjpeg_to_rtmp.sh
```

## Single Frame

```bash
python scripts/capture_one_frame.py --interface "$GO2_NETWORK_INTERFACE"
```

Output:

```text
~/go2-wireless-test/frame.jpg
```

## Soak Test

```bash
python scripts/soak_test_camera.py --duration-seconds 600
```

Report:

```text
~/go2-wireless-test/soak-report.json
```

## Simulated Upload

Start mock receiver on port 8092, then:

```bash
curl -X POST http://127.0.0.1:8091/upload/start
curl http://127.0.0.1:8091/upload/status
curl -X POST http://127.0.0.1:8091/upload/stop
```

The upload worker samples latest frames only. It does not upload every captured frame and does not block capture on upload failures.
