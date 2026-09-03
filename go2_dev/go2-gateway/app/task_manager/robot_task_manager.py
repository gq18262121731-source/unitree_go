from __future__ import annotations

from app.core.errors import ErrorCode, GatewayError
from app.schemas.tasks import FallEventRequest, TargetMoveRequest
from app.services.task_service import RobotTaskService


class RobotTaskManager:
    def __init__(self, task_service: RobotTaskService) -> None:
        self.task_service = task_service

    def create_confirm_fall_task(self, event: FallEventRequest) -> dict:
        return self.task_service.submit_fall_event(event)

    def create_target_move_task(self, request: TargetMoveRequest) -> dict:
        return self.task_service.submit_target_move(request)

    def create_follow_task(self, target: str) -> dict:
        raise GatewayError(ErrorCode.TASK_NOT_SUPPORTED, f"Follow task is reserved but not implemented: {target}", 501)

    def create_patrol_task(self, route: str) -> dict:
        raise GatewayError(ErrorCode.TASK_NOT_SUPPORTED, f"Patrol task is reserved but not implemented: {route}", 501)

    def list_tasks(self, limit: int | None = None) -> list[dict]:
        return self.task_service.list_tasks(limit)

    def list_task_summaries(self, limit: int = 50) -> list[dict]:
        return [self._task_summary(task) for task in self.task_service.list_tasks(limit)]

    def task_queue(self) -> dict:
        queued = [
            self._task_summary(task)
            for task in self.task_service.list_tasks()
            if not self._task_finished(task)
        ]
        queued.sort(key=lambda item: item.get("queue_position") or 0)
        return {
            "size": len(queued),
            "active": queued[0] if queued else None,
            "waiting": queued[1:] if len(queued) > 1 else [],
            "tasks": queued,
        }

    def latest_task_summary(self) -> dict:
        task = self.task_service.latest_task()
        if task is None:
            return {"exists": False, "task_id": None, "task": None, "status": "none"}
        latest = self._task_summary(task)
        latest["exists"] = True
        return latest

    def fall_event_status(self, source_event_id: str) -> dict:
        status = self.task_service.fall_event_status(source_event_id)
        task = status.get("task")
        if task is not None:
            status["task"] = self._task_summary(task)
        return status

    def external_task_status(self, external_task_id: str) -> dict:
        status = self.task_service.external_task_status(external_task_id)
        task = status.get("task")
        if task is not None:
            status["task"] = self._task_summary(task)
        return status

    def locations(self) -> dict:
        return self.task_service.location_plans()

    def resolve_location(self, location: str) -> dict:
        return self.task_service.resolve_location(location)

    def audit_entries(self, limit: int = 50) -> dict:
        return self.task_service.audit_entries(limit)

    def task_audit_entries(self, task_id: str, limit: int = 50) -> dict:
        return self.task_service.audit_entries_for_task(task_id, limit)

    def external_task_audit_entries(self, external_task_id: str, limit: int = 50) -> dict:
        return self.task_service.audit_entries_for_external_task(external_task_id, limit)

    def get_task(self, task_id: str) -> dict:
        return self.task_service.get_task(task_id)

    def task_status(self, task_id: str) -> dict:
        task = self.get_task(task_id)
        return self._task_summary(task)

    def external_task_status_detail(self, external_task_id: str) -> dict:
        return self.task_status(self._task_id_for_external_task(external_task_id))

    def task_timeline(self, task_id: str) -> dict:
        task = self.get_task(task_id)
        return {
            "task_id": task.get("task_id") or task.get("taskId"),
            "task": task.get("task"),
            "status": task.get("status"),
            "status_v2": task.get("statusV2"),
            "revision": task.get("revision"),
            "finished": self._task_finished(task),
            **self._source_fields(task),
            **self._queue_fields(task),
            "current_step": task.get("current_step"),
            "legacy_step": task.get("currentStep"),
            "progress": self._task_progress(task),
            "steps": task.get("steps", []),
            "events": task.get("events", []),
            "updated_at": task.get("updatedAt"),
        }

    def external_task_timeline(self, external_task_id: str) -> dict:
        timeline = self.task_timeline(self._task_id_for_external_task(external_task_id))
        timeline["external_task_id"] = external_task_id
        return timeline

    def task_result(self, task_id: str) -> dict:
        task = self.get_task(task_id)
        result = task.get("result", {})
        source = task.get("source") or {}
        return {
            "task_id": task.get("task_id") or task.get("taskId"),
            "task": task.get("task"),
            "status": task.get("status"),
            "status_v2": task.get("statusV2"),
            "revision": task.get("revision"),
            "finished": self._task_finished(task),
            **self._source_fields(task),
            **self._queue_fields(task),
            "progress": self._task_progress(task),
            "camera": task.get("camera"),
            "voice": task.get("voice"),
            "confirm": result.get("confirm"),
            "robot_camera": result.get("robotCamera"),
            "voice_result": result.get("voiceResult"),
            "voice_delivery": result.get("voiceDelivery"),
            "voice_prompt_url": result.get("voicePromptUrl"),
            "voice_error": result.get("voiceError"),
            "need_help": result.get("needHelp"),
            "outcome": result.get("outcome"),
            "observation": result.get("observation"),
            "elder_response": result.get("elderResponse"),
            "location_resolution": result.get("locationResolution") or source.get("locationResolution"),
            "error_code": result.get("errorCode"),
            "failure_step": result.get("failureStep"),
            "source": task.get("source"),
            "error": task.get("error"),
            "finished": self._task_finished(task),
            "updated_at": task.get("updatedAt"),
        }

    def external_task_result(self, external_task_id: str) -> dict:
        result = self.task_result(self._task_id_for_external_task(external_task_id))
        result["external_task_id"] = external_task_id
        return result

    def active_task_status(self) -> dict:
        task = self.active_task()
        if task is None:
            return {"active": False, "task_id": None, "task": None, "status": "idle"}
        summary = self._task_summary(task)
        summary["active"] = True
        return summary

    def _task_summary(self, task: dict) -> dict:
        return {
            "task_id": task.get("task_id") or task.get("taskId"),
            "task": task.get("task"),
            "status": task.get("status"),
            "status_v2": task.get("statusV2"),
            "revision": task.get("revision"),
            "finished": self._task_finished(task),
            **self._source_fields(task),
            **self._queue_fields(task),
            "step": task.get("currentStep"),
            "current_step": task.get("current_step"),
            "steps": task.get("steps", []),
            "progress": self._task_progress(task),
            "camera": task.get("camera"),
            "voice": task.get("voice"),
            "source": task.get("source"),
            "result": task.get("result"),
            "error": task.get("error"),
            "updated_at": task.get("updatedAt"),
        }

    def _source_fields(self, task: dict) -> dict:
        source = task.get("source") or {}
        return {
            "elder_id": source.get("elderId"),
            "location": task.get("location") or source.get("location"),
            "confidence": source.get("confidence"),
            "source_event_id": source.get("sourceEventId"),
            "camera_id": source.get("cameraId"),
            "external_task_id": source.get("externalTaskId"),
            "location_resolution": source.get("locationResolution") or task.get("result", {}).get("locationResolution"),
        }

    def _queue_fields(self, task: dict) -> dict:
        queue = task.get("queue") or {}
        return {
            "queue_position": queue.get("position", task.get("queuePosition")),
            "queue_size": queue.get("size", task.get("queueSize")),
            "queue_head": queue.get("head", task.get("queueHead")),
            "blocked_by_task_id": queue.get("blockedByTaskId", task.get("blockedByTaskId")),
            "queue": queue,
        }

    def _task_progress(self, task: dict) -> dict:
        steps = task.get("steps") or []
        total_steps = len(steps)
        completed_steps = sum(1 for step in steps if step.get("status") == "done")
        current_step = task.get("currentStep")
        current_index = None
        for index, step in enumerate(steps, start=1):
            if step.get("name") == current_step:
                current_index = index
                break
        percent = int(round((completed_steps / total_steps) * 100)) if total_steps else 0
        return {
            "completed_steps": completed_steps,
            "total_steps": total_steps,
            "current_index": current_index,
            "percent": percent,
        }

    def _task_finished(self, task: dict) -> bool:
        return task.get("status") in {"finished", "failed", "cancelled", "BLOCKED", "BLOCKED_ROBOT_OFFLINE"}

    def record_voice_result(self, task_id: str, voice_result: str, need_help: bool | None = None) -> dict:
        return self.task_service.record_voice_result(task_id, voice_result, need_help)

    def record_elder_response(self, task_id: str, response_type: str, transcript: str | None = None) -> dict:
        return self.task_service.record_elder_response(task_id, response_type, transcript)

    def record_external_task_voice_result(
        self,
        external_task_id: str,
        voice_result: str,
        need_help: bool | None = None,
    ) -> dict:
        return self.record_voice_result(self._task_id_for_external_task(external_task_id), voice_result, need_help)

    def replay_task_feedback(self, task_id: str, callback_url: str | None = None) -> dict:
        return self.task_service.replay_task_feedback(task_id, callback_url)

    def callback_deliveries(self, task_id: str) -> dict:
        self.get_task(task_id)
        return self.task_service.callback_deliveries(task_id)

    def replay_external_task_feedback(self, external_task_id: str, callback_url: str | None = None) -> dict:
        return self.task_service.replay_external_task_feedback(external_task_id, callback_url)

    def cancel_task(self, task_id: str, reason: str) -> dict:
        return self.task_service.cancel_task(task_id, reason)

    def cancel_external_task(self, external_task_id: str, reason: str) -> dict:
        return self.cancel_task(self._task_id_for_external_task(external_task_id), reason)

    def active_task(self) -> dict | None:
        return self.task_service.active_task()

    def _task_id_for_external_task(self, external_task_id: str) -> str:
        status = self.task_service.external_task_status(external_task_id)
        task_id = status.get("task_id")
        if not task_id:
            raise GatewayError(ErrorCode.TASK_NOT_FOUND, f"External task not found: {external_task_id}", 404)
        return task_id
