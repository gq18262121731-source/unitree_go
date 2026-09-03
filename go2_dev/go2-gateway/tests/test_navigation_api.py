from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.navigation.store import NavigationStore


def assert_mock_payload(response, status_code: int = 200) -> dict:
    assert response.status_code == status_code, response.text
    payload = response.json()
    assert payload["data"]["provider"] == "mock"
    assert payload["data"]["real_motion_enabled"] is False
    return payload


def create_active_map(client: TestClient, name: str = "demo_map") -> str:
    started = assert_mock_payload(
        client.post("/api/navigation/mapping/start", json={"session_name": "demo"})
    )
    session_id = started["data"]["session_id"]
    stopped = assert_mock_payload(
        client.post("/api/navigation/mapping/stop", json={"session_id": session_id})
    )
    assert stopped["data"]["mapping_state"] == "preview_ready"
    saved = assert_mock_payload(
        client.post(
            "/api/navigation/maps/save",
            json={"session_id": session_id, "name": name, "confirmed": True},
        )
    )
    return saved["data"]["map_id"]


def patrol_request(map_id: str, suffix: str = "1") -> dict:
    return {
        "external_task_id": f"health_task_{suffix}",
        "route_id": f"route_{suffix}",
        "map_id": map_id,
        "point_ids": ["patrol_1", "patrol_2"],
        "return_home_point_id": "robot_home",
    }


def test_capability_and_state_snapshots_are_mock_only(client: TestClient) -> None:
    capabilities = assert_mock_payload(client.get("/api/navigation/capabilities"))
    assert capabilities["code"] == "OK"
    assert capabilities["data"]["mapping"] == "mock"
    assert capabilities["data"]["audio_input"] == "mock"
    assert capabilities["data"]["ros2"] == "unavailable"

    state = assert_mock_payload(client.get("/api/navigation/state"))
    assert state["data"]["mapping_state"] == "idle"
    assert state["data"]["control_owner"] == "NONE"
    assert state["data"]["active_task"] is None


def test_mapping_requires_preview_and_exposes_one_active_map(client: TestClient) -> None:
    premature = assert_mock_payload(
        client.post(
            "/api/navigation/maps/save",
            json={"session_id": "missing", "name": "bad", "confirmed": True},
        ),
        409,
    )
    assert premature["code"] == "MAP_PREVIEW_NOT_READY"

    map_id = create_active_map(client)
    active = assert_mock_payload(client.get("/api/navigation/maps/active"))
    assert active["data"]["active_map"]["map_id"] == map_id
    assert active["data"]["active_map"]["preview"]["source"] == "mock"


def test_mapping_and_navigation_cannot_overlap(client: TestClient) -> None:
    started = assert_mock_payload(
        client.post("/api/navigation/mapping/start", json={"session_name": "active_mapping"})
    )
    duplicate = assert_mock_payload(
        client.post("/api/navigation/mapping/start", json={"session_name": "duplicate"}), 409
    )
    assert duplicate["code"] == "MAPPING_ALREADY_ACTIVE"
    navigation = assert_mock_payload(
        client.post("/api/navigation/patrol/start", json=patrol_request("map_mock_missing")), 409
    )
    assert navigation["code"] == "NAVIGATION_NOT_READY"
    assert_mock_payload(
        client.post(
            "/api/navigation/mapping/stop",
            json={"session_id": started["data"]["session_id"]},
        )
    )


def test_map_replacement_requires_identifier_and_confirmation(client: TestClient) -> None:
    first_map_id = create_active_map(client, "first")
    started = assert_mock_payload(
        client.post("/api/navigation/mapping/start", json={"session_name": "replacement"})
    )
    session_id = started["data"]["session_id"]
    assert_mock_payload(client.post("/api/navigation/mapping/stop", json={"session_id": session_id}))

    rejected = assert_mock_payload(
        client.post(
            "/api/navigation/maps/save",
            json={"session_id": session_id, "name": "second", "confirmed": False},
        ),
        409,
    )
    assert rejected["code"] == "MAP_REPLACEMENT_CONFIRMATION_REQUIRED"

    omitted_confirmation = assert_mock_payload(
        client.post(
            "/api/navigation/maps/save",
            json={
                "session_id": session_id,
                "name": "second",
                "replace_map_id": first_map_id,
            },
        ),
        409,
    )
    assert omitted_confirmation["code"] == "MAP_REPLACEMENT_CONFIRMATION_REQUIRED"

    saved = assert_mock_payload(
        client.post(
            "/api/navigation/maps/save",
            json={
                "session_id": session_id,
                "name": "second",
                "replace_map_id": first_map_id,
                "confirmed": True,
            },
        )
    )
    assert saved["data"]["map_id"] != first_map_id
    assert saved["data"]["revision"] == 2


def test_patrol_pause_resume_and_stop_are_mock_transitions(client: TestClient) -> None:
    map_id = create_active_map(client)
    started = assert_mock_payload(client.post("/api/navigation/patrol/start", json=patrol_request(map_id)))
    task_id = started["data"]["task_id"]
    assert started["data"]["execution_state"] == "navigating"
    assert started["data"]["control_owner"] == "NAVIGATION"

    paused = assert_mock_payload(client.post(f"/api/navigation/tasks/{task_id}/pause", json={}))
    assert paused["data"]["execution_state"] == "paused_admin"
    assert paused["data"]["control_owner"] == "NONE"

    resumed = assert_mock_payload(client.post(f"/api/navigation/tasks/{task_id}/resume", json={}))
    assert resumed["data"]["execution_state"] == "navigating"
    assert resumed["data"]["control_owner"] == "NAVIGATION"

    stopped = assert_mock_payload(client.post(f"/api/navigation/tasks/{task_id}/stop", json={}))
    assert stopped["data"]["execution_state"] == "cancelled"
    assert stopped["data"]["status"] == "CANCELLED"
    assert stopped["data"]["control_owner"] == "NONE"


def test_manual_takeover_pauses_without_automatic_resume(client: TestClient) -> None:
    map_id = create_active_map(client)
    task = assert_mock_payload(client.post("/api/navigation/patrol/start", json=patrol_request(map_id)))
    task_id = task["data"]["task_id"]

    acquired = assert_mock_payload(client.post("/api/navigation/control/manual-takeover", json={}))
    assert acquired["data"]["control_owner"] == "MANUAL"
    assert acquired["data"]["active_task"]["execution_state"] == "paused_manual"

    released = assert_mock_payload(client.post("/api/navigation/control/release", json={}))
    assert released["data"]["control_owner"] == "NONE"
    assert released["data"]["active_task"]["execution_state"] == "paused_manual"

    resumed = assert_mock_payload(client.post(f"/api/navigation/tasks/{task_id}/resume", json={}))
    assert resumed["data"]["execution_state"] == "navigating"


def test_manual_control_compatibility_aliases_and_input_rejection(client: TestClient) -> None:
    acquired = assert_mock_payload(client.post("/api/navigation/control/manual/acquire", json={}))
    assert acquired["data"]["control_owner"] == "MANUAL"
    released = assert_mock_payload(client.post("/api/navigation/control/manual/release", json={}))
    assert released["data"]["control_owner"] == "NONE"

    invalid = assert_mock_payload(
        client.post("/api/navigation/control/manual-takeover", json={"vx": 1.0}),
        422,
    )
    assert invalid["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize(
    ("scenario", "error_code"),
    [
        ("robot_offline", "ROBOT_OFFLINE"),
        ("dds_no_samples", "DDS_NOT_READY"),
        ("lidar_unavailable", "LIDAR_NOT_READY"),
        ("localization_invalid", "LOCALIZATION_INVALID"),
        ("map_not_loaded", "MAP_NOT_LOADED"),
        ("emergency_stop_active", "EMERGENCY_STOP_ACTIVE"),
        ("path_not_plannable", "PATH_NOT_PLANNABLE"),
        ("manual_takeover", "CONTROL_NOT_AVAILABLE"),
    ],
)
def test_each_safety_scenario_blocks_and_retains_task(
    client: TestClient, scenario: str, error_code: str
) -> None:
    map_id = create_active_map(client)
    assert_mock_payload(client.post("/api/navigation/mock/scenario", json={"scenario": scenario}))
    blocked = assert_mock_payload(
        client.post("/api/navigation/patrol/start", json=patrol_request(map_id, scenario)),
        409,
    )
    assert blocked["code"] == error_code
    assert blocked["data"]["task"]["execution_state"] == "blocked"
    state = assert_mock_payload(client.get("/api/navigation/state"))
    assert state["data"]["active_task"]["task_id"] == blocked["data"]["task"]["task_id"]


def test_interlock_recovery_does_not_automatically_retry(client: TestClient) -> None:
    map_id = create_active_map(client)
    assert_mock_payload(client.post("/api/navigation/mock/scenario", json={"scenario": "robot_offline"}))
    blocked = assert_mock_payload(
        client.post("/api/navigation/patrol/start", json=patrol_request(map_id)), 409
    )
    task_id = blocked["data"]["task"]["task_id"]
    recovered = assert_mock_payload(
        client.post("/api/navigation/mock/scenario", json={"scenario": "robot_ready"})
    )
    assert recovered["data"]["active_task"]["execution_state"] == "blocked"
    resumed = assert_mock_payload(client.post(f"/api/navigation/tasks/{task_id}/resume", json={}))
    assert resumed["data"]["execution_state"] == "navigating"


@pytest.mark.parametrize(
    ("scenario", "expected_state", "expected_status"),
    [
        ("navigation_success", "completed", "COMPLETED"),
        ("navigation_failure", "failed", "FAILED"),
    ],
)
def test_deterministic_navigation_outcomes(
    client: TestClient, scenario: str, expected_state: str, expected_status: str
) -> None:
    map_id = create_active_map(client)
    assert_mock_payload(client.post("/api/navigation/mock/scenario", json={"scenario": scenario}))
    result = assert_mock_payload(
        client.post("/api/navigation/patrol/start", json=patrol_request(map_id, scenario))
    )
    assert result["data"]["execution_state"] == expected_state
    assert result["data"]["status"] == expected_status


@pytest.mark.parametrize(
    ("scenario", "branch"),
    [
        ("safe_response", "safe_response"),
        ("need_help", "need_help"),
        ("no_response", "no_response"),
        ("uncertain_response", "uncertain"),
    ],
)
def test_emergency_response_branches_are_deterministic(
    client: TestClient, scenario: str, branch: str
) -> None:
    map_id = create_active_map(client)
    assert_mock_payload(client.post("/api/navigation/mock/scenario", json={"scenario": scenario}))
    result = assert_mock_payload(
        client.post(
            "/api/navigation/emergency/dispatch",
            json={
                "incident_id": f"incident_{scenario}",
                "external_task_id": f"health_{scenario}",
                "map_id": map_id,
                "target_point_id": "observation_1",
            },
        )
    )
    assert result["data"]["execution_state"] == "waiting_admin_confirmation"
    assert result["data"]["metadata"]["emergency_branch"] == branch


@pytest.mark.parametrize(
    ("scenario", "expected_state"),
    [("return_home_success", "completed"), ("return_home_failure", "failed")],
)
def test_return_home_always_runs_safety_and_uses_scenario(
    client: TestClient, scenario: str, expected_state: str
) -> None:
    create_active_map(client)
    assert_mock_payload(client.post("/api/navigation/mock/scenario", json={"scenario": scenario}))
    result = assert_mock_payload(
        client.post(
            "/api/navigation/return-home",
            json={
                "external_task_id": f"health_{scenario}",
                "home_point_id": "robot_home",
                "reason": "admin_confirmed",
            },
        )
    )
    assert result["data"]["execution_state"] == expected_state
    assert result["data"]["real_motion_enabled"] is False


def test_dispatch_rejects_camera_area_as_navigation_target(client: TestClient) -> None:
    map_id = create_active_map(client)
    invalid = assert_mock_payload(
        client.post(
            "/api/navigation/emergency/dispatch",
            json={
                "incident_id": "incident_1",
                "external_task_id": "health_1",
                "map_id": map_id,
                "area_id": "camera_area_1",
            },
        ),
        422,
    )
    assert invalid["code"] == "INVALID_REQUEST"


def test_unknown_task_and_invalid_scenario_have_machine_codes(client: TestClient) -> None:
    missing = assert_mock_payload(client.post("/api/navigation/tasks/missing/pause", json={}), 404)
    assert missing["code"] == "TASK_NOT_FOUND"
    invalid = assert_mock_payload(
        client.post("/api/navigation/mock/scenario", json={"scenario": "random_mode"}), 422
    )
    assert invalid["code"] == "MOCK_SCENARIO_INVALID"


def test_navigation_domain_never_calls_existing_motion_paths(client: TestClient) -> None:
    robot_move = Mock(side_effect=AssertionError("robot_service.move must not be called"))
    adapter_move = Mock(side_effect=AssertionError("adapter.move must not be called"))
    client.app.state.robot_service.move = robot_move
    client.app.state.adapter.move = adapter_move

    map_id = create_active_map(client)
    assert_mock_payload(client.post("/api/navigation/patrol/start", json=patrol_request(map_id)))
    assert robot_move.call_count == 0
    assert adapter_move.call_count == 0


def test_mock_startup_does_not_construct_unitree_adapter(monkeypatch) -> None:
    unitree_constructor = Mock(side_effect=AssertionError("Unitree adapter must not be constructed"))
    monkeypatch.setattr("app.main.UnitreeGo2Adapter", unitree_constructor)
    app = create_app(Settings(mode="mock", task_audit_enabled=False))
    with TestClient(app) as test_client:
        assert_mock_payload(test_client.get("/api/navigation/state"))
    assert unitree_constructor.call_count == 0


def test_navigation_store_serializes_concurrent_mutation() -> None:
    store = NavigationStore()

    def increment() -> None:
        with store.locked() as data:
            data.task_counter += 1

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: increment(), range(200)))

    with store.locked() as data:
        assert data.task_counter == 200
