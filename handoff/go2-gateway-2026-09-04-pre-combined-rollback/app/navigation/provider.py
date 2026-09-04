from __future__ import annotations

from typing import Any, Protocol

from app.navigation.models import (
    MockMap,
    MockMappingSession,
    MockNavigationTask,
    NavigationCapability,
    NavigationErrorCode,
    NavigationState,
)
from app.navigation.schemas import (
    EmergencyDispatchRequest,
    MapSaveRequest,
    MappingStartRequest,
    MappingStopRequest,
    MockScenarioRequest,
    PatrolStartRequest,
    ReturnHomeRequest,
)


class NavigationDomainError(RuntimeError):
    def __init__(
        self,
        code: NavigationErrorCode,
        message: str,
        http_status: int = 409,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.http_status = http_status
        self.data = data or {}
        super().__init__(f"{code.value}: {message}")


class NavigationProvider(Protocol):
    def capabilities(self) -> NavigationCapability: ...

    def get_state(self) -> NavigationState: ...

    def get_active_map(self) -> MockMap | None: ...

    def start_mapping(self, request: MappingStartRequest) -> MockMappingSession: ...

    def stop_mapping(self, request: MappingStopRequest) -> MockMappingSession: ...

    def save_map(self, request: MapSaveRequest) -> MockMap: ...

    def start_patrol(self, request: PatrolStartRequest) -> MockNavigationTask: ...

    def dispatch_navigation(self, request: EmergencyDispatchRequest) -> MockNavigationTask: ...

    def pause_task(self, task_id: str) -> MockNavigationTask: ...

    def resume_task(self, task_id: str) -> MockNavigationTask: ...

    def stop_task(self, task_id: str) -> MockNavigationTask: ...

    def return_home(self, request: ReturnHomeRequest) -> MockNavigationTask: ...

    def acquire_manual_control(self) -> NavigationState: ...

    def release_manual_control(self) -> NavigationState: ...

    def set_mock_scenario(self, request: MockScenarioRequest) -> NavigationState: ...
