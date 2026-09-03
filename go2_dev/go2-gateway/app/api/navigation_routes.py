from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.navigation.schemas import (
    EmergencyDispatchRequest,
    MapSaveRequest,
    MappingStartRequest,
    MappingStopRequest,
    MockScenarioRequest,
    PatrolStartRequest,
    ReturnHomeRequest,
    TaskControlRequest,
    ManualControlRequest,
    PointCloudScenarioRequest,
)
from app.navigation.mock_point_cloud import MockPointCloudStream, PointCloudDomainError
from app.navigation.service import NavigationService
from app.schemas.common import ok_response


router = APIRouter(prefix="/api/navigation", tags=["mock-navigation"])


def _service(request: Request) -> NavigationService:
    return request.app.state.mock_navigation_service


def _request_id(request: Request, body_request_id: str | None = None) -> str | None:
    return body_request_id or getattr(request.state, "request_id", None)


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _ok(request: Request, data: Any, body_request_id: str | None = None) -> dict:
    return ok_response(
        "ok",
        _dump(data),
        code="OK",  # type: ignore[arg-type]
        request_id=_request_id(request, body_request_id),
    )


@router.get("/capabilities")
def get_capabilities(request: Request) -> dict:
    return _ok(request, _service(request).capabilities())


@router.get("/state")
def get_state(request: Request) -> dict:
    return _ok(request, _service(request).get_state())


@router.get("/maps/active")
def get_active_map(request: Request) -> dict:
    active_map = _service(request).get_active_map()
    return _ok(
        request,
        {
            "provider": "mock",
            "real_motion_enabled": False,
            "active_map": _dump(active_map) if active_map else None,
        },
    )


@router.get("/control")
def get_control(request: Request) -> dict:
    state = _service(request).get_state()
    return _ok(
        request,
        {
            "provider": "mock",
            "real_motion_enabled": False,
            "control_owner": state.control_owner.value,
            "active_task_id": state.active_task_id,
            "emergency_stop_active": state.emergency_stop_active,
        },
    )


@router.post("/mapping/start")
def start_mapping(request: Request, body: MappingStartRequest) -> dict:
    return _ok(request, _service(request).start_mapping(body), body.request_id)


@router.post("/mapping/stop")
def stop_mapping(request: Request, body: MappingStopRequest) -> dict:
    return _ok(request, _service(request).stop_mapping(body), body.request_id)


@router.post("/maps/save")
def save_map(request: Request, body: MapSaveRequest) -> dict:
    return _ok(request, _service(request).save_map(body), body.request_id)


@router.post("/patrol/start")
def start_patrol(request: Request, body: PatrolStartRequest) -> dict:
    return _ok(request, _service(request).start_patrol(body), body.request_id)


@router.post("/emergency/dispatch")
def dispatch_emergency(request: Request, body: EmergencyDispatchRequest) -> dict:
    return _ok(request, _service(request).dispatch_navigation(body), body.request_id)


@router.post("/tasks/{task_id}/pause")
def pause_task(task_id: str, request: Request, body: TaskControlRequest | None = None) -> dict:
    return _ok(request, _service(request).pause_task(task_id), body.request_id if body else None)


@router.post("/tasks/{task_id}/resume")
def resume_task(task_id: str, request: Request, body: TaskControlRequest | None = None) -> dict:
    return _ok(request, _service(request).resume_task(task_id), body.request_id if body else None)


@router.post("/tasks/{task_id}/stop")
def stop_task(task_id: str, request: Request, body: TaskControlRequest | None = None) -> dict:
    return _ok(request, _service(request).stop_task(task_id), body.request_id if body else None)


@router.post("/return-home")
def return_home(request: Request, body: ReturnHomeRequest) -> dict:
    return _ok(request, _service(request).return_home(body), body.request_id)


@router.post("/control/manual-takeover")
@router.post("/control/manual/acquire", include_in_schema=False)
def acquire_manual_control(request: Request, body: ManualControlRequest | None = None) -> dict:
    return _ok(
        request,
        _service(request).acquire_manual_control(),
        body.request_id if body else None,
    )


@router.post("/control/release")
@router.post("/control/manual/release", include_in_schema=False)
def release_manual_control(request: Request, body: ManualControlRequest | None = None) -> dict:
    return _ok(
        request,
        _service(request).release_manual_control(),
        body.request_id if body else None,
    )


@router.post("/mock/scenario")
def set_mock_scenario(request: Request, body: MockScenarioRequest) -> dict:
    return _ok(request, _service(request).set_mock_scenario(body), body.request_id)


@router.post("/mock/point-cloud/scenario")
def set_mock_point_cloud_scenario(
    request: Request, body: PointCloudScenarioRequest
) -> dict:
    stream: MockPointCloudStream | None = getattr(
        request.app.state, "mock_point_cloud_stream", None
    )
    if stream is None:
        from app.navigation.point_cloud_models import PointCloudErrorCode

        raise PointCloudDomainError(
            PointCloudErrorCode.NAVIGATION_STORE_UNAVAILABLE,
            "Navigation Store is unavailable for the Mock point-cloud stream.",
            http_status=503,
        )
    return _ok(request, stream.set_scenario(body.scenario), body.request_id)
