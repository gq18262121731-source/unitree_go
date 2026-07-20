from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.adapters.mock_adapter import MockGo2Adapter
from app.adapters.unitree_adapter import UnitreeGo2Adapter
from app.config import Settings, load_settings
from app.core.errors import ErrorCode, GatewayError
from app.core.state_store import StateStore
from app.middleware.request_id import RequestIdMiddleware
from app.schemas.common import error_response, ok_response
from app.schemas.robot import MoveRequest
from app.schemas.tasks import FallEventRequest, TargetMoveRequest
from app.services.camera_service import CameraService
from app.services.robot_service import RobotService
from app.services.task_service import RobotTaskService


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    logger = logging.getLogger("go2_gateway")
    handler = RotatingFileHandler("logs/go2-gateway.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)


def build_adapter(settings: Settings):
    if settings.mode == "real":
        return UnitreeGo2Adapter(settings.network_interface, settings.sdk_timeout_seconds, settings.robot_id)
    return MockGo2Adapter(settings.robot_id)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings)
    adapter = build_adapter(settings)
    state_store = StateStore(settings.robot_id, settings.state_stale_seconds)
    robot_service = RobotService(adapter, settings, state_store)
    camera_service = CameraService(adapter, state_store)
    task_service = RobotTaskService(robot_service, camera_service)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.adapter = adapter
        app.state.robot_service = robot_service
        app.state.camera_service = camera_service
        app.state.task_service = task_service
        app.state.state_store = state_store
        try:
            robot_service.initialize()
        except GatewayError:
            logging.getLogger("go2_gateway").exception("Gateway started with SDK initialization error")
        yield
        robot_service.close()

    app = FastAPI(title="Go2 Gateway", version=settings.version, lifespan=lifespan)
    app.add_middleware(RequestIdMiddleware)

    @app.exception_handler(GatewayError)
    async def gateway_error_handler(request: Request, exc: GatewayError):
        return JSONResponse(
            status_code=exc.http_status,
            content=error_response(exc.code.value, exc.message, getattr(request.state, "request_id", None)),
        )

    @app.exception_handler(Exception)
    async def internal_error_handler(request: Request, exc: Exception):
        logging.getLogger("go2_gateway").exception("Unhandled error")
        return JSONResponse(
            status_code=500,
            content=error_response(ErrorCode.INTERNAL_ERROR.value, str(exc), getattr(request.state, "request_id", None)),
        )

    @app.get("/health")
    def health(request: Request) -> dict:
        status = state_store.snapshot()
        data = {
            "service": "go2-gateway",
            "version": settings.version,
            "mode": settings.mode,
            "initialized": adapter.is_initialized(),
            "robotOnline": status.get("online", False),
            "sdkVersion": adapter.sdk_version,
            "networkInterface": settings.network_interface,
        }
        return ok_response("Gateway is running.", data=data, request_id=request.state.request_id)

    @app.get("/api/robot/status")
    def robot_status(request: Request) -> dict:
        status = robot_service.status()
        status["activeTask"] = task_service.active_task()
        return ok_response("Robot status loaded.", data=status, request_id=request.state.request_id)

    @app.post("/api/robot/stand")
    def stand(request: Request) -> dict:
        result = robot_service.stand()
        return ok_response("Robot stand command sent.", code=result["code"], request_id=request.state.request_id)

    @app.post("/api/robot/lie-down")
    def lie_down(request: Request) -> dict:
        result = robot_service.lie_down()
        return ok_response("Robot lie-down command sent.", code=result["code"], request_id=request.state.request_id)

    @app.post("/api/robot/stop")
    def stop(request: Request) -> dict:
        result = robot_service.stop()
        return ok_response("Robot stopped.", code=result["code"], request_id=request.state.request_id)

    @app.post("/api/robot/emergency-stop")
    def emergency_stop(request: Request) -> dict:
        result = robot_service.emergency_stop()
        return ok_response("Emergency stop executed.", code=result["code"], request_id=request.state.request_id)

    @app.post("/api/robot/move")
    def move(payload: MoveRequest, request: Request) -> dict:
        result = robot_service.move(payload.vx, payload.vy, payload.wz, payload.duration, payload.control_source)
        return ok_response("Robot move command completed.", code=result["code"], request_id=request.state.request_id)

    @app.post("/api/robot/events/fall")
    def receive_fall_event(payload: FallEventRequest, request: Request) -> dict:
        task = task_service.submit_fall_event(payload)
        return ok_response("Fall confirmation task accepted.", data=task, request_id=request.state.request_id)

    @app.post("/api/robot/tasks/target-move")
    def target_move(payload: TargetMoveRequest, request: Request) -> dict:
        task = task_service.submit_target_move(payload)
        return ok_response("Target move task accepted.", data=task, request_id=request.state.request_id)

    @app.get("/api/robot/tasks")
    def list_tasks(request: Request) -> dict:
        return ok_response("Robot tasks loaded.", data=task_service.list_tasks(), request_id=request.state.request_id)

    @app.get("/api/robot/tasks/{task_id}")
    def get_task(task_id: str, request: Request) -> dict:
        return ok_response("Robot task loaded.", data=task_service.get_task(task_id), request_id=request.state.request_id)

    @app.get("/api/robot/camera/snapshot")
    def snapshot() -> Response:
        return Response(content=camera_service.snapshot(), media_type="image/jpeg")

    return app


app = create_app()
