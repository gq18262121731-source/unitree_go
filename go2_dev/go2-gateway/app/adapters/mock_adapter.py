from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from app.schemas.lidar import empty_lidar_topic_status

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
        self.moves: list[tuple[float, float, float]] = []
        self.joystick_enabled = True
        self.joystick_switches: list[bool] = []
        self.stand_count = 0
        self.sit_count = 0
        self.fail_next_move = False
        self.bad_camera = False
        self._lidar_diagnostics: dict | Exception | None = None

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
            "dds": self.dds_diagnostics(),
        }

    def dds_diagnostics(self) -> dict:
        now = datetime.now(timezone.utc).astimezone().isoformat() if self._online else None

        def topic(timeout_code: str) -> dict:
            return {
                "created": True,
                "received": self._online,
                "sampleCount": 1 if self._online else 0,
                "firstSampleAt": now,
                "lastSampleAt": now,
                "frequencyHz": None,
                "timeout": None if self._online else True,
                "timeoutCode": None if self._online else timeout_code,
            }

        diagnostics = {
            "domainId": 0,
            "networkInterface": "mock",
            "robotIp": "mock",
            "ddsInitialized": self._initialized,
            "ddsStateAvailable": self._online,
            "sportState": topic("SPORT_STATE_TIMEOUT"),
            "lowState": topic("LOW_STATE_TIMEOUT"),
            "errorCode": None if self._online else "UNITREE_DDS_NO_STATE_SAMPLES",
        }
        diagnostics["lidarState"] = self.lidar_diagnostics()
        return diagnostics

    def set_lidar_diagnostics(self, diagnostics: dict | Exception | None) -> None:
        self._lidar_diagnostics = diagnostics

    def lidar_diagnostics(self) -> dict:
        if isinstance(self._lidar_diagnostics, Exception):
            raise self._lidar_diagnostics
        if self._lidar_diagnostics is not None:
            topic = empty_lidar_topic_status()
            topic.update(self._lidar_diagnostics)
            return topic
        topic = empty_lidar_topic_status()
        topic["created"] = False
        topic["discovered"] = False
        return topic

    def stand_up(self) -> int:
        with self._lock:
            self.stand_count += 1
        return 0

    def stand_down(self) -> int:
        self.stop()
        return 0

    def sit(self) -> int:
        with self._lock:
            self.sit_count += 1
        return 0

    def stop(self) -> int:
        with self._lock:
            self.stop_count += 1
        return 0

    def move(self, vx: float, vy: float, wz: float) -> int:
        with self._lock:
            self.move_count += 1
            self.moves.append((vx, vy, wz))
        if self.fail_next_move:
            self.fail_next_move = False
            raise RuntimeError("mock move failure")
        time.sleep(0.01)
        return 0

    def switch_joystick(self, enabled: bool) -> int:
        with self._lock:
            self.joystick_enabled = enabled
            self.joystick_switches.append(enabled)
        return 0

    def get_camera_jpeg(self) -> bytes:
        if self.bad_camera:
            return b"not-a-jpeg"
        return _make_mock_jpeg()
