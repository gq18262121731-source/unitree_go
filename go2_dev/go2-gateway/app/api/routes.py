from __future__ import annotations

from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from app.config import Settings
from app.core.errors import ErrorCode, GatewayError
from app.core.state_store import StateStore
from app.event.fall_event_receiver import FallEventReceiver
from app.gateway.go2_gateway import Go2Gateway
from app.schemas.common import error_response, ok_response
from app.schemas.robot import MoveRequest
from app.schemas.tasks import (
    CancelTaskRequest,
    ConfirmFallTaskRequest,
    ElderResponseRequest,
    FallEventRequest,
    FollowTaskRequest,
    PatrolTaskRequest,
    ReplayFeedbackRequest,
    TargetMoveRequest,
    VoiceResultRequest,
)
from app.services.capability_service import CapabilityService
from app.services.camera_service import CameraService
from app.companion.lifecycle_service import CompanionLifecycleService
from app.services.feedback_service import HealthNewFeedbackService
from app.services.lidar_status_service import LidarStatusService
from app.services.network_diagnostics import NetworkDiagnosticsService
from app.services.robot_service import RobotService
from app.services.status_service import RobotStatusService
from app.services.voice_service import VoiceService
from app.task_manager.robot_task_manager import RobotTaskManager


def register_routes(
    app: FastAPI,
    settings: Settings,
    state_store: StateStore,
    gateway: Go2Gateway,
    robot_service: RobotService,
    camera_service: CameraService,
    voice_service: VoiceService,
    feedback_service: HealthNewFeedbackService,
    status_service: RobotStatusService,
    capability_service: CapabilityService,
    network_diagnostics_service: NetworkDiagnosticsService,
    lidar_status_service: LidarStatusService,
    fall_event_receiver: FallEventReceiver,
    task_manager: RobotTaskManager,
    companion_lifecycle_service: CompanionLifecycleService,
) -> None:
    @app.get("/health")
    def health(request: Request) -> dict:
        status = state_store.snapshot()
        active_task = task_manager.active_task_status()
        feedback = feedback_service.status()
        initialized = gateway.is_initialized()
        robot_online = bool(status.get("online", False))
        dds = status.get("dds") or gateway.dds_diagnostics()
        dds_initialized = bool(dds.get("ddsInitialized", initialized))
        dds_state_available = bool(dds.get("ddsStateAvailable", robot_online))
        robot_state_stale = bool(status.get("stateStale"))
        robot_busy = bool(status.get("control", {}).get("busy"))
        control_enabled = bool(status.get("control", {}).get("enabled", settings.control_enabled))
        motion_ready = initialized and dds_initialized and dds_state_available and robot_online and control_enabled and not robot_state_stale
        data = {
            "service": "go2-gateway",
            "version": settings.version,
            "mode": settings.mode,
            "ready": motion_ready and not robot_busy and not active_task["active"],
            "initialized": initialized,
            "ddsInitialized": dds_initialized,
            "ddsStateAvailable": dds_state_available,
            "motionReady": motion_ready,
            "robotOnline": robot_online,
            "controlEnabled": control_enabled,
            "robotStateStale": robot_state_stale,
            "robotBusy": robot_busy,
            "sdkVersion": gateway.sdk_version,
            "domainId": settings.domain_id,
            "networkInterface": settings.network_interface,
            "activeTask": active_task,
            "feedback": {
                "configured": feedback["configured"],
                "pending": feedback["pending"],
                "sent": feedback["sent"],
                "failed": feedback["failed"],
                "dropped": feedback["dropped"],
                "workerAlive": feedback["worker_alive"],
                "lastError": feedback["last_error"],
            },
        }
        return ok_response("Gateway is running.", data=data, request_id=request.state.request_id)

    @app.get("/api/status")
    def compact_status(request: Request) -> dict:
        return ok_response("Robot status loaded.", data=status_service.compact_status(), request_id=request.state.request_id)

    @app.get("/api/capabilities")
    def capabilities(request: Request) -> dict:
        return ok_response("Robot capabilities loaded.", data=capability_service.capabilities(), request_id=request.state.request_id)

    @app.get("/api/robot/capabilities")
    def capabilities_compat(request: Request) -> dict:
        return ok_response("Robot capabilities loaded.", data=capability_service.capabilities(), request_id=request.state.request_id)

    @app.get("/api/connection")
    def connection_status(request: Request) -> dict:
        return ok_response("Robot connection status loaded.", data=status_service.connection_status(), request_id=request.state.request_id)

    @app.get("/api/robot/diagnostics/dds")
    def dds_diagnostics(request: Request) -> dict:
        return ok_response(
            "Robot DDS diagnostics loaded.",
            data=network_diagnostics_service.diagnostics(),
            request_id=request.state.request_id,
        )

    @app.get("/api/lidar/status")
    def lidar_status(request: Request) -> dict:
        return ok_response(
            "Go2 LiDAR diagnostic status loaded.",
            data=request.app.state.lidar_status_service.technical_status(),
            request_id=request.state.request_id,
        )

    @app.get("/api/robot/lidar/status")
    def robot_lidar_status(request: Request) -> dict:
        return ok_response(
            "Robot LiDAR status loaded.",
            data=request.app.state.lidar_status_service.robot_status(),
            request_id=request.state.request_id,
        )

    @app.get("/api/preflight")
    def preflight(request: Request) -> dict:
        connection = status_service.connection_status()
        readiness_data = status_service.readiness()
        camera = camera_service.status()
        voice = voice_service.status()
        feedback = feedback_service.status()
        capabilities_data = capability_service.capabilities()
        active_task = task_manager.active_task_status()
        checks = {
            "sdk_initialized": {"ok": bool(connection["initialized"]), "value": connection["initialized"]},
            "dds_state_available": {
                "ok": bool(readiness_data["dds_state_available"]),
                "value": readiness_data["dds_state_available"],
            },
            "motion_ready": {"ok": bool(readiness_data["motion_ready"]), "value": readiness_data["motion_ready"]},
            "robot_online": {"ok": bool(connection["online"]), "value": connection["online"]},
            "state_fresh": {"ok": not bool(connection["state_stale"]), "value": not bool(connection["state_stale"])},
            "control_enabled": {"ok": bool(readiness_data["control_enabled"]), "value": readiness_data["control_enabled"]},
            "dispatch_accepting": {"ok": bool(readiness_data["accepting_tasks"]), "error": readiness_data["acceptance_error"]},
            "dispatch_idle": {"ok": active_task["active"] is False, "active_task": active_task},
            "voice_ready": {"ok": bool(voice["ready"]), "mode": voice["mode"], "delivery_mode": voice["delivery_mode"]},
            "feedback_worker": {"ok": bool(feedback["worker_alive"]), "pending": feedback["pending"], "failed": feedback["failed"]},
        }
        dispatch_accepting = bool(readiness_data["accepting_tasks"])
        dispatch_immediate_ready = bool(readiness_data["ready"])
        if dispatch_immediate_ready:
            next_action = "dispatch"
        elif dispatch_accepting:
            next_action = "queue"
        else:
            next_action = readiness_data["acceptance_error"] or readiness_data["error"]
        data = {
            "service": "go2-gateway",
            "version": settings.version,
            "mode": settings.mode,
            "ready": readiness_data["ready"],
            "dispatch_ready": readiness_data["ready"],
            "dispatch_immediate_ready": dispatch_immediate_ready,
            "dispatch_accepting": dispatch_accepting,
            "readiness": readiness_data,
            "connection": connection,
            "camera": {**camera, "sampled": False},
            "voice": voice,
            "feedback": feedback,
            "capabilities": {
                "fall_event": capabilities_data["events"]["fall"],
                "tasks": capabilities_data["tasks"]["urls"],
                "status": capabilities_data["status"],
            },
            "checks": checks,
            "next_action": next_action,
        }
        return ok_response("Gateway preflight loaded.", data=data, request_id=request.state.request_id)

    @app.get("/api/robot/preflight")
    def preflight_compat(request: Request) -> dict:
        return preflight(request)

    @app.post("/api/connection/reconnect")
    def reconnect(request: Request) -> dict:
        active = task_manager.active_task()
        if active:
            raise GatewayError(ErrorCode.CONTROL_BUSY, f"Robot task already running: {active['taskId']}", 409)
        robot_service.reconnect()
        return ok_response("Robot reconnected.", data=status_service.connection_status(), request_id=request.state.request_id)

    @app.post("/api/robot/connection/reconnect")
    def reconnect_compat(request: Request) -> dict:
        active = task_manager.active_task()
        if active:
            raise GatewayError(ErrorCode.CONTROL_BUSY, f"Robot task already running: {active['taskId']}", 409)
        robot_service.reconnect()
        return ok_response("Robot reconnected.", data=status_service.connection_status(), request_id=request.state.request_id)

    @app.get("/api/readiness")
    def readiness(request: Request) -> dict:
        data = status_service.readiness()
        if data["ready"]:
            return ok_response("Robot is ready for task dispatch.", data=data, request_id=request.state.request_id)
        code = ErrorCode(data["error"] or ErrorCode.INTERNAL_ERROR.value)
        if code == ErrorCode.CONTROL_BUSY:
            http_status = 409
        elif code == ErrorCode.CONTROL_DISABLED:
            http_status = 403
        else:
            http_status = 503
        return JSONResponse(
            status_code=http_status,
            content=error_response(code.value, "Robot is not ready for task dispatch.", request.state.request_id, data),
        )

    @app.get("/api/robot/readiness")
    def readiness_compat(request: Request) -> dict:
        return readiness(request)

    @app.get("/api/feedback/status")
    def feedback_status(request: Request) -> dict:
        return ok_response("health_new feedback status loaded.", data=feedback_service.status(), request_id=request.state.request_id)

    @app.get("/api/robot/feedback/status")
    def feedback_status_compat(request: Request) -> dict:
        return ok_response("health_new feedback status loaded.", data=feedback_service.status(), request_id=request.state.request_id)

    @app.get("/api/robot/status")
    def robot_status(request: Request) -> dict:
        return ok_response("Robot status loaded.", data=status_service.detailed_status(), request_id=request.state.request_id)

    @app.get("/api/v1/robot/companion/status")
    def companion_status(request: Request) -> dict:
        return ok_response(
            "Companion status loaded.",
            data=companion_lifecycle_service.status(),
            request_id=request.state.request_id,
        )

    @app.post("/api/v1/robot/companion/start")
    def companion_start(request: Request) -> dict:
        return ok_response(
            "Companion start accepted.",
            data=companion_lifecycle_service.start(),
            request_id=request.state.request_id,
        )

    @app.post("/api/v1/robot/companion/stop")
    def companion_stop(request: Request) -> dict:
        return ok_response(
            "Companion stopped.",
            data=companion_lifecycle_service.stop(),
            request_id=request.state.request_id,
        )

    @app.post("/api/v1/robot/companion/resume")
    def companion_resume(request: Request) -> dict:
        return ok_response(
            "Companion resume accepted.",
            data=companion_lifecycle_service.resume(),
            request_id=request.state.request_id,
        )

    @app.post("/api/robot/stand")
    def stand(request: Request) -> dict:
        result = robot_service.stand()
        return ok_response("Robot stand command sent.", code=result["code"], request_id=request.state.request_id)

    @app.post("/api/robot/lie-down")
    def lie_down(request: Request) -> dict:
        result = robot_service.lie_down()
        return ok_response("Robot lie-down command sent.", code=result["code"], request_id=request.state.request_id)

    @app.post("/api/robot/sit")
    def sit(request: Request) -> dict:
        result = robot_service.sit()
        return ok_response("Robot sit command sent.", code=result["code"], request_id=request.state.request_id)

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
        # ``controlSource`` is caller-supplied audit metadata, not an
        # authority token. Prefix it so HTTP cannot impersonate the private
        # companion runtime owner.
        source = f"api:{payload.control_source}"
        result = robot_service.move(
            payload.vx, payload.vy, payload.wz, payload.duration, source
        )
        return ok_response("Robot move command completed.", code=result["code"], request_id=request.state.request_id)

    @app.post("/api/robot/events/fall")
    def receive_fall_event(payload: FallEventRequest, request: Request) -> dict:
        task = fall_event_receiver.receive(payload)
        return ok_response("Fall confirmation task accepted.", data=task, request_id=request.state.request_id)

    @app.post("/api/events/fall")
    def receive_fall_event_compat(payload: FallEventRequest, request: Request) -> dict:
        task = fall_event_receiver.receive(payload)
        return ok_response("Fall confirmation task accepted.", data=task, request_id=request.state.request_id)

    @app.get("/api/events/fall/{source_event_id}")
    def fall_event_status(source_event_id: str, request: Request) -> dict:
        data = task_manager.fall_event_status(source_event_id)
        return ok_response("Fall event status loaded.", data=data, request_id=request.state.request_id)

    @app.get("/api/robot/events/fall/{source_event_id}")
    def fall_event_status_compat(source_event_id: str, request: Request) -> dict:
        data = task_manager.fall_event_status(source_event_id)
        return ok_response("Fall event status loaded.", data=data, request_id=request.state.request_id)

    @app.post("/api/tasks/confirm-fall")
    def confirm_fall_task(payload: ConfirmFallTaskRequest, request: Request) -> dict:
        task = fall_event_receiver.receive(payload.to_fall_event())
        return ok_response("Fall confirmation task accepted.", data=task, request_id=request.state.request_id)

    @app.post("/api/robot/tasks/confirm-fall")
    def confirm_fall_task_compat(payload: ConfirmFallTaskRequest, request: Request) -> dict:
        task = fall_event_receiver.receive(payload.to_fall_event())
        return ok_response("Fall confirmation task accepted.", data=task, request_id=request.state.request_id)

    @app.post("/api/robot/tasks/target-move")
    def target_move(payload: TargetMoveRequest, request: Request) -> dict:
        task = task_manager.create_target_move_task(payload)
        return ok_response("Target move task accepted.", data=task, request_id=request.state.request_id)

    @app.post("/api/tasks/target-move")
    def target_move_compat(payload: TargetMoveRequest, request: Request) -> dict:
        task = task_manager.create_target_move_task(payload)
        return ok_response("Target move task accepted.", data=task, request_id=request.state.request_id)

    @app.get("/api/locations")
    def locations(request: Request) -> dict:
        return ok_response("Robot locations loaded.", data=task_manager.locations(), request_id=request.state.request_id)

    @app.get("/api/robot/locations")
    def locations_compat(request: Request) -> dict:
        return ok_response("Robot locations loaded.", data=task_manager.locations(), request_id=request.state.request_id)

    @app.get("/api/locations/resolve")
    def resolve_location(request: Request, location: str = Query(..., min_length=1)) -> dict:
        return ok_response("Robot location resolved.", data=task_manager.resolve_location(location), request_id=request.state.request_id)

    @app.get("/api/robot/locations/resolve")
    def resolve_location_compat(request: Request, location: str = Query(..., min_length=1)) -> dict:
        return ok_response("Robot location resolved.", data=task_manager.resolve_location(location), request_id=request.state.request_id)

    @app.post("/api/tasks/follow")
    def follow_task(payload: FollowTaskRequest, request: Request) -> dict:
        task = task_manager.create_follow_task(payload.target)
        return ok_response("Follow task accepted.", data=task, request_id=request.state.request_id)

    @app.post("/api/tasks/patrol")
    def patrol_task(payload: PatrolTaskRequest, request: Request) -> dict:
        task = task_manager.create_patrol_task(payload.route)
        return ok_response("Patrol task accepted.", data=task, request_id=request.state.request_id)

    @app.get("/api/robot/tasks")
    def list_tasks(request: Request, limit: int = Query(default=50, ge=1, le=500)) -> dict:
        return ok_response("Robot tasks loaded.", data=task_manager.list_tasks(limit), request_id=request.state.request_id)

    @app.get("/api/tasks")
    def list_tasks_compat(request: Request, limit: int = Query(default=50, ge=1, le=500)) -> dict:
        return ok_response("Robot tasks loaded.", data=task_manager.list_tasks(limit), request_id=request.state.request_id)

    @app.get("/api/tasks/summary")
    def list_task_summaries(request: Request, limit: int = Query(default=50, ge=1, le=500)) -> dict:
        return ok_response("Robot task summaries loaded.", data=task_manager.list_task_summaries(limit), request_id=request.state.request_id)

    @app.get("/api/robot/tasks/summary")
    def list_task_summaries_compat(request: Request, limit: int = Query(default=50, ge=1, le=500)) -> dict:
        return ok_response("Robot task summaries loaded.", data=task_manager.list_task_summaries(limit), request_id=request.state.request_id)

    @app.get("/api/tasks/latest")
    def latest_task(request: Request) -> dict:
        return ok_response("Latest robot task loaded.", data=task_manager.latest_task_summary(), request_id=request.state.request_id)

    @app.get("/api/robot/tasks/latest")
    def latest_task_compat(request: Request) -> dict:
        return ok_response("Latest robot task loaded.", data=task_manager.latest_task_summary(), request_id=request.state.request_id)

    @app.get("/api/tasks/active")
    def active_task(request: Request) -> dict:
        return ok_response("Active robot task loaded.", data=task_manager.active_task_status(), request_id=request.state.request_id)

    @app.get("/api/robot/tasks/active")
    def active_task_compat(request: Request) -> dict:
        return ok_response("Active robot task loaded.", data=task_manager.active_task_status(), request_id=request.state.request_id)

    @app.get("/api/robot/tasks/current")
    def current_task_compat(request: Request) -> dict:
        task = task_manager.active_task()
        return ok_response("Current robot task loaded.", data=task, request_id=request.state.request_id)

    @app.get("/api/tasks/queue")
    def task_queue(request: Request) -> dict:
        return ok_response("Robot task queue loaded.", data=task_manager.task_queue(), request_id=request.state.request_id)

    @app.get("/api/robot/tasks/queue")
    def task_queue_compat(request: Request) -> dict:
        return ok_response("Robot task queue loaded.", data=task_manager.task_queue(), request_id=request.state.request_id)

    @app.get("/api/tasks/audit-log")
    def task_audit_log(request: Request, limit: int = Query(default=50, ge=1, le=500)) -> dict:
        return ok_response("Robot task audit log loaded.", data=task_manager.audit_entries(limit), request_id=request.state.request_id)

    @app.get("/api/robot/tasks/audit-log")
    def task_audit_log_compat(request: Request, limit: int = Query(default=50, ge=1, le=500)) -> dict:
        return ok_response("Robot task audit log loaded.", data=task_manager.audit_entries(limit), request_id=request.state.request_id)

    @app.get("/api/tasks/{task_id}/audit-log")
    def task_audit_log_by_id(task_id: str, request: Request, limit: int = Query(default=50, ge=1, le=500)) -> dict:
        return ok_response(
            "Robot task audit log loaded.",
            data=task_manager.task_audit_entries(task_id, limit),
            request_id=request.state.request_id,
        )

    @app.get("/api/robot/tasks/{task_id}/audit-log")
    def task_audit_log_by_id_compat(task_id: str, request: Request, limit: int = Query(default=50, ge=1, le=500)) -> dict:
        return ok_response(
            "Robot task audit log loaded.",
            data=task_manager.task_audit_entries(task_id, limit),
            request_id=request.state.request_id,
        )

    @app.get("/api/tasks/external/{external_task_id}")
    def get_task_by_external_id(external_task_id: str, request: Request) -> dict:
        return ok_response(
            "External robot task status loaded.",
            data=task_manager.external_task_status(external_task_id),
            request_id=request.state.request_id,
        )

    @app.get("/api/robot/tasks/external/{external_task_id}")
    def get_task_by_external_id_compat(external_task_id: str, request: Request) -> dict:
        return ok_response(
            "External robot task status loaded.",
            data=task_manager.external_task_status(external_task_id),
            request_id=request.state.request_id,
        )

    @app.get("/api/tasks/external/{external_task_id}/status")
    def get_external_task_status(external_task_id: str, request: Request) -> dict:
        return ok_response(
            "External robot task status loaded.",
            data=task_manager.external_task_status_detail(external_task_id),
            request_id=request.state.request_id,
        )

    @app.get("/api/robot/tasks/external/{external_task_id}/status")
    def get_external_task_status_compat(external_task_id: str, request: Request) -> dict:
        return ok_response(
            "External robot task status loaded.",
            data=task_manager.external_task_status_detail(external_task_id),
            request_id=request.state.request_id,
        )

    @app.get("/api/tasks/external/{external_task_id}/result")
    def get_external_task_result(external_task_id: str, request: Request) -> dict:
        return ok_response(
            "External robot task result loaded.",
            data=task_manager.external_task_result(external_task_id),
            request_id=request.state.request_id,
        )

    @app.get("/api/robot/tasks/external/{external_task_id}/result")
    def get_external_task_result_compat(external_task_id: str, request: Request) -> dict:
        return ok_response(
            "External robot task result loaded.",
            data=task_manager.external_task_result(external_task_id),
            request_id=request.state.request_id,
        )

    @app.get("/api/tasks/external/{external_task_id}/timeline")
    def get_external_task_timeline(external_task_id: str, request: Request) -> dict:
        return ok_response(
            "External robot task timeline loaded.",
            data=task_manager.external_task_timeline(external_task_id),
            request_id=request.state.request_id,
        )

    @app.get("/api/robot/tasks/external/{external_task_id}/timeline")
    def get_external_task_timeline_compat(external_task_id: str, request: Request) -> dict:
        return ok_response(
            "External robot task timeline loaded.",
            data=task_manager.external_task_timeline(external_task_id),
            request_id=request.state.request_id,
        )

    @app.get("/api/tasks/external/{external_task_id}/audit-log")
    def get_external_task_audit_log(external_task_id: str, request: Request, limit: int = Query(default=50, ge=1, le=500)) -> dict:
        return ok_response(
            "External robot task audit log loaded.",
            data=task_manager.external_task_audit_entries(external_task_id, limit),
            request_id=request.state.request_id,
        )

    @app.get("/api/robot/tasks/external/{external_task_id}/audit-log")
    def get_external_task_audit_log_compat(
        external_task_id: str,
        request: Request,
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict:
        return ok_response(
            "External robot task audit log loaded.",
            data=task_manager.external_task_audit_entries(external_task_id, limit),
            request_id=request.state.request_id,
        )

    @app.post("/api/tasks/external/{external_task_id}/feedback/replay")
    def replay_external_task_feedback(external_task_id: str, payload: ReplayFeedbackRequest, request: Request) -> dict:
        return ok_response(
            "External robot task feedback replay queued.",
            data=task_manager.replay_external_task_feedback(external_task_id, payload.callback_url),
            request_id=request.state.request_id,
        )

    @app.post("/api/robot/tasks/external/{external_task_id}/feedback/replay")
    def replay_external_task_feedback_compat(external_task_id: str, payload: ReplayFeedbackRequest, request: Request) -> dict:
        return ok_response(
            "External robot task feedback replay queued.",
            data=task_manager.replay_external_task_feedback(external_task_id, payload.callback_url),
            request_id=request.state.request_id,
        )

    @app.post("/api/tasks/external/{external_task_id}/voice-result")
    def record_external_task_voice_result(external_task_id: str, payload: VoiceResultRequest, request: Request) -> dict:
        task = task_manager.record_external_task_voice_result(external_task_id, payload.voice_result, payload.need_help)
        return ok_response("External robot voice result recorded.", data=task, request_id=request.state.request_id)

    @app.post("/api/robot/tasks/external/{external_task_id}/voice-result")
    def record_external_task_voice_result_compat(external_task_id: str, payload: VoiceResultRequest, request: Request) -> dict:
        task = task_manager.record_external_task_voice_result(external_task_id, payload.voice_result, payload.need_help)
        return ok_response("External robot voice result recorded.", data=task, request_id=request.state.request_id)

    @app.post("/api/tasks/external/{external_task_id}/cancel")
    def cancel_external_task(external_task_id: str, payload: CancelTaskRequest, request: Request) -> dict:
        task = task_manager.cancel_external_task(external_task_id, payload.reason)
        return ok_response("External robot task cancelled.", data=task, request_id=request.state.request_id)

    @app.post("/api/robot/tasks/external/{external_task_id}/cancel")
    def cancel_external_task_compat(external_task_id: str, payload: CancelTaskRequest, request: Request) -> dict:
        task = task_manager.cancel_external_task(external_task_id, payload.reason)
        return ok_response("External robot task cancelled.", data=task, request_id=request.state.request_id)

    @app.get("/api/robot/tasks/{task_id}")
    def get_task(task_id: str, request: Request) -> dict:
        return ok_response("Robot task loaded.", data=task_manager.get_task(task_id), request_id=request.state.request_id)

    @app.get("/api/tasks/{task_id}")
    def get_task_compat(task_id: str, request: Request) -> dict:
        return ok_response("Robot task loaded.", data=task_manager.get_task(task_id), request_id=request.state.request_id)

    @app.get("/api/tasks/{task_id}/status")
    def get_task_status(task_id: str, request: Request) -> dict:
        return ok_response("Robot task status loaded.", data=task_manager.task_status(task_id), request_id=request.state.request_id)

    @app.get("/api/robot/tasks/{task_id}/status")
    def get_task_status_compat(task_id: str, request: Request) -> dict:
        return ok_response("Robot task status loaded.", data=task_manager.task_status(task_id), request_id=request.state.request_id)

    @app.get("/api/tasks/{task_id}/result")
    def get_task_result(task_id: str, request: Request) -> dict:
        return ok_response("Robot task result loaded.", data=task_manager.task_result(task_id), request_id=request.state.request_id)

    @app.get("/api/robot/tasks/{task_id}/result")
    def get_task_result_compat(task_id: str, request: Request) -> dict:
        return ok_response("Robot task result loaded.", data=task_manager.task_result(task_id), request_id=request.state.request_id)

    @app.get("/api/tasks/{task_id}/timeline")
    def get_task_timeline(task_id: str, request: Request) -> dict:
        return ok_response("Robot task timeline loaded.", data=task_manager.task_timeline(task_id), request_id=request.state.request_id)

    @app.get("/api/robot/tasks/{task_id}/timeline")
    def get_task_timeline_compat(task_id: str, request: Request) -> dict:
        return ok_response("Robot task timeline loaded.", data=task_manager.task_timeline(task_id), request_id=request.state.request_id)

    @app.post("/api/tasks/{task_id}/feedback/replay")
    def replay_task_feedback(task_id: str, payload: ReplayFeedbackRequest, request: Request) -> dict:
        return ok_response(
            "Robot task feedback replay queued.",
            data=task_manager.replay_task_feedback(task_id, payload.callback_url),
            request_id=request.state.request_id,
        )

    @app.post("/api/robot/tasks/{task_id}/feedback/replay")
    def replay_task_feedback_compat(task_id: str, payload: ReplayFeedbackRequest, request: Request) -> dict:
        return ok_response(
            "Robot task feedback replay queued.",
            data=task_manager.replay_task_feedback(task_id, payload.callback_url),
            request_id=request.state.request_id,
        )

    @app.post("/api/robot/tasks/{task_id}/callbacks/replay")
    def replay_task_callbacks_compat(task_id: str, payload: ReplayFeedbackRequest, request: Request) -> dict:
        return ok_response(
            "Robot task callback replay queued.",
            data=task_manager.replay_task_feedback(task_id, payload.callback_url),
            request_id=request.state.request_id,
        )

    @app.get("/api/robot/tasks/{task_id}/callback-deliveries")
    def task_callback_deliveries(task_id: str, request: Request) -> dict:
        return ok_response(
            "Robot task callback deliveries loaded.",
            data=task_manager.callback_deliveries(task_id),
            request_id=request.state.request_id,
        )

    @app.post("/api/robot/tasks/{task_id}/elder-response")
    def record_elder_response(task_id: str, payload: ElderResponseRequest, request: Request) -> dict:
        task = task_manager.record_elder_response(task_id, payload.response_type, payload.transcript)
        return ok_response("Robot elder response recorded.", data=task, request_id=request.state.request_id)

    @app.get("/api/robot/tasks/{task_id}/evidence/arrival.jpg")
    def task_arrival_evidence(task_id: str) -> FileResponse:
        task_manager.get_task(task_id)
        return FileResponse(camera_service.evidence_file(task_id), media_type="image/jpeg")

    @app.post("/api/tasks/{task_id}/voice-result")
    def record_voice_result(task_id: str, payload: VoiceResultRequest, request: Request) -> dict:
        task = task_manager.record_voice_result(task_id, payload.voice_result, payload.need_help)
        return ok_response("Robot voice result recorded.", data=task, request_id=request.state.request_id)

    @app.post("/api/robot/tasks/{task_id}/voice-result")
    def record_voice_result_compat(task_id: str, payload: VoiceResultRequest, request: Request) -> dict:
        task = task_manager.record_voice_result(task_id, payload.voice_result, payload.need_help)
        return ok_response("Robot voice result recorded.", data=task, request_id=request.state.request_id)

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel_task(task_id: str, payload: CancelTaskRequest, request: Request) -> dict:
        task = task_manager.cancel_task(task_id, payload.reason)
        return ok_response("Robot task cancelled.", data=task, request_id=request.state.request_id)

    @app.post("/api/robot/tasks/{task_id}/cancel")
    def cancel_task_compat(task_id: str, payload: CancelTaskRequest, request: Request) -> dict:
        task = task_manager.cancel_task(task_id, payload.reason)
        return ok_response("Robot task cancelled.", data=task, request_id=request.state.request_id)

    @app.get("/api/voice/status")
    def voice_status(request: Request) -> dict:
        return ok_response("Robot voice status loaded.", data=voice_service.status(), request_id=request.state.request_id)

    @app.get("/api/camera/status")
    def camera_status(request: Request) -> dict:
        return ok_response("Robot camera status loaded.", data=camera_service.status(), request_id=request.state.request_id)

    @app.get("/api/camera/stream")
    def camera_stream(frames: int | None = Query(default=None, ge=1, le=300)) -> StreamingResponse:
        return StreamingResponse(
            camera_service.mjpeg_stream(frame_limit=frames),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    @app.get("/api/robot/camera/stream")
    def robot_camera_stream(frames: int | None = Query(default=None, ge=1, le=300)) -> StreamingResponse:
        return StreamingResponse(
            camera_service.mjpeg_stream(frame_limit=frames),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    @app.get("/api/camera/snapshot")
    def snapshot_compat() -> Response:
        return Response(content=camera_service.snapshot(), media_type="image/jpeg")

    @app.get("/api/robot/camera/snapshot")
    def snapshot() -> Response:
        return Response(content=camera_service.snapshot(), media_type="image/jpeg")
