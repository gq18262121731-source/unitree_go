from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import cv2
import numpy as np
import requests

from .config import Settings
from .frame_store import Frame, LatestFrameStore


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


@dataclass
class UploadStats:
    enabled: bool = False
    success_count: int = 0
    failure_count: int = 0
    last_success_at: str | None = None
    last_failure_at: str | None = None
    last_error: str | None = None
    average_latency_ms: float = 0.0
    last_uploaded_frame_seq: int | None = None
    heartbeat_success_count: int = 0
    heartbeat_failure_count: int = 0


class UploadWorker:
    def __init__(self, settings: Settings, store: LatestFrameStore, collector_stats_provider) -> None:
        self.settings = settings
        self.store = store
        self.collector_stats_provider = collector_stats_provider
        self.stats = UploadStats(enabled=settings.upload_enabled)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self.logger = logging.getLogger("go2_wireless_camera.upload")

    def start(self) -> None:
        with self._lock:
            self.stats.enabled = True
            if not self._thread or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._run_upload, name="go2-upload", daemon=True)
                self._thread.start()
            if not self._heartbeat_thread or not self._heartbeat_thread.is_alive():
                self._heartbeat_thread = threading.Thread(target=self._run_heartbeat, name="go2-heartbeat", daemon=True)
                self._heartbeat_thread.start()

    def stop(self) -> None:
        with self._lock:
            self.stats.enabled = False

    def close(self) -> None:
        self._stop.set()
        for thread in (self._thread, self._heartbeat_thread):
            if thread:
                thread.join(timeout=2.0)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "enabled": self.stats.enabled,
                "targetUrl": self.settings.upload_url,
                "uploadFps": self.settings.upload_fps,
                "successCount": self.stats.success_count,
                "failureCount": self.stats.failure_count,
                "lastSuccessAt": self.stats.last_success_at,
                "lastFailureAt": self.stats.last_failure_at,
                "lastError": self.stats.last_error,
                "averageLatencyMs": self.stats.average_latency_ms,
                "lastUploadedFrameSeq": self.stats.last_uploaded_frame_seq,
                "heartbeatSuccessCount": self.stats.heartbeat_success_count,
                "heartbeatFailureCount": self.stats.heartbeat_failure_count,
            }

    def _run_upload(self) -> None:
        interval = 1.0 / max(0.1, self.settings.upload_fps)
        last_seq: int | None = None
        while not self._stop.wait(interval):
            if not self.snapshot()["enabled"]:
                continue
            frame = self.store.latest()
            if frame is None or frame.frame_seq == last_seq:
                continue
            last_seq = frame.frame_seq
            try:
                self._upload_frame(frame)
            except Exception as exc:
                self._record_failure(str(exc))

    def _upload_frame(self, frame: Frame) -> None:
        jpeg = ensure_max_size(frame.jpeg, self.settings.upload_max_bytes, self.settings.jpeg_quality)
        if len(jpeg) > self.settings.upload_max_bytes:
            raise ValueError("frame exceeds GO2_UPLOAD_MAX_BYTES after compression")
        started = time.monotonic()
        response = requests.post(
            self.settings.upload_url,
            data={
                "robot_id": self.settings.robot_id,
                "camera_id": self.settings.camera_id,
                "frame_seq": str(frame.frame_seq),
                "captured_at": frame.captured_at,
                "width": str(frame.width),
                "height": str(frame.height),
                "capture_fps": str(frame.capture_fps),
            },
            files={"file": ("frame.jpg", jpeg, "image/jpeg")},
            headers={"X-Video-Source": "go2", "X-Upload-Token": self.settings.upload_token},
            timeout=self.settings.upload_timeout_seconds,
        )
        response.raise_for_status()
        latency_ms = (time.monotonic() - started) * 1000.0
        with self._lock:
            self.stats.success_count += 1
            self.stats.last_success_at = iso_now()
            self.stats.last_error = None
            self.stats.last_uploaded_frame_seq = frame.frame_seq
            n = self.stats.success_count
            self.stats.average_latency_ms = ((self.stats.average_latency_ms * (n - 1)) + latency_ms) / n

    def _record_failure(self, message: str) -> None:
        with self._lock:
            self.stats.failure_count += 1
            self.stats.last_failure_at = iso_now()
            self.stats.last_error = message
        self.logger.warning("upload failed: %s", message)

    def _run_heartbeat(self) -> None:
        while not self._stop.wait(self.settings.heartbeat_interval_seconds):
            if not self.snapshot()["enabled"]:
                continue
            try:
                status = self.collector_stats_provider()
                frame_status = self.store.status(self.settings.frame_stale_seconds, time.monotonic())
                requests.post(
                    self.settings.heartbeat_url,
                    json={
                        "robotId": self.settings.robot_id,
                        "cameraId": self.settings.camera_id,
                        "online": bool(frame_status["hasFrame"]),
                        "lastFrameAt": status.get("lastFrameAt"),
                        "captureFps": status.get("captureFps", 0.0),
                        "frameAgeMs": frame_status.get("frameAgeMs"),
                        "networkInterface": self.settings.network_interface,
                    },
                    headers={"X-Upload-Token": self.settings.upload_token},
                    timeout=self.settings.upload_timeout_seconds,
                ).raise_for_status()
                with self._lock:
                    self.stats.heartbeat_success_count += 1
            except Exception as exc:
                with self._lock:
                    self.stats.heartbeat_failure_count += 1
                    self.stats.last_error = str(exc)


def ensure_max_size(jpeg: bytes, max_bytes: int, quality: int) -> bytes:
    if len(jpeg) <= max_bytes:
        return jpeg
    image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return jpeg
    for q in (quality, 60, 50, 40, 30):
        ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), q])
        if ok and len(encoded) <= max_bytes:
            return bytes(encoded)
    return bytes(encoded) if ok else jpeg
