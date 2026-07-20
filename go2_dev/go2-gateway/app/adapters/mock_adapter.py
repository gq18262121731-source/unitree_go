from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from .base import RobotAdapter


_FALLBACK_JPEG = b"\xff\xd8\xff\xd9"


def _make_mock_jpeg() -> bytes:
    try:
        import cv2
        import numpy as np
    except Exception:
        return _FALLBACK_JPEG
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    image[:, :] = (32, 96, 160)
    ok, buffer = cv2.imencode(".jpg", image)
    return bytes(buffer) if ok else _FALLBACK_JPEG


class MockGo2Adapter(RobotAdapter):
    sdk_version = "mock"

    def __init__(self, robot_id: str) -> None:
        self.robot_id = robot_id
        self._initialized = False
        self._online = True
        self._lock = threading.RLock()
        self.stop_count = 0
        self.move_count = 0
        self.stand_count = 0
        self.fail_next_move = False
        self.bad_camera = False

    def initialize(self) -> None:
        self._initialized = True

    def close(self) -> None:
        self.stop()
        self._initialized = False

    def is_initialized(self) -> bool:
        return self._initialized

    def set_online(self, online: bool) -> None:
        self._online = online

    def get_status(self) -> dict:
        now = datetime.now(timezone.utc).astimezone().isoformat()
        return {
            "robotId": self.robot_id,
            "online": self._online,
            "lastSeen": now if self._online else None,
            "stateStale": not self._online,
            "motion": {
                "mode": 3 if self._online else None,
                "modeName": "mock-locomotion" if self._online else None,
                "gaitType": 1 if self._online else None,
                "velocityX": 0.0,
                "velocityY": 0.0,
                "yawSpeed": 0.0,
                "bodyHeight": 0.32 if self._online else None,
            },
            "attitude": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
            "battery": {"percentage": 78, "voltage": 31.2, "current": 1.4, "raw": {"mock": True}},
        }

    def stand_up(self) -> int:
        with self._lock:
            self.stand_count += 1
        return 0

    def stand_down(self) -> int:
        self.stop()
        return 0

    def stop(self) -> int:
        with self._lock:
            self.stop_count += 1
        return 0

    def move(self, vx: float, vy: float, wz: float) -> int:
        with self._lock:
            self.move_count += 1
        if self.fail_next_move:
            self.fail_next_move = False
            raise RuntimeError("mock move failure")
        time.sleep(0.01)
        return 0

    def get_camera_jpeg(self) -> bytes:
        if self.bad_camera:
            return b"not-a-jpeg"
        return _make_mock_jpeg()
