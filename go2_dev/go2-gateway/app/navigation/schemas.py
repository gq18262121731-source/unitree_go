from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.navigation.models import MockNavigationPoint, MockPose


class NavigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str | None = Field(default=None, max_length=160)


class MappingStartRequest(NavigationRequest):
    session_name: str = Field(min_length=1, max_length=160)


class MappingStopRequest(NavigationRequest):
    session_id: str = Field(min_length=1, max_length=160)


class MapSaveRequest(NavigationRequest):
    session_id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=160)
    replace_map_id: str | None = Field(default=None, max_length=160)
    confirmed: bool = False


class PatrolStartRequest(NavigationRequest):
    external_task_id: str = Field(min_length=1, max_length=160)
    route_id: str = Field(min_length=1, max_length=160)
    map_id: str = Field(min_length=1, max_length=160)
    point_ids: list[str] = Field(min_length=1)
    points: list[MockNavigationPoint] = Field(default_factory=list)
    return_home_point_id: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_points(self) -> "PatrolStartRequest":
        if len(set(self.point_ids)) != len(self.point_ids):
            raise ValueError("point_ids must be unique")
        if self.points and [point.point_id for point in self.points] != self.point_ids:
            raise ValueError("points must match point_ids in order")
        return self


class EmergencyDispatchRequest(NavigationRequest):
    incident_id: str = Field(min_length=1, max_length=160)
    external_task_id: str = Field(min_length=1, max_length=160)
    map_id: str = Field(min_length=1, max_length=160)
    target_point_id: str = Field(min_length=1, max_length=160)
    target_pose: MockPose | None = None


class ReturnHomeRequest(NavigationRequest):
    external_task_id: str = Field(min_length=1, max_length=160)
    home_point_id: str = Field(min_length=1, max_length=160)
    home_pose: MockPose | None = None
    reason: str = Field(min_length=1, max_length=240)


class TaskControlRequest(NavigationRequest):
    pass


class ManualControlRequest(NavigationRequest):
    pass


class MockScenarioRequest(NavigationRequest):
    scenario: str = Field(min_length=1, max_length=120)


class PointCloudScenarioRequest(NavigationRequest):
    scenario: str = Field(min_length=1, max_length=120)
