from __future__ import annotations

import logging
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.core.errors import ErrorCode, GatewayError
from app.schemas.tasks import FallEventRequest, TargetMoveRequest
from app.services.camera_service import CameraService
from app.services.robot_service import RobotService


CONFIRM_FALL_STEPS = ["receive_event", "moving", "arrived", "robot_camera", "voice_check", "finished"]
MOVE_TO_TARGET_STEPS = ["receive_task", "moving", "arrived", "finished"]
TERMINAL_STATUSES = {"finished", "failed", "cancelled"}


LOCATION_MOTION_PLANS: dict[str, list[tuple[float, float, float, float]]] = {
    "bedroom": [(0.10, 0.0, 0.0, 0.20)],
    "bathroom": [(0.08, 0.0, 0.0, 0.20), (0.0, 0.0, 0.12, 0.20)],
    "living_room": [(0.12, 0.0, 0.0, 0.25)],
    "kitchen": [(0.08, 0.0, 0.0, 0.20), (0.0, 0.0, -0.12, 0.20)],
}
DEFAULT_MOTION_PLAN = [(0.08, 0.0, 0.0, 0.20)]


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


class RobotTaskService:
    def __init__(self, robot_service: RobotService, camera_service: CameraService) -> None:
        self.robot_service = robot_service
        self.camera_service = camera_service
        self.logger = logging.getLogger("go2_gateway.tasks")
        self._lock = threading.RLock()
        self._tasks: dict[str, dict[str, Any]] = {}

    def submit_fall_event(self, event: FallEventRequest) -> dict:
        self._ensure_no_active_task()
        task_id = self._new_task_id()
        task = self._build_task(
            task_id=task_id,
            task_name="confirm_fall",
            priority="high",
            location=event.location,
            steps=CONFIRM_FALL_STEPS,
            source={
                "event": event.event,
                "elderId": event.elder_id,
                "location": event.location,
                "confidence": event.confidence,
                "sourceEventId": event.source_event_id,
                "cameraId": event.camera_id,
            },
        )
        self._store(task)
        self._start_worker(task_id, self._run_confirm_fall)
        return self.get_task(task_id)

    def submit_target_move(self, request: TargetMoveRequest) -> dict:
        self._ensure_no_active_task()
        task_id = self._new_task_id()
        task = self._build_task(
            task_id=task_id,
            task_name=request.task,
            priority=request.priority,
            location=request.location,
            steps=MOVE_TO_TARGET_STEPS,
            source={"location": request.location},
        )
        self._store(task)
        self._start_worker(task_id, self._run_target_move)
        return self.get_task(task_id)

    def list_tasks(self) -> list[dict]:
        with self._lock:
            return [deepcopy(task) for task in sorted(self._tasks.values(), key=lambda item: item["createdAt"], reverse=True)]

    def get_task(self, task_id: str) -> dict:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise GatewayError(ErrorCode.TASK_NOT_FOUND, f"Task not found: {task_id}", 404)
            return deepcopy(task)

    def active_task(self) -> dict | None:
        with self._lock:
            for task in self._tasks.values():
                if task["status"] not in TERMINAL_STATUSES:
                    return deepcopy(task)
        return None

    def _run_confirm_fall(self, task_id: str) -> None:
        try:
            task = self.get_task(task_id)
            self._advance(task_id, "receive_event", "Fall event accepted.")
            self._move_to_location(task_id, task["location"])
            self._advance(task_id, "arrived", "Robot reached the first-stage target point.")
            self._capture_robot_view(task_id)
            self._advance(task_id, "voice_check", "Voice prompt sent.", {"voiceResult": "awaiting_response"})
            self._finish(task_id, {"confirm": "elder_present"})
        except Exception as exc:
            self._fail(task_id, exc)

    def _run_target_move(self, task_id: str) -> None:
        try:
            task = self.get_task(task_id)
            self._advance(task_id, "receive_task", "Move task accepted.")
            self._move_to_location(task_id, task["location"])
            self._advance(task_id, "arrived", "Robot reached the first-stage target point.")
            self._finish(task_id)
        except Exception as exc:
            self._fail(task_id, exc)

    def _move_to_location(self, task_id: str, location: str) -> None:
        self._advance(task_id, "moving", f"Moving toward location: {location}.")
        for vx, vy, wz, duration in self._motion_plan(location):
            self.robot_service.move(vx, vy, wz, duration, source=f"task:{task_id}")

    def _capture_robot_view(self, task_id: str) -> None:
        self._advance(task_id, "robot_camera", "Robot camera snapshot requested.")
        self.camera_service.snapshot()
        self._merge_result(task_id, {"robotCamera": {"streamUrl": "/api/robot/camera/snapshot", "snapshot": "available"}})

    def _advance(self, task_id: str, step: str, message: str, result: dict | None = None) -> None:
        with self._lock:
            task = self._tasks[task_id]
            task["status"] = "running"
            task["currentStep"] = step
            task["updatedAt"] = _now_iso()
            for item in task["steps"]:
                if item["name"] == step:
                    item["status"] = "done"
                    item["time"] = task["updatedAt"]
            task["events"].append({"time": task["updatedAt"], "step": step, "message": message})
            if result:
                task["result"].update(result)

    def _finish(self, task_id: str, result: dict | None = None) -> None:
        with self._lock:
            task = self._tasks[task_id]
            task["status"] = "finished"
            task["currentStep"] = "finished"
            task["updatedAt"] = _now_iso()
            for item in task["steps"]:
                if item["name"] == "finished":
                    item["status"] = "done"
                    item["time"] = task["updatedAt"]
            if result:
                task["result"].update(result)
            task["events"].append({"time": task["updatedAt"], "step": "finished", "message": "Task finished."})

    def _fail(self, task_id: str, exc: Exception) -> None:
        self.logger.exception("task failed task_id=%s", task_id)
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task["status"] = "failed"
            task["updatedAt"] = _now_iso()
            task["error"] = str(exc)
            task["events"].append({"time": task["updatedAt"], "step": task.get("currentStep"), "message": str(exc)})

    def _merge_result(self, task_id: str, result: dict) -> None:
        with self._lock:
            task = self._tasks[task_id]
            task["result"].update(result)
            task["updatedAt"] = _now_iso()

    def _ensure_no_active_task(self) -> None:
        active = self.active_task()
        if active:
            raise GatewayError(ErrorCode.CONTROL_BUSY, f"Robot task already running: {active['taskId']}", 409)

    def _build_task(self, task_id: str, task_name: str, priority: str, location: str, steps: list[str], source: dict) -> dict:
        now = _now_iso()
        return {
            "taskId": task_id,
            "robotId": self.robot_service.settings.robot_id,
            "task": task_name,
            "priority": priority,
            "status": "queued",
            "location": location,
            "currentStep": None,
            "step": steps,
            "steps": [{"name": step, "status": "pending", "time": None} for step in steps],
            "source": source,
            "result": {},
            "error": None,
            "events": [{"time": now, "step": "queued", "message": "Task queued."}],
            "createdAt": now,
            "updatedAt": now,
        }

    def _store(self, task: dict) -> None:
        with self._lock:
            self._tasks[task["taskId"]] = task

    def _start_worker(self, task_id: str, target) -> None:
        worker = threading.Thread(target=target, args=(task_id,), name=f"go2-task-{task_id}", daemon=True)
        worker.start()

    def _motion_plan(self, location: str) -> list[tuple[float, float, float, float]]:
        key = location.strip().lower().replace("-", "_").replace(" ", "_")
        return LOCATION_MOTION_PLANS.get(key, DEFAULT_MOTION_PLAN)

    def _new_task_id(self) -> str:
        return f"task_{uuid.uuid4().hex[:12]}"
