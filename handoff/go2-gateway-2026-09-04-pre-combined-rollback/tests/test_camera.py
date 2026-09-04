from __future__ import annotations


def test_camera_snapshot_endpoint(client):
    response = client.get("/api/robot/camera/snapshot")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content.startswith(b"\xff\xd8")


def test_camera_snapshot_short_endpoint(client):
    response = client.get("/api/camera/snapshot")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content.startswith(b"\xff\xd8")


def test_camera_status_endpoint(client):
    client.get("/api/camera/snapshot")

    response = client.get("/api/camera/status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["camera"] == "ready"
    assert data["online"] is True
    assert data["snapshot_url"] == "/api/camera/snapshot"
    assert data["stream_url"] == "/api/camera/stream"
    assert data["last_frame_time"] is not None


def test_camera_stream_endpoint_returns_mjpeg_frame(client):
    with client.stream("GET", "/api/camera/stream?frames=1") as response:
        content = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("multipart/x-mixed-replace")
    assert b"--frame\r\n" in content
    assert b"Content-Type: image/jpeg" in content
    assert b"\xff\xd8" in content


def test_robot_camera_stream_endpoint_returns_mjpeg_frame(client):
    with client.stream("GET", "/api/robot/camera/stream?frames=1") as response:
        content = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("multipart/x-mixed-replace")
    assert b"--frame\r\n" in content


def test_camera_decode_failure(client):
    client.app.state.adapter.bad_camera = True

    response = client.get("/api/robot/camera/snapshot")

    assert response.status_code == 503
    assert response.json()["code"] == "CAMERA_DECODE_FAILED"
