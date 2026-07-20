# Go2 Wired Bridge Video Runbook

Current recommended first-stage route:

```text
Go2 wired camera -> bridge computer -> local MJPEG -> RTMP media server -> community page
```

The Go2 built-in AP can be used for App management, but current SDK camera capture is verified on wired `eth0`.

## Start Local Camera Bridge

```bash
cd "/mnt/e/笨笨狗/go2_dev/go2-wireless-camera/collector"
source ~/.venvs/go2-gateway/bin/activate
export GO2_NETWORK_INTERFACE=eth0
export GO2_CAPTURE_FPS=15
export GO2_MJPEG_FPS=15
uvicorn app.main:app --host 0.0.0.0 --port 8091
```

Local verification:

```text
http://127.0.0.1:8091/health
http://127.0.0.1:8091/status
http://127.0.0.1:8091/snapshot
http://127.0.0.1:8091/stream.mjpg
```

## Push To RTMP

Install `ffmpeg` first if needed.

```bash
cd "/mnt/e/笨笨狗/go2_dev/go2-wireless-camera/collector"
RTMP_URL=rtmp://your-server/live/go2x-001 \
MJPEG_URL=http://127.0.0.1:8091/stream.mjpg \
VIDEO_FPS=15 \
bash scripts/push_mjpeg_to_rtmp.sh
```

Recommended first acceptance target:

```text
15 minutes continuous playback, no bridge crash, no frequent frame stalls.
```
