from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.navigation.models import (
    ControlOwner,
    MappingState,
    MockMap,
    MockMapPreview,
    MockMappingSession,
    MockNavigationPoint,
    MockNavigationTask,
    MockPatrolRoute,
    MockPose,
    NavigationCapability,
    NavigationErrorCode,
    NavigationExecutionState,
    NavigationState,
    NavigationTaskState,
    SafetyChecks,
    SafetyInterlockResult,
    utc_now,
)
from app.navigation.provider import NavigationDomainError
from app.navigation.safety import evaluate_mapping_interlock, evaluate_safety_interlock
from app.navigation.schemas import (
    EmergencyDispatchRequest,
    MapSaveRequest,
    MappingStartRequest,
    MappingStopRequest,
    MockScenarioRequest,
    PatrolStartRequest,
    ReturnHomeRequest,
)
from app.navigation.state_machine import (
    ControlOwnershipStateMachine,
    MappingStateMachine,
    NavigationStateMachine,
    NavigationTransitionError,
)
from app.navigation.store import NavigationStore, NavigationStoreData


@dataclass(frozen=True)
class ScenarioDefinition:
    robot_online: bool = True
    emergency_stop_clear: bool = True
    localization_valid: bool = True
    map_loaded: bool = True
    path_plannable: bool = True
    robot_stationary: bool = True
    control_available: bool = True
    forced_error: NavigationErrorCode | None = None
    navigation_outcome: Literal["pending", "success", "failure"] = "pending"
    return_home_outcome: Literal["pending", "success", "failure"] = "pending"
    emergency_branch: str | None = None


SCENARIOS: dict[str, ScenarioDefinition] = {
    "robot_ready": ScenarioDefinition(),
    "robot_offline": ScenarioDefinition(robot_online=False),
    "dds_no_samples": ScenarioDefinition(forced_error=NavigationErrorCode.DDS_NOT_READY),
    "lidar_unavailable": ScenarioDefinition(forced_error=NavigationErrorCode.LIDAR_NOT_READY),
    "localization_invalid": ScenarioDefinition(localization_valid=False),
    "map_not_loaded": ScenarioDefinition(map_loaded=False),
    "emergency_stop_active": ScenarioDefinition(emergency_stop_clear=False, control_available=False),
    "path_not_plannable": ScenarioDefinition(path_plannable=False),
    "manual_takeover": ScenarioDefinition(control_available=False),
    "navigation_success": ScenarioDefinition(navigation_outcome="success"),
    "navigation_failure": ScenarioDefinition(navigation_outcome="failure"),
    "safe_response": ScenarioDefinition(navigation_outcome="success", emergency_branch="safe_response"),
    "need_help": ScenarioDefinition(navigation_outcome="success", emergency_branch="need_help"),
    "no_response": ScenarioDefinition(navigation_outcome="success", emergency_branch="no_response"),
    "uncertain_response": ScenarioDefinition(navigation_outcome="success", emergency_branch="uncertain"),
    "return_home_success": ScenarioDefinition(return_home_outcome="success"),
    "return_home_failure": ScenarioDefinition(return_home_outcome="failure"),
}


_TERMINAL_STATES = {
    NavigationExecutionState.COMPLETED,
    NavigationExecutionState.FAILED,
    NavigationExecutionState.CANCELLED,
}


class MockNavigationProvider:
    def __init__(self, store: NavigationStore) -> None:
        self.store = store

    def capabilities(self) -> NavigationCapability:
        return NavigationCapability()

    def get_state(self) -> NavigationState:
        return self.store.snapshot()

    def get_active_map(self) -> MockMap | None:
        state = self.store.snapshot()
        return state.active_map

    def start_mapping(self, request: MappingStartRequest) -> MockMappingSession:
        with self.store.locked() as data:
            self._ensure_no_active_task(data)
            if data.mapping_state in {MappingState.MAPPING, MappingState.PREVIEW_READY}:
                raise NavigationDomainError(
                    NavigationErrorCode.MAPPING_ALREADY_ACTIVE,
                    "A Mock mapping session is already active.",
                )
            interlock = self._check_safety(data, mapping=True)
            data.safety_interlock = interlock
            if not interlock.passed:
                self._raise_interlock(interlock)
            data.mapping_counter += 1
            session_id = f"mapping_{data.mapping_counter:04d}"
            MappingStateMachine().transition(MappingState.MAPPING)
            session = MockMappingSession(
                session_id=session_id,
                session_name=request.session_name,
                mapping_state=MappingState.MAPPING,
                guidance="Use administrative remote control to demonstrate the Mock mapping path.",
            )
            data.mapping_session = session
            data.map_preview = None
            data.mapping_state = MappingState.MAPPING
            data.last_error = None
            return session.model_copy(deep=True)

    def stop_mapping(self, request: MappingStopRequest) -> MockMappingSession:
        with self.store.locked() as data:
            session = data.mapping_session
            if data.mapping_state != MappingState.MAPPING or session is None:
                raise NavigationDomainError(
                    NavigationErrorCode.MAPPING_NOT_ACTIVE,
                    "No Mock mapping session is active.",
                )
            if session.session_id != request.session_id:
                raise NavigationDomainError(
                    NavigationErrorCode.MAPPING_NOT_ACTIVE,
                    "The mapping session identifier does not match the active session.",
                )
            next_state = MappingStateMachine(MappingState.MAPPING).transition(MappingState.PREVIEW_READY)
            preview = MockMapPreview(
                session_id=session.session_id,
                occupied_cells=[[0, index] for index in range(3, 17)]
                + [[19, index] for index in range(3, 17)]
                + [[index, 0] for index in range(3, 17)]
                + [[index, 19] for index in range(3, 17)],
            )
            session = session.model_copy(
                update={"mapping_state": next_state, "preview": preview, "updated_at": utc_now()},
                deep=True,
            )
            data.mapping_state = next_state
            data.map_preview = preview
            data.mapping_session = session
            return session.model_copy(deep=True)

    def save_map(self, request: MapSaveRequest) -> MockMap:
        with self.store.locked() as data:
            session = data.mapping_session
            if (
                data.mapping_state != MappingState.PREVIEW_READY
                or session is None
                or data.map_preview is None
                or session.session_id != request.session_id
            ):
                raise NavigationDomainError(
                    NavigationErrorCode.MAP_PREVIEW_NOT_READY,
                    "Stop the active Mock mapping session and preview it before saving.",
                )
            if not request.confirmed:
                raise NavigationDomainError(
                    NavigationErrorCode.MAP_REPLACEMENT_CONFIRMATION_REQUIRED,
                    "Saving a formal Mock map requires explicit confirmation.",
                )
            if data.active_map and request.replace_map_id != data.active_map.map_id:
                raise NavigationDomainError(
                    NavigationErrorCode.MAP_REPLACEMENT_CONFIRMATION_REQUIRED,
                    "Replacing the active Mock map requires its identifier and confirmation.",
                )
            MappingStateMachine(MappingState.PREVIEW_READY).transition(MappingState.SAVED)
            data.map_counter += 1
            map_value = MockMap(
                map_id=f"map_mock_{data.map_counter:04d}",
                session_id=session.session_id,
                name=request.name,
                revision=(data.active_map.revision + 1 if data.active_map else 1),
                preview=data.map_preview,
            )
            data.active_map = map_value
            data.mapping_state = MappingState.SAVED
            data.mapping_session = session.model_copy(
                update={"mapping_state": MappingState.SAVED, "updated_at": utc_now()}, deep=True
            )
            data.map_loaded = True
            data.localization_valid = True
            return map_value.model_copy(deep=True)

    def start_patrol(self, request: PatrolStartRequest) -> MockNavigationTask:
        with self.store.locked() as data:
            self._ensure_mapping_inactive(data)
            self._ensure_no_active_task(data)
            if not data.active_map or request.map_id != data.active_map.map_id:
                return self._create_blocked_task(
                    data,
                    task_type="patrol",
                    external_task_id=request.external_task_id,
                    code=NavigationErrorCode.MAP_NOT_LOADED,
                    message="The requested Mock map is not active.",
                )
            points = request.points or [
                MockNavigationPoint(point_id=point_id, x=float(index + 1), y=float(index % 2), yaw=0.0)
                for index, point_id in enumerate(request.point_ids)
            ]
            route = MockPatrolRoute(
                route_id=request.route_id,
                map_id=request.map_id,
                points=points,
                return_home_point_id=request.return_home_point_id,
            )
            task = self._new_task(
                data,
                task_type="patrol",
                external_task_id=request.external_task_id,
                map_id=request.map_id,
                patrol_route=route,
            )
            data.patrol_route = route
            return self._start_navigation_task(data, task)

    def dispatch_navigation(self, request: EmergencyDispatchRequest) -> MockNavigationTask:
        with self.store.locked() as data:
            self._ensure_mapping_inactive(data)
            self._ensure_no_active_task(data)
            if not data.active_map or request.map_id != data.active_map.map_id:
                return self._create_blocked_task(
                    data,
                    task_type="emergency",
                    external_task_id=request.external_task_id,
                    incident_id=request.incident_id,
                    target_point_id=request.target_point_id,
                    code=NavigationErrorCode.MAP_NOT_LOADED,
                    message="The requested Mock map is not active.",
                )
            target = request.target_pose or MockPose(x=5.0, y=2.0, yaw=0.0)
            task = self._new_task(
                data,
                task_type="emergency",
                external_task_id=request.external_task_id,
                incident_id=request.incident_id,
                map_id=request.map_id,
                target_point_id=request.target_point_id,
                target_pose=target,
            )
            data.target_pose = target
            return self._start_navigation_task(data, task)

    def pause_task(self, task_id: str) -> MockNavigationTask:
        with self.store.locked() as data:
            task = self._task_or_error(data, task_id)
            if task.execution_state != NavigationExecutionState.NAVIGATING:
                raise NavigationDomainError(
                    NavigationErrorCode.TASK_STATE_CONFLICT,
                    "Only a navigating Mock task can be paused by an administrator.",
                )
            task = self._transition(data, task, NavigationExecutionState.PAUSED_ADMIN)
            self._set_control(data, ControlOwner.NONE)
            return data.tasks[task.task_id].model_copy(deep=True)

    def resume_task(self, task_id: str) -> MockNavigationTask:
        with self.store.locked() as data:
            task = self._task_or_error(data, task_id)
            if task.execution_state not in {
                NavigationExecutionState.BLOCKED,
                NavigationExecutionState.PAUSED_ADMIN,
                NavigationExecutionState.PAUSED_MANUAL,
            }:
                raise NavigationDomainError(
                    NavigationErrorCode.TASK_STATE_CONFLICT,
                    "The Mock task is not in a resumable state.",
                )
            self._assert_real_motion_disabled(task)
            task = self._transition(data, task, NavigationExecutionState.SAFETY_CHECKING)
            interlock = self._check_safety(data)
            data.safety_interlock = interlock
            if not interlock.passed:
                task = self._transition(data, task, NavigationExecutionState.BLOCKED)
                task = self._record_task_error(
                    data, task, interlock.blocked_by[0], "Safety interlock blocked resume."
                )
                self._raise_interlock(interlock, task)
            task = self._transition(data, task, NavigationExecutionState.QUEUED)
            task = self._transition(data, task, NavigationExecutionState.NAVIGATING)
            self._set_control(data, ControlOwner.NAVIGATION)
            task = data.tasks[task.task_id]
            return self._apply_navigation_outcome(data, task)

    def stop_task(self, task_id: str) -> MockNavigationTask:
        with self.store.locked() as data:
            task = self._task_or_error(data, task_id)
            if task.execution_state in _TERMINAL_STATES:
                raise NavigationDomainError(
                    NavigationErrorCode.TASK_STATE_CONFLICT,
                    "The Mock task is already terminal.",
                )
            task = self._transition(data, task, NavigationExecutionState.CANCELLED)
            self._set_control(data, ControlOwner.NONE)
            return task.model_copy(deep=True)

    def return_home(self, request: ReturnHomeRequest) -> MockNavigationTask:
        with self.store.locked() as data:
            self._ensure_mapping_inactive(data)
            active = data.tasks.get(data.active_task_id or "")
            if active and active.execution_state == NavigationExecutionState.WAITING_ADMIN_CONFIRMATION:
                self._transition(data, active, NavigationExecutionState.COMPLETED)
            self._ensure_no_active_task(data)
            task = self._new_task(
                data,
                task_type="return_home",
                external_task_id=request.external_task_id,
                map_id=data.active_map.map_id if data.active_map else None,
                home_point_id=request.home_point_id,
                target_pose=request.home_pose or MockPose(x=0.0, y=0.0, yaw=0.0),
                metadata={"reason": request.reason},
            )
            self._assert_real_motion_disabled(task)
            task = self._transition(data, task, NavigationExecutionState.SAFETY_CHECKING)
            interlock = self._check_safety(data)
            data.safety_interlock = interlock
            if not interlock.passed:
                task = self._transition(data, task, NavigationExecutionState.BLOCKED)
                task = self._record_task_error(
                    data, task, interlock.blocked_by[0], "Safety interlock blocked return-home."
                )
                self._raise_interlock(interlock, task)
            task = self._transition(data, task, NavigationExecutionState.RETURNING_HOME)
            self._set_control(data, ControlOwner.NAVIGATION)
            task = data.tasks[task.task_id]
            scenario = SCENARIOS[data.mock_scenario]
            if scenario.return_home_outcome == "success":
                data.current_pose = task.target_pose or MockPose(x=0.0, y=0.0, yaw=0.0)
                task = self._transition(data, task, NavigationExecutionState.COMPLETED)
                self._set_control(data, ControlOwner.NONE)
            elif scenario.return_home_outcome == "failure":
                task = self._fail_task(data, task, "MOCK_RETURN_HOME_FAILED", "Mock return-home failed.")
            return task.model_copy(deep=True)

    def acquire_manual_control(self) -> NavigationState:
        with self.store.locked() as data:
            if data.control_owner == ControlOwner.EMERGENCY_STOP:
                raise NavigationDomainError(
                    NavigationErrorCode.EMERGENCY_STOP_ACTIVE,
                    "Emergency stop owns control.",
                )
            active = data.tasks.get(data.active_task_id or "")
            if active and active.execution_state == NavigationExecutionState.NAVIGATING:
                self._transition(data, active, NavigationExecutionState.PAUSED_MANUAL)
            self._set_control(data, ControlOwner.MANUAL)
        return self.store.snapshot()

    def release_manual_control(self) -> NavigationState:
        with self.store.locked() as data:
            if data.control_owner != ControlOwner.MANUAL:
                raise NavigationDomainError(
                    NavigationErrorCode.INVALID_CONTROL_TRANSITION,
                    "Manual control is not active.",
                )
            self._set_control(data, ControlOwner.NONE)
        return self.store.snapshot()

    def set_mock_scenario(self, request: MockScenarioRequest) -> NavigationState:
        scenario = SCENARIOS.get(request.scenario)
        if scenario is None:
            raise NavigationDomainError(
                NavigationErrorCode.MOCK_SCENARIO_INVALID,
                f"Unknown Mock scenario: {request.scenario}",
                http_status=422,
            )
        with self.store.locked() as data:
            data.mock_scenario = request.scenario
            data.robot_online = scenario.robot_online
            data.emergency_stop_clear = scenario.emergency_stop_clear
            data.localization_valid = scenario.localization_valid and data.active_map is not None
            data.map_loaded = scenario.map_loaded and data.active_map is not None
            data.path_plannable = scenario.path_plannable
            data.robot_stationary = scenario.robot_stationary
            data.control_available = scenario.control_available
            if not scenario.emergency_stop_clear:
                data.control_owner = ControlOwner.EMERGENCY_STOP
            elif request.scenario == "manual_takeover":
                active = data.tasks.get(data.active_task_id or "")
                if active and active.execution_state == NavigationExecutionState.NAVIGATING:
                    self._transition(data, active, NavigationExecutionState.PAUSED_MANUAL)
                data.control_owner = ControlOwner.MANUAL
            elif data.control_owner in {ControlOwner.EMERGENCY_STOP, ControlOwner.MANUAL}:
                data.control_owner = ControlOwner.NONE
            data.safety_interlock = self._check_safety(data)
        return self.store.snapshot()

    def _start_navigation_task(
        self, data: NavigationStoreData, task: MockNavigationTask
    ) -> MockNavigationTask:
        self._assert_real_motion_disabled(task)
        task = self._transition(data, task, NavigationExecutionState.SAFETY_CHECKING)
        interlock = self._check_safety(data)
        data.safety_interlock = interlock
        if not interlock.passed:
            task = self._transition(data, task, NavigationExecutionState.BLOCKED)
            task = self._record_task_error(
                data, task, interlock.blocked_by[0], "Safety interlock blocked departure."
            )
            self._raise_interlock(interlock, task)
        task = self._transition(data, task, NavigationExecutionState.QUEUED)
        task = self._transition(data, task, NavigationExecutionState.NAVIGATING)
        self._set_control(data, ControlOwner.NAVIGATION)
        task = data.tasks[task.task_id]
        return self._apply_navigation_outcome(data, task)

    def _apply_navigation_outcome(
        self, data: NavigationStoreData, task: MockNavigationTask
    ) -> MockNavigationTask:
        scenario = SCENARIOS[data.mock_scenario]
        if scenario.navigation_outcome == "failure":
            return self._fail_task(data, task, "MOCK_NAVIGATION_FAILED", "Mock navigation failed.")
        if scenario.navigation_outcome != "success":
            return task.model_copy(deep=True)
        if task.patrol_route:
            for index, point in enumerate(task.patrol_route.points):
                data.current_pose = MockPose(x=point.x, y=point.y, yaw=point.yaw)
                task = task.model_copy(
                    update={
                        "current_point_index": index,
                        "progress": (index + 1) / len(task.patrol_route.points),
                        "updated_at": utc_now(),
                    }
                )
                data.tasks[task.task_id] = task
                data.progress = task.progress
            task = self._transition(data, task, NavigationExecutionState.ARRIVED)
            interlock = self._check_safety(data)
            data.safety_interlock = interlock
            if not interlock.passed:
                task = self._fail_task(data, task, interlock.blocked_by[0], "Return-home safety check failed.")
                return task
            self._assert_real_motion_disabled(task)
            task = self._transition(data, task, NavigationExecutionState.RETURNING_HOME)
            data.current_pose = MockPose(x=0.0, y=0.0, yaw=0.0)
            task = self._transition(data, task, NavigationExecutionState.COMPLETED)
            self._set_control(data, ControlOwner.NONE)
            return task
        task = self._transition(data, task, NavigationExecutionState.ARRIVED)
        if task.task_type == "emergency" and scenario.emergency_branch:
            task = self._transition(data, task, NavigationExecutionState.VOICE_PROMPTING)
            task = self._transition(data, task, NavigationExecutionState.WAITING_RESPONSE)
            task = task.model_copy(
                update={
                    "metadata": {**task.metadata, "emergency_branch": scenario.emergency_branch},
                    "updated_at": utc_now(),
                }
            )
            data.tasks[task.task_id] = task
            task = self._transition(data, task, NavigationExecutionState.WAITING_ADMIN_CONFIRMATION)
            self._set_control(data, ControlOwner.NONE)
            return task
        task = self._transition(data, task, NavigationExecutionState.COMPLETED)
        self._set_control(data, ControlOwner.NONE)
        return task

    def _new_task(
        self,
        data: NavigationStoreData,
        *,
        task_type: Literal["point_navigation", "patrol", "emergency", "return_home"],
        external_task_id: str | None = None,
        incident_id: str | None = None,
        map_id: str | None = None,
        target_point_id: str | None = None,
        home_point_id: str | None = None,
        target_pose: MockPose | None = None,
        patrol_route: MockPatrolRoute | None = None,
        metadata: dict | None = None,
    ) -> MockNavigationTask:
        data.task_counter += 1
        task = MockNavigationTask(
            task_id=f"nav_task_{data.task_counter:04d}",
            external_task_id=external_task_id,
            incident_id=incident_id,
            task_type=task_type,
            status="QUEUED",
            execution_state=NavigationExecutionState.CREATED,
            navigation_state=NavigationTaskState.CREATED,
            map_id=map_id,
            target_point_id=target_point_id,
            home_point_id=home_point_id,
            target_pose=target_pose,
            patrol_route=patrol_route,
            metadata=metadata or {},
        )
        data.tasks[task.task_id] = task
        data.active_task_id = task.task_id
        data.execution_state = task.execution_state
        data.navigation_state = task.navigation_state
        data.progress = 0.0
        data.last_error = None
        return task

    def _create_blocked_task(
        self,
        data: NavigationStoreData,
        *,
        task_type: Literal["point_navigation", "patrol", "emergency", "return_home"],
        external_task_id: str,
        code: NavigationErrorCode,
        message: str,
        incident_id: str | None = None,
        target_point_id: str | None = None,
    ) -> MockNavigationTask:
        task = self._new_task(
            data,
            task_type=task_type,
            external_task_id=external_task_id,
            incident_id=incident_id,
            target_point_id=target_point_id,
        )
        task = self._transition(data, task, NavigationExecutionState.SAFETY_CHECKING)
        task = self._transition(data, task, NavigationExecutionState.BLOCKED)
        checks = self._safety_checks(data)
        interlock = SafetyInterlockResult(passed=False, checks=checks, blocked_by=[code.value])
        data.safety_interlock = interlock
        task = self._record_task_error(data, task, code.value, message)
        self._raise_interlock(interlock, task, message)
        return task

    def _transition(
        self,
        data: NavigationStoreData,
        task: MockNavigationTask,
        target: NavigationExecutionState,
    ) -> MockNavigationTask:
        state = NavigationState(
            execution_state=task.execution_state,
            navigation_state=task.navigation_state,
        )
        try:
            transitioned = NavigationStateMachine(state).transition(target)
        except NavigationTransitionError as exc:
            raise NavigationDomainError(exc.code, str(exc)) from exc
        status = self._status_for(target)
        owner = ControlOwner.NONE if target in _TERMINAL_STATES else data.control_owner
        task = task.model_copy(
            update={
                "execution_state": transitioned.execution_state,
                "navigation_state": transitioned.navigation_state,
                "status": status,
                "control_owner": owner,
                "updated_at": utc_now(),
            }
        )
        data.tasks[task.task_id] = task
        data.active_task_id = task.task_id
        data.execution_state = task.execution_state
        data.navigation_state = task.navigation_state
        data.progress = task.progress
        if target in _TERMINAL_STATES:
            data.active_task_id = None
        return task

    @staticmethod
    def _status_for(target: NavigationExecutionState) -> str:
        if target == NavigationExecutionState.BLOCKED:
            return "BLOCKED"
        if target in {NavigationExecutionState.CREATED, NavigationExecutionState.SAFETY_CHECKING, NavigationExecutionState.QUEUED}:
            return "QUEUED"
        if target == NavigationExecutionState.COMPLETED:
            return "COMPLETED"
        if target == NavigationExecutionState.FAILED:
            return "FAILED"
        if target == NavigationExecutionState.CANCELLED:
            return "CANCELLED"
        return "RUNNING"

    def _check_safety(self, data: NavigationStoreData, mapping: bool = False) -> SafetyInterlockResult:
        checks = self._safety_checks(data)
        scenario = SCENARIOS[data.mock_scenario]
        if scenario.forced_error:
            return SafetyInterlockResult(
                passed=False,
                checks=checks,
                blocked_by=[scenario.forced_error.value],
            )
        return evaluate_mapping_interlock(checks) if mapping else evaluate_safety_interlock(checks)

    @staticmethod
    def _safety_checks(data: NavigationStoreData) -> SafetyChecks:
        return SafetyChecks(
            robot_online=data.robot_online,
            emergency_stop_clear=data.emergency_stop_clear,
            localization_valid=data.localization_valid,
            map_loaded=data.map_loaded,
            path_plannable=data.path_plannable,
            robot_stationary=data.robot_stationary,
            control_available=data.control_available,
        )

    @staticmethod
    def _assert_real_motion_disabled(task: MockNavigationTask) -> None:
        if task.provider != "mock":
            raise NavigationDomainError(
                NavigationErrorCode.MOCK_PROVIDER_REQUIRED,
                "Only the Mock navigation provider is permitted in phase one.",
            )
        if task.real_motion_enabled is not False:
            raise NavigationDomainError(
                NavigationErrorCode.REAL_MOTION_DISABLED,
                "Real motion must remain disabled.",
            )

    @staticmethod
    def _task_or_error(data: NavigationStoreData, task_id: str) -> MockNavigationTask:
        task = data.tasks.get(task_id)
        if task is None:
            raise NavigationDomainError(
                NavigationErrorCode.TASK_NOT_FOUND,
                f"Mock navigation task not found: {task_id}",
                http_status=404,
            )
        return task

    @staticmethod
    def _ensure_no_active_task(data: NavigationStoreData) -> None:
        task = data.tasks.get(data.active_task_id or "")
        if task and task.execution_state not in _TERMINAL_STATES:
            raise NavigationDomainError(
                NavigationErrorCode.TASK_ALREADY_ACTIVE,
                f"Mock navigation task {task.task_id} is still active.",
            )

    @staticmethod
    def _ensure_mapping_inactive(data: NavigationStoreData) -> None:
        if data.mapping_state in {MappingState.MAPPING, MappingState.PREVIEW_READY}:
            raise NavigationDomainError(
                NavigationErrorCode.NAVIGATION_NOT_READY,
                "Mock navigation cannot start while a mapping session is active.",
            )

    @staticmethod
    def _record_task_error(
        data: NavigationStoreData,
        task: MockNavigationTask,
        code: str,
        message: str,
    ) -> MockNavigationTask:
        task = task.model_copy(
            update={"error_code": code, "error_message": message, "updated_at": utc_now()}
        )
        data.tasks[task.task_id] = task
        data.last_error = code
        return task

    def _fail_task(
        self, data: NavigationStoreData, task: MockNavigationTask, code: str, message: str
    ) -> MockNavigationTask:
        task = self._transition(data, task, NavigationExecutionState.FAILED)
        task = self._record_task_error(data, task, code, message)
        self._set_control(data, ControlOwner.NONE)
        return task

    @staticmethod
    def _raise_interlock(
        interlock: SafetyInterlockResult,
        task: MockNavigationTask | None = None,
        message: str = "The Mock safety interlock blocked this transition.",
    ) -> None:
        code_value = interlock.blocked_by[0]
        try:
            code = NavigationErrorCode(code_value)
        except ValueError:
            code = NavigationErrorCode.SAFETY_INTERLOCK_FAILED
        data = {"provider": "mock", "real_motion_enabled": False, "safety_interlock": interlock}
        if task:
            data["task"] = task
        raise NavigationDomainError(code, message, data=data)

    @staticmethod
    def _set_control(data: NavigationStoreData, target: ControlOwner) -> None:
        if data.control_owner == target:
            return
        try:
            data.control_owner = ControlOwnershipStateMachine(data.control_owner).transition(target)
        except NavigationTransitionError as exc:
            raise NavigationDomainError(exc.code, str(exc)) from exc
        active = data.tasks.get(data.active_task_id or "")
        if active:
            data.tasks[active.task_id] = active.model_copy(
                update={"control_owner": data.control_owner, "updated_at": utc_now()}
            )
