from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from .config import Settings, load_settings
from .errors import CollectorError, ErrorCode
from .frame_store import LatestFrameStore
from .unitree_camera import CameraCollector
from .upload_worker import UploadWorker


def create_app(
    settings: Settings | None = None,
    collector: CameraCollector | None = None,
    store: LatestFrameStore | None = None,
    upload_worker: UploadWorker | None = None,
    autostart: bool = True,
) -> FastAPI:
    settings = settings or load_settings()
    store = store or LatestFrameStore()
    collector = collector or CameraCollector(settings, store)
    upload_worker = upload_worker or UploadWorker(settings, store, collector.snapshot_stats)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.store = store
        app.state.collector = collector
        app.state.upload_worker = upload_worker
        if autostart:
            collector.start()
            if settings.upload_enabled:
                upload_worker.start()
        yield
        upload_worker.close()
        collector.stop()

    app = FastAPI(title="Go2 Wireless Camera Collector", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(CollectorError)
    async def collector_error_handler(request: Request, exc: CollectorError):
        return JSONResponse(status_code=exc.http_status, content={"success": False, "code": exc.code.value, "message": exc.message})

    @app.get("/health")
    def health() -> dict:
        frame_status = store.status(settings.frame_stale_seconds, time.monotonic())
        stats = collector.snapshot_stats()
        return {
            "success": True,
            "data": {
                "service": "go2-wireless-camera",
                "sdkInitialized": stats["initialized"],
                "networkInterface": settings.network_interface,
                "hasValidFrame": frame_status["hasFrame"],
                "frameAgeMs": frame_status["frameAgeMs"],
                "sdkErrors": stats["sdkErrorCount"],
                "sdkErrorCodes": stats["sdkErrorCodes"],
                "reconnectCount": stats["reconnectCount"],
                "upload": upload_worker.snapshot(),
            },
        }

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return _dashboard_html()

    @app.get("/status")
    def status() -> dict:
        return {
            "success": True,
            "data": {
                "collector": collector.snapshot_stats(),
                "frame": store.status(settings.frame_stale_seconds, time.monotonic()),
                "upload": upload_worker.snapshot(),
                "config": {
                    "robotId": settings.robot_id,
                    "cameraId": settings.camera_id,
                    "networkInterface": settings.network_interface,
                    "captureFpsLimit": settings.capture_fps,
                    "mjpegFps": settings.mjpeg_fps,
                    "uploadFps": settings.upload_fps,
                },
            },
        }

    @app.get("/snapshot")
    def snapshot() -> Response:
        frame = store.latest()
        if frame is None:
            raise CollectorError(ErrorCode.CAMERA_FRAME_UNAVAILABLE, "No camera frame is available.", 503)
        return Response(content=frame.jpeg, media_type="image/jpeg", headers={"X-Frame-Seq": str(frame.frame_seq)})

    @app.get("/stream.mjpg")
    def stream_mjpg(frames: Optional[int] = None) -> StreamingResponse:
        boundary = "go2frame"

        def generate():
            interval = 1.0 / max(0.1, settings.mjpeg_fps)
            sent = 0
            while True:
                frame = store.latest()
                if frame is not None:
                    yield (
                        f"--{boundary}\r\n"
                        "Content-Type: image/jpeg\r\n"
                        f"X-Frame-Seq: {frame.frame_seq}\r\n"
                        f"Content-Length: {len(frame.jpeg)}\r\n\r\n"
                    ).encode("ascii") + frame.jpeg + b"\r\n"
                    sent += 1
                    if frames is not None and sent >= frames:
                        break
                time.sleep(interval)

        return StreamingResponse(generate(), media_type=f"multipart/x-mixed-replace; boundary={boundary}")

    @app.post("/upload/start")
    def upload_start() -> dict:
        upload_worker.start()
        return {"success": True, "data": upload_worker.snapshot()}

    @app.post("/upload/stop")
    def upload_stop() -> dict:
        upload_worker.stop()
        return {"success": True, "data": upload_worker.snapshot()}

    @app.get("/upload/status")
    def upload_status() -> dict:
        return {"success": True, "data": upload_worker.snapshot()}

    return app


app = create_app()


def _dashboard_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Go2 视频桥接后台</title>
  <style>
    :root {
      color-scheme: dark;
      font-family: Arial, "Microsoft YaHei", sans-serif;
      background: #111318;
      color: #edf1f7;
    }
    body {
      margin: 0;
      min-height: 100vh;
      background: #111318;
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 16px 20px;
      border-bottom: 1px solid #2a2f3a;
      background: #171a21;
    }
    h1 {
      margin: 0;
      font-size: 20px;
      font-weight: 600;
    }
    main {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 320px;
      gap: 16px;
      padding: 16px;
    }
    .video-shell {
      min-height: 0;
      background: #050608;
      border: 1px solid #2a2f3a;
      border-radius: 6px;
      overflow: hidden;
    }
    img {
      display: block;
      width: 100%;
      height: calc(100vh - 120px);
      object-fit: contain;
      background: #050608;
    }
    aside {
      border: 1px solid #2a2f3a;
      border-radius: 6px;
      padding: 14px;
      background: #171a21;
    }
    .row {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 0;
      border-bottom: 1px solid #2a2f3a;
      font-size: 14px;
    }
    .row:last-child { border-bottom: 0; }
    .label { color: #aab3c2; }
    .value { text-align: right; }
    .ok { color: #5ee08a; }
    .warn { color: #ffd166; }
    .bad { color: #ff6b6b; }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; }
      img { height: 56vh; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Go2 视频桥接后台</h1>
    <span id="state" class="warn">连接中</span>
  </header>
  <main>
    <section class="video-shell">
      <img src="/stream.mjpg" alt="Go2 第一视角视频">
    </section>
    <aside>
      <div class="row"><span class="label">服务</span><span class="value" id="service">-</span></div>
      <div class="row"><span class="label">网卡</span><span class="value" id="iface">-</span></div>
      <div class="row"><span class="label">画面</span><span class="value" id="frame">-</span></div>
      <div class="row"><span class="label">采集 FPS</span><span class="value" id="fps">-</span></div>
      <div class="row"><span class="label">延迟</span><span class="value" id="age">-</span></div>
      <div class="row"><span class="label">SDK 错误</span><span class="value" id="errors">-</span></div>
    </aside>
  </main>
  <script>
    async function refreshStatus() {
      try {
        const res = await fetch('/status', { cache: 'no-store' });
        const json = await res.json();
        const data = json.data;
        const frame = data.frame.latestFrame;
        document.getElementById('state').textContent = data.frame.hasFrame ? '在线' : '等待画面';
        document.getElementById('state').className = data.frame.hasFrame ? 'ok' : 'warn';
        document.getElementById('service').textContent = 'Go2 Bridge';
        document.getElementById('iface').textContent = data.config.networkInterface;
        document.getElementById('frame').textContent = frame ? `${frame.width}x${frame.height} #${frame.frameSeq}` : '-';
        document.getElementById('fps').textContent = data.collector.captureFps.toFixed(1);
        document.getElementById('age').textContent = `${Math.round(data.frame.frameAgeMs || 0)} ms`;
        document.getElementById('errors').textContent = data.collector.sdkErrorCount;
      } catch (error) {
        document.getElementById('state').textContent = '离线';
        document.getElementById('state').className = 'bad';
      }
    }
    refreshStatus();
    setInterval(refreshStatus, 1000);
  </script>
</body>
</html>"""
