from __future__ import annotations

from app.adapters.base import RobotAdapter
from app.core.errors import ErrorCode, GatewayError
from app.core.state_store import iso, utc_now
from app.core.state_store import StateStore


class CameraService:
    def __init__(self, adapter: RobotAdapter, state_store: StateStore) -> None:
        self.adapter = adapter
        self.state_store = state_store

    def snapshot(self) -> bytes:
        try:
            jpeg = self.adapter.get_camera_jpeg()
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

