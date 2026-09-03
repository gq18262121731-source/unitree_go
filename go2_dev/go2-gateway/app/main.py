from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routes import register_routes
from app.api.navigation_routes import router as navigation_router
from app.api.navigation_ws import router as navigation_ws_router
from app.api.navigation_point_cloud_ws import router as navigation_point_cloud_ws_router
from app.adapters.mock_adapter import MockGo2Adapter
from app.adapters.unitree_adapter import UnitreeGo2Adapter
from app.config import Settings, load_settings
from app.companion.exceptions import CompanionLifecycleError
from app.companion.lifecycle_service import CompanionLifecycleService
from app.core.errors import ErrorCode, GatewayError
from app.core.state_store import StateStore
from app.event.fall_event_receiver import FallEventReceiver
from app.gateway.go2_gateway import Go2Gateway
from app.middleware.request_id import RequestIdMiddleware
from app.navigation.mock_provider import MockNavigationProvider
from app.navigation.event_bus import NavigationEventBus
from app.navigation.mock_point_cloud import (
    MockPointCloudStream,
    PointCloudDomainError,
)
from app.navigation.provider import NavigationDomainError
from app.navigation.service import NavigationService
from app.navigation.store import NavigationStore
from app.schemas.common import error_response
from app.services.capability_service import CapabilityService
from app.services.camera_service import CameraService
from app.services.feedback_service import HealthNewFeedbackService
from app.services.lidar_status_service import LidarStatusService
from app.services.network_diagnostics import NetworkDiagnosticsService
from app.services.robot_service import RobotService
from app.services.status_service import RobotStatusService
from app.services.task_service import RobotTaskService
from app.services.voice_service import VoiceService
from app.task_manager.robot_task_manager import RobotTaskManager


class SafeRotatingFileHandler(RotatingFileHandler):
    def doRollover(self) -> None:
        try:
            super().doRollover()
        except PermissionError:
            if self.stream:
                self.stream.close()
                self.stream = None
            self.stream = self._open()


def configure_logging(settings: Settings) -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(level=level)
    logger = logging.getLogger("go2_gateway")
    logger.setLevel(level)
    log_path = Path("logs/go2-gateway.log").resolve()
    for existing in logger.handlers:
        if isinstance(existing, RotatingFileHandler) and Path(existing.baseFilename).resolve() == log_path:
            existing.setLevel(level)
            return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = SafeRotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)


def log_effective_config(settings: Settings) -> None:
    logging.getLogger("go2_gateway").info(
        "effective config mode=%s robot_id=%s robot_ip=%s network_interface=%s domain_id=%s "
        "control_enabled=%s dds_timeout_seconds=%s require_dds_state=%s sdk_timeout_seconds=%s",
        settings.mode,
        settings.robot_id,
        settings.robot_ip,
        settings.network_interface,
        settings.domain_id,
        settings.control_enabled,
        settings.dds_timeout_seconds,
        settings.require_dds_state,
        settings.sdk_timeout_seconds,
    )


def build_adapter(settings: Settings):
    if settings.mode == "real":
        return UnitreeGo2Adapter(
            settings.network_interface,
            settings.sdk_timeout_seconds,
            settings.robot_id,
            settings.robot_ip,
            settings.domain_id,
        )
    return MockGo2Adapter(settings.robot_id)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings)
    log_effective_config(settings)
    adapter = build_adapter(settings)
    gateway = Go2Gateway(adapter)
    state_store = StateStore(settings.robot_id, settings.state_stale_seconds)
    robot_service = RobotService(gateway, settings, state_store)
    camera_service = CameraService(gateway, state_store, settings)
    voice_service = VoiceService(settings)
    feedback_service = HealthNewFeedbackService(settings)
    task_service = RobotTaskService(robot_service, camera_service, voice_service, feedback_service, settings)
    task_manager = RobotTaskManager(task_service)
    companion_lifecycle_service = CompanionLifecycleService(
        robot_service=robot_service,
        settings=settings,
        active_task_provider=task_manager.active_task,
    )
    fall_event_receiver = FallEventReceiver(task_manager)
    status_service = RobotStatusService(robot_service, task_manager, settings, gateway)
    capability_service = CapabilityService(settings)
    network_diagnostics_service = NetworkDiagnosticsService(settings, gateway)
    lidar_status_service = LidarStatusService(settings, gateway, network_diagnostics_service)
    mock_navigation_store = NavigationStore()
    mock_navigation_event_bus = NavigationEventBus()
    mock_navigation_provider = MockNavigationProvider(mock_navigation_store)
    mock_point_cloud_stream = MockPointCloudStream(mock_navigation_store.snapshot)
    mock_navigation_service = NavigationService(
        mock_navigation_provider,
        scenario_switch_enabled=settings.mode == "mock",
        event_bus=mock_navigation_event_bus,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.adapter = adapter
        app.state.gateway = gateway
        app.state.robot_service = robot_service
        app.state.camera_service = camera_service
        app.state.voice_service = voice_service
        app.state.feedback_service = feedback_service
        app.state.task_service = task_service
        app.state.task_manager = task_manager
        app.state.companion_lifecycle_service = companion_lifecycle_service
        app.state.fall_event_receiver = fall_event_receiver
        app.state.status_service = status_service
        app.state.capability_service = capability_service
        app.state.network_diagnostics_service = network_diagnostics_service
        app.state.lidar_status_service = lidar_status_service
        app.state.state_store = state_store
        app.state.mock_navigation_store = mock_navigation_store
        app.state.mock_navigation_event_bus = mock_navigation_event_bus
        app.state.mock_navigation_provider = mock_navigation_provider
        app.state.mock_navigation_service = mock_navigation_service
        app.state.mock_point_cloud_stream = mock_point_cloud_stream
        task_service.restore_terminal_tasks_from_audit()
        try:
            robot_service.initialize()
        except GatewayError:
            logging.getLogger("go2_gateway").exception("Gateway started with SDK initialization error")
        companion_lifecycle_service.initialize()
        yield
        await mock_point_cloud_stream.close()
        mock_navigation_event_bus.close()
        companion_lifecycle_service.close()
        task_service.cancel_active_tasks("gateway_shutdown")
        feedback_service.close()
        robot_service.close()

    app = FastAPI(title="Go2 Gateway", version=settings.version, lifespan=lifespan)
    app.add_middleware(RequestIdMiddleware)

    @app.exception_handler(GatewayError)
    async def gateway_error_handler(request: Request, exc: GatewayError):
        return JSONResponse(
            status_code=exc.http_status,
            content=error_response(exc.code.value, exc.message, getattr(request.state, "request_id", None)),
        )

    @app.exception_handler(CompanionLifecycleError)
    async def companion_lifecycle_error_handler(
        request: Request, exc: CompanionLifecycleError
    ):
        return JSONResponse(
            status_code=exc.http_status,
            content=error_response(
                exc.code,
                exc.message,
                getattr(request.state, "request_id", None),
            ),
        )

    @app.exception_handler(NavigationDomainError)
    async def navigation_error_handler(request: Request, exc: NavigationDomainError):
        data = {"provider": "mock", "real_motion_enabled": False, **exc.data}
        return JSONResponse(
            status_code=exc.http_status,
            content=jsonable_encoder(
                error_response(
                    exc.code.value,
                    exc.message,
                    getattr(request.state, "request_id", None),
                    data,
                )
            ),
        )

    @app.exception_handler(PointCloudDomainError)
    async def point_cloud_error_handler(request: Request, exc: PointCloudDomainError):
        return JSONResponse(
            status_code=exc.http_status,
            content=error_response(
                exc.code.value,
                exc.message,
                getattr(request.state, "request_id", None),
                {"provider": "mock", "real_motion_enabled": False},
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        data = {"errors": jsonable_encoder(exc.errors())}
        if request.url.path.startswith("/api/navigation"):
            data.update({"provider": "mock", "real_motion_enabled": False})
        return JSONResponse(
            status_code=422,
            content=error_response(
                ErrorCode.INVALID_REQUEST.value,
                "Request validation failed.",
                getattr(request.state, "request_id", None),
                data,
            ),
        )

    @app.exception_handler(Exception)
    async def internal_error_handler(request: Request, exc: Exception):
        logging.getLogger("go2_gateway").exception("Unhandled error")
        data = None
        if request.url.path.startswith("/api/navigation"):
            data = {"provider": "mock", "real_motion_enabled": False}
        return JSONResponse(
            status_code=500,
            content=error_response(
                ErrorCode.INTERNAL_ERROR.value,
                str(exc),
                getattr(request.state, "request_id", None),
                data,
            ),
        )

    register_routes(
        app,
        settings,
        state_store,
        gateway,
        robot_service,
        camera_service,
        voice_service,
        feedback_service,
        status_service,
        capability_service,
        network_diagnostics_service,
        lidar_status_service,
        fall_event_receiver,
        task_manager,
        companion_lifecycle_service,
    )
    app.include_router(navigation_router)
    app.include_router(navigation_ws_router)
    app.include_router(navigation_point_cloud_ws_router)

    return app


app = create_app()
