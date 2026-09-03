from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from typing import Iterator

from app.navigation.models import (
    ControlOwner,
    MappingState,
    MockMap,
    MockMapPreview,
    MockMappingSession,
    MockNavigationTask,
    MockPatrolRoute,
    MockPose,
    NavigationExecutionState,
    NavigationState,
    NavigationTaskState,
    SafetyInterlockResult,
    utc_now,
)


@dataclass
class NavigationStoreData:
    mapping_state: MappingState = MappingState.IDLE
    execution_state: NavigationExecutionState = NavigationExecutionState.CREATED
    navigation_state: NavigationTaskState = NavigationTaskState.IDLE
    control_owner: ControlOwner = ControlOwner.NONE
    active_map: MockMap | None = None
    mapping_session: MockMappingSession | None = None
    map_preview: MockMapPreview | None = None
    current_pose: MockPose = field(default_factory=lambda: MockPose(x=0.0, y=0.0, yaw=0.0))
    target_pose: MockPose | None = None
    active_task_id: str | None = None
    patrol_route: MockPatrolRoute | None = None
    progress: float = 0.0
    last_error: str | None = None
    safety_interlock: SafetyInterlockResult | None = None
    mock_scenario: str = "robot_ready"
    robot_online: bool = True
    emergency_stop_clear: bool = True
    localization_valid: bool = False
    map_loaded: bool = False
    path_plannable: bool = True
    robot_stationary: bool = True
    control_available: bool = True
    tasks: dict[str, MockNavigationTask] = field(default_factory=dict)
    mapping_counter: int = 0
    map_counter: int = 0
    task_counter: int = 0
    updated_at: datetime = field(default_factory=utc_now)


class NavigationStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._data = NavigationStoreData()

    @contextmanager
    def locked(self) -> Iterator[NavigationStoreData]:
        with self._lock:
            try:
                yield self._data
            finally:
                self._data.updated_at = utc_now()

    def reset(self) -> None:
        with self._lock:
            self._data = NavigationStoreData()

    def snapshot(self) -> NavigationState:
        with self._lock:
            data = self._data
            active_task = data.tasks.get(data.active_task_id or "")
            return NavigationState(
                mapping_state=data.mapping_state,
                active_map_id=data.active_map.map_id if data.active_map else None,
                localization_valid=data.localization_valid,
                map_loaded=data.map_loaded,
                path_plannable=data.path_plannable,
                robot_online=data.robot_online,
                emergency_stop_clear=data.emergency_stop_clear,
                robot_stationary=data.robot_stationary,
                control_available=data.control_available,
                control_owner=data.control_owner,
                emergency_stop_active=not data.emergency_stop_clear,
                execution_state=data.execution_state,
                navigation_state=data.navigation_state,
                active_task_id=data.active_task_id,
                active_map=data.active_map.model_copy(deep=True) if data.active_map else None,
                current_pose=data.current_pose.model_copy(deep=True),
                target_pose=data.target_pose.model_copy(deep=True) if data.target_pose else None,
                active_task=active_task.model_copy(deep=True) if active_task else None,
                patrol_route=data.patrol_route.model_copy(deep=True) if data.patrol_route else None,
                progress=data.progress,
                last_error=data.last_error,
                safety_interlock=(
                    data.safety_interlock.model_copy(deep=True) if data.safety_interlock else None
                ),
                mock_scenario=data.mock_scenario,
                updated_at=data.updated_at,
            )
