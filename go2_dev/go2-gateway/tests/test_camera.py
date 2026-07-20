from __future__ import annotations


def test_camera_snapshot_endpoint(client):
    response = client.get("/api/robot/camera/snapshot")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content.startswith(b"\xff\xd8")


def test_camera_decode_failure(client):
    client.app.state.adapter.bad_camera = True

    response = client.get("/api/robot/camera/snapshot")

    assert response.status_code == 503
    assert response.json()["code"] == "CAMERA_DECODE_FAILED"

