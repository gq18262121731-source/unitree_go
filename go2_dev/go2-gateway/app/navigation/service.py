from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from app.navigation.event_bus import NavigationEventBus
from app.navigation.events import NavigationEventType
from app.navigation.models import (
    MockMap,
    MockMappingSession,
    MockNavigationTask,
    NavigationCapability,
    NavigationErrorCode,
    NavigationExecutionState,
    NavigationState,
    SafetyInterlockResult,
)
from app.navigation.provider import NavigationDomainError, NavigationProvider
from app.navigation.schemas import (
    EmergencyDispatchRequest,
    MapSaveRequest,
    MappingStartRequest,
    MappingStopRequest,
    MockScenarioRequest,
    PatrolStartRequest,
    ReturnHomeRequest,
)


class NavigationService:
    def __init__(
        self,
        provider: NavigationProvider,
        *,
        scenario_switch_enabled: bool,
        event_bus: NavigationEventBus | None = None,
    ) -> None:
        self._provider = provider
        self._scenario_switch_enabled = scenario_switch_enabled
        self._event_bus = event_bus
        self._logger = logging.getLogger("go2_gateway.navigation")

    def capabilities(self) -> NavigationCapability:
        return self._provider.capabilities()

    def get_state(self) -> NavigationState:
        return self._provider.get_state()

    def get_active_map(self) -> MockMap | None:
        return self._provider.get_active_map()

    def start_mapping(self, request: MappingStartRequest) -> MockMappingSession:
        try:
            session = self._provider.start_mapping(request)
        except NavigationDomainError as exc:
            self._publish_safety_from_error(exc)
            raise
        self._publish_current_safety()
        self._publish(
            NavigationEventType.MAPPING_STATE_CHANGED,
            {"mapping_state": session.mapping_state.value, "session_id": session.session_id},
        )
        return session

    def stop_mapping(self, request: MappingStopRequest) -> MockMappingSession:
        session = self._provider.stop_mapping(request)
        self._publish(
            NavigationEventType.MAPPING_STATE_CHANGED,
            {"mapping_state": session.mapping_state.value, "session_id": session.session_id},
        )
        self._publish(
            NavigationEventType.MAP_PREVIEW_READY,
            {
                "session_id": session.session_id,
                "mapping_state": session.mapping_state.value,
                "preview": self._dump(session.preview),
            },
        )
        return session

    def save_map(self, request: MapSaveRequest) -> MockMap:
        map_value = self._provider.save_map(request)
        self._publish(
            NavigationEventType.MAPPING_STATE_CHANGED,
            {"mapping_state": "saved", "session_id": map_value.session_id},
        )
        self._publish(
            NavigationEventType.MAP_SAVED,
            {
                "map_id": map_value.map_id,
                "name": map_value.name,
                "revision": map_value.revision,
                "status": map_value.status,
            },
        )
        return map_value

    def start_patrol(self, request: PatrolStartRequest) -> MockNavigationTask:
        return self._start_task(lambda: self._provider.start_patrol(request))

    def dispatch_navigation(self, request: EmergencyDispatchRequest) -> MockNavigationTask:
        return self._start_task(lambda: self._provider.dispatch_navigation(request))

    def pause_task(self, task_id: str) -> MockNavigationTask:
        task = self._provider.pause_task(task_id)
        self._publish(NavigationEventType.TASK_PAUSED, self._task_data(task))
        self._publish_navigation_state()
        return task

    def resume_task(self, task_id: str) -> MockNavigationTask:
        try:
            task = self._provider.resume_task(task_id)
        except NavigationDomainError as exc:
            self._publish_blocked_error(exc, include_created=False)
            raise
        self._publish_current_safety()
        self._publish(NavigationEventType.TASK_RESUMED, self._task_data(task))
        self._publish_task_outcome(task)
        self._publish_navigation_state()
        return task

    def stop_task(self, task_id: str) -> MockNavigationTask:
        task = self._provider.stop_task(task_id)
        self._publish(NavigationEventType.TASK_CANCELLED, self._task_data(task))
        self._publish_navigation_state()
        return task

    def return_home(self, request: ReturnHomeRequest) -> MockNavigationTask:
        try:
            task = self._provider.return_home(request)
        except NavigationDomainError as exc:
            self._publish_blocked_error(exc, include_created=True)
            raise
        self._publish(NavigationEventType.TASK_CREATED, self._task_data(task))
        self._publish_current_safety()
        self._publish(NavigationEventType.RETURN_HOME_STARTED, self._task_data(task))
        if task.execution_state == NavigationExecutionState.COMPLETED:
            self._publish(NavigationEventType.RETURN_HOME_COMPLETED, self._task_data(task))
            self._publish(NavigationEventType.TASK_COMPLETED, self._task_data(task))
        elif task.execution_state == NavigationExecutionState.FAILED:
            self._publish(NavigationEventType.RETURN_HOME_FAILED, self._task_data(task))
            self._publish(NavigationEventType.TASK_FAILED, self._task_data(task))
        self._publish_navigation_state()
        return task

    def acquire_manual_control(self) -> NavigationState:
        before = self.get_state()
        state = self._provider.acquire_manual_control()
        self._publish(
            NavigationEventType.MANUAL_CONTROL_ACQUIRED,
            {"control_owner": state.control_owner.value, "active_task_id": state.active_task_id},
        )
        if (
            before.active_task is not None
            and before.active_task.execution_state == NavigationExecutionState.NAVIGATING
            and state.active_task is not None
        ):
            self._publish(NavigationEventType.TASK_PAUSED, self._task_data(state.active_task))
        self._publish_navigation_state(state)
        return state

    def release_manual_control(self) -> NavigationState:
        state = self._provider.release_manual_control()
        self._publish(
            NavigationEventType.MANUAL_CONTROL_RELEASED,
            {"control_owner": state.control_owner.value, "active_task_id": state.active_task_id},
        )
        self._publish_navigation_state(state)
        return state

    def set_mock_scenario(self, request: MockScenarioRequest) -> NavigationState:
        if not self._scenario_switch_enabled:
            raise NavigationDomainError(
                NavigationErrorCode.MOCK_PROVIDER_REQUIRED,
                "Mock scenario switching is available only in explicit mock mode.",
                http_status=403,
            )
        before = self.get_state()
        state = self._provider.set_mock_scenario(request)
        if before.mock_scenario == state.mock_scenario:
            return state
        self._publish(
            NavigationEventType.MOCK_SCENARIO_CHANGED,
            {
                "mock_scenario": state.mock_scenario,
                "control_owner": state.control_owner.value,
                "safety_interlock": self._dump(state.safety_interlock),
            },
        )
        self._publish_navigation_state(state)
        return state

    def _start_task(self, operation) -> MockNavigationTask:
        try:
            task = operation()
        except NavigationDomainError as exc:
            self._publish_blocked_error(exc, include_created=True)
            raise
        self._publish(NavigationEventType.TASK_CREATED, self._task_data(task))
        self._publish_current_safety()
        self._publish(NavigationEventType.TASK_STARTED, self._task_data(task))
        self._publish_task_outcome(task)
        self._publish_navigation_state()
        return task

    def _publish_task_outcome(self, task: MockNavigationTask) -> None:
        if task.execution_state == NavigationExecutionState.FAILED:
            self._publish(NavigationEventType.TASK_FAILED, self._task_data(task))
            return
        if task.execution_state == NavigationExecutionState.WAITING_ADMIN_CONFIRMATION:
            self._publish(NavigationEventType.TASK_ARRIVED, self._task_data(task))
            return
        if task.execution_state != NavigationExecutionState.COMPLETED:
            return
        self._publish(NavigationEventType.TASK_ARRIVED, self._task_data(task))
        if task.task_type == "patrol":
            self._publish(NavigationEventType.RETURN_HOME_STARTED, self._task_data(task))
            self._publish(NavigationEventType.RETURN_HOME_COMPLETED, self._task_data(task))
        self._publish(NavigationEventType.TASK_COMPLETED, self._task_data(task))

    def _publish_blocked_error(
        self, exc: NavigationDomainError, *, include_created: bool
    ) -> None:
        task = exc.data.get("task")
        safety = exc.data.get("safety_interlock")
        if include_created and isinstance(task, MockNavigationTask):
            self._publish(NavigationEventType.TASK_CREATED, self._task_data(task))
        if isinstance(safety, SafetyInterlockResult):
            self._publish_safety(safety)
        if isinstance(task, MockNavigationTask):
            data = self._task_data(task)
            data["blocked_by"] = list(safety.blocked_by) if isinstance(safety, SafetyInterlockResult) else []
            data["error_code"] = task.error_code or exc.code.value
            self._publish(NavigationEventType.TASK_BLOCKED, data)
            self._publish_navigation_state()

    def _publish_safety_from_error(self, exc: NavigationDomainError) -> None:
        safety = exc.data.get("safety_interlock")
        if isinstance(safety, SafetyInterlockResult):
            self._publish_safety(safety)

    def _publish_current_safety(self) -> None:
        safety = self.get_state().safety_interlock
        if safety is not None:
            self._publish_safety(safety)

    def _publish_safety(self, safety: SafetyInterlockResult) -> None:
        self._publish(
            NavigationEventType.SAFETY_INTERLOCK_CHECKED,
            {
                "passed": safety.passed,
                "checks": safety.checks.model_dump(mode="json"),
                "blocked_by": list(safety.blocked_by),
                "checked_at": safety.checked_at.isoformat(),
            },
        )

    def _publish_navigation_state(self, state: NavigationState | None = None) -> None:
        state = state or self.get_state()
        self._publish(
            NavigationEventType.NAVIGATION_STATE_CHANGED,
            {
                "navigation_state": state.navigation_state.value,
                "execution_state": state.execution_state.value,
                "control_owner": state.control_owner.value,
                "active_task_id": state.active_task_id,
                "progress": state.progress,
                "current_pose": self._pose_data(state.current_pose),
                "target_pose": self._pose_data(state.target_pose),
            },
        )

    def _publish(self, event_type: NavigationEventType, data: dict[str, Any]) -> None:
        if self._event_bus is None:
            return
        try:
            self._event_bus.publish(event_type, data)
        except Exception as exc:
            self._logger.warning("Mock navigation event publication failed: %s", exc)

    @staticmethod
    def _task_data(task: MockNavigationTask) -> dict[str, Any]:
        return {
            "task_id": task.task_id,
            "external_task_id": task.external_task_id,
            "incident_id": task.incident_id,
            "task_type": task.task_type,
            "status": task.status,
            "execution_state": task.execution_state.value,
            "navigation_state": task.navigation_state.value,
            "control_owner": task.control_owner.value,
            "progress": task.progress,
            "error_code": task.error_code,
        }

    @staticmethod
    def _pose_data(pose) -> dict[str, float] | None:
        if pose is None:
            return None
        return {"x": pose.x, "y": pose.y, "yaw": pose.yaw}

    @staticmethod
    def _dump(value: BaseModel | None) -> Any:
        return value.model_dump(mode="json") if value is not None else None
