from __future__ import annotations

import asyncio
import math
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.navigation.mock_point_cloud import (
    MockPointCloudGenerator,
    MockPointCloudStream,
    PointCloudStreamConfig,
)
from app.navigation.models import NavigationState
from app.navigation.point_cloud_models import (
    PointCloudErrorCode,
    PointCloudFrame,
    PointCloudPose,
    PointCloudScenario,
)
from app.navigation.store import NavigationStore


def assert_mock_message(message: dict) -> dict:
    assert message["provider"] == "mock"
    assert message["real_motion_enabled"] is False
    return message


def create_active_map(client: TestClient) -> str:
    started = client.post(
        "/api/navigation/mapping/start", json={"session_name": "point_cloud_map"}
    )
    assert started.status_code == 200
    session_id = started.json()["data"]["session_id"]
    assert client.post(
        "/api/navigation/mapping/stop", json={"session_id": session_id}
    ).status_code == 200
    saved = client.post(
        "/api/navigation/maps/save",
        json={"session_id": session_id, "name": "point_cloud_map", "confirmed": True},
    )
    assert saved.status_code == 200
    return saved.json()["data"]["map_id"]


def wait_for_stream_state(client: TestClient, subscribers: int, task_active: bool) -> None:
    stream = client.app.state.mock_point_cloud_stream
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if stream.subscriber_count == subscribers and stream.producer_task_active == task_active:
            return
        time.sleep(0.01)
    assert stream.subscriber_count == subscribers
    assert stream.producer_task_active == task_active


def receive_until_type(websocket, message_type: str, limit: int = 10) -> dict:
    for _ in range(limit):
        message = assert_mock_message(websocket.receive_json())
        if message["type"] == message_type:
            return message
    raise AssertionError(f"Did not receive {message_type}")


def test_point_cloud_websocket_sends_info_then_valid_frame(client: TestClient) -> None:
    with client.websocket_connect("/ws/navigation/point-cloud") as websocket:
        info = assert_mock_message(websocket.receive_json())
        frame = assert_mock_message(websocket.receive_json())

        assert info["type"] == "point_cloud_stream_info"
        assert info["encoding"] == "json_xyz_intensity_v1"
        assert info["target_fps"] <= 5
        assert info["max_points"] <= 5000
        assert info["queue_size"] == 2
        assert frame["type"] == "point_cloud_frame"
        assert frame["frame_id"] == "mock_lidar"
        assert frame["coordinate_frame"] == "map"
        assert frame["point_count"] == len(frame["points"])
        assert 0 < frame["point_count"] <= info["max_points"]
        assert all(
            len(point) == 4 and all(math.isfinite(value) for value in point)
            for point in frame["points"]
        )


def test_generator_is_deterministic_for_seed_scenario_and_frame() -> None:
    config = PointCloudStreamConfig(default_points=100, max_points=100, seed=1234)
    generator = MockPointCloudGenerator(config)
    state = NavigationState()

    first = generator.generate(PointCloudScenario.CLASSROOM_DEFAULT, 7, state)
    second = generator.generate(PointCloudScenario.CLASSROOM_DEFAULT, 7, state)
    different_frame = generator.generate(PointCloudScenario.CLASSROOM_DEFAULT, 8, state)
    different_scenario = generator.generate(PointCloudScenario.EMPTY, 7, state)

    assert first == second
    assert first != different_frame
    assert first != different_scenario
    assert len(first) == 100


def test_frame_model_rejects_count_mismatch_and_non_finite_values() -> None:
    common = {
        "sequence": 1,
        "scenario": "empty",
        "robot_pose": {"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0},
        "navigation_state": "idle",
        "control_owner": "NONE",
    }
    with pytest.raises(ValidationError):
        PointCloudFrame(point_count=2, points=[(0.0, 0.0, 0.0, 1.0)], **common)
    with pytest.raises(ValidationError):
        PointCloudFrame(
            point_count=1,
            points=[(float("nan"), 0.0, 0.0, 1.0)],
            **common,
        )
    with pytest.raises(ValidationError):
        PointCloudPose(x=0.0, y=float("inf"), z=0.0, yaw=0.0)
    with pytest.raises(ValidationError):
        PointCloudFrame(
            point_count=1,
            points=[(0.0, 0.0, 0.0, 1.0)],
            real_motion_enabled=True,
            **common,
        )


def test_point_cloud_scenario_endpoint_switches_bounded_scene(client: TestClient) -> None:
    response = client.post(
        "/api/navigation/mock/point-cloud/scenario",
        json={"scenario": "classroom_sparse"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["provider"] == "mock"
    assert data["real_motion_enabled"] is False
    assert data["scenario"] == "classroom_sparse"

    with client.websocket_connect("/ws/navigation/point-cloud") as websocket:
        info = websocket.receive_json()
        frame = websocket.receive_json()
        assert info["scenario"] == "classroom_sparse"
        assert frame["scenario"] == "classroom_sparse"
        assert frame["point_count"] == 1200

        assert client.post(
            "/api/navigation/mock/point-cloud/scenario",
            json={"scenario": "classroom_obstacle"},
        ).status_code == 200
        changed = None
        for _ in range(10):
            candidate = websocket.receive_json()
            if candidate.get("type") == "point_cloud_frame" and candidate.get("scenario") == "classroom_obstacle":
                changed = candidate
                break
        assert changed is not None
        assert changed["point_count"] == 3000


def test_invalid_scenario_and_arbitrary_points_are_rejected(client: TestClient) -> None:
    invalid = client.post(
        "/api/navigation/mock/point-cloud/scenario",
        json={"scenario": "real_lidar"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "POINT_CLOUD_SCENARIO_INVALID"
    assert invalid.json()["data"]["real_motion_enabled"] is False

    injected = client.post(
        "/api/navigation/mock/point-cloud/scenario",
        json={"scenario": "empty", "points": [[1, 2, 3, 4]]},
    )
    assert injected.status_code == 422
    assert injected.json()["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize(
    ("scenario", "status"),
    [("stream_stale", "stale"), ("stream_error", "error")],
)
def test_unavailable_scenarios_are_explicit_structured_states(
    client: TestClient, scenario: str, status: str
) -> None:
    assert client.post(
        "/api/navigation/mock/point-cloud/scenario", json={"scenario": scenario}
    ).status_code == 200
    with client.websocket_connect("/ws/navigation/point-cloud") as websocket:
        info = assert_mock_message(websocket.receive_json())
        error = assert_mock_message(websocket.receive_json())
        assert info["stream_status"] == status
        assert info["scenario"] == scenario
        assert error["type"] == "error"
        assert error["code"] == "POINT_CLOUD_STREAM_UNAVAILABLE"


def test_pose_metadata_reads_navigation_store_without_mutating_it(client: TestClient) -> None:
    map_id = create_active_map(client)
    dispatched = client.post(
        "/api/navigation/emergency/dispatch",
        json={
            "incident_id": "incident_point_cloud",
            "external_task_id": "health_point_cloud",
            "map_id": map_id,
            "target_point_id": "observation_1",
            "target_pose": {"x": 3.2, "y": 1.4, "yaw": 0.75},
        },
    )
    assert dispatched.status_code == 200
    state_before = client.get("/api/navigation/state").json()["data"]

    with client.websocket_connect("/ws/navigation/point-cloud") as websocket:
        websocket.receive_json()
        frame = websocket.receive_json()
        assert frame["robot_pose"] == {
            "x": state_before["current_pose"]["x"],
            "y": state_before["current_pose"]["y"],
            "z": 0.0,
            "yaw": state_before["current_pose"]["yaw"],
        }
        assert frame["target_pose"] == {"x": 3.2, "y": 1.4, "z": 0.0, "yaw": 0.75}
        assert frame["navigation_state"] == state_before["navigation_state"]
        assert frame["control_owner"] == state_before["control_owner"]

    state_after = client.get("/api/navigation/state").json()["data"]
    assert state_after == state_before


def test_point_cloud_sequence_is_independent_from_navigation_events(client: TestClient) -> None:
    navigation_sequence = client.app.state.mock_navigation_event_bus.current_sequence
    with client.websocket_connect("/ws/navigation/point-cloud") as websocket:
        websocket.receive_json()
        first = websocket.receive_json()
        second = receive_until_type(websocket, "point_cloud_frame")
        assert second["sequence"] > first["sequence"]
    assert client.app.state.mock_navigation_event_bus.current_sequence == navigation_sequence


def test_two_clients_receive_frames_and_disconnect_independently(client: TestClient) -> None:
    with client.websocket_connect("/ws/navigation/point-cloud") as first:
        first.receive_json()
        first.receive_json()
        with client.websocket_connect("/ws/navigation/point-cloud") as second:
            second.receive_json()
            second.receive_json()
            assert client.app.state.mock_point_cloud_stream.subscriber_count == 2
            first_frame = receive_until_type(first, "point_cloud_frame")
            second_frame = receive_until_type(second, "point_cloud_frame")
            assert first_frame["point_count"] > 0
            assert second_frame["point_count"] > 0
        wait_for_stream_state(client, subscribers=1, task_active=True)
        assert receive_until_type(first, "point_cloud_frame")["point_count"] > 0
    wait_for_stream_state(client, subscribers=0, task_active=False)


def test_state_and_point_cloud_websockets_can_run_in_parallel(client: TestClient) -> None:
    with client.websocket_connect("/ws/navigation/state") as state_ws:
        assert state_ws.receive_json()["type"] == "navigation_snapshot"
        with client.websocket_connect("/ws/navigation/point-cloud") as cloud_ws:
            assert cloud_ws.receive_json()["type"] == "point_cloud_stream_info"
            assert cloud_ws.receive_json()["type"] == "point_cloud_frame"
            started = client.post(
                "/api/navigation/mapping/start", json={"session_name": "parallel_ws"}
            )
            assert started.status_code == 200
            assert receive_until_type(state_ws, "mapping_state_changed")["data"]["mapping_state"] == "mapping"
            assert receive_until_type(cloud_ws, "point_cloud_frame")["point_count"] > 0


def test_reconnect_and_sync_send_info_without_history_replay(client: TestClient) -> None:
    with client.websocket_connect("/ws/navigation/point-cloud") as first:
        assert first.receive_json()["type"] == "point_cloud_stream_info"
        first_frame = first.receive_json()
    wait_for_stream_state(client, subscribers=0, task_active=False)

    with client.websocket_connect("/ws/navigation/point-cloud") as second:
        assert second.receive_json()["type"] == "point_cloud_stream_info"
        reconnect_frame = second.receive_json()
        assert reconnect_frame["sequence"] >= first_frame["sequence"]
        second.send_json({"type": "sync"})
        info = receive_until_type(second, "point_cloud_stream_info")
        latest = second.receive_json()
        assert info["scenario"] == "classroom_default"
        assert latest["type"] == "point_cloud_frame"


def test_ping_does_not_increment_point_cloud_sequence(client: TestClient) -> None:
    with client.websocket_connect("/ws/navigation/point-cloud") as websocket:
        websocket.receive_json()
        websocket.receive_json()
        stream = client.app.state.mock_point_cloud_stream
        before = stream.current_sequence
        websocket.send_json({"type": "ping"})
        pong = websocket.receive_json()
        assert pong["type"] == "pong"
        assert "sequence" not in pong
        assert stream.current_sequence == before


def test_invalid_websocket_message_returns_structured_error(client: TestClient) -> None:
    with client.websocket_connect("/ws/navigation/point-cloud") as websocket:
        websocket.receive_json()
        websocket.receive_json()
        websocket.send_json({"type": "write_points", "points": []})
        error = assert_mock_message(websocket.receive_json())
        assert error["type"] == "error"
        assert error["code"] == "INVALID_WEBSOCKET_MESSAGE"


def test_slow_subscriber_keeps_only_two_latest_frames() -> None:
    async def exercise() -> None:
        store = NavigationStore()
        stream = MockPointCloudStream(
            store.snapshot,
            config=PointCloudStreamConfig(
                target_fps=5,
                default_points=20,
                max_points=20,
                subscriber_queue_size=2,
            ),
        )
        subscription = await stream.subscribe()
        await asyncio.sleep(0.72)
        assert subscription.queue.qsize() == 2
        assert subscription.dropped_frames >= 2
        messages = [await subscription.queue.get(), await subscription.queue.get()]
        sequences = [message.sequence for message in messages if isinstance(message, PointCloudFrame)]
        assert sequences == sorted(sequences)
        assert sequences[-1] == stream.current_sequence
        await stream.unsubscribe(subscription)
        assert stream.subscriber_count == 0
        assert stream.producer_task_active is False
        await stream.close()

    asyncio.run(exercise())


def test_slow_subscriber_does_not_block_an_independent_subscriber() -> None:
    async def exercise() -> None:
        stream = MockPointCloudStream(
            NavigationStore().snapshot,
            config=PointCloudStreamConfig(
                target_fps=5,
                default_points=20,
                max_points=20,
                subscriber_queue_size=2,
            ),
        )
        slow = await stream.subscribe()
        active = await stream.subscribe()
        seen_sequences: list[int] = []
        for _ in range(4):
            message = await asyncio.wait_for(active.queue.get(), timeout=1)
            assert isinstance(message, PointCloudFrame)
            seen_sequences.append(message.sequence)
        assert seen_sequences == sorted(seen_sequences)
        assert slow.queue.qsize() == 2
        assert slow.dropped_frames >= 2
        await stream.unsubscribe(slow)
        await stream.unsubscribe(active)
        assert stream.producer_task_active is False
        await stream.close()

    asyncio.run(exercise())


def test_store_failure_returns_navigation_store_error_and_cleans_task() -> None:
    async def exercise() -> None:
        def unavailable() -> NavigationState:
            raise RuntimeError("store unavailable")

        stream = MockPointCloudStream(
            unavailable,
            config=PointCloudStreamConfig(default_points=20, max_points=20),
        )
        subscription = await stream.subscribe()
        message = await asyncio.wait_for(subscription.queue.get(), timeout=1)
        assert message is not None
        assert message.type == "error"
        assert message.code == PointCloudErrorCode.NAVIGATION_STORE_UNAVAILABLE
        await stream.unsubscribe(subscription)
        assert stream.producer_task_active is False
        await stream.close()

    asyncio.run(exercise())


def test_heartbeat_timeout_releases_subscriber(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.navigation_point_cloud_ws.HEARTBEAT_IDLE_SECONDS", 0.02
    )
    monkeypatch.setattr(
        "app.api.navigation_point_cloud_ws.HEARTBEAT_GRACE_SECONDS", 0.02
    )
    with client.websocket_connect("/ws/navigation/point-cloud") as websocket:
        websocket.receive_json()
        websocket.receive_json()
        ping = websocket.receive_json()
        assert ping["type"] == "ping"
        error = receive_until_type(websocket, "error")
        assert error["code"] == "HEARTBEAT_TIMEOUT"
    wait_for_stream_state(client, subscribers=0, task_active=False)


def test_point_cloud_stream_never_calls_real_devices_or_motion(client: TestClient) -> None:
    robot_move = Mock(side_effect=AssertionError("robot_service.move must not be called"))
    adapter_move = Mock(side_effect=AssertionError("adapter.move must not be called"))
    client.app.state.robot_service.move = robot_move
    client.app.state.adapter.move = adapter_move
    with client.websocket_connect("/ws/navigation/point-cloud") as websocket:
        websocket.receive_json()
        websocket.receive_json()
    assert robot_move.call_count == 0
    assert adapter_move.call_count == 0

    root = Path(__file__).resolve().parents[1]
    paths = [
        root / "app" / "navigation" / "point_cloud_models.py",
        root / "app" / "navigation" / "mock_point_cloud.py",
        root / "app" / "api" / "navigation_point_cloud_ws.py",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths).lower()
    forbidden = [
        "unitree",
        "cyclonedds",
        "rt/utlidar",
        "rt/lidar",
        "rclpy",
        "sensor_msgs",
        "pointcloud2",
        "192.168.",
        "8093",
        "requests",
        "httpx",
        "subprocess",
        "open3d",
        "app.adapters",
        "app.gateway",
        "sqlite",
        "write_text",
        "write_bytes",
        "open(",
    ]
    assert [token for token in forbidden if token in source] == []
