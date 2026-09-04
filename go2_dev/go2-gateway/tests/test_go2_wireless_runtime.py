from __future__ import annotations

import asyncio
import inspect
import logging
from pathlib import Path
import threading
import time

import numpy as np
import pytest

from app.core.errors import GatewayError
from app.webrtc.go2_wireless_runtime import (
    ExpectedAioiceBindNoiseFilter,
    Go2WirelessRuntime,
    HighFrequencyUnitreeDataLogFilter,
)
from app.adapters.webrtc_motion_backend import WebRTCMotionBackend
from app.webrtc.video_bridge import create_video_bridge


TOPICS = {
    "LOW_STATE": "rt/lf/lowstate",
    "MULTIPLE_STATE": "rt/multiplestate",
    "UWB_STATE": "rt/uwbstate",
    "LF_SPORT_MOD_STATE": "rt/lf/sportmodestate",
    "SPORT_MOD_STATE": "rt/sportmodestate",
    "SPORT_MOD": "rt/api/sport/request",
}
COMMANDS = {
    "BalanceStand": 1002,
    "StopMove": 1003,
    "Euler": 1007,
    "Move": 1008,
    "BodyHeight": 1013,
}


class FakePubSub:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.subscriptions: dict[str, object] = {}
        self.unsubscriptions: list[str] = []

    def subscribe(self, topic: str, callback) -> None:
        import json

        self.subscriptions[topic] = callback
        if topic in {"rt/lf/sportmodestate", "rt/sportmodestate"}:
            callback(
                {
                    "data": {
                        "position": [0.0, 0.0, 0.0],
                        "velocity": [0.0, 0.0, 0.0],
                        "imu_state": {"rpy": [0.0, 0.0, 0.0]},
                    }
                }
            )
        elif topic in {"rt/lf/lowstate", "rt/lowstate"}:
            callback({"data": {"power_v": 29.8}})
        elif topic == "rt/multiplestate":
            callback({"data": json.dumps({"uwbSwitch": True})})

    async def publish_request_new(self, _topic: str, options: dict) -> dict:
        self.requests.append(options)
        return {"data": {"header": {"status": {"code": 0}}}}

    def unsubscribe(self, topic: str) -> None:
        self.unsubscriptions.append(topic)
        self.subscriptions.pop(topic, None)


class AutoReadyPubSub(FakePubSub):
    def subscribe(self, topic: str, callback) -> None:
        super().subscribe(topic, callback)
        if topic == "rt/uwbstate":
            callback(
                {
                    "data": {
                        "distance_est": 1.5,
                        "orientation_est": 0.1,
                        "yaw_est": 0.0,
                        "enabled_from_app": 1,
                    }
                }
            )


class BlockingMotionPubSub(FakePubSub):
    def __init__(self) -> None:
        super().__init__()
        self.move_entered = threading.Event()
        self.release_move = threading.Event()
        self.active_requests = 0
        self.maximum_active_requests = 0

    async def publish_request_new(self, _topic: str, options: dict) -> dict:
        self.requests.append(options)
        self.active_requests += 1
        self.maximum_active_requests = max(
            self.maximum_active_requests, self.active_requests
        )
        try:
            if options.get("api_id") == COMMANDS["Move"]:
                self.move_entered.set()
                while not self.release_move.is_set():
                    await asyncio.sleep(0.005)
            return {"data": {"header": {"status": {"code": 0}}}}
        finally:
            self.active_requests -= 1


class NeverAckMotionPubSub(FakePubSub):
    async def publish_request_new(self, _topic: str, options: dict) -> dict:
        self.requests.append(options)
        await asyncio.sleep(10.0)
        return {"data": {"header": {"status": {"code": 0}}}}


class BlockingStopPubSub(FakePubSub):
    def __init__(self) -> None:
        super().__init__()
        self.stop_entered = threading.Event()
        self.release_stop = threading.Event()

    async def publish_request_new(self, _topic: str, options: dict) -> dict:
        self.requests.append(options)
        if options.get("api_id") == COMMANDS["StopMove"]:
            self.stop_entered.set()
            while not self.release_stop.is_set():
                await asyncio.sleep(0.005)
        return {"data": {"header": {"status": {"code": 0}}}}


class FakeVideo:
    def __init__(self) -> None:
        self.callback = None
        self.callbacks = []
        self.enabled = False
        self.switches: list[bool] = []

    def add_track_callback(self, callback) -> None:
        self.callback = callback
        self.callbacks.append(callback)

    def switchVideoChannel(self, enabled: bool) -> None:
        self.enabled = enabled
        self.switches.append(enabled)


class FakeAudioFrame:
    sample_rate = 100
    format = type("Format", (), {"name": "s16"})()
    layout = type("Layout", (), {"channels": ("left", "right")})()

    def to_ndarray(self):
        return np.full((1, 100), 1000, dtype=np.int16)


class FakeAudio:
    def __init__(self) -> None:
        self.callback = None
        self.switches = []

    def add_track_callback(self, callback) -> None:
        self.callback = callback

    def switchAudioChannel(self, enabled: bool) -> None:
        self.switches.append(enabled)
        if enabled and self.callback is not None:
            asyncio.get_running_loop().create_task(self.callback(FakeAudioFrame()))


class FakeConnection:
    def __init__(self) -> None:
        self.datachannel = type("DataChannel", (), {})()
        self.datachannel.pub_sub = FakePubSub()
        self.video = FakeVideo()
        self.audio = FakeAudio()
        self.connect_count = 0
        self.disconnect_count = 0

    async def connect(self) -> None:
        self.connect_count += 1

    async def disconnect(self) -> None:
        self.disconnect_count += 1


class FakeEventEmitter:
    def __init__(self) -> None:
        self.callbacks: dict[str, list[object]] = {}

    def on(self, event: str, callback=None):
        if callback is None:
            def decorator(observed):
                self.callbacks.setdefault(event, []).append(observed)
                return observed

            return decorator
        self.callbacks.setdefault(event, []).append(callback)
        return callback

    def emit(self, event: str) -> None:
        for callback in list(self.callbacks.get(event, [])):
            callback()


class LifecyclePeer(FakeEventEmitter):
    def __init__(self) -> None:
        super().__init__()
        self.connectionState = "connected"
        self.iceConnectionState = "completed"


class LifecycleConnection(FakeConnection):
    def __init__(self, *, connect_blocked: bool = False) -> None:
        super().__init__()
        self.pc = LifecyclePeer()
        self.datachannel.channel = FakeEventEmitter()
        self.datachannel.data_channel_opened = True
        self.isConnected = False
        self.connect_started = threading.Event()
        self.allow_connect = threading.Event()
        if not connect_blocked:
            self.allow_connect.set()

    async def connect(self) -> None:
        self.connect_count += 1
        self.connect_started.set()
        while not self.allow_connect.is_set():
            await asyncio.sleep(0.005)
        self.isConnected = True
        self.pc.connectionState = "connected"
        self.pc.iceConnectionState = "completed"
        self.datachannel.data_channel_opened = True

    async def disconnect(self) -> None:
        self.disconnect_count += 1
        self.isConnected = False
        self.datachannel.data_channel_opened = False

    def close_peer(self) -> None:
        self.isConnected = False
        self.pc.connectionState = "closed"
        self.pc.iceConnectionState = "closed"
        self.pc.emit("connectionstatechange")

    def close_datachannel(self) -> None:
        self.datachannel.data_channel_opened = False
        self.datachannel.channel.emit("close")

    def fail_ice(self) -> None:
        self.pc.iceConnectionState = "failed"
        self.pc.emit("iceconnectionstatechange")

    def disconnect_ice(self) -> None:
        self.pc.iceConnectionState = "disconnected"
        self.pc.emit("iceconnectionstatechange")

    def restore_ice(self) -> None:
        self.pc.connectionState = "connected"
        self.pc.iceConnectionState = "completed"
        self.pc.emit("connectionstatechange")
        self.pc.emit("iceconnectionstatechange")


class FailingLifecycleConnection(LifecycleConnection):
    async def connect(self) -> None:
        self.connect_count += 1
        self.connect_started.set()
        raise RuntimeError("simulated reconnect failure")


def wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true before timeout")


class FakeAudioHub:
    def __init__(self) -> None:
        self.entries: list[dict[str, str]] = []
        self.audio_list_requests = 0
        self.uploads: list[str] = []
        self.played: list[str] = []
        self.deleted: list[str] = []
        self.uploaded_bytes: list[bytes] = []

    async def get_audio_list(self) -> dict:
        import json

        self.audio_list_requests += 1
        return {"data": {"data": json.dumps({"audio_list": self.entries})}}

    async def upload_audio_file(self, path: str) -> None:
        name = Path(path).stem
        unique_id = f"uuid-{len(self.entries) + 1}"
        self.uploads.append(path)
        self.uploaded_bytes.append(Path(path).read_bytes())
        self.entries.append({"CUSTOM_NAME": name, "UNIQUE_ID": unique_id})

    async def play_by_uuid(self, unique_id: str) -> None:
        self.played.append(unique_id)

    async def delete_record(self, unique_id: str) -> None:
        self.deleted.append(unique_id)
        self.entries = [
            entry for entry in self.entries if entry["UNIQUE_ID"] != unique_id
        ]


class FakeFrame:
    def to_ndarray(self, format: str):
        assert format == "bgr24"
        return np.zeros((8, 12, 3), dtype=np.uint8)


class FailingFrame:
    def to_ndarray(self, format: str):
        assert format == "bgr24"
        raise RuntimeError("simulated JPEG input failure")


class OneFrameTrack:
    def __init__(self) -> None:
        self.sent = False

    async def recv(self):
        if not self.sent:
            self.sent = True
            return FakeFrame()
        await asyncio.sleep(60)


class OneFailingFrameTrack:
    def __init__(self) -> None:
        self.sent = False

    async def recv(self):
        if not self.sent:
            self.sent = True
            return FailingFrame()
        await asyncio.sleep(60)


class RepeatingFrameTrack:
    async def recv(self):
        await asyncio.sleep(0.01)
        return FakeFrame()


class ResumableFrameTrack:
    def __init__(self, *, initial_duration_seconds: float = 1.10) -> None:
        self.initial_duration_seconds = initial_duration_seconds
        self.initial_started: float | None = None
        self.resume = threading.Event()

    async def recv(self):
        await asyncio.sleep(0.01)
        now = time.monotonic()
        if self.initial_started is None:
            self.initial_started = now
        if now - self.initial_started < self.initial_duration_seconds:
            return FakeFrame()
        while not self.resume.is_set():
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.01)
        return FakeFrame()


class EndedTrack:
    async def recv(self):
        raise RuntimeError("simulated track end")


def test_single_connection_carries_state_motion_and_video() -> None:
    connection = FakeConnection()
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        command_timeout_seconds=0.5,
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        capture_fps=30,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
    )
    runtime.start()
    try:
        assert connection.connect_count == 1
        assert connection.video.enabled is True
        assert connection.video.callback is not None
        video_future = asyncio.run_coroutine_threadsafe(
            connection.video.callback(RepeatingFrameTrack()), runtime._loop
        )
        wait_until(lambda: runtime.status()["videoReady"] is True)

        frame = runtime.latest_frame()
        assert frame is not None
        assert frame.width == 12
        assert frame.height == 8
        assert runtime.send_move(0.2, 0.0, 0.0) == 0
        assert runtime.stop_motion() == 0
        status = runtime.status()
        assert status["connectionOwner"] == "Go2WirelessRuntime"
        assert status["connectionCount"] == 1
        assert status["sportStateReady"] is True
        assert status["videoReady"] is True
        assert status["rawFrameCount"] >= 1
        assert status["encodedFrameCount"] >= 1
        assert status["lastRawFrameAt"] is not None
        assert status["lastEncodedFrameAt"] is not None
        assert status["rawFrameAgeSeconds"] is not None
        assert status["encodedFrameAgeSeconds"] is not None
        assert status["encodeQueueDepth"] <= 1
        assert status["encodeDurationMsLast"] is not None
        assert status["encodeDurationMsMax"] is not None
        assert status["encodeDurationMsEwma"] is not None
        assert status["lastSportStateAt"] is not None
        assert status["sportStateAgeSeconds"] is not None
        assert connection.connect_count == 1
    finally:
        video_future.cancel()
        runtime.close()

    assert connection.disconnect_count == 1


def test_motion_rpc_is_single_flight_when_stop_arrives_during_move_ack_wait() -> None:
    connection = FakeConnection()
    pub_sub = BlockingMotionPubSub()
    connection.datachannel.pub_sub = pub_sub
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        command_timeout_seconds=0.5,
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        enable_video=False,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
    )
    errors = []
    runtime.start()
    move_thread = threading.Thread(
        target=lambda: _capture_thread_error(
            errors, lambda: runtime.send_move(0.1, 0.0, 0.0)
        )
    )
    stop_thread = threading.Thread(
        target=lambda: _capture_thread_error(errors, runtime.stop_motion)
    )
    try:
        move_thread.start()
        assert pub_sub.move_entered.wait(0.5)
        stop_thread.start()
        time.sleep(0.05)
        assert len(pub_sub.requests) == 1
        assert stop_thread.is_alive()
        pub_sub.release_move.set()
        move_thread.join(timeout=1.0)
        stop_thread.join(timeout=1.0)

        assert errors == []
        assert [row["api_id"] for row in pub_sub.requests] == [1008, 1003]
        assert pub_sub.maximum_active_requests == 1
        motion_rpc = runtime.status()["motionRpc"]
        assert motion_rpc["inFlight"] is None
        assert motion_rpc["lastAckCommand"] == "StopMove"
        assert motion_rpc["lastAckLatencyMs"] is not None
        assert motion_rpc["timeoutCount"] == 0
    finally:
        pub_sub.release_move.set()
        move_thread.join(timeout=1.0)
        stop_thread.join(timeout=1.0)
        runtime.close(send_stop=False)


def test_motion_rpc_timeout_is_bounded_and_reported_without_stale_inflight() -> None:
    connection = FakeConnection()
    connection.datachannel.pub_sub = NeverAckMotionPubSub()
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        command_timeout_seconds=0.05,
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        enable_video=False,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
    )
    runtime.start()
    try:
        with pytest.raises(GatewayError, match="Move request failed: TimeoutError"):
            runtime.send_move(0.1, 0.0, 0.0)
        motion_rpc = runtime.status()["motionRpc"]
        assert motion_rpc["inFlight"] is None
        assert motion_rpc["lastError"].startswith("TimeoutError")
        assert motion_rpc["timeoutCount"] == 1
    finally:
        runtime.close(send_stop=False)


def _capture_thread_error(errors: list[Exception], callback) -> None:
    try:
        callback()
    except Exception as exc:  # pragma: no cover - assertion reports the value
        errors.append(exc)


def test_raw_frame_is_observed_even_when_jpeg_encode_fails() -> None:
    connection = FakeConnection()
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        stale_timeout_seconds=5.0,
        capture_fps=30,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
    )
    runtime.start()
    future = asyncio.run_coroutine_threadsafe(
        connection.video.callback(OneFailingFrameTrack()), runtime._loop
    )
    try:
        wait_until(lambda: runtime.status()["videoErrorCount"] == 1)
        status = runtime.status()
        assert status["rawFrameCount"] == 1
        assert status["lastRawFrameAt"] is not None
        assert status["rawFrameAgeSeconds"] is not None
        assert status["encodedFrameCount"] == 0
        assert status["lastEncodedFrameAt"] is None
        assert status["encodedFrameAgeSeconds"] is None
        assert status["encodeDurationMsLast"] is not None
    finally:
        future.cancel()
        runtime.close(send_stop=False)


def test_status_distinguishes_fresh_raw_from_stale_encoded_frame() -> None:
    runtime = Go2WirelessRuntime("192.168.8.252")
    now = time.monotonic()
    with runtime._lock:
        runtime._connected = True
        runtime._data_channel_ready = True
        runtime._connection_generation = 4
        runtime._last_raw_frame_generation = 4
        runtime._last_frame_generation = 4
        runtime._last_raw_frame_monotonic = now - 0.02
        runtime._last_frame_monotonic = now - 3.2
        runtime._state_received_monotonic = now - 0.01

    status = runtime.status()

    assert status["rawFrameAgeSeconds"] < 0.2
    assert status["encodedFrameAgeSeconds"] >= 3.0
    assert status["sportStateAgeSeconds"] < 0.2
    assert status["dataChannelReady"] is True


def test_disconnect_snapshot_preserves_both_stale_frame_ages() -> None:
    runtime = Go2WirelessRuntime("192.168.8.252")
    connection = LifecycleConnection()
    now = time.monotonic()
    with runtime._lock:
        runtime._connection = connection
        runtime._connected = True
        runtime._data_channel_ready = True
        runtime._connection_generation = 1
        runtime._last_raw_frame_generation = 1
        runtime._last_frame_generation = 1
        runtime._connected_monotonic = now - 4.0
        runtime._last_raw_frame_monotonic = now - 3.2
        runtime._last_frame_monotonic = now - 3.3
        runtime._state_received_monotonic = now - 0.02

    assert runtime._handle_connection_lost(
        "video_frame_stale",
        connection,
        diagnostic_reason="encoded_frame_stale",
    )
    snapshot = runtime.status()["recentDisconnects"][-1]

    assert snapshot["reason"] == "video_frame_stale"
    assert snapshot["diagnosticReason"] == "encoded_frame_stale"
    assert snapshot["rawFrameAgeSeconds"] >= 3.0
    assert snapshot["encodedFrameAgeSeconds"] >= 3.0
    assert snapshot["sportStateAgeSeconds"] < 0.2
    assert snapshot["peerState"] == "connected"
    assert snapshot["iceState"] == "completed"
    assert snapshot["dataChannelReady"] is True


def test_recent_disconnects_is_bounded_and_snapshots_are_immutable() -> None:
    runtime = Go2WirelessRuntime("192.168.8.252", enable_video=False)
    for index in range(25):
        connection = LifecycleConnection()
        with runtime._lock:
            runtime._connection = connection
            runtime._connected = True
            runtime._data_channel_ready = True
            runtime._connection_generation = index + 1
            runtime._peer_connection_state = f"peer-{index}"
            runtime._ice_connection_state = f"ice-{index}"
        assert runtime._handle_connection_lost(f"loss-{index}", connection)

    first_read = runtime.status()["recentDisconnects"]
    assert len(first_read) == 20
    assert first_read[0]["reason"] == "loss-5"
    assert first_read[-1]["reason"] == "loss-24"

    first_read[0]["peerState"] = "mutated"
    with runtime._lock:
        runtime._peer_connection_state = "later-state"
    second_read = runtime.status()["recentDisconnects"]
    assert second_read[0]["peerState"] == "connected"


def test_supervisor_marks_loss_reconnects_and_ignores_old_callbacks() -> None:
    first = LifecycleConnection()
    second = LifecycleConnection(connect_blocked=True)
    third = LifecycleConnection()
    connections = iter((first, second, third))
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        command_timeout_seconds=0.5,
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        state_stale_seconds=5.0,
        frame_stale_seconds=5.0,
        reconnect_delay_seconds=0.02,
        capture_fps=30,
        connection_factory=lambda _ip, _key: (
            next(connections),
            TOPICS,
            COMMANDS,
        ),
    )
    runtime.start()
    pending = []
    try:
        pending.append(
            asyncio.run_coroutine_threadsafe(
                first.video.callback(RepeatingFrameTrack()), runtime._loop
            )
        )
        wait_until(lambda: runtime.status()["videoReady"] is True)
        initial = runtime.status()
        assert initial["connected"] is True
        assert initial["connectionState"] == "connected"
        assert initial["videoState"] == "live"
        assert initial["connectionCount"] == 1

        first.close_peer()
        assert second.connect_started.wait(timeout=1.0)
        lost = runtime.status()
        assert lost["connected"] is False
        assert lost["connectionState"] == "reconnecting"
        assert lost["dataChannelReady"] is False
        assert lost["sportStateReady"] is False
        assert lost["videoReady"] is False
        assert lost["lastDisconnectReason"] == "peer_connection_closed"
        assert lost["reconnectCount"] == 1
        assert lost["lastDisconnectAt"] is not None
        assert lost["recentDisconnects"][-1]["peerState"] == "closed"
        assert lost["recentDisconnects"][-1]["iceState"] == "closed"

        second.allow_connect.set()
        wait_until(
            lambda: runtime.status()["connected"] is True
            and runtime.status()["successfulConnectionCount"] == 2
        )
        awaiting_video = runtime.status()
        assert awaiting_video["videoHealthState"] == "recovering"
        assert awaiting_video["videoState"] == "awaiting-first-frame"
        assert awaiting_video["lastRawFrameAt"] is None
        assert awaiting_video["lastEncodedFrameAt"] is None
        assert awaiting_video["rawFrameAgeSeconds"] is None
        assert awaiting_video["encodedFrameAgeSeconds"] is None
        assert awaiting_video["frameAgeSeconds"] is None
        pending.append(
            asyncio.run_coroutine_threadsafe(
                second.video.callback(RepeatingFrameTrack()), runtime._loop
            )
        )
        wait_until(lambda: runtime.status()["videoReady"] is True)
        recovered = runtime.status()
        assert recovered["connectionState"] == "connected"
        assert recovered["videoState"] == "live"
        assert recovered["lastReconnectAt"] is not None

        first.pc.emit("connectionstatechange")
        assert runtime.status()["connected"] is True
        assert runtime.status()["successfulConnectionCount"] == 2

        second.close_datachannel()
        wait_until(
            lambda: runtime.status()["connected"] is True
            and runtime.status()["successfulConnectionCount"] == 3
        )
        assert runtime.status()["reconnectCount"] == 2
        assert runtime.status()["lastDisconnectReason"] == "data_channel_closed"
        for connection in (first, second, third):
            assert len(connection.pc.callbacks["connectionstatechange"]) == 1
            assert len(connection.pc.callbacks["iceconnectionstatechange"]) == 1
            assert len(connection.datachannel.channel.callbacks["close"]) == 1
            assert len(connection.video.callbacks) == 1
    finally:
        runtime.close(send_stop=False)
        for future in pending:
            future.cancel()

    assert first.disconnect_count == 1
    assert second.disconnect_count == 1
    assert third.disconnect_count == 1


def test_reconnect_is_not_exposed_until_stopmove_is_acknowledged() -> None:
    first = LifecycleConnection()
    second = LifecycleConnection()
    blocking_stop = BlockingStopPubSub()
    second.datachannel.pub_sub = blocking_stop
    connections = iter((first, second))
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        command_timeout_seconds=1.0,
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        reconnect_delay_seconds=0.02,
        connection_factory=lambda _ip, _key: (
            next(connections),
            TOPICS,
            COMMANDS,
        ),
    )
    runtime.start()
    try:
        first.close_peer()
        assert blocking_stop.stop_entered.wait(timeout=1.0)
        waiting = runtime.status()
        assert waiting["connected"] is True
        assert waiting["dataChannelReady"] is True
        assert waiting["motionReady"] is False
        assert second.video.enabled is True
        assert waiting["motionRpc"]["stopRequiredAfterReconnect"] is True
        assert waiting["motionRpc"]["remoteStopState"] == (
            "STOP_UNCONFIRMED_TRANSPORT_LOST"
        )
        with pytest.raises(GatewayError):
            runtime.send_move(0.2, 0.0, 0.0)
        assert runtime.stop_motion() == -1
        assert [row["api_id"] for row in blocking_stop.requests] == [
            COMMANDS["StopMove"]
        ]

        blocking_stop.release_stop.set()
        wait_until(lambda: runtime.status()["motionReady"] is True)
        recovered = runtime.status()
        assert [row["api_id"] for row in blocking_stop.requests] == [
            COMMANDS["StopMove"]
        ]
        assert recovered["motionRpc"]["lastAckCommand"] == "StopMove"
        assert recovered["motionRpc"]["remoteStopState"] == (
            "STOP_CONFIRMED_AFTER_RECONNECT"
        )
        assert recovered["motionRpc"]["stopRequiredAfterReconnect"] is False
        assert recovered["motionReady"] is True
    finally:
        blocking_stop.release_stop.set()
        runtime.close(send_stop=False)


def test_cleanup_quiesces_old_generation_before_next_connect() -> None:
    first = LifecycleConnection()
    second = LifecycleConnection(connect_blocked=True)
    connections = iter((first, second))
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        stale_timeout_seconds=5.0,
        reconnect_delay_seconds=0.02,
        connection_factory=lambda _ip, _key: (next(connections), TOPICS, COMMANDS),
    )
    runtime.start()

    class Heartbeat:
        stopped = False

        def stop_heartbeat(self) -> None:
            self.stopped = True

    class NetworkStatus:
        stopped = False

        def stop_network_status_fetch(self) -> None:
            self.stopped = True

    heartbeat = Heartbeat()
    network_status = NetworkStatus()
    resolver = type(
        "Resolver",
        (),
        {"pending_callbacks": {}, "chunk_data_storage": {"old": [b"data"]}},
    )()
    first.datachannel.heartbeat = heartbeat
    first.datachannel.rtc_inner_req = type(
        "InnerRequest", (), {"network_status": network_status}
    )()
    first.datachannel.pub_sub.future_resolver = resolver

    async def install_pending_future():
        pending = asyncio.get_running_loop().create_future()
        resolver.pending_callbacks["old"] = [pending]
        return pending

    resolver_future = asyncio.run_coroutine_threadsafe(
        install_pending_future(), runtime._loop
    ).result(timeout=1.0)
    track_future = asyncio.run_coroutine_threadsafe(
        first.video.callback(OneFrameTrack()), runtime._loop
    )
    try:
        wait_until(lambda: runtime.status()["rawFrameCount"] == 1)
        first.close_peer()
        assert second.connect_started.wait(timeout=1.0)
        assert heartbeat.stopped is True
        assert network_status.stopped is True
        assert resolver_future.cancelled() is True
        assert resolver.pending_callbacks == {}
        assert resolver.chunk_data_storage == {}
        assert first.datachannel.pub_sub.subscriptions == {}
        wait_until(track_future.done)
        second.allow_connect.set()
        wait_until(lambda: runtime.status()["connected"] is True)
    finally:
        track_future.cancel()
        runtime.close(send_stop=False)


def test_video_track_end_uses_the_same_connection_loss_path() -> None:
    first = LifecycleConnection()
    second = LifecycleConnection(connect_blocked=True)
    connections = iter((first, second))
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        command_timeout_seconds=0.5,
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        state_stale_seconds=5.0,
        frame_stale_seconds=5.0,
        reconnect_delay_seconds=0.02,
        connection_factory=lambda _ip, _key: (
            next(connections),
            TOPICS,
            COMMANDS,
        ),
    )
    runtime.start()
    try:
        future = asyncio.run_coroutine_threadsafe(
            first.video.callback(EndedTrack()), runtime._loop
        )
        future.result(timeout=1.0)
        assert second.connect_started.wait(timeout=1.0)
        status = runtime.status()
        assert status["connected"] is False
        assert status["videoReady"] is False
        assert status["lastDisconnectReason"] == "video_track_ended:RuntimeError"
        assert status["reconnectCount"] == 1
        second.allow_connect.set()
        wait_until(lambda: runtime.status()["connected"] is True)
    finally:
        runtime.close(send_stop=False)


def test_ice_failure_wakes_the_single_reconnect_supervisor() -> None:
    first = LifecycleConnection()
    second = LifecycleConnection(connect_blocked=True)
    connections = iter((first, second))
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        state_stale_seconds=5.0,
        frame_stale_seconds=5.0,
        reconnect_delay_seconds=0.02,
        connection_factory=lambda _ip, _key: (
            next(connections),
            TOPICS,
            COMMANDS,
        ),
    )
    runtime.start()
    try:
        first.fail_ice()
        assert second.connect_started.wait(timeout=1.0)
        assert runtime.status()["lastDisconnectReason"] == "ice_connection_failed"
        assert runtime.status()["reconnectCount"] == 1
        second.allow_connect.set()
        wait_until(lambda: runtime.status()["connected"] is True)
    finally:
        runtime.close(send_stop=False)


def test_reconnect_backoff_is_exponential_and_capped() -> None:
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        reconnect_delay_seconds=2.0,
        reconnect_backoff_step_seconds=2.0,
        reconnect_max_delay_seconds=15.0,
    )

    assert [
        runtime._reconnect_delay_for_failure_streak(streak)
        for streak in range(5)
    ] == [2.0, 4.0, 8.0, 15.0, 15.0]


def test_reconnect_failure_streak_resets_only_after_stable_window() -> None:
    first = LifecycleConnection()
    failed = FailingLifecycleConnection()
    recovered = LifecycleConnection()
    connections = iter((first, failed, recovered))
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        reconnect_delay_seconds=0.01,
        reconnect_backoff_step_seconds=0.01,
        reconnect_max_delay_seconds=0.08,
        reconnect_stable_reset_seconds=0.20,
        connection_factory=lambda _ip, _key: (
            next(connections),
            TOPICS,
            COMMANDS,
        ),
    )
    runtime.start()
    try:
        first.close_peer()
        wait_until(
            lambda: runtime.status()["successfulConnectionCount"] == 2
        )
        before_stable = runtime.status()
        assert before_stable["reconnectFailureStreak"] == 1
        assert before_stable["lastReconnectDelaySeconds"] == pytest.approx(
            0.02
        )
        wait_until(
            lambda: runtime.status()["reconnectFailureStreak"] == 0,
            timeout=1.0,
        )
    finally:
        runtime.close(send_stop=False)


def test_ice_disconnected_uses_grace_and_stop_ack_without_reconnect() -> None:
    first = LifecycleConnection()
    second = LifecycleConnection(connect_blocked=True)
    connections = iter((first, second))
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        disconnect_grace_seconds=0.30,
        reconnect_delay_seconds=0.01,
        connection_factory=lambda _ip, _key: (
            next(connections),
            TOPICS,
            COMMANDS,
        ),
    )
    runtime.start()
    try:
        assert runtime.status()["motionReady"] is True
        first.disconnect_ice()
        wait_until(
            lambda: runtime.status()["transportDisconnectGrace"]["active"]
        )
        grace = runtime.status()
        assert grace["connected"] is True
        assert grace["connectionState"] == "transport_grace"
        assert grace["motionReady"] is False
        assert grace["motionRpc"]["remoteStopState"] == (
            "STOP_UNCONFIRMED_TRANSPORT_LOST"
        )
        assert not second.connect_started.is_set()

        first.restore_ice()
        wait_until(lambda: runtime.status()["motionReady"] is True)
        recovered = runtime.status()
        assert recovered["reconnectCount"] == 0
        assert recovered["transportDisconnectGrace"]["active"] is False
        assert recovered["motionRpc"]["remoteStopState"] == (
            "STOP_CONFIRMED_AFTER_TRANSPORT_GRACE"
        )
        assert [row["api_id"] for row in first.datachannel.pub_sub.requests] == [
            COMMANDS["StopMove"]
        ]
        assert not second.connect_started.is_set()
    finally:
        second.allow_connect.set()
        runtime.close(send_stop=False)


def test_ice_disconnected_grace_expiry_reconnects() -> None:
    first = LifecycleConnection()
    second = LifecycleConnection(connect_blocked=True)
    connections = iter((first, second))
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        disconnect_grace_seconds=0.10,
        reconnect_delay_seconds=0.01,
        connection_factory=lambda _ip, _key: (
            next(connections),
            TOPICS,
            COMMANDS,
        ),
    )
    runtime.start()
    try:
        first.disconnect_ice()
        assert second.connect_started.wait(timeout=1.0)
        status = runtime.status()
        assert status["motionReady"] is False
        assert status["lastDisconnectReason"] == (
            "ice_connection_disconnected_grace_expired"
        )
        assert status["diagnosticReason"] == "ice_connection_disconnected"
    finally:
        second.allow_connect.set()
        runtime.close(send_stop=False)


def _start_sport_keepalive(connection: LifecycleConnection):
    stop = threading.Event()

    def keep_fresh() -> None:
        callback = connection.datachannel.pub_sub.subscriptions["rt/sportmodestate"]
        while not stop.wait(0.02):
            callback(
                {
                    "data": {
                        "position": [0.0, 0.0, 0.0],
                        "velocity": [0.0, 0.0, 0.0],
                        "imu_state": {"rpy": [0.0, 0.0, 0.0]},
                    }
                }
            )

    thread = threading.Thread(target=keep_fresh, daemon=True)
    thread.start()
    return stop, thread


def test_video_watchdog_waits_for_current_generation_first_raw_frame() -> None:
    first = LifecycleConnection()
    second = LifecycleConnection(connect_blocked=True)
    connections = iter((first, second))
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        stale_timeout_seconds=1.0,
        reconnect_delay_seconds=0.02,
        connection_factory=lambda _ip, _key: (
            next(connections),
            TOPICS,
            COMMANDS,
        ),
    )
    runtime.start()
    keepalive_stop, keepalive_thread = _start_sport_keepalive(first)
    try:
        time.sleep(0.4)
        status = runtime.status()
        assert status["connected"] is True
        assert status["reconnectCount"] == 0
        assert status["videoHealthState"] == "awaiting_first_raw_frame"
        assert status["videoWatchdogArmed"] is False
        assert status["rawFrameAgeSeconds"] is None
        assert status["encodedFrameAgeSeconds"] is None
        assert not second.connect_started.is_set()
    finally:
        keepalive_stop.set()
        keepalive_thread.join(timeout=1.0)
        runtime.close(send_stop=False)


def test_video_stale_alone_marks_degraded_without_reconnect(caplog) -> None:
    first = LifecycleConnection()
    second = LifecycleConnection(connect_blocked=True)
    connections = iter((first, second))
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        stale_timeout_seconds=0.1,
        reconnect_delay_seconds=0.02,
        capture_fps=30,
        connection_factory=lambda _ip, _key: (next(connections), TOPICS, COMMANDS),
    )
    caplog.set_level(logging.INFO)
    runtime.start()
    keepalive_stop, keepalive_thread = _start_sport_keepalive(first)
    track = ResumableFrameTrack()
    future = asyncio.run_coroutine_threadsafe(
        first.video.callback(track), runtime._loop
    )
    try:
        wait_until(lambda: runtime.status()["videoReady"] is True)
        wait_until(lambda: runtime.status()["videoHealthState"] == "degraded")
        status = runtime.status()
        assert status["connected"] is True
        assert status["reconnectCount"] == 0
        assert status["videoDegradedReason"] == "raw_frame_stale"
        assert status["dataHealthState"] == "healthy"
        assert not second.connect_started.is_set()
        wait_until(lambda: "action=keep_transport" in caplog.text)
        assert "action=keep_transport" in caplog.text
        # The initial True enables the track. No OFF/ON recovery was issued.
        assert first.video.switches == [True]
        track.resume.set()
        wait_until(lambda: runtime.status()["videoHealthState"] == "healthy")
        assert runtime.status()["reconnectCount"] == 0
        wait_until(lambda: "HEALTH_SIGNAL_RECOVERED signal=video" in caplog.text)
    finally:
        future.cancel()
        keepalive_stop.set()
        keepalive_thread.join(timeout=1.0)
        runtime.close(send_stop=False)


def test_video_watchdog_soft_toggle_requires_stable_recovery_frames() -> None:
    connection = LifecycleConnection()
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        enable_video_active_recovery=True,
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        stale_timeout_seconds=0.10,
        video_soft_recovery_seconds=0.20,
        video_soft_toggle_delay_seconds=0.05,
        video_soft_observe_seconds=0.50,
        video_recovery_min_frames=5,
        video_recovery_min_duration_seconds=0.05,
        video_recovery_max_gap_seconds=0.05,
        capture_fps=30,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
    )
    runtime.start()
    track = ResumableFrameTrack()
    future = asyncio.run_coroutine_threadsafe(
        connection.video.callback(track), runtime._loop
    )
    try:
        wait_until(lambda: runtime.status()["videoReady"] is True)
        wait_until(
            lambda: runtime.status()["videoWatchdog"]["soft_recovery_count"]
            == 1
        )
        wait_until(lambda: connection.video.switches[-2:] == [False, True])
        assert runtime.status()["videoHealthState"] == "soft_recovery"

        track.resume.set()
        wait_until(lambda: runtime.status()["videoHealthState"] == "healthy")
        watchdog = runtime.status()["videoWatchdog"]
        assert watchdog["video_stale_count"] == 1
        assert watchdog["soft_recovery_count"] == 1
        assert watchdog["soft_recovery_success_count"] == 1
        assert watchdog["full_reconnect_count"] == 0
        assert watchdog["false_recovery_count"] == 0
        assert watchdog["unrecovered_video_stale"] == 0
        assert watchdog["max_raw_frame_age_ms"] >= 200.0
        assert watchdog["max_recovery_duration_ms"] >= 50.0
    finally:
        future.cancel()
        runtime.close(send_stop=False)


def test_video_watchdog_full_reconnects_after_failed_soft_recovery() -> None:
    first = LifecycleConnection()
    second = LifecycleConnection(connect_blocked=True)
    connections = iter((first, second))
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        enable_video_active_recovery=True,
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        stale_timeout_seconds=0.10,
        reconnect_delay_seconds=0.02,
        video_soft_recovery_seconds=0.20,
        video_soft_toggle_delay_seconds=0.05,
        video_soft_observe_seconds=0.10,
        video_first_frame_wait_seconds=0.20,
        video_recovery_min_frames=5,
        video_recovery_min_duration_seconds=0.05,
        video_recovery_max_gap_seconds=0.05,
        capture_fps=30,
        connection_factory=lambda _ip, _key: (
            next(connections),
            TOPICS,
            COMMANDS,
        ),
    )
    runtime.start()
    future = asyncio.run_coroutine_threadsafe(
        first.video.callback(ResumableFrameTrack()), runtime._loop
    )
    try:
        wait_until(lambda: runtime.status()["videoReady"] is True)
        assert second.connect_started.wait(timeout=2.0)
        status = runtime.status()
        assert first.video.switches[-2:] == [False, True]
        assert status["lastDisconnectReason"] == "video_watchdog_reconnect"
        assert status["diagnosticReason"] == (
            "video_only_stale_after_soft_recovery"
        )
        assert status["videoWatchdog"]["video_stale_count"] == 1
        assert status["videoWatchdog"]["soft_recovery_count"] == 1
        assert status["videoWatchdog"]["soft_recovery_success_count"] == 0
        assert status["videoWatchdog"]["full_reconnect_count"] == 1
        assert status["videoWatchdog"]["unrecovered_video_stale"] == 1
    finally:
        second.allow_connect.set()
        future.cancel()
        runtime.close(send_stop=False)


def test_video_watchdog_recovers_connection_that_never_delivers_first_frame() -> None:
    first = LifecycleConnection()
    second = LifecycleConnection(connect_blocked=True)
    connections = iter((first, second))
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        enable_video_active_recovery=True,
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        stale_timeout_seconds=0.10,
        reconnect_delay_seconds=0.02,
        video_soft_recovery_seconds=0.20,
        video_soft_toggle_delay_seconds=0.05,
        video_soft_observe_seconds=0.10,
        video_first_frame_wait_seconds=0.20,
        connection_factory=lambda _ip, _key: (
            next(connections),
            TOPICS,
            COMMANDS,
        ),
    )
    runtime.start()
    try:
        assert second.connect_started.wait(timeout=2.0)
        status = runtime.status()
        assert first.video.switches[-2:] == [False, True]
        assert status["lastDisconnectReason"] == "video_watchdog_reconnect"
        assert status["videoWatchdog"]["video_stale_count"] == 1
        assert status["videoWatchdog"]["soft_recovery_count"] == 1
        assert status["videoWatchdog"]["full_reconnect_count"] == 1
        assert status["videoWatchdog"]["unrecovered_video_stale"] == 1
        assert status["diagnosticReason"] == (
            "video_only_stale_after_soft_recovery"
        )
    finally:
        second.allow_connect.set()
        runtime.close(send_stop=False)


def test_single_returned_frame_does_not_create_false_video_recovery() -> None:
    connection = LifecycleConnection()
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        enable_video_active_recovery=True,
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        stale_timeout_seconds=0.10,
        video_soft_recovery_seconds=1.0,
        video_recovery_min_frames=5,
        video_recovery_min_duration_seconds=0.10,
        video_recovery_max_gap_seconds=0.05,
        capture_fps=30,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
    )
    runtime.start()
    first = asyncio.run_coroutine_threadsafe(
        connection.video.callback(ResumableFrameTrack()), runtime._loop
    )
    second = None
    try:
        wait_until(lambda: runtime.status()["videoHealthState"] == "degraded")
        second = asyncio.run_coroutine_threadsafe(
            connection.video.callback(OneFrameTrack()), runtime._loop
        )
        wait_until(lambda: runtime.status()["videoHealthState"] == "recovering")
        time.sleep(0.20)
        status = runtime.status()
        assert status["videoHealthState"] == "recovering"
        assert status["videoReady"] is False
        assert status["videoWatchdog"]["recovery_frame_count"] == 1
        assert status["videoWatchdog"]["false_recovery_count"] == 0
        assert status["videoWatchdog"]["unrecovered_video_stale"] == 1
    finally:
        first.cancel()
        if second is not None:
            second.cancel()
        runtime.close(send_stop=False)


def test_old_outage_age_does_not_interrupt_new_recovery_frames() -> None:
    connection = LifecycleConnection()
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        enable_video_active_recovery=True,
        stale_timeout_seconds=0.10,
        video_soft_recovery_seconds=0.20,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
    )
    now = time.monotonic()
    with runtime._lock:
        runtime._connection = connection
        runtime._connected = True
        runtime._connection_generation = 2
        runtime._video_watchdog_state = "RECOVERING"
        runtime._video_recovery_started_monotonic = now - 188.0
        runtime._video_recovery_candidate_started_monotonic = now - 0.03
        runtime._video_recovery_last_frame_monotonic = now - 0.03
        runtime._video_recovery_frame_count = 1
        runtime._last_raw_frame_monotonic = now - 0.03
        runtime._last_raw_frame_generation = 2
        runtime._video_channel_enabled_monotonic = now - 1.0

    runtime._advance_video_watchdog(
        connection,
        now=now,
        raw_stale=False,
        raw_age=0.03,
        encoded_age=None,
        sport_age=0.03,
    )

    assert connection.video.switches == []
    assert runtime.status()["videoHealthState"] == "recovering"


def test_first_frame_wait_does_not_toggle_video_before_fifteen_seconds() -> None:
    connection = LifecycleConnection()
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        enable_video_active_recovery=True,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
    )
    now = time.monotonic()
    with runtime._lock:
        runtime._connection = connection
        runtime._connected = True
        runtime._connection_generation = 1
        runtime._video_watchdog_state = "AWAITING_FIRST_FRAME"
        runtime._video_channel_enabled_monotonic = now - 14.9

    runtime._advance_video_watchdog(
        connection,
        now=now,
        raw_stale=False,
        raw_age=None,
        encoded_age=None,
        sport_age=None,
    )
    assert connection.video.switches == []
    assert runtime.status()["videoHealthState"] == "awaiting_first_raw_frame"

    runtime._advance_video_watchdog(
        connection,
        now=now + 0.2,
        raw_stale=False,
        raw_age=None,
        encoded_age=None,
        sport_age=None,
    )
    assert connection.video.switches == [False]


@pytest.mark.parametrize("fresh_raw_age", [0.016, 0.985])
def test_post_soft_frame_prevents_fixed_timer_reconnect(
    fresh_raw_age: float,
) -> None:
    connection = LifecycleConnection()
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        enable_video_active_recovery=True,
        stale_timeout_seconds=3.0,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
    )
    now = time.monotonic()
    with runtime._lock:
        runtime._connection = connection
        runtime._connected = True
        runtime._connection_generation = 3
        runtime._video_watchdog_state = "SOFT_RECOVERY"
        runtime._video_soft_attempted = True
        runtime._video_soft_toggle_on_monotonic = now - 7.0
        runtime._video_soft_recovery_start_raw_frame_count = 100
        runtime._raw_frame_count = 101
        runtime._last_raw_frame_monotonic = now - fresh_raw_age
        runtime._last_raw_frame_generation = 3
        runtime._record_video_recovery_frame_locked(now - fresh_raw_age)

    runtime._advance_video_watchdog(
        connection,
        now=now,
        raw_stale=False,
        raw_age=fresh_raw_age,
        encoded_age=fresh_raw_age,
        sport_age=None,
    )

    status = runtime.status()
    assert status["connected"] is True
    assert status["lastDisconnectReason"] is None
    assert status["videoHealthState"] == "recovering"
    assert status["videoWatchdog"]["soft_recovery_success_count"] == 1


def test_soft_recovery_ignores_buffered_frame_until_video_is_back_on() -> None:
    connection = LifecycleConnection()
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        enable_video_active_recovery=True,
        video_soft_toggle_delay_seconds=0.05,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
    )
    now = time.monotonic()
    with runtime._lock:
        runtime._connection = connection
        runtime._connected = True
        runtime._connection_generation = 5
        runtime._video_watchdog_state = "SOFT_RECOVERY"
        runtime._video_soft_attempted = True
        runtime._video_soft_toggle_off_monotonic = now - 0.10
        runtime._video_soft_toggle_on_monotonic = None
        runtime._video_soft_recovery_start_raw_frame_count = 100
        runtime._raw_frame_count = 101
        runtime._last_raw_frame_monotonic = now - 0.01
        runtime._last_raw_frame_generation = 5
        runtime._record_video_recovery_frame_locked(now - 0.01)

    assert runtime.status()["videoHealthState"] == "soft_recovery"
    assert runtime.status()["videoWatchdog"]["soft_recovery_success_count"] == 0

    runtime._advance_video_watchdog(
        connection,
        now=now,
        raw_stale=False,
        raw_age=0.01,
        encoded_age=0.01,
        sport_age=None,
    )
    assert connection.video.switches == [True]
    assert runtime._video_soft_recovery_start_raw_frame_count == 101


def test_video_reconnect_waits_for_zero_frames_and_cooldown() -> None:
    connection = LifecycleConnection()
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        enable_video_active_recovery=True,
        stale_timeout_seconds=0.10,
        video_soft_observe_seconds=0.10,
        video_reconnect_cooldown_seconds=0.50,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
    )
    now = time.monotonic()
    with runtime._lock:
        runtime._connection = connection
        runtime._connected = True
        runtime._connection_generation = 4
        runtime._video_watchdog_state = "SOFT_RECOVERY"
        runtime._video_soft_attempted = True
        runtime._video_soft_toggle_on_monotonic = now - 0.20
        runtime._video_soft_recovery_start_raw_frame_count = 50
        runtime._raw_frame_count = 50
        runtime._video_watchdog_cooldown_until_monotonic = now + 0.30

    runtime._advance_video_watchdog(
        connection,
        now=now,
        raw_stale=False,
        raw_age=None,
        encoded_age=None,
        sport_age=None,
    )
    assert runtime.status()["connected"] is True

    runtime._advance_video_watchdog(
        connection,
        now=now + 0.31,
        raw_stale=False,
        raw_age=None,
        encoded_age=None,
        sport_age=None,
    )
    assert runtime.status()["connected"] is False
    assert runtime.status()["lastDisconnectReason"] == "video_watchdog_reconnect"


def test_sport_state_stale_alone_marks_degraded_without_reconnect() -> None:
    first = LifecycleConnection()
    second = LifecycleConnection(connect_blocked=True)
    connections = iter((first, second))
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        stale_timeout_seconds=0.1,
        reconnect_delay_seconds=0.02,
        capture_fps=30,
        connection_factory=lambda _ip, _key: (next(connections), TOPICS, COMMANDS),
    )
    runtime.start()
    future = asyncio.run_coroutine_threadsafe(
        first.video.callback(RepeatingFrameTrack()), runtime._loop
    )
    try:
        wait_until(lambda: runtime.status()["videoReady"] is True)
        wait_until(lambda: runtime.status()["dataHealthState"] == "degraded")
        status = runtime.status()
        assert status["connected"] is True
        assert status["reconnectCount"] == 0
        assert status["dataDegradedReason"] == "sport_state_stale"
        assert status["videoHealthState"] == "healthy"
        assert not second.connect_started.is_set()
        callback = first.datachannel.pub_sub.subscriptions["rt/sportmodestate"]
        callback({"data": {"position": [0.0, 0.0, 0.0]}})
        wait_until(lambda: runtime.status()["dataHealthState"] == "healthy")
        assert runtime.status()["reconnectCount"] == 0
    finally:
        future.cancel()
        runtime.close(send_stop=False)


def test_raw_and_sport_stale_together_reconnect_transport(caplog) -> None:
    first = LifecycleConnection()
    second = LifecycleConnection(connect_blocked=True)
    connections = iter((first, second))
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        stale_timeout_seconds=0.1,
        reconnect_delay_seconds=0.02,
        reconnect_on_multi_signal_stale=True,
        multi_signal_stale_grace_seconds=0.1,
        capture_fps=30,
        connection_factory=lambda _ip, _key: (next(connections), TOPICS, COMMANDS),
    )
    caplog.set_level(logging.WARNING)
    runtime.start()
    future = asyncio.run_coroutine_threadsafe(
        first.video.callback(ResumableFrameTrack()), runtime._loop
    )
    try:
        wait_until(lambda: runtime.status()["videoReady"] is True)
        assert second.connect_started.wait(timeout=2.5)
        status = runtime.status()
        assert status["lastDisconnectReason"] == "transport_health_stale"
        assert status["diagnosticReason"] == "raw_and_sport_state_stale"
        snapshot = status["recentDisconnects"][-1]
        assert snapshot["rawFrameAgeSeconds"] >= 0.1
        assert snapshot["sportStateAgeSeconds"] >= 0.1
        assert snapshot["peerState"] == "connected"
        assert snapshot["iceState"] == "completed"
        assert "diagnostic_reason=raw_and_sport_state_stale" in caplog.text
        second.allow_connect.set()
        wait_until(lambda: runtime.status()["connected"] is True)
    finally:
        future.cancel()
        runtime.close(send_stop=False)


def test_raw_and_sport_stale_default_to_degraded_without_reconnect() -> None:
    first = LifecycleConnection()
    second = LifecycleConnection(connect_blocked=True)
    connections = iter((first, second))
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        stale_timeout_seconds=0.1,
        reconnect_delay_seconds=0.02,
        capture_fps=30,
        connection_factory=lambda _ip, _key: (next(connections), TOPICS, COMMANDS),
    )
    runtime.start()
    future = asyncio.run_coroutine_threadsafe(
        first.video.callback(ResumableFrameTrack()), runtime._loop
    )
    try:
        wait_until(lambda: runtime.status()["videoReady"] is True)
        wait_until(
            lambda: runtime.status()["multiSignalStaleSeconds"] is not None
        )
        time.sleep(0.2)
        status = runtime.status()
        assert status["connected"] is True
        assert status["connectionState"] == "degraded"
        assert status["watchdogPolicy"] == "hard_transport_video_degraded_only"
        assert status["videoWatchdogPolicy"] == "degraded_only_keep_transport"
        assert status["videoActiveRecoveryEnabled"] is False
        assert status["reconnectCount"] == 0
        assert not second.connect_started.is_set()
    finally:
        future.cancel()
        runtime.close(send_stop=False)


def test_subscription_profile_can_remove_high_rate_and_unused_tracks() -> None:
    connection = FakeConnection()
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        enable_video=False,
        enable_uwb=False,
        enable_multiple_state=False,
        enable_low_state=False,
        enable_audio=False,
        diagnostic_mode=True,
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
    )
    runtime.start()
    try:
        subscriptions = connection.datachannel.pub_sub.subscriptions
        assert set(subscriptions) == {
            "rt/lf/sportmodestate",
            "rt/sportmodestate",
        }
        assert connection.video.callback is None
        assert connection.audio.callback is None
        profile = runtime.status()["subscriptionProfile"]
        assert runtime.status()["low_state_enabled"] is False
        assert profile == {
            "video": False,
            "uwb": False,
            "sport": True,
            "multipleState": False,
            "lowState": False,
            "audio": False,
            "topics": ["rt/lf/sportmodestate", "rt/sportmodestate"],
        }
    finally:
        runtime.close(send_stop=False)


def test_base_video_runtime_activates_and_deactivates_optional_layers() -> None:
    connection = FakeConnection()
    connection.datachannel.pub_sub = AutoReadyPubSub()
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        enable_sport_state=False,
        enable_uwb=False,
        enable_multiple_state=False,
        enable_low_state=False,
        enable_audio=False,
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
    )

    runtime.start()
    try:
        base = runtime.status()
        assert base["layers"] == {
            "base": "active",
            "companion": "standby",
            "voice": "standby",
        }
        assert base["dataHealthState"] == "standby"
        assert base["subscriptionProfile"]["topics"] == []
        assert connection.video.enabled is True
        assert connection.audio.callback is None
        assert connection.audio.switches == [False]

        active = runtime.activate_companion_inputs(
            timeout_seconds=0.5,
            enable_multiple_state=False,
        )
        assert active["layers"]["companion"] == "active"
        assert active["sportStateReady"] is True
        assert active["uwb"]["fresh"] is True
        assert set(active["subscriptionProfile"]["topics"]) == {
            "rt/lf/sportmodestate",
            "rt/sportmodestate",
            "rt/uwbstate",
        }
        assert active["subscriptionProfile"]["multipleState"] is False

        runtime.deactivate_companion_inputs()
        standby = runtime.status()
        assert standby["layers"]["companion"] == "standby"
        assert standby["subscriptionProfile"]["topics"] == []
        assert standby["sportStateReady"] is False
        assert standby["uwb"]["received"] is False
        assert set(connection.datachannel.pub_sub.unsubscriptions) == {
            "rt/lf/sportmodestate",
            "rt/sportmodestate",
            "rt/uwbstate",
        }

        voice = runtime.activate_voice()
        assert voice["layers"]["voice"] == "active"
        assert connection.audio.callback is not None
        runtime.deactivate_voice()
        assert runtime.status()["layers"]["voice"] == "standby"

        runtime.request_shutdown()
        assert runtime.is_connected() is True
        assert runtime.stop_motion() == 0
        assert connection.datachannel.pub_sub.requests[-1]["api_id"] == 1003
    finally:
        runtime.close(send_stop=False)


def test_only_runtime_module_constructs_the_peer_connection() -> None:
    backend_source = inspect.getsource(WebRTCMotionBackend)
    bridge_source = inspect.getsource(create_video_bridge)
    runtime_source = inspect.getsource(Go2WirelessRuntime)

    assert "UnitreeWebRTCConnection" not in backend_source
    assert "UnitreeWebRTCConnection" not in bridge_source
    assert "self._connection_factory" in runtime_source


def test_formal_launcher_disables_lidar_decoder() -> None:
    launcher = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "Start-Go2WirelessRuntime.ps1"
    ).read_text(encoding="utf-8")

    assert '"GO2_LIDAR_ENABLED"' in launcher
    assert '$env:GO2_LIDAR_ENABLED = "false"' in launcher


def test_formal_launcher_uses_current_robot_and_lan_video_defaults() -> None:
    launcher = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "Start-Go2WirelessRuntime.ps1"
    ).read_text(encoding="utf-8")

    assert '[string]$RobotIp = "192.168.8.245"' in launcher
    assert '[string]$ListenHost = "0.0.0.0"' in launcher
    assert '$env:GO2_MAX_VX = "0.42"' in launcher
    assert '$env:GO2_MAX_WZ = "0.55"' in launcher
    assert "[switch]$EnableVideoActiveRecovery" in launcher
    assert 'GO2_WEBRTC_ENABLE_VIDEO_ACTIVE_RECOVERY' in launcher
    assert '$env:GO2_WEBRTC_RECONNECT_INITIAL_SECONDS = "2"' in launcher
    assert '$env:GO2_WEBRTC_RECONNECT_STEP_SECONDS = "2"' in launcher
    assert '$env:GO2_WEBRTC_RECONNECT_MAX_SECONDS = "15"' in launcher
    assert (
        '$env:GO2_WEBRTC_RECONNECT_STABLE_RESET_SECONDS = "30"'
        in launcher
    )
    assert '$env:GO2_WEBRTC_DISCONNECT_GRACE_SECONDS = "3"' in launcher
    assert "[switch]$ManualConfirmStart" in launcher
    assert '$Arguments += "--manual-confirm-start"' in launcher


def test_runtime_tool_filters_only_expected_aioice_bind_noise() -> None:
    runtime_tool = (
        Path(__file__).resolve().parents[1] / "tools" / "go2_wireless_runtime.py"
    ).read_text(encoding="utf-8")

    assert "addFilter(ExpectedAioiceBindNoiseFilter())" in runtime_tool
    assert 'logging.getLogger("aioice.ice").setLevel(logging.WARNING)' not in runtime_tool

    log_filter = ExpectedAioiceBindNoiseFilter()
    expected_noise = logging.LogRecord(
        "aioice.ice",
        logging.INFO,
        __file__,
        1,
        "Connection(1) Could not bind to 192.168.123.222 - [WinError 10049] bad address",
        (),
        None,
    )
    useful_ice_info = logging.LogRecord(
        "aioice.ice",
        logging.INFO,
        __file__,
        1,
        "Connection(1) ICE completed",
        (),
        None,
    )
    unexpected_bind_error = logging.LogRecord(
        "aioice.ice",
        logging.INFO,
        __file__,
        1,
        "Connection(1) Could not bind to 192.168.8.254 - [WinError 10013] denied",
        (),
        None,
    )

    assert log_filter.filter(expected_noise) is False
    assert log_filter.filter(useful_ice_info) is True
    assert log_filter.filter(unexpected_bind_error) is True


def test_runtime_filters_high_frequency_protocol_logs_by_default() -> None:
    log_filter = HighFrequencyUnitreeDataLogFilter()

    def record(message: str) -> logging.LogRecord:
        return logging.LogRecord(
            "root", logging.INFO, __file__, 1, message, (), None
        )

    assert log_filter.filter(
        record(
            'Received message on data channel: {"topic": "rt/sportmodestate"}'
        )
    ) is False
    assert log_filter.filter(
        record(
            'Received message on data channel: {"topic": "rt/lf/sportmodestate"}'
        )
    ) is False
    assert log_filter.filter(
        record(
            'Received message on data channel: {"topic":"rt/sportmodestate"}'
        )
    ) is False
    assert log_filter.filter(
        record(
            'Received message on data channel: {"topic":"rt/lf/sportmodestate"}'
        )
    ) is False
    assert log_filter.filter(
        record('Received message on data channel: {"topic": "rt/uwbstate"}')
    ) is False
    assert log_filter.filter(
        record('Received message on data channel: {"topic":"rt/lf/lowstate"}')
    ) is False
    assert log_filter.filter(
        record('Received message on data channel: {"topic":"rt/lowstate"}')
    ) is False
    assert log_filter.filter(
        record('Received message on data channel: {"topic":"rt/multiplestate"}')
    ) is False
    assert log_filter.filter(record("Heartbeat response received.")) is False
    assert log_filter.filter(
        record('> message sent: {"type": "heartbeat", "topic": ""}')
    ) is False
    assert log_filter.filter(
        record('> message sent: {"type": "rtc_inner_req", "topic": ""}')
    ) is False
    assert log_filter.filter(record("WebRTC connected")) is True

    audio_progress = logging.LogRecord(
        "WebRTCAudioHub",
        logging.INFO,
        __file__,
        1,
        "Splitting file into 123 chunks",
        (),
        None,
    )
    audio_chunk = logging.LogRecord(
        "WebRTCAudioHub",
        logging.INFO,
        __file__,
        1,
        "Sending chunk 1/123",
        (),
        None,
    )
    assert log_filter.filter(audio_progress) is False
    assert log_filter.filter(audio_chunk) is False
    assert log_filter.filter(
        record(
            '> message sent: {"topic":"rt/api/audiohub/request",'
            '"data":{"parameter":"{\\"block_content\\":\\"AAAA\\"}"}}'
        )
    ) is False
    assert log_filter.filter(
        record(
            'Received message on data channel: '
            '{"topic":"rt/api/audiohub/response",'
            '"data":{"audio_list":[{"CUSTOM_NAME":"preset"}]}}'
        )
    ) is False

    warning = logging.LogRecord(
        "root",
        logging.WARNING,
        __file__,
        1,
        'Received message on data channel: {"topic":"rt/lowstate"}',
        (),
        None,
    )
    assert log_filter.filter(warning) is True
    audio_warning = logging.LogRecord(
        "WebRTCAudioHub",
        logging.WARNING,
        __file__,
        1,
        "Sending chunk failed",
        (),
        None,
    )
    assert log_filter.filter(audio_warning) is True


def test_runtime_protocol_log_debug_switches_are_scoped() -> None:
    def record(message: str) -> logging.LogRecord:
        return logging.LogRecord(
            "root", logging.INFO, __file__, 1, message, (), None
        )

    uwb_only = HighFrequencyUnitreeDataLogFilter(uwb_verbose=True)
    assert uwb_only.filter(
        record('Received message on data channel: {"topic":"rt/uwbstate"}')
    ) is True
    assert uwb_only.filter(
        record('Received message on data channel: {"topic":"rt/lowstate"}')
    ) is False

    protocol = HighFrequencyUnitreeDataLogFilter(protocol_verbose=True)
    assert protocol.filter(
        record('Received message on data channel: {"topic":"rt/lowstate"}')
    ) is True
    assert protocol.filter(
        record('> message sent: {"type": "heartbeat", "topic": ""}')
    ) is True
    assert protocol.filter(
        logging.LogRecord(
            "WebRTCAudioHub",
            logging.INFO,
            __file__,
            1,
            "Sending chunk 1/3",
            (),
            None,
        )
    ) is True
    assert protocol.filter(
        record(
            '> message sent: {"topic":"rt/api/audiohub/request",'
            '"data":{"block_content":"AAAA"}}'
        )
    ) is True


def test_pose_and_audio_share_the_existing_connection(tmp_path, monkeypatch) -> None:
    connection = FakeConnection()
    audio_hub = FakeAudioHub()
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        enable_video=False,
        command_timeout_seconds=0.5,
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
        audio_hub_factory=lambda observed: audio_hub,
    )
    audio_file = tmp_path / "demo.wav"
    audio_file.write_bytes(b"RIFF-fake-wave")

    def fake_tts(_text: str, path: str) -> None:
        Path(path).write_bytes(b"RIFF-fake-speech")

    monkeypatch.setattr(runtime, "_render_windows_speech", fake_tts)
    runtime.start()
    try:
        assert runtime.apply_pose(
            roll_rad=-0.1,
            pitch_rad=0.2,
            yaw_rad=0.0,
            body_height_m=-0.08,
        ) == 0
        assert runtime.reset_pose() == 0
        assert runtime.play_audio_file(audio_file) == 0
        assert runtime.speak("演示完成") == 0
        assert connection.connect_count == 1
        assert [request["api_id"] for request in connection.datachannel.pub_sub.requests[:5]] == [
            1002,
            1007,
            1013,
            1007,
            1013,
        ]
        assert connection.datachannel.pub_sub.requests[1]["parameter"] == {
            "x": -0.1,
            "y": 0.2,
            "z": 0.0,
        }
        assert len(audio_hub.uploads) == 2
        assert audio_hub.played == ["uuid-1", "uuid-2"]
    finally:
        runtime.close()


def test_audio_preload_uploads_without_playing(tmp_path) -> None:
    connection = FakeConnection()
    audio_hub = FakeAudioHub()
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        enable_video=False,
        command_timeout_seconds=0.5,
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
        audio_hub_factory=lambda _connection: audio_hub,
    )
    audio_file = tmp_path / "ack.wav"
    audio_file.write_bytes(b"RIFF" + b"\0" * 40)
    runtime.start()
    try:
        assert runtime.preload_audio_file(audio_file) == 0
        assert len(audio_hub.uploads) == 1
        assert audio_hub.played == []
        assert connection.datachannel.pub_sub.requests == []
    finally:
        runtime.close(send_stop=False)


def test_audio_preset_batch_queries_catalogue_once_and_uploads_only_missing(
    tmp_path,
) -> None:
    connection = FakeConnection()
    audio_hub = FakeAudioHub()
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        enable_video=False,
        command_timeout_seconds=0.5,
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
        audio_hub_factory=lambda _connection: audio_hub,
    )
    existing = tmp_path / "existing.wav"
    missing = tmp_path / "missing.wav"
    existing.write_bytes(b"RIFF" + b"a" * 40)
    missing.write_bytes(b"RIFF" + b"b" * 40)
    existing_name = (
        f"go2_existing_{runtime._audiohub_digest(str(existing))}"
    )
    audio_hub.entries.append(
        {"CUSTOM_NAME": existing_name, "UNIQUE_ID": "uuid-existing"}
    )

    runtime.start()
    try:
        results = runtime.preload_audio_files((existing, missing))

        assert results[str(existing.resolve())].ready is True
        assert results[str(existing.resolve())].uploaded is False
        assert results[str(existing.resolve())].attempts == 0
        assert results[str(missing.resolve())].ready is True
        assert results[str(missing.resolve())].uploaded is True
        assert results[str(missing.resolve())].attempts == 1
        assert len(audio_hub.uploads) == 1
        assert Path(audio_hub.uploads[0]).name.startswith("go2_missing_")
        assert audio_hub.audio_list_requests == 2
        assert audio_hub.deleted == []
        assert audio_hub.played == []
    finally:
        runtime.close(send_stop=False)


def test_audio_preset_batch_retries_failed_upload_and_reports_ready(tmp_path) -> None:
    class RetryAudioHub(FakeAudioHub):
        def __init__(self) -> None:
            super().__init__()
            self.upload_attempts = 0

        async def upload_audio_file(self, path: str) -> None:
            self.upload_attempts += 1
            if self.upload_attempts == 1:
                raise RuntimeError("temporary AudioHub failure")
            await super().upload_audio_file(path)

    connection = FakeConnection()
    audio_hub = RetryAudioHub()
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        enable_video=False,
        command_timeout_seconds=0.5,
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
        audio_hub_factory=lambda _connection: audio_hub,
    )
    audio_file = tmp_path / "NO_RESPONSE_ESCALATED.wav"
    audio_file.write_bytes(b"RIFF" + b"x" * 372_520)

    runtime.start()
    try:
        result = runtime.preload_audio_files((audio_file,), retry_attempts=2)[
            str(audio_file.resolve())
        ]

        assert result.ready is True
        assert result.uploaded is True
        assert result.attempts == 2
        assert result.error is None
        assert audio_hub.upload_attempts == 2
        assert audio_hub.audio_list_requests == 3
        assert runtime._audiohub_upload_timeout(str(audio_file)) > 15.0
    finally:
        runtime.close(send_stop=False)


def test_audiohub_raw_base64_stdout_is_only_visible_in_protocol_verbose_mode(
    tmp_path, monkeypatch, capsys
) -> None:
    class PrintingAudioHub(FakeAudioHub):
        async def upload_audio_file(self, path: str) -> None:
            print('{"block_content":"RAW_BASE64"}')
            await super().upload_audio_file(path)

    connection = FakeConnection()
    audio_hub = PrintingAudioHub()
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        enable_video=False,
        command_timeout_seconds=0.5,
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
        audio_hub_factory=lambda _connection: audio_hub,
    )
    quiet_file = tmp_path / "quiet.wav"
    verbose_file = tmp_path / "verbose.wav"
    quiet_file.write_bytes(b"RIFF" + b"q" * 40)
    verbose_file.write_bytes(b"RIFF" + b"v" * 40)

    runtime.start()
    try:
        monkeypatch.delenv("GO2_VERBOSE_PROTOCOL_LOG", raising=False)
        runtime.preload_audio_files((quiet_file,))
        assert "RAW_BASE64" not in capsys.readouterr().out

        monkeypatch.setenv("GO2_VERBOSE_PROTOCOL_LOG", "1")
        runtime.preload_audio_files((verbose_file,))
        assert "RAW_BASE64" in capsys.readouterr().out
    finally:
        runtime.close(send_stop=False)


def test_replacing_preloaded_speech_slot_deletes_previous_robot_audio(tmp_path) -> None:
    connection = FakeConnection()
    audio_hub = FakeAudioHub()
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        enable_video=False,
        command_timeout_seconds=0.5,
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
        audio_hub_factory=lambda _connection: audio_hub,
    )
    audio_file = tmp_path / "start_companion_current.wav"
    runtime.start()
    try:
        audio_file.write_bytes(b"RIFF" + b"a" * 40)
        assert runtime.preload_audio_file(
            audio_file, replace_existing_stem=True
        ) == 0
        first_uuid = audio_hub.entries[0]["UNIQUE_ID"]

        audio_file.write_bytes(b"RIFF" + b"b" * 40)
        assert runtime.preload_audio_file(
            audio_file, replace_existing_stem=True
        ) == 0

        assert audio_hub.deleted == [first_uuid]
        assert len(audio_hub.entries) == 1
        assert len(audio_hub.uploads) == 2
    finally:
        runtime.close(send_stop=False)


def test_audiohub_upload_repairs_streaming_wav_header_and_adds_silent_tail(
    tmp_path,
) -> None:
    connection = FakeConnection()
    audio_hub = FakeAudioHub()
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        enable_video=False,
        command_timeout_seconds=0.5,
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
        audio_hub_factory=lambda _connection: audio_hub,
    )
    audio_file = tmp_path / "streaming.wav"
    pcm = (1000).to_bytes(2, "little", signed=True) * 2400
    audio_file.write_bytes(
        b"RIFF"
        + (0x7FFFFFFF).to_bytes(4, "little")
        + b"WAVEfmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + (24000).to_bytes(4, "little")
        + (48000).to_bytes(4, "little")
        + (2).to_bytes(2, "little")
        + (16).to_bytes(2, "little")
        + b"data"
        + (0x7FFFFFFF).to_bytes(4, "little")
        + pcm
    )
    runtime.start()
    try:
        assert runtime.preload_audio_file(audio_file) == 0
        uploaded = audio_hub.uploaded_bytes[0]
        assert int.from_bytes(uploaded[4:8], "little") == len(uploaded) - 8
        assert int.from_bytes(uploaded[40:44], "little") == len(uploaded) - 44
        assert uploaded[-100:] == b"\0" * 100
    finally:
        runtime.close(send_stop=False)


def test_microphone_capture_uses_existing_connection_and_sends_no_motion(tmp_path) -> None:
    connection = FakeConnection()
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        enable_video=False,
        command_timeout_seconds=0.5,
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
    )
    runtime.start()
    try:
        result = runtime.record_microphone_wav(
            tmp_path / "mic.wav", duration_seconds=0.5
        )

        assert result.sample_rate == 100
        assert result.channels == 2
        assert result.duration_seconds == 0.5
        assert result.frame_count == 1
        assert result.peak == 1000
        assert result.rms == 1000.0
        assert connection.audio.switches == [True, False]
        assert connection.connect_count == 1
        assert connection.datachannel.pub_sub.requests == []
        assert runtime.status()["microphone"]["available"] is True
    finally:
        runtime.close(send_stop=False)


def test_background_audio_preload_lock_does_not_block_microphone_capture(
    tmp_path,
) -> None:
    connection = FakeConnection()
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        enable_video=False,
        command_timeout_seconds=0.5,
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
    )
    runtime.start()
    runtime._audio_preload_lock.acquire()
    try:
        started = time.monotonic()
        result = runtime.record_microphone_wav(
            tmp_path / "mic-during-preload.wav", duration_seconds=0.5
        )
        assert result.byte_count > 0
        assert time.monotonic() - started < 2.0
    finally:
        runtime._audio_preload_lock.release()
        runtime.close(send_stop=False)


def test_stop_motion_marks_unconfirmed_after_peer_connection_closed() -> None:
    connection = FakeConnection()
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        enable_video=False,
        command_timeout_seconds=0.5,
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
    )
    runtime.start()
    try:
        connection.isConnected = False
        assert runtime.stop_motion() == -1
        assert connection.datachannel.pub_sub.requests == []
        motion_rpc = runtime.status()["motionRpc"]
        assert motion_rpc["remoteStopState"] == "STOP_UNCONFIRMED_TRANSPORT_LOST"
        assert motion_rpc["stopRequiredAfterReconnect"] is True
    finally:
        runtime.close(send_stop=False)


def test_microphone_vad_waits_for_speech_then_trailing_silence(tmp_path) -> None:
    class Frame:
        sample_rate = 100
        format = type("Format", (), {"name": "s16"})()
        layout = type("Layout", (), {"channels": ("left", "right")})()

        def __init__(self, amplitude: int) -> None:
            self.amplitude = amplitude

        def to_ndarray(self):
            # 50 ms frames keep this endpoint test fine-grained enough to
            # distinguish the configured 300 ms silence from the old 500 ms
            # minimum clamp.
            return np.full((1, 5), self.amplitude, dtype=np.int16)

    class SequencedAudio(FakeAudio):
        def switchAudioChannel(self, enabled: bool) -> None:
            self.switches.append(enabled)
            if enabled and self.callback is not None:
                async def pump() -> None:
                    for amplitude in (
                        [0] * 20
                        + [3000] * 5
                        + [0] * 20
                    ):
                        await self.callback(Frame(amplitude))
                        await asyncio.sleep(0)

                asyncio.get_running_loop().create_task(pump())

    connection = FakeConnection()
    connection.audio = SequencedAudio()
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        enable_video=False,
        command_timeout_seconds=0.5,
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
    )
    runtime.start()
    try:
        result = runtime.record_microphone_wav(
            tmp_path / "vad.wav",
            duration_seconds=3.0,
            vad_enabled=True,
            vad_trailing_silence_seconds=0.3,
            vad_min_capture_seconds=0.2,
        )

        assert result.vad_enabled is True
        assert result.speech_detected is True
        assert result.endpoint_reason == "vad_trailing_silence"
        assert 0.2 <= result.trailing_silence_seconds <= 0.4
        assert result.duration_seconds < 3.0
        assert connection.datachannel.pub_sub.requests == []
    finally:
        runtime.close(send_stop=False)


def test_runtime_subscribes_and_normalizes_web_rtc_uwb_without_commands() -> None:
    connection = FakeConnection()
    runtime = Go2WirelessRuntime(
        "192.168.8.252",
        enable_video=False,
        connect_timeout_seconds=0.5,
        state_timeout_seconds=0.5,
        connection_factory=lambda _ip, _key: (connection, TOPICS, COMMANDS),
    )
    runtime.start()
    try:
        callback = connection.datachannel.pub_sub.subscriptions["rt/uwbstate"]
        callback(
            {
                "data": {
                    "distance_est": 1.82,
                    "orientation_est": -0.31,
                    "yaw_est": 0.12,
                    "enabled_from_app": 1,
                    "error_state": 0,
                }
            }
        )

        status = runtime.status()
        assert status["uwb"]["received"] is True
        assert status["uwb"]["fresh"] is True
        assert status["uwb"]["sampleCount"] == 1
        assert status["uwb"]["fields"] == {
            "distance_est": 1.82,
            "orientation_est": -0.31,
            "yaw_est": 0.12,
            "enabled_from_app": 1,
            "error_state": 0,
        }
        assert status["uwb"]["sourceKeys"] == [
            "distance_est",
            "enabled_from_app",
            "error_state",
            "orientation_est",
            "yaw_est",
        ]
        assert status["multipleState"]["uwbSwitch"] is True
        assert status["lowState"]["received"] is True
        assert status["commandCounts"] == {}
        assert connection.datachannel.pub_sub.requests == []
        telemetry = runtime.companion_telemetry_status()
        assert telemetry["connected"] is True
        assert telemetry["connectionCount"] == 1
        assert telemetry["uwb"]["sampleCount"] == 1
        assert telemetry["uwb"]["fields"]["distance_est"] == pytest.approx(1.82)
        assert telemetry["uwb"]["receivedMonotonic"] is not None
        assert telemetry["uwb"]["receivedTimestampMs"] is not None
    finally:
        runtime.close(send_stop=False)
