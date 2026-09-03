"""Retired independent STA video application.

The production STA launcher now delegates to Go2WirelessRuntime in
go2-gateway.  This module retains the legacy HTTP/status contract for imports
and diagnostics, but deliberately contains no Go2 WebRTC connection code.
"""

from __future__ import annotations

import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse


SERVICE_ID = "go2-wireless-camera"
API_VERSION = "1"
SERVICE_VERSION = "1.0.0-retired"
FRAME_STALE_SECONDS = 3.0


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
    """Legacy status/frame facade; it cannot create a WebRTC connection."""

    def __init__(self, capture_fps: float = 15.0, jpeg_quality: int = 80) -> None:
        self.capture_fps = max(1.0, capture_fps)
        self.jpeg_quality = max(40, min(95, jpeg_quality))
        self.connection_mode = "sta"
        self.robot_ip = "192.168.8.252"
        self._lock = threading.RLock()
        self._frame: Frame | None = None
        self._last_frame_monotonic: float | None = None
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
        self._started_monotonic = time.monotonic()
        self._started_at = now_iso()
        with self._lock:
            self._last_error_code = "LEGACY_CLIENT_RETIRED"
            self._last_error = (
                "Independent WebRTC video is retired; use "
                "Start-Go2WirelessRuntime.ps1."
            )

    def stop(self) -> None:
        return None

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
            age_ms = (
                None
                if self._last_frame_monotonic is None
                else (now - self._last_frame_monotonic) * 1000.0
            )
            has_frame = bool(
                self._frame is not None
                and age_ms is not None
                and age_ms < FRAME_STALE_SECONDS * 1000.0
            )
            error_code = self._last_error_code
            error_message = self._last_error
            if has_frame:
                video_state = "ready"
                error_code = None
                error_message = None
            elif self._connected and self._frame is None:
                video_state = "no-frame"
                if now - self._started_monotonic >= FRAME_STALE_SECONDS:
                    error_code = "NO_FRAME_TIMEOUT"
                    error_message = "WebRTC 已连接，但尚未收到有效视频帧"
            elif self._connected:
                video_state = "stalled"
                error_code = "FRAME_STALLED"
                error_message = "视频帧已停止更新"
            elif self._reconnect_count > 0:
                video_state = "reconnecting"
            else:
                video_state = "connecting"
            resolution = (
                None
                if self._frame is None
                else {"width": self._frame.width, "height": self._frame.height}
            )
            return {
                "serviceState": "running",
                "videoState": video_state,
                "startedAt": self._started_at,
                "connectionMode": "Go2 STA / WebRTC (retired independent client)",
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
                    "networkMode": "STA",
                    "transport": "WebRTC",
                    "robotIp": self.robot_ip,
                },
                "clientCount": self._client_count,
                "errorCount": self._error_count,
                "reconnectCount": self._reconnect_count,
                "lastErrorCode": error_code,
                "lastError": error_message,
                "error": (
                    None
                    if error_code is None
                    else {"code": error_code, "message": error_message}
                ),
                "latestFrame": (
                    None
                    if self._frame is None
                    else {
                        "sequence": self._frame.sequence,
                        "capturedAt": self._frame.captured_at,
                        "width": self._frame.width,
                        "height": self._frame.height,
                        "size": len(self._frame.jpeg),
                    }
                ),
            }


camera = WirelessCamera()


@asynccontextmanager
async def lifespan(_: FastAPI):
    camera.start()
    yield
    camera.stop()


app = FastAPI(title="Retired Go2 Independent Video Bridge", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["Accept", "Content-Type"],
)


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return (
        "<h1>Independent client retired</h1>"
        "<p>Use E:\\笨笨狗\\go2_dev\\go2-gateway\\scripts\\"
        "Start-Go2WirelessRuntime.ps1.</p>"
    )


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
        return Response(status_code=503, content="Independent client retired")
    return Response(frame.jpeg, media_type="image/jpeg")


@app.get("/stream.mjpg")
def stream(frames: Optional[int] = None) -> StreamingResponse:
    def generate():
        sent = 0
        last_sequence = -1
        camera.register_client()
        try:
            while True:
                frame = camera.latest()
                if frame is not None and frame.sequence != last_sequence:
                    last_sequence = frame.sequence
                    yield b"--go2frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame.jpeg + b"\r\n"
                    sent += 1
                    if frames is not None and sent >= frames:
                        return
                time.sleep(0.02)
        finally:
            camera.unregister_client()

    return StreamingResponse(
        generate(), media_type="multipart/x-mixed-replace; boundary=go2frame"
    )
