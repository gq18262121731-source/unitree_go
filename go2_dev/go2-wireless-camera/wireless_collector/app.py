from __future__ import annotations

import asyncio
import os
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import cv2
from aiortc import MediaStreamTrack
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse

from unitree_webrtc_connect import UnitreeWebRTCConnection, WebRTCConnectionMethod


SERVICE_ID = "go2-wireless-camera"
API_VERSION = "1"
SERVICE_VERSION = "1.0.0"
FRAME_STALE_SECONDS = max(1.0, float(os.environ.get("GO2_FRAME_STALE_SECONDS", "3")))


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def classify_connection_error(exc: Exception) -> str:
    message = str(exc).lower()
    if any(token in message for token in ("unreachable", "timed out", "timeout", "no route")):
        return "ROBOT_UNREACHABLE"
    if any(token in message for token in ("disconnect", "closed", "connection lost")):
        return "WEBRTC_DISCONNECTED"
    return "WEBRTC_CONNECT_FAILED"


@dataclass(frozen=True)
class Frame:
    jpeg: bytes
    sequence: int
    captured_at: str
    width: int
    height: int
    fps: float


class WirelessCamera:
    def __init__(self, capture_fps: float = 15.0, jpeg_quality: int = 80) -> None:
        self.capture_fps = max(1.0, capture_fps)
        self.jpeg_quality = max(40, min(95, jpeg_quality))
        self.connection_mode = os.environ.get("GO2_WEBRTC_MODE", "ap").strip().lower()
        self.robot_ip = os.environ.get("GO2_WEBRTC_IP", "192.168.12.1").strip()
        self.aes_key = os.environ.get("GO2_AES_KEY", "").strip() or None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._connection: UnitreeWebRTCConnection | None = None
        self._frame: Frame | None = None
        self._last_frame_monotonic: float | None = None
        self._last_encode_monotonic = 0.0
        self._started_monotonic = 0.0
        self._started_at: str | None = None
        self._sequence = 0
        self._error_count = 0
        self._reconnect_count = 0
        self._client_count = 0
        self._last_error_code: str | None = None
        self._last_error: str | None = None
        self._connected = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._started_monotonic = time.monotonic()
        self._started_at = now_iso()
        self._thread = threading.Thread(target=self._run, name="go2-webrtc-camera", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5.0)

    def latest(self) -> Frame | None:
        with self._lock:
            return self._frame

    def register_client(self) -> None:
        with self._lock:
            self._client_count += 1

    def unregister_client(self) -> None:
        with self._lock:
            self._client_count = max(0, self._client_count - 1)

    def status(self) -> dict:
        with self._lock:
            now = time.monotonic()
            age_ms = None if self._last_frame_monotonic is None else (now - self._last_frame_monotonic) * 1000.0
            has_frame = self._frame is not None and age_ms is not None and age_ms < FRAME_STALE_SECONDS * 1000.0
            active_error_code = self._last_error_code
            active_error_message = self._last_error

            if has_frame:
                video_state = "ready"
                active_error_code = None
                active_error_message = None
            elif self._connected and self._frame is None:
                video_state = "no-frame"
                if now - self._started_monotonic >= FRAME_STALE_SECONDS:
                    active_error_code = "NO_FRAME_TIMEOUT"
                    active_error_message = "WebRTC 已连接，但尚未收到有效视频帧"
            elif self._connected:
                video_state = "stalled"
                active_error_code = "FRAME_STALLED"
                active_error_message = "视频帧已停止更新"
            elif self._reconnect_count > 0:
                video_state = "reconnecting"
                active_error_code = active_error_code or "RECONNECTING"
                active_error_message = active_error_message or "正在重新连接 Go2 无线视频"
            else:
                video_state = "connecting"

            resolution = None if self._frame is None else {
                "width": self._frame.width,
                "height": self._frame.height,
            }
            return {
                "serviceState": "running",
                "videoState": video_state,
                "startedAt": self._started_at,
                "connectionMode": f"Go2 {self.connection_mode.upper()} / WebRTC",
                "robotIp": self.robot_ip,
                "connected": self._connected,
                "hasFrame": has_frame,
                "lastFrameAt": None if self._frame is None else self._frame.captured_at,
                "frameAgeMs": age_ms,
                "frameCount": self._sequence,
                "captureFps": self._frame.fps if self._frame else 0.0,
                "fps": self._frame.fps if self._frame else 0.0,
                "resolution": resolution,
                "source": {
                    "device": "Go2",
                    "networkMode": self.connection_mode.upper(),
                    "transport": "WebRTC",
                    "robotIp": self.robot_ip,
                },
                "clientCount": self._client_count,
                "errorCount": self._error_count,
                "reconnectCount": self._reconnect_count,
                "lastErrorCode": active_error_code,
                "lastError": active_error_message,
                "error": None if active_error_code is None else {
                    "code": active_error_code,
                    "message": active_error_message,
                },
                "latestFrame": None if self._frame is None else {
                    "sequence": self._frame.sequence,
                    "capturedAt": self._frame.captured_at,
                    "width": self._frame.width,
                    "height": self._frame.height,
                    "size": len(self._frame.jpeg),
                },
            }

    def _run(self) -> None:
        asyncio.run(self._connection_loop())

    async def _connection_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.connection_mode == "sta":
                    connection = UnitreeWebRTCConnection(
                        WebRTCConnectionMethod.LocalSTA,
                        ip=self.robot_ip,
                        aes_128_key=self.aes_key,
                    )
                else:
                    connection = UnitreeWebRTCConnection(
                        WebRTCConnectionMethod.LocalAP,
                        aes_128_key=self.aes_key,
                    )
                self._connection = connection
                await connection.connect()
                connection.video.add_track_callback(self._receive_video)
                connection.video.switchVideoChannel(True)
                with self._lock:
                    self._connected = True
                    self._last_error_code = None
                    self._last_error = None

                while connection.isConnected and not self._stop.is_set():
                    await asyncio.sleep(0.25)
            except Exception as exc:
                with self._lock:
                    self._error_count += 1
                    self._last_error_code = classify_connection_error(exc)
                    self._last_error = f"{type(exc).__name__}: {exc}"
            finally:
                with self._lock:
                    was_connected = self._connected
                    self._connected = False
                    if was_connected and not self._stop.is_set() and self._last_error_code is None:
                        self._last_error_code = "WEBRTC_DISCONNECTED"
                        self._last_error = "Go2 WebRTC 连接已断开"
                if self._connection:
                    try:
                        await self._connection.disconnect()
                    except Exception:
                        pass
                    self._connection = None

            if not self._stop.is_set():
                with self._lock:
                    self._reconnect_count += 1
                await asyncio.sleep(2.0)

    async def _receive_video(self, track: MediaStreamTrack) -> None:
        interval = 1.0 / self.capture_fps
        while not self._stop.is_set():
            frame = await track.recv()
            now = time.monotonic()
            if now - self._last_encode_monotonic < interval:
                continue

            image = frame.to_ndarray(format="bgr24")
            ok, encoded = cv2.imencode(
                ".jpg",
                image,
                [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality],
            )
            if not ok:
                with self._lock:
                    self._error_count += 1
                    self._last_error_code = "JPEG_ENCODE_FAILED"
                    self._last_error = "OpenCV 无法编码当前视频帧"
                continue

            self._last_encode_monotonic = now
            with self._lock:
                self._sequence += 1
                elapsed = max(0.001, now - self._started_monotonic)
                self._frame = Frame(
                    jpeg=encoded.tobytes(),
                    sequence=self._sequence,
                    captured_at=now_iso(),
                    width=int(image.shape[1]),
                    height=int(image.shape[0]),
                    fps=self._sequence / elapsed,
                )
                self._last_frame_monotonic = now
                self._last_error_code = None
                self._last_error = None


camera = WirelessCamera()


@asynccontextmanager
async def lifespan(_: FastAPI):
    camera.start()
    yield
    camera.stop()


app = FastAPI(title="Go2 Wireless Video Bridge", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["Accept", "Content-Type"],
    max_age=600,
)


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return DASHBOARD


@app.get("/status")
def status() -> dict:
    return {
        "success": True,
        "ok": True,
        "apiVersion": API_VERSION,
        "serviceVersion": SERVICE_VERSION,
        "serviceId": SERVICE_ID,
        "timestamp": now_iso(),
        "data": camera.status(),
    }


@app.get("/snapshot")
def snapshot() -> Response:
    frame = camera.latest()
    if frame is None:
        return Response(status_code=503, content="No wireless camera frame yet")
    return Response(frame.jpeg, media_type="image/jpeg", headers={"X-Frame-Seq": str(frame.sequence)})


@app.get("/stream.mjpg")
def stream(frames: Optional[int] = None) -> StreamingResponse:
    boundary = "go2frame"

    def generate():
        sent = 0
        last_sequence = -1
        camera.register_client()
        try:
            while True:
                frame = camera.latest()
                if frame is not None and frame.sequence != last_sequence:
                    last_sequence = frame.sequence
                    yield (
                        f"--{boundary}\r\nContent-Type: image/jpeg\r\n"
                        f"Content-Length: {len(frame.jpeg)}\r\n\r\n"
                    ).encode("ascii") + frame.jpeg + b"\r\n"
                    sent += 1
                    if frames is not None and sent >= frames:
                        return
                time.sleep(0.02)
        finally:
            camera.unregister_client()

    return StreamingResponse(generate(), media_type=f"multipart/x-mixed-replace; boundary={boundary}")


DASHBOARD = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Go2 无线视频</title><style>
body{margin:0;background:#111318;color:#eef2f7;font-family:Arial,"Microsoft YaHei",sans-serif}header{padding:16px 20px;background:#191d24;border-bottom:1px solid #333942;display:flex;justify-content:space-between}h1{font-size:20px;margin:0}main{padding:16px;display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:16px}.video{background:#050608;border:1px solid #333942;border-radius:6px;overflow:hidden}.video img{display:block;width:100%;height:calc(100vh - 90px);object-fit:contain}.panel{background:#191d24;border:1px solid #333942;border-radius:6px;padding:14px}.row{display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid #333942}.label{color:#aeb7c5}.ok{color:#61df8a}.bad{color:#ff7070}@media(max-width:850px){main{grid-template-columns:1fr}.video img{height:55vh}}
</style></head><body><header><h1>Go2 无线视频</h1><span id="state" class="bad">正在连接</span></header><main><section class="video"><img src="/stream.mjpg" alt="Go2 第一视角"></section><aside class="panel"><div class="row"><span class="label">连接</span><span id="mode">正在读取</span></div><div class="row"><span class="label">机器人</span><span id="robot">-</span></div><div class="row"><span class="label">画面</span><span id="frame">-</span></div><div class="row"><span class="label">帧率</span><span id="fps">-</span></div><div class="row"><span class="label">错误</span><span id="error">-</span></div></aside></main><script>
async function refresh(){try{const j=await(await fetch('/status',{cache:'no-store'})).json();const d=j.data;const f=d.latestFrame;state.textContent=d.hasFrame?'无线画面在线':(d.connected?'等待画面':'正在连接');state.className=d.hasFrame?'ok':'bad';mode.textContent=d.connectionMode||'-';robot.textContent=d.robotIp||'-';frame.textContent=f?`${f.width}x${f.height} #${f.sequence}`:'-';fps.textContent=d.captureFps.toFixed(1);error.textContent=d.lastError||'无'}catch(e){state.textContent='服务离线';state.className='bad'}}refresh();setInterval(refresh,1000)
</script></body></html>"""
