from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.config import Settings
from app.frame_store import Frame, LatestFrameStore, now_iso
from app.main import create_app
from app.unitree_camera import CameraCollector
from app.upload_worker import UploadWorker

from conftest import MockCameraClient, make_jpeg


def test_health_without_frame(settings):
    store = LatestFrameStore()
    app = create_app(settings=settings, store=store, autostart=False)
    with TestClient(app) as client:
        data = client.get("/health").json()["data"]
    assert data["hasValidFrame"] is False


def test_health_with_frame(app_client):
    data = app_client.get("/health").json()["data"]
    assert data["hasValidFrame"] is True


def test_dashboard_page(app_client):
    response = app_client.get("/")
    assert response.status_code == 200
    assert "Go2 视频桥接后台" in response.text
    assert "/stream.mjpg" in response.text


def test_snapshot_normal(app_client):
    response = app_client.get("/snapshot")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content.startswith(b"\xff\xd8")


def test_snapshot_without_frame_503(settings):
    app = create_app(settings=settings, store=LatestFrameStore(), autostart=False)
    with TestClient(app) as client:
        response = client.get("/snapshot")
    assert response.status_code == 503
    assert response.json()["code"] == "CAMERA_FRAME_UNAVAILABLE"


def test_mjpeg_format(app_client):
    response = app_client.get("/stream.mjpg?frames=1")
    chunk = response.content
    assert b"--go2frame" in chunk
    assert b"Content-Type: image/jpeg" in chunk


def test_frame_store_copy_safe(store_with_frame):
    frame = store_with_frame.latest()
    assert frame is not None
    changed = bytearray(frame.jpeg)
    changed[0] = 0
    assert store_with_frame.latest().jpeg.startswith(b"\xff\xd8")


def test_new_frame_overwrites_old(store_with_frame):
    jpeg = make_jpeg(color=(1, 2, 3))
    store_with_frame.update(Frame(jpeg, 2, now_iso(), 32, 24, len(jpeg), 0, 2.0, 11.0), time.monotonic())
    assert store_with_frame.latest().frame_seq == 2


def test_sdk_error_statistics(settings):
    store = LatestFrameStore()
    collector = CameraCollector(settings, store, client_factory=lambda: MockCameraClient(failures=[3102, 3102, 3102]))
    collector.start()
    time.sleep(0.15)
    collector.stop()
    stats = collector.snapshot_stats()
    assert stats["sdkErrorCount"] >= 1
    assert stats["sdkErrorCodes"]


def test_reconnect_backoff(settings):
    settings = Settings(network_interface="eth-test", reconnect_initial_seconds=0.01, reconnect_max_seconds=0.02)
    store = LatestFrameStore()
    collector = CameraCollector(settings, store, client_factory=lambda: MockCameraClient(failures=[1, 1, 1]))
    collector.start()
    time.sleep(0.12)
    collector.stop()
    assert collector.snapshot_stats()["reconnectCount"] >= 1


def test_upload_start_stop(app_client):
    assert app_client.post("/upload/start").json()["data"]["enabled"] is True
    assert app_client.post("/upload/stop").json()["data"]["enabled"] is False


def test_same_frame_not_uploaded_twice(settings, store_with_frame, monkeypatch):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(kwargs)
        class Response:
            def raise_for_status(self): return None
        return Response()

    monkeypatch.setattr("app.upload_worker.requests.post", fake_post)
    worker = UploadWorker(settings, store_with_frame, lambda: {"captureFps": 10, "lastFrameAt": now_iso()})
    worker.start()
    time.sleep(0.12)
    worker.stop()
    worker.close()
    assert worker.snapshot()["successCount"] == 1


def test_upload_failure_does_not_block_capture(settings, store_with_frame, monkeypatch):
    def fake_post(*args, **kwargs):
        raise RuntimeError("receiver down")

    monkeypatch.setattr("app.upload_worker.requests.post", fake_post)
    worker = UploadWorker(settings, store_with_frame, lambda: {"captureFps": 10, "lastFrameAt": now_iso()})
    worker.start()
    time.sleep(0.08)
    worker.stop()
    worker.close()
    assert worker.snapshot()["failureCount"] >= 1


def test_upload_recovery(settings, store_with_frame, monkeypatch):
    attempts = {"count": 0}

    def fake_post(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("temporary")
        class Response:
            def raise_for_status(self): return None
        return Response()

    monkeypatch.setattr("app.upload_worker.requests.post", fake_post)
    worker = UploadWorker(settings, store_with_frame, lambda: {"captureFps": 10, "lastFrameAt": now_iso()})
    worker.start()
    time.sleep(0.08)
    jpeg = make_jpeg(color=(9, 9, 9))
    store_with_frame.update(Frame(jpeg, 2, now_iso(), 32, 24, len(jpeg), 0, 1.0, 10.0), time.monotonic())
    time.sleep(0.08)
    worker.stop()
    worker.close()
    assert worker.snapshot()["successCount"] >= 1
