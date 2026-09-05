from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app.webrtc.go2_wireless_runtime import LatestVideoFrame
from app.webrtc.video_bridge import (
    WirelessCompanionControlError,
    create_video_bridge,
)


class FakeRuntime:
    def __init__(self) -> None:
        self.frame = LatestVideoFrame(
            jpeg=b"jpeg-bytes",
            sequence=9,
            captured_at="2026-08-26T20:30:00+08:00",
            width=1280,
            height=720,
            fps=14.5,
        )
        self.clients = 0
        self.connected = True
        self.started = True
        self.video_ready = True
        self.video_state = "live"
        self.frame_age_ms = 10.0
        self.reconnect_count = 0

    def status(self) -> dict:
        return {
            "started": self.started,
            "videoState": self.video_state,
            "robotIp": "192.168.8.252",
            "connected": self.connected,
            "connectionState": "connected" if self.connected else "disconnected",
            "transportHealthState": "healthy" if self.connected else "offline",
            "watchdogPolicy": "hard_transport_or_raw_plus_sport_stale",
            "multiSignalStaleSeconds": None,
            "peerConnectionState": "connected" if self.connected else "closed",
            "iceConnectionState": "completed" if self.connected else "closed",
            "connectedSince": "2026-08-26T20:29:00+08:00",
            "lastDisconnectAt": None,
            "lastDisconnectReason": None,
            "diagnosticReason": None,
            "connectionDiagnostics": {
                "lastConnectTrace": {"generation": 1},
                "recentConnectTraces": [{"generation": 1}],
            },
            "recentDisconnects": [],
            "lastReconnectAt": None,
            "reconnectCount": self.reconnect_count,
            "videoReady": self.video_ready,
            "frameAgeMs": self.frame_age_ms,
            "frameAgeSeconds": self.frame_age_ms / 1000.0,
            "frameCount": 9,
            "rawFrameCount": 11,
            "encodedFrameCount": 9,
            "lastRawFrameAt": "2026-08-26T20:30:00.010000+08:00",
            "lastEncodedFrameAt": "2026-08-26T20:30:00+08:00",
            "rawFrameAgeSeconds": 0.005,
            "encodedFrameAgeSeconds": 0.01,
            "encodeQueueDepth": 0,
            "droppedFrameCount": 2,
            "encodeDurationMsLast": 12.5,
            "encodeDurationMsMax": 18.0,
            "encodeDurationMsEwma": 13.1,
            "lastSportStateAt": "2026-08-26T20:30:00.005000+08:00",
            "sportStateAgeSeconds": 0.006,
            "sportWatchdogArmed": True,
            "videoClientCount": self.clients,
            "videoErrorCount": 0,
            "lastVideoError": None,
            "latestFrame": self.frame.metadata(),
            "dataChannelReady": True,
            "sportStateReady": True,
            "dataHealthState": "healthy",
            "dataDegradedReason": None,
            "videoHealthState": "healthy",
            "videoDegradedReason": None,
            "firstRawFrameReceived": True,
            "firstEncodedFrameProduced": True,
            "videoWatchdogArmed": True,
            "connectionCount": 1 if self.connected else 0,
            "connectionOwner": "Go2WirelessRuntime",
        }

    def latest_frame(self) -> LatestVideoFrame:
        return self.frame

    def register_video_client(self) -> None:
        self.clients += 1

    def unregister_video_client(self) -> None:
        self.clients -= 1


def test_status_and_snapshot_are_views_over_shared_runtime() -> None:
    runtime = FakeRuntime()
    client = TestClient(create_video_bridge(runtime))

    response_payload = client.get("/status").json()
    assert response_payload["serviceId"] == "go2-wireless-camera"
    assert response_payload["runtimeId"] == "go2-wireless-runtime"
    payload = response_payload["data"]
    assert payload["connectionCount"] == 1
    assert payload["source"]["connectionOwner"] == "Go2WirelessRuntime"
    assert payload["dataChannelReady"] is True
    assert payload["hasFrame"] is True
    assert payload["latestFrame"]["capturedAt"] == "2026-08-26T20:30:00+08:00"
    assert payload["rawFrameCount"] == 11
    assert payload["encodedFrameCount"] == 9
    assert payload["rawFrameAgeSeconds"] == 0.005
    assert payload["encodedFrameAgeSeconds"] == 0.01
    assert payload["encodeDurationMsLast"] == 12.5
    assert payload["sportStateAgeSeconds"] == 0.006
    assert payload["recentDisconnects"] == []
    assert payload["connectionDiagnostics"]["lastConnectTrace"]["generation"] == 1
    assert payload["transportHealthState"] == "healthy"
    assert payload["watchdogPolicy"] == "hard_transport_or_raw_plus_sport_stale"
    assert payload["videoHealthState"] == "healthy"
    assert payload["dataHealthState"] == "healthy"
    assert payload["videoWatchdogArmed"] is True
    assert payload["sportWatchdogArmed"] is True

    response = client.get("/snapshot")
    assert response.status_code == 200
    assert response.content == b"jpeg-bytes"
    assert response.headers["x-frame-seq"] == "9"
    assert response.headers["cache-control"] == (
        "no-store, no-cache, must-revalidate, max-age=0"
    )
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["expires"] == "0"


def test_public_video_gateway_contract_hides_webrtc_details() -> None:
    runtime = FakeRuntime()
    client = TestClient(create_video_bridge(runtime, robot_id="go2-test-01"))

    health = client.get("/healthz")
    video_status = client.get("/api/v1/video/status")
    discovery = client.get(
        "/api/v1/robot/video", headers={"host": "robot-gateway:8093"}
    )

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["service"] == "robot-video-gateway"
    assert health.headers["cache-control"] == "no-store"

    status_payload = video_status.json()
    assert status_payload == {
        "robot_id": "go2-test-01",
        "status": "online",
        "robot_connected": True,
        "video_connected": True,
        "streaming": True,
        "fps": 14.5,
        "width": 1280,
        "height": 720,
        "last_frame_age_ms": 10.0,
        "frame_count": 9,
        "dropped_frame_count": 2,
        "clients": 0,
        "timestamp": status_payload["timestamp"],
    }
    assert "robotIp" not in status_payload
    assert "transport" not in status_payload

    discovery_payload = discovery.json()
    assert discovery_payload["robot_id"] == "go2-test-01"
    assert discovery_payload["video"] == {
        "available": True,
        "protocol": "mjpeg",
        "stream_url": "http://robot-gateway:8093/stream.mjpg",
        "width": 1280,
        "height": 720,
        "fps": 14.5,
    }


def test_public_video_status_distinguishes_liveness_from_video_health() -> None:
    runtime = FakeRuntime()
    runtime.connected = True
    runtime.video_ready = False
    runtime.video_state = "stalled"
    runtime.frame_age_ms = 4_000.0
    client = TestClient(create_video_bridge(runtime))

    assert client.get("/healthz").status_code == 200
    payload = client.get("/api/v1/video/status").json()
    assert payload["status"] == "degraded"
    assert payload["robot_connected"] is True
    assert payload["video_connected"] is True
    assert payload["streaming"] is False
    assert payload["last_frame_age_ms"] == 4_000.0


def test_mjpeg_stream_does_not_create_connection_or_leak_client() -> None:
    runtime = FakeRuntime()
    client = TestClient(create_video_bridge(runtime))

    response = client.get("/stream.mjpg?frames=1")

    assert response.status_code == 200
    assert b"Content-Type: image/jpeg" in response.content
    assert b"jpeg-bytes" in response.content
    assert response.headers["cache-control"] == (
        "no-store, no-cache, must-revalidate, max-age=0"
    )
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["expires"] == "0"
    assert runtime.clients == 0


def test_versioned_stream_alias_uses_same_latest_frame_stream() -> None:
    runtime = FakeRuntime()
    client = TestClient(create_video_bridge(runtime))

    response = client.get("/api/v1/robot/video/stream?frames=1")

    assert response.status_code == 200
    assert b"jpeg-bytes" in response.content
    assert runtime.clients == 0


def test_stale_snapshot_returns_503_instead_of_cached_jpeg() -> None:
    runtime = FakeRuntime()
    runtime.connected = False
    runtime.video_ready = False
    runtime.video_state = "offline"
    runtime.frame_age_ms = 15_000.0
    client = TestClient(create_video_bridge(runtime))

    response = client.get("/snapshot")

    assert response.status_code == 503
    assert response.json()["code"] == "video_frame_stale"
    assert response.json()["lastFrameAt"] == runtime.frame.captured_at
    assert response.json()["frameAgeSeconds"] == 15.0


def test_runtime_shutdown_ends_mjpeg_stream_and_releases_client() -> None:
    runtime = FakeRuntime()
    runtime.connected = False
    runtime.started = False
    runtime.video_ready = False
    runtime.video_state = "offline"
    client = TestClient(create_video_bridge(runtime))

    response = client.get("/stream.mjpg")

    assert response.status_code == 200
    assert response.content == b""
    assert runtime.clients == 0


def test_stalled_and_offline_mjpeg_streams_wait_and_resume_same_connection() -> None:
    async def exercise(video_state: str) -> None:
        runtime = FakeRuntime()
        runtime.connected = video_state != "offline"
        runtime.video_ready = False
        runtime.video_state = video_state
        app = create_video_bridge(runtime)
        route = next(
            route
            for route in app.routes
            if getattr(route, "path", None) == "/stream.mjpg"
        )
        response = route.endpoint(frames=1)
        pending_frame = asyncio.create_task(response.body_iterator.__anext__())

        await asyncio.sleep(0.08)
        assert not pending_frame.done()
        assert runtime.clients == 1

        runtime.connected = True
        runtime.video_ready = True
        runtime.video_state = "live"
        runtime.frame = LatestVideoFrame(
            jpeg=f"recovered-{video_state}".encode(),
            sequence=10,
            captured_at="2026-09-05T10:00:00+08:00",
            width=1280,
            height=720,
            fps=15.0,
        )
        recovered = await asyncio.wait_for(pending_frame, timeout=0.5)
        assert f"recovered-{video_state}".encode() in recovered
        await response.body_iterator.aclose()
        assert runtime.clients == 0

    asyncio.run(exercise("stalled"))
    asyncio.run(exercise("offline"))


def test_dashboard_reconnects_stream_on_error_and_video_recovery() -> None:
    response = TestClient(create_video_bridge(FakeRuntime())).get("/")

    assert response.status_code == 200
    assert 'id="video"' in response.text
    assert "video.addEventListener('error'" in response.text
    assert "previousVideoReady===false&&d.hasFrame" in response.text
    assert "/stream.mjpg?t=${Date.now()}" in response.text


def test_mjpeg_generator_cancellation_releases_client() -> None:
    runtime = FakeRuntime()
    app = create_video_bridge(runtime)
    route = next(route for route in app.routes if getattr(route, "path", None) == "/stream.mjpg")
    response = route.endpoint(frames=None)

    async def consume_then_cancel() -> None:
        first = await response.body_iterator.__anext__()
        assert b"jpeg-bytes" in first
        assert runtime.clients == 1
        await response.body_iterator.aclose()

    asyncio.run(consume_then_cancel())

    assert runtime.clients == 0


def test_follow_target_debug_is_read_only_view() -> None:
    class Forwarder:
        def debug_status(self) -> dict:
            return {
                "enabled": True,
                "running": True,
                "destination": {"host": "192.168.8.10", "port": 8766},
                "state_age_ms": 12,
                "state": {"target_valid": True, "bearing_deg": -15.0},
            }

    client = TestClient(
        create_video_bridge(FakeRuntime(), follow_target_forwarder=Forwarder())
    )

    response = client.get("/debug/follow-target")
    assert response.status_code == 200
    assert response.json()["state"]["bearing_deg"] == -15.0


class _CompanionControl:
    def __init__(self) -> None:
        self.state = "IDLE"
        self.start_error: WirelessCompanionControlError | None = None

    def companion_status(self) -> dict[str, object]:
        active = self.state == "FOLLOWING"
        return {
            "state": self.state,
            "runtime_active": active,
            "robot_online": True,
            "uwb": {"valid": True, "distance_m": 1.3, "bearing_deg": -12.0},
        }

    def start_companion(self) -> dict[str, object]:
        if self.start_error is not None:
            raise self.start_error
        self.state = "FOLLOWING"
        return self.companion_status()

    def stop_companion(self) -> dict[str, object]:
        self.state = "IDLE"
        return self.companion_status()

    def resume_companion(self) -> dict[str, object]:
        self.state = "FOLLOWING"
        return self.companion_status()

    def apply_voice_intent(self, intent_value: str) -> dict[str, object]:
        return {"intent": intent_value, "executed": True}

    def ingest_risk_event(self, payload: dict[str, object]) -> dict[str, object]:
        self.state = "VOICE_CHECK"
        return {"eventAccepted": True, "incident_id": payload.get("incident_id")}

    def record_no_response(self) -> dict[str, object]:
        self.state = "RECHECK"
        return self.companion_status()

    def manual_key(self, key: str) -> dict[str, object]:
        self.state = "MANUAL_CONTROL"
        return {"key": key, **self.companion_status()}

    def release_manual(self) -> dict[str, object]:
        self.state = "IDLE"
        return self.companion_status()

    def reset_demo(self) -> dict[str, object]:
        self.state = "IDLE"
        return {"reset": True, "companion": self.companion_status()}

    def robot_status(self) -> dict[str, object]:
        return {"robotId": "go2_edu_01", "online": True, "transport": "webrtc"}


def test_companion_http_control_reuses_attached_runtime_control() -> None:
    control = _CompanionControl()
    client = TestClient(create_video_bridge(FakeRuntime(), companion_control=control))

    idle = client.get("/api/v1/robot/companion/status")
    started = client.post("/api/v1/robot/companion/start")
    robot = client.get("/api/robot/status")
    stopped = client.post("/api/v1/robot/companion/stop")

    assert idle.json()["data"]["state"] == "IDLE"
    assert started.json()["data"]["state"] == "FOLLOWING"
    assert robot.json()["data"]["transport"] == "webrtc"
    assert stopped.json()["data"]["state"] == "IDLE"


def test_companion_start_preserves_safety_failure_code() -> None:
    control = _CompanionControl()
    control.start_error = WirelessCompanionControlError(
        "UWB_NOT_READY", "wireless follow preflight failed: uwb_stale", 503
    )
    client = TestClient(create_video_bridge(FakeRuntime(), companion_control=control))

    response = client.post("/api/v1/robot/companion/start")

    assert response.status_code == 503
    assert response.json()["code"] == "UWB_NOT_READY"


def test_competition_lifecycle_http_endpoints_share_attached_control() -> None:
    control = _CompanionControl()
    client = TestClient(create_video_bridge(FakeRuntime(), companion_control=control))

    intent = client.post(
        "/api/v1/robot/companion/intent", json={"intent": "I_AM_OK"}
    )
    risk = client.post(
        "/api/v1/robot/companion/risk-event",
        json={
            "event_type": "FALL_SUSPECTED",
            "incident_id": "FALL-HTTP-001",
            "timestamp": "2026-08-31T10:00:00+08:00",
            "confidence": 0.8,
        },
    )
    no_response = client.post("/api/v1/robot/companion/no-response")
    manual = client.post(
        "/api/v1/robot/companion/manual", json={"key": "W"}
    )
    release = client.post("/api/v1/robot/companion/manual/release")
    reset = client.post("/api/v1/robot/companion/reset-demo")

    assert intent.status_code == 200
    assert intent.json()["data"]["executed"] is True
    assert risk.json()["data"]["incident_id"] == "FALL-HTTP-001"
    assert no_response.json()["data"]["state"] == "RECHECK"
    assert manual.json()["data"]["state"] == "MANUAL_CONTROL"
    assert release.json()["data"]["state"] == "IDLE"
    assert reset.json()["data"]["reset"] is True
