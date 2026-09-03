from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

from app.config import Settings
from app.core.errors import ErrorCode, GatewayError
from app.gateway.go2_gateway import Go2Gateway
from app.core.state_store import iso, utc_now
from app.core.state_store import StateStore


class CameraService:
    def __init__(self, gateway: Go2Gateway, state_store: StateStore, settings: Settings) -> None:
        self.gateway = gateway
        self.state_store = state_store
        self.settings = settings

    def snapshot(self) -> bytes:
        try:
            jpeg = self.gateway.get_camera()
        except GatewayError:
            self.state_store.update_camera(False, None)
            raise
        except Exception as exc:
            self.state_store.update_camera(False, None)
            raise GatewayError(ErrorCode.CAMERA_UNAVAILABLE, f"Camera unavailable: {exc}", 503) from exc
        if not self._is_decodable_jpeg(jpeg):
            self.state_store.update_camera(False, None)
            raise GatewayError(ErrorCode.CAMERA_DECODE_FAILED, "Camera returned data that is not a decodable JPEG.", 503)
        self.state_store.update_camera(True, iso(utc_now()))
        return jpeg

    def save_task_evidence(self, task_id: str, name: str = "arrival.jpg") -> dict:
        jpeg = self.snapshot()
        task_dir = Path(self.settings.task_evidence_dir) / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        final_path = task_dir / name
        temp_path = task_dir / f".{name}.{os.getpid()}.tmp"
        try:
            with temp_path.open("wb") as file:
                file.write(jpeg)
                file.flush()
                os.fsync(file.fileno())
            temp_path.replace(final_path)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
        relative_path = f"{task_id}/{name}"
        return {
            "camera_available": True,
            "captured_at": iso(utc_now()),
            "source": "mock" if self.settings.mode == "mock" else "go2_camera",
            "evidence_path": relative_path,
            "snapshot_url": f"/api/robot/tasks/{task_id}/evidence/{name}",
        }

    def evidence_file(self, task_id: str, name: str = "arrival.jpg") -> Path:
        path = Path(self.settings.task_evidence_dir) / task_id / name
        if not path.exists():
            raise GatewayError(ErrorCode.TASK_NOT_FOUND, f"Task evidence not found: {task_id}/{name}", 404)
        return path

    def status(self) -> dict:
        camera = self.state_store.snapshot().get("camera", {})
        online = bool(camera.get("online"))
        return {
            "camera": "ready" if online else "idle",
            "online": online,
            "snapshot_url": self.settings.camera_snapshot_url,
            "stream_url": self.settings.camera_stream_url,
            "last_frame_time": camera.get("lastFrameTime"),
        }

    def mjpeg_stream(self, frame_limit: int | None = None) -> Iterator[bytes]:
        frame_count = 0
        while frame_limit is None or frame_count < frame_limit:
            jpeg = self.snapshot()
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                + f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii")
                + jpeg
                + b"\r\n"
            )
            frame_count += 1
            if frame_limit is None or frame_count < frame_limit:
                time.sleep(self.settings.camera_stream_interval_seconds)

    def _is_decodable_jpeg(self, data: bytes) -> bool:
        if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
            return False
        try:
            import cv2
            import numpy as np
        except Exception:
            return True
        frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        return frame is not None
