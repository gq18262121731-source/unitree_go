from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.navigation.event_bus import NavigationEventBus
from app.navigation.events import NavigationEventType


def assert_mock_message(message: dict) -> dict:
    assert message["provider"] == "mock"
    assert message["real_motion_enabled"] is False
    assert "timestamp" in message
    return message


def receive_events(websocket, count: int) -> list[dict]:
    return [assert_mock_message(websocket.receive_json()) for _ in range(count)]


def create_active_map(client: TestClient) -> str:
    started = client.post(
        "/api/navigation/mapping/start", json={"session_name": "websocket_demo"}
    )
    assert started.status_code == 200
    session_id = started.json()["data"]["session_id"]
    assert client.post(
        "/api/navigation/mapping/stop", json={"session_id": session_id}
    ).status_code == 200
    saved = client.post(
        "/api/navigation/maps/save",
        json={"session_id": session_id, "name": "websocket_map", "confirmed": True},
    )
    assert saved.status_code == 200
    return saved.json()["data"]["map_id"]


def patrol_request(map_id: str) -> dict:
    return {
        "external_task_id": "health_ws_task",
        "route_id": "route_ws",
        "map_id": map_id,
        "point_ids": ["patrol_1", "patrol_2"],
        "return_home_point_id": "robot_home",
    }


def wait_for_subscriber_count(client: TestClient, expected: int) -> None:
    deadline = time.monotonic() + 1.0
    event_bus = client.app.state.mock_navigation_event_bus
    while time.monotonic() < deadline:
        if event_bus.subscriber_count == expected:
            return
        time.sleep(0.01)
    assert event_bus.subscriber_count == expected


def test_websocket_first_message_is_rest_consistent_snapshot(client: TestClient) -> None:
    with client.websocket_connect("/ws/navigation/state") as websocket:
        snapshot = assert_mock_message(websocket.receive_json())
        rest_state = client.get("/api/navigation/state").json()["data"]

        assert snapshot["type"] == "navigation_snapshot"
        assert snapshot["data"] == rest_state
        assert snapshot["sequence"] >= 1


def test_websocket_ping_pong_and_explicit_sync(client: TestClient) -> None:
    with client.websocket_connect("/ws/navigation/state") as websocket:
        first = assert_mock_message(websocket.receive_json())
        sequence_after_snapshot = client.app.state.mock_navigation_event_bus.current_sequence
        websocket.send_json({"type": "ping"})
        pong = assert_mock_message(websocket.receive_json())
        assert pong["type"] == "pong"
        assert "sequence" not in pong
        assert client.app.state.mock_navigation_event_bus.current_sequence == sequence_after_snapshot

        websocket.send_json({"type": "sync"})
        snapshot = assert_mock_message(websocket.receive_json())
        assert snapshot["type"] == "navigation_snapshot"
        assert snapshot["sequence"] > first["sequence"]


def test_changed_mock_scenario_publishes_named_protocol_event(client: TestClient) -> None:
    with client.websocket_connect("/ws/navigation/state") as websocket:
        websocket.receive_json()
        response = client.post(
            "/api/navigation/mock/scenario", json={"scenario": "localization_invalid"}
        )
        assert response.status_code == 200
        events = receive_events(websocket, 2)
        assert [event["type"] for event in events] == [
            "mock_scenario_changed",
            "navigation_state_changed",
        ]
        assert events[0]["data"]["mock_scenario"] == "localization_invalid"


def test_mapping_rest_operations_publish_deterministic_events(client: TestClient) -> None:
    with client.websocket_connect("/ws/navigation/state") as websocket:
        snapshot = websocket.receive_json()
        started = client.post(
            "/api/navigation/mapping/start", json={"session_name": "mapping_events"}
        )
        assert started.status_code == 200
        start_events = receive_events(websocket, 2)
        assert [event["type"] for event in start_events] == [
            "safety_interlock_checked",
            "mapping_state_changed",
        ]

        session_id = started.json()["data"]["session_id"]
        stopped = client.post(
            "/api/navigation/mapping/stop", json={"session_id": session_id}
        )
        assert stopped.status_code == 200
        stop_events = receive_events(websocket, 2)
        assert [event["type"] for event in stop_events] == [
            "mapping_state_changed",
            "map_preview_ready",
        ]

        saved = client.post(
            "/api/navigation/maps/save",
            json={"session_id": session_id, "name": "event_map", "confirmed": True},
        )
        assert saved.status_code == 200
        save_events = receive_events(websocket, 2)
        assert [event["type"] for event in save_events] == [
            "mapping_state_changed",
            "map_saved",
        ]

        all_events = [snapshot, *start_events, *stop_events, *save_events]
        sequences = [event["sequence"] for event in all_events]
        assert sequences == sorted(sequences)
        assert len(sequences) == len(set(sequences))


def test_patrol_manual_control_resume_and_cancel_event_order(client: TestClient) -> None:
    map_id = create_active_map(client)
    with client.websocket_connect("/ws/navigation/state") as websocket:
        websocket.receive_json()
        started = client.post("/api/navigation/patrol/start", json=patrol_request(map_id))
        assert started.status_code == 200
        task_id = started.json()["data"]["task_id"]
        start_events = receive_events(websocket, 4)
        assert [event["type"] for event in start_events] == [
            "task_created",
            "safety_interlock_checked",
            "task_started",
            "navigation_state_changed",
        ]

        assert client.post("/api/navigation/control/manual-takeover", json={}).status_code == 200
        takeover_events = receive_events(websocket, 3)
        assert [event["type"] for event in takeover_events] == [
            "manual_control_acquired",
            "task_paused",
            "navigation_state_changed",
        ]

        assert client.post("/api/navigation/control/release", json={}).status_code == 200
        release_events = receive_events(websocket, 2)
        assert [event["type"] for event in release_events] == [
            "manual_control_released",
            "navigation_state_changed",
        ]
        assert all(event["type"] != "task_resumed" for event in release_events)

        assert client.post(f"/api/navigation/tasks/{task_id}/resume", json={}).status_code == 200
        resume_events = receive_events(websocket, 3)
        assert [event["type"] for event in resume_events] == [
            "safety_interlock_checked",
            "task_resumed",
            "navigation_state_changed",
        ]

        assert client.post(f"/api/navigation/tasks/{task_id}/stop", json={}).status_code == 200
        stop_events = receive_events(websocket, 2)
        assert [event["type"] for event in stop_events] == [
            "task_cancelled",
            "navigation_state_changed",
        ]


def test_interlock_failure_publishes_safety_and_blocked_task(client: TestClient) -> None:
    map_id = create_active_map(client)
    assert client.post(
        "/api/navigation/mock/scenario", json={"scenario": "localization_invalid"}
    ).status_code == 200
    with client.websocket_connect("/ws/navigation/state") as websocket:
        websocket.receive_json()
        blocked = client.post("/api/navigation/patrol/start", json=patrol_request(map_id))
        assert blocked.status_code == 409
        events = receive_events(websocket, 4)
        assert [event["type"] for event in events] == [
            "task_created",
            "safety_interlock_checked",
            "task_blocked",
            "navigation_state_changed",
        ]
        assert events[1]["data"]["blocked_by"] == ["LOCALIZATION_INVALID"]
        assert events[2]["data"]["execution_state"] == "blocked"
        assert events[2]["data"]["error_code"] == "LOCALIZATION_INVALID"


@pytest.mark.parametrize(
    ("scenario", "expected_types"),
    [
        (
            "navigation_success",
            [
                "task_created",
                "safety_interlock_checked",
                "task_started",
                "task_arrived",
                "return_home_started",
                "return_home_completed",
                "task_completed",
                "navigation_state_changed",
            ],
        ),
        (
            "navigation_failure",
            [
                "task_created",
                "safety_interlock_checked",
                "task_started",
                "task_failed",
                "navigation_state_changed",
            ],
        ),
    ],
)
def test_navigation_outcome_event_sequences(
    client: TestClient, scenario: str, expected_types: list[str]
) -> None:
    map_id = create_active_map(client)
    assert client.post(
        "/api/navigation/mock/scenario", json={"scenario": scenario}
    ).status_code == 200
    with client.websocket_connect("/ws/navigation/state") as websocket:
        websocket.receive_json()
        assert client.post(
            "/api/navigation/patrol/start", json=patrol_request(map_id)
        ).status_code == 200
        events = receive_events(websocket, len(expected_types))
        assert [event["type"] for event in events] == expected_types


@pytest.mark.parametrize(
    ("scenario", "expected_types"),
    [
        (
            "return_home_success",
            [
                "task_created",
                "safety_interlock_checked",
                "return_home_started",
                "return_home_completed",
                "task_completed",
                "navigation_state_changed",
            ],
        ),
        (
            "return_home_failure",
            [
                "task_created",
                "safety_interlock_checked",
                "return_home_started",
                "return_home_failed",
                "task_failed",
                "navigation_state_changed",
            ],
        ),
    ],
)
def test_return_home_event_outcomes(
    client: TestClient, scenario: str, expected_types: list[str]
) -> None:
    create_active_map(client)
    assert client.post(
        "/api/navigation/mock/scenario", json={"scenario": scenario}
    ).status_code == 200
    with client.websocket_connect("/ws/navigation/state") as websocket:
        websocket.receive_json()
        response = client.post(
            "/api/navigation/return-home",
            json={
                "external_task_id": f"health_{scenario}",
                "home_point_id": "robot_home",
                "reason": "websocket_test",
            },
        )
        assert response.status_code == 200
        events = receive_events(websocket, len(expected_types))
        assert [event["type"] for event in events] == expected_types


def test_invalid_rest_operation_does_not_publish_success_event(client: TestClient) -> None:
    with client.websocket_connect("/ws/navigation/state") as websocket:
        websocket.receive_json()
        event_bus = client.app.state.mock_navigation_event_bus
        sequence_before = event_bus.current_sequence
        response = client.post("/api/navigation/tasks/missing/pause", json={})
        assert response.status_code == 404
        assert event_bus.current_sequence == sequence_before


def test_repeating_unchanged_scenario_does_not_publish_duplicate_event(
    client: TestClient,
) -> None:
    with client.websocket_connect("/ws/navigation/state") as websocket:
        websocket.receive_json()
        event_bus = client.app.state.mock_navigation_event_bus
        sequence_before = event_bus.current_sequence
        response = client.post(
            "/api/navigation/mock/scenario", json={"scenario": "robot_ready"}
        )
        assert response.status_code == 200
        assert event_bus.current_sequence == sequence_before


def test_event_type_names_are_frozen_protocol_values() -> None:
    assert {event_type.value for event_type in NavigationEventType} == {
        "navigation_snapshot",
        "navigation_state_changed",
        "mapping_state_changed",
        "task_created",
        "task_blocked",
        "task_started",
        "task_paused",
        "task_resumed",
        "task_cancelled",
        "task_arrived",
        "task_completed",
        "task_failed",
        "manual_control_acquired",
        "manual_control_released",
        "safety_interlock_checked",
        "map_preview_ready",
        "map_saved",
        "return_home_started",
        "return_home_completed",
        "return_home_failed",
        "mock_scenario_changed",
    }


def test_two_clients_receive_events_and_disconnect_independently(client: TestClient) -> None:
    with client.websocket_connect("/ws/navigation/state") as first:
        first.receive_json()
        with client.websocket_connect("/ws/navigation/state") as second:
            second.receive_json()
            assert client.app.state.mock_navigation_event_bus.subscriber_count == 2
            started = client.post(
                "/api/navigation/mapping/start", json={"session_name": "two_clients"}
            )
            assert started.status_code == 200
            assert [event["type"] for event in receive_events(first, 2)] == [
                "safety_interlock_checked",
                "mapping_state_changed",
            ]
            assert [event["type"] for event in receive_events(second, 2)] == [
                "safety_interlock_checked",
                "mapping_state_changed",
            ]
        wait_for_subscriber_count(client, 1)
        session_id = started.json()["data"]["session_id"]
        assert client.post(
            "/api/navigation/mapping/stop", json={"session_id": session_id}
        ).status_code == 200
        assert [event["type"] for event in receive_events(first, 2)] == [
            "mapping_state_changed",
            "map_preview_ready",
        ]
    wait_for_subscriber_count(client, 0)


def test_reconnect_starts_with_new_snapshot_without_history_replay(client: TestClient) -> None:
    with client.websocket_connect("/ws/navigation/state") as first:
        first_snapshot = first.receive_json()
    wait_for_subscriber_count(client, 0)
    assert client.post(
        "/api/navigation/mapping/start", json={"session_name": "disconnected_change"}
    ).status_code == 200
    with client.websocket_connect("/ws/navigation/state") as second:
        second_snapshot = assert_mock_message(second.receive_json())
        assert second_snapshot["type"] == "navigation_snapshot"
        assert second_snapshot["sequence"] > first_snapshot["sequence"]
        assert second_snapshot["data"]["mapping_state"] == "mapping"


def test_invalid_websocket_message_returns_machine_error(client: TestClient) -> None:
    with client.websocket_connect("/ws/navigation/state") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "unsupported", "taskId": "mixed_case"})
        error = assert_mock_message(websocket.receive_json())
        assert error["type"] == "error"
        assert error["code"] == "INVALID_WEBSOCKET_MESSAGE"
        websocket.send_json({"type": "ping"})
        assert websocket.receive_json()["type"] == "pong"


def test_heartbeat_timeout_closes_and_releases_subscription(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("app.api.navigation_ws.HEARTBEAT_IDLE_SECONDS", 0.02)
    monkeypatch.setattr("app.api.navigation_ws.HEARTBEAT_GRACE_SECONDS", 0.02)
    with client.websocket_connect("/ws/navigation/state") as websocket:
        websocket.receive_json()
        ping = assert_mock_message(websocket.receive_json())
        assert ping["type"] == "ping"
        error = assert_mock_message(websocket.receive_json())
        assert error["type"] == "error"
        assert error["code"] == "HEARTBEAT_TIMEOUT"
    wait_for_subscriber_count(client, 0)


def test_slow_subscriber_queue_is_bounded_and_keeps_latest_events() -> None:
    async def exercise() -> None:
        event_bus = NavigationEventBus(subscriber_queue_size=2)
        subscription = event_bus.subscribe()
        for index in range(5):
            event_bus.publish(
                NavigationEventType.NAVIGATION_STATE_CHANGED,
                {"progress": index / 4},
            )
        await asyncio.sleep(0)
        assert subscription.queue.qsize() == 2
        assert subscription.dropped_events == 3
        events = [await subscription.queue.get(), await subscription.queue.get()]
        assert [event.sequence for event in events if event is not None] == [4, 5]
        event_bus.unsubscribe(subscription)
        assert event_bus.subscriber_count == 0
        event_bus.close()

    asyncio.run(exercise())


def test_event_bus_failure_does_not_break_rest_result(client: TestClient) -> None:
    client.app.state.mock_navigation_event_bus.close()
    response = client.post(
        "/api/navigation/mapping/start", json={"session_name": "closed_event_bus"}
    )
    assert response.status_code == 200
    assert response.json()["data"]["mapping_state"] == "mapping"


def test_websocket_flow_never_calls_real_motion_or_device_paths(client: TestClient) -> None:
    robot_move = Mock(side_effect=AssertionError("robot_service.move must not be called"))
    adapter_move = Mock(side_effect=AssertionError("adapter.move must not be called"))
    client.app.state.robot_service.move = robot_move
    client.app.state.adapter.move = adapter_move
    with client.websocket_connect("/ws/navigation/state") as websocket:
        websocket.receive_json()
        assert client.post(
            "/api/navigation/mapping/start", json={"session_name": "isolated"}
        ).status_code == 200
        receive_events(websocket, 2)
    assert robot_move.call_count == 0
    assert adapter_move.call_count == 0

    root = Path(__file__).resolve().parents[1]
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            *(root / "app" / "navigation").glob("*.py"),
            root / "app" / "api" / "navigation_ws.py",
        ]
    ).lower()
    forbidden = [
        "app.adapters",
        "app.gateway",
        "robotservice",
        "go2gateway",
        "rclpy",
        "cmd_vel",
        "192.168.",
        "8093",
    ]
    assert [token for token in forbidden if token in source] == []
