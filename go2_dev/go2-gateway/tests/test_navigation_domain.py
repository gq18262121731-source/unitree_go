from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.navigation.models import (
    ControlOwner,
    MappingState,
    NavigationCapability,
    NavigationErrorCode,
    NavigationExecutionState,
    NavigationState,
    NavigationTaskState,
    SafetyChecks,
)
from app.navigation.safety import evaluate_safety_interlock
from app.navigation.state_machine import (
    ControlOwnershipStateMachine,
    MappingStateMachine,
    NavigationStateMachine,
    NavigationTransitionError,
)


def test_contract_enum_values_are_stable() -> None:
    assert {item.value for item in ControlOwner} == {
        "NONE",
        "MANUAL",
        "NAVIGATION",
        "FOLLOW",
        "EMERGENCY_STOP",
    }
    assert [item.value for item in MappingState] == [
        "idle",
        "mapping",
        "preview_ready",
        "saved",
        "cancelled",
        "failed",
    ]
    assert NavigationExecutionState.PAUSED_MANUAL.value == "paused_manual"
    assert NavigationExecutionState.RETURNING_HOME.value == "returning_home"


def test_legal_navigation_state_transitions_pass() -> None:
    machine = NavigationStateMachine()

    machine.transition(NavigationExecutionState.SAFETY_CHECKING)
    machine.transition(NavigationExecutionState.QUEUED)
    machine.transition(NavigationExecutionState.NAVIGATING)
    machine.transition(NavigationExecutionState.ARRIVED)
    final = machine.transition(NavigationExecutionState.COMPLETED)

    assert final.execution_state == NavigationExecutionState.COMPLETED
    assert final.navigation_state == NavigationTaskState.COMPLETED
    assert final.provider == "mock"
    assert final.real_motion_enabled is False


def test_mapping_requires_preview_before_save() -> None:
    machine = MappingStateMachine()

    assert machine.transition(MappingState.MAPPING) == MappingState.MAPPING
    assert machine.transition(MappingState.PREVIEW_READY) == MappingState.PREVIEW_READY
    assert machine.transition(MappingState.SAVED) == MappingState.SAVED

    invalid = MappingStateMachine()
    with pytest.raises(NavigationTransitionError) as caught:
        invalid.transition(MappingState.SAVED)
    assert caught.value.code == NavigationErrorCode.INVALID_STATE_TRANSITION


def test_illegal_navigation_transition_has_machine_readable_code() -> None:
    machine = NavigationStateMachine()

    with pytest.raises(NavigationTransitionError) as caught:
        machine.transition(NavigationExecutionState.COMPLETED)

    assert caught.value.code == NavigationErrorCode.INVALID_STATE_TRANSITION
    assert caught.value.current == "created"
    assert caught.value.target == "completed"


def test_return_home_requires_a_fresh_safety_check_transition() -> None:
    machine = NavigationStateMachine(
        NavigationState(
            execution_state=NavigationExecutionState.WAITING_ADMIN_CONFIRMATION,
            navigation_state=NavigationTaskState.ARRIVED,
        )
    )

    assert machine.can_transition(NavigationExecutionState.RETURNING_HOME) is False
    machine.transition(NavigationExecutionState.SAFETY_CHECKING)
    returned = machine.transition(NavigationExecutionState.RETURNING_HOME)
    assert returned.navigation_state == NavigationTaskState.RETURNING_HOME


def test_emergency_stop_overrides_every_control_owner() -> None:
    for owner in ControlOwner:
        machine = ControlOwnershipStateMachine(owner=owner)
        assert machine.emergency_stop() == ControlOwner.EMERGENCY_STOP
        with pytest.raises(NavigationTransitionError) as caught:
            machine.transition(ControlOwner.NAVIGATION)
        assert caught.value.code == NavigationErrorCode.EMERGENCY_STOP_ACTIVE


def test_manual_control_can_interrupt_navigation_without_auto_resume() -> None:
    machine = ControlOwnershipStateMachine(owner=ControlOwner.NAVIGATION)

    assert machine.transition(ControlOwner.MANUAL) == ControlOwner.MANUAL
    assert machine.transition(ControlOwner.NONE) == ControlOwner.NONE
    assert machine.owner == ControlOwner.NONE


def test_safety_interlock_uses_all_contract_checks() -> None:
    result = evaluate_safety_interlock(
        SafetyChecks(
            robot_online=True,
            emergency_stop_clear=True,
            localization_valid=False,
            map_loaded=True,
            path_plannable=False,
            robot_stationary=True,
            control_available=True,
        )
    )

    assert result.passed is False
    assert result.blocked_by == ["LOCALIZATION_INVALID", "PATH_NOT_PLANNABLE"]
    assert result.real_motion_enabled is False


def test_real_motion_enabled_is_immutable_false() -> None:
    assert NavigationCapability().real_motion_enabled is False
    assert NavigationState().real_motion_enabled is False
    with pytest.raises(ValidationError):
        NavigationCapability(real_motion_enabled=True)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        NavigationState(real_motion_enabled=True)  # type: ignore[arg-type]


def test_navigation_package_has_no_real_motion_dependencies_or_calls() -> None:
    root = Path(__file__).resolve().parents[1]
    forbidden_import_prefixes = (
        "app.adapters",
        "app.gateway",
        "app.services",
        "unitree_sdk2py",
        "rclpy",
        "nav2",
    )
    forbidden_calls = {"move", "stand", "sit", "stop", "lie_down"}
    offenders: list[str] = []

    for path in sorted((root / "app" / "navigation").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                modules = []
            for module in modules:
                if module.startswith(forbidden_import_prefixes):
                    offenders.append(f"{path.name} imports {module}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in forbidden_calls:
                    offenders.append(f"{path.name} calls {node.func.attr}()")

    assert offenders == []
