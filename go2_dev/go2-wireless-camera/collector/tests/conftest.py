from __future__ import annotations

import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.frame_store import Frame, LatestFrameStore, now_iso
from app.main import create_app
from app.unitree_camera import CameraCollector


def make_jpeg(width: int = 32, height: int = 24, color=(20, 80, 140)) -> bytes:
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :] = color
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return bytes(encoded)


class MockCameraClient:
    def __init__(self, failures: list[int] | None = None) -> None:
        self.failures = failures or []
        self.calls = 0

    def get_image_sample(self) -> tuple[int, bytes]:
        self.calls += 1
        if self.failures:
            return self.failures.pop(0), b""
        return 0, make_jpeg()


@pytest.fixture
def settings() -> Settings:
    return Settings(network_interface="eth-test", upload_fps=20, heartbeat_interval_seconds=0.1, upload_timeout_seconds=0.2)


@pytest.fixture
def store_with_frame() -> LatestFrameStore:
    store = LatestFrameStore()
    jpeg = make_jpeg()
    store.update(Frame(jpeg, 1, now_iso(), 32, 24, len(jpeg), 0, 1.0, 10.0), time.monotonic())
    return store


@pytest.fixture
def app_client(settings: Settings, store_with_frame: LatestFrameStore):
    collector = CameraCollector(settings, store_with_frame, client_factory=lambda: MockCameraClient())
    app = create_app(settings=settings, collector=collector, store=store_with_frame, autostart=False)
    with TestClient(app) as client:
        yield client
