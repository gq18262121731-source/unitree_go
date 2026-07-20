from __future__ import annotations

from conftest import make_jpeg


def upload(client, token="local-test-token", data=None, jpeg=None):
    data = data or {
        "robot_id": "go2-edu-001",
        "camera_id": "go2_front",
        "frame_seq": "1",
        "captured_at": "2026-07-14T10:00:00+08:00",
        "width": "32",
        "height": "24",
        "capture_fps": "10",
    }
    return client.post(
        "/api/video/frame",
        data=data,
        files={"file": ("frame.jpg", jpeg if jpeg is not None else make_jpeg(), "image/jpeg")},
        headers={"X-Upload-Token": token},
    )


def test_token_validation(client):
    response = upload(client, token="bad")
    assert response.status_code == 401


def test_non_jpeg_rejected(client):
    response = upload(client, jpeg=b"not-jpeg")
    assert response.status_code == 415


def test_oversized_frame_rejected(client, monkeypatch):
    monkeypatch.setattr("app.main.MAX_FRAME_BYTES", 4)
    response = upload(client)
    assert response.status_code == 413


def test_receiver_latest_jpg(client):
    response = upload(client)
    assert response.status_code == 200
    latest = client.get("/latest.jpg")
    assert latest.status_code == 200
    assert latest.content.startswith(b"\xff\xd8")


def test_heartbeat_saved(client):
    response = client.post(
        "/api/video/heartbeat",
        json={"robotId": "go2-edu-001", "cameraId": "go2_front", "online": True},
        headers={"X-Upload-Token": "local-test-token"},
    )
    assert response.status_code == 200
    assert client.get("/status").json()["data"]["collectorOnline"] is True


def test_receiver_status(client):
    upload(client)
    data = client.get("/status").json()["data"]
    assert data["totalFrames"] == 1
    assert data["lastFrameSeq"] == 1


def test_receiver_mjpeg_format(client):
    upload(client)
    response = client.get("/stream.mjpg?frames=1")
    chunk = response.content
    assert b"--receiverframe" in chunk
    assert b"Content-Type: image/jpeg" in chunk
