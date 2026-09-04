from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import Settings
from app.core.errors import ErrorCode, GatewayError
from app.schemas.tasks import FallEventRequest, TargetMoveRequest
from app.services.camera_service import CameraService
from app.services.feedback_service import HealthNewFeedbackService
from app.services.robot_service import RobotService
from app.services.voice_service import VoiceService


CONFIRM_FALL_LEGACY_STEPS = ["receive_event", "moving", "arrived", "robot_camera", "voice_check", "finished"]
CONFIRM_FALL_STEPS = [
    "RECEIVED",
    "PREFLIGHT",
    "MOVING",
    "ARRIVED",
    "CAMERA_CHECK",
    "VOICE_PROMPT",
    "WAITING_RESPONSE",
    "REPORTING",
]
MOVE_TO_TARGET_STEPS = ["receive_task", "moving", "arrived", "finished"]
TERMINAL_STATUSES = {"finished", "failed", "cancelled"}
BLOCKED_STATUSES = {"BLOCKED", "BLOCKED_ROBOT_OFFLINE"}
CONFIRM_FALL_OUTCOMES = {"SAFE", "NEED_HELP", "NO_RESPONSE", "UNKNOWN"}

UNIFIED_STEP_TO_LEGACY = {
    "RECEIVED": "receive_event",
    "PREFLIGHT": "receive_event",
    "MOVING": "moving",
    "ARRIVED": "arrived",
    "CAMERA_CHECK": "robot_camera",
    "VOICE_PROMPT": "voice_check",
    "WAITING_RESPONSE": "voice_check",
    "REPORTING": "finished",
}


LOCATION_MOTION_PLANS: dict[str, list[tuple[float, float, float, float]]] = {
    "bedroom": [(0.10, 0.0, 0.0, 0.20)],
    "bathroom": [(0.08, 0.0, 0.0, 0.20), (0.0, 0.0, 0.12, 0.20)],
    "living_room": [(0.12, 0.0, 0.0, 0.25)],
    "kitchen": [(0.08, 0.0, 0.0, 0.20), (0.0, 0.0, -0.12, 0.20)],
}
LOCATION_ALIASES: dict[str, str] = {
    "卧室": "bedroom",
    "主卧": "bedroom",
    "老人卧室": "bedroom",
    "bed_room": "bedroom",
    "卫生间": "bathroom",
    "洗手间": "bathroom",
    "浴室": "bathroom",
    "厕所": "bathroom",
    "客厅": "living_room",
    "起居室": "living_room",
    "livingroom": "living_room",
    "living-room": "living_room",
    "厨房": "kitchen",
}
DEFAULT_MOTION_PLAN = [(0.08, 0.0, 0.0, 0.20)]


class _TaskCancelled(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


class RobotTaskService:
    def __init__(
        self,
        robot_service: RobotService,
        camera_service: CameraService,
        voice_service: VoiceService,
        feedback_service: HealthNewFeedbackService,
        settings: Settings,
    ) -> None:
        self.robot_service = robot_service
        self.camera_service = camera_service
        self.voice_service = voice_service
        self.feedback_service = feedback_service
        self.settings = settings
        self.logger = logging.getLogger("go2_gateway.tasks")
        self._lock = threading.RLock()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._fall_event_index: dict[str, str] = {}
        self._external_task_index: dict[str, str] = {}
        self._workers: dict[str, threading.Thread] = {}
        self._response_events: dict[str, threading.Event] = {}
        self._audit_restored = False
        self._task_sequence = 0

    def submit_fall_event(self, event: FallEventRequest) -> dict:
        event_key = self._fall_event_key(event)
        if event_key:
            existing = self._task_id_for_fall_event(event_key)
            if existing:
                changed = False
                if self._attach_event_callback_if_needed(existing, event.callback_url):
                    changed = True
                    self._audit_task(existing, "callback_attached")
                if self._attach_external_task_id_if_needed(existing, event.external_task_id):
                    changed = True
                    self._audit_task(existing, "external_task_attached")
                if changed:
                    self._notify_task(existing)
                return self.get_task(existing)
        external_task_key = self._external_task_key(event.external_task_id)
        if external_task_key:
            existing = self._task_id_for_external_task(external_task_key)
            if existing:
                if self._attach_event_callback_if_needed(existing, event.callback_url):
                    self._notify_task(existing)
                    self._audit_task(existing, "callback_attached")
                return self.get_task(existing)
        task_id = self._new_task_id()
        location_resolution = self._location_resolution_payload(event.location)
        blocked_error = self._fall_event_blocking_error(location_resolution)
        task = self._build_task(
            task_id=task_id,
            task_name="confirm_fall",
            priority="high",
            location=event.location,
            steps=CONFIRM_FALL_LEGACY_STEPS,
            workflow_steps=CONFIRM_FALL_STEPS,
            source={
                "event": event.event,
                "elderId": event.elder_id,
                "location": event.location,
                "confidence": event.confidence,
                "sourceEventId": event.source_event_id,
                "cameraId": event.camera_id,
                "externalTaskId": event.external_task_id,
                "callbackUrl": event.callback_url,
                "locationResolution": location_resolution,
                "traceId": self._new_trace_id(),
            },
            result={
                "locationResolution": location_resolution,
                "outcome": None,
                "observation": {
                    "snapshot_url": None,
                    "camera_available": None,
                    "voice_available": None,
                    "response_type": None,
                    "transcript": None,
                },
            },
        )
        self._store(task)
        if event_key:
            self._index_fall_event(event_key, task_id)
        if external_task_key:
            self._index_external_task(external_task_key, task_id)
        if blocked_error is not None:
            self._block_task_robot_offline(task_id, blocked_error)
            return self.get_task(task_id)
        self._notify_task(task_id)
        self._start_worker(task_id, self._run_confirm_fall)
        return self.get_task(task_id)

    def submit_target_move(self, request: TargetMoveRequest) -> dict:
        self.robot_service.ensure_ready_for_task_acceptance()
        task_id = self._new_task_id()
        location_resolution = self._location_resolution_payload(request.location)
        task = self._build_task(
            task_id=task_id,
            task_name=request.task,
            priority=request.priority,
            location=request.location,
            steps=MOVE_TO_TARGET_STEPS,
            source={"location": request.location, "locationResolution": location_resolution},
            result={"locationResolution": location_resolution},
        )
        self._store(task)
        self._notify_task(task_id)
        self._start_worker(task_id, self._run_target_move)
        return self.get_task(task_id)

    def list_tasks(self, limit: int | None = None) -> list[dict]:
        with self._lock:
            tasks = sorted(
                self._tasks.values(),
                key=lambda item: (item["createdAt"], self._queue_sequence(item)),
                reverse=True,
            )
            if limit is not None:
                tasks = tasks[:limit]
            return [self._copy_task_for_response(task) for task in tasks]

    def latest_task(self) -> dict | None:
        with self._lock:
            if not self._tasks:
                return None
            task = max(self._tasks.values(), key=lambda item: item["updatedAt"])
            return self._copy_task_for_response(task)

    def fall_event_status(self, source_event_id: str) -> dict:
        event_key = source_event_id.strip()
        with self._lock:
            task_id = self._fall_event_index.get(event_key)
            task = self._copy_task_for_response(self._tasks[task_id]) if task_id and task_id in self._tasks else None
        return {
            "source_event_id": source_event_id,
            "received": task is not None,
            "task_id": task_id if task is not None else None,
            "task": task,
        }

    def external_task_status(self, external_task_id: str) -> dict:
        external_key = external_task_id.strip()
        with self._lock:
            task_id = self._external_task_index.get(external_key)
            task = self._copy_task_for_response(self._tasks[task_id]) if task_id and task_id in self._tasks else None
            if task is None:
                for candidate in self._tasks.values():
                    source = candidate.get("source") or {}
                    if source.get("externalTaskId") == external_key:
                        task_id = candidate.get("task_id") or candidate.get("taskId")
                        task = self._copy_task_for_response(candidate)
                        if task_id:
                            self._external_task_index[external_key] = task_id
                        break
        return {
            "external_task_id": external_task_id,
            "received": task is not None,
            "task_id": task_id if task is not None else None,
            "task": task,
        }

    def get_task(self, task_id: str) -> dict:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise GatewayError(ErrorCode.TASK_NOT_FOUND, f"Task not found: {task_id}", 404)
            return self._copy_task_for_response(task)

    def replay_task_feedback(self, task_id: str, callback_url: str | None = None) -> dict:
        task = self.get_task(task_id)
        configured_callback_url = callback_url or self.feedback_service.callback_url_for(task)
        queued = self.feedback_service.publish_task_update_to(task, callback_url)
        return {
            "task_id": task.get("task_id") or task.get("taskId"),
            "queued": queued,
            "callback_configured": bool(configured_callback_url),
            "callback_url": configured_callback_url or None,
            "revision": task.get("revision"),
            "status": task.get("status"),
            "step": task.get("currentStep"),
            "feedback": self.feedback_service.status(),
        }

    def replay_external_task_feedback(self, external_task_id: str, callback_url: str | None = None) -> dict:
        external_status = self.external_task_status(external_task_id)
        task_id = external_status.get("task_id")
        if not task_id:
            raise GatewayError(ErrorCode.TASK_NOT_FOUND, f"External task not found: {external_task_id}", 404)
        replay = self.replay_task_feedback(task_id, callback_url)
        replay["external_task_id"] = external_task_id
        return replay

    def callback_deliveries(self, task_id: str) -> dict:
        self.get_task(task_id)
        return self.feedback_service.deliveries_for_task(task_id)

    def record_voice_result(self, task_id: str, voice_result: str, need_help: bool | None = None) -> dict:
        if need_help is True:
            response_type = "NEED_HELP"
        elif need_help is False:
            response_type = "SAFE"
        else:
            response_type = "UNKNOWN"
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise GatewayError(ErrorCode.TASK_NOT_FOUND, f"Task not found: {task_id}", 404)
            if task["task"] != "confirm_fall":
                raise GatewayError(
                    ErrorCode.TASK_STATE_CONFLICT,
                    f"Voice result can only be recorded for confirm_fall task: {task_id}",
                    409,
                )
            if task["status"] in {"cancelled", "failed"}:
                raise GatewayError(
                    ErrorCode.TASK_STATE_CONFLICT,
                    f"Voice result cannot be recorded for {task['status']} task: {task_id}",
                    409,
                )
            task["voice"] = "completed"
            current_result = task.get("result") or {}
            response = self._elder_response_result(response_type, voice_result, source="manual")
            response["voiceResult"] = voice_result
            if current_result.get("confirm") is not None:
                response["confirm"] = current_result["confirm"]
            self._merge_task_result_unlocked(task, response)
            self._touch_task(task)
            task["events"].append(
                {
                    "time": task["updatedAt"],
                    "step": "voice_result",
                    "stepV2": task.get("current_step"),
                    "message": "Voice result recorded.",
                }
            )
            response_event = self._response_events.get(task_id)
            copied = self._copy_task_for_response(task)
        if response_event:
            response_event.set()
        self._notify_task(task_id)
        self._audit_task(task_id, "voice_result")
        return copied

    def record_elder_response(self, task_id: str, response_type: str, transcript: str | None = None) -> dict:
        response_type = response_type.strip().upper()
        if response_type not in {"SAFE", "NEED_HELP", "UNKNOWN"}:
            raise GatewayError(ErrorCode.INVALID_REQUEST, f"Unsupported elder response type: {response_type}", 422)
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise GatewayError(ErrorCode.TASK_NOT_FOUND, f"Task not found: {task_id}", 404)
            if task["task"] != "confirm_fall":
                raise GatewayError(
                    ErrorCode.TASK_STATE_CONFLICT,
                    f"Voice result can only be recorded for confirm_fall task: {task_id}",
                    409,
                )
            existing = task.get("result", {}).get("elderResponse")
            if task["status"] in TERMINAL_STATUSES or task.get("statusV2") in {"COMPLETED", "FAILED", "CANCELLED", "BLOCKED"}:
                if existing and existing.get("responseType") == response_type and (existing.get("transcript") or None) == (transcript or None):
                    return self._copy_task_for_response(task)
                raise GatewayError(
                    ErrorCode.TASK_STATE_CONFLICT,
                    f"Voice result cannot be recorded for {task['status']} task: {task_id}",
                    409,
                )
            if task.get("current_step") != "WAITING_RESPONSE":
                raise GatewayError(
                    ErrorCode.TASK_STATE_CONFLICT,
                    f"Elder response can only be recorded while task is WAITING_RESPONSE: {task_id}",
                    409,
                )
            if existing:
                if existing.get("responseType") == response_type and (existing.get("transcript") or None) == (transcript or None):
                    return self._copy_task_for_response(task)
                raise GatewayError(
                    ErrorCode.TASK_STATE_CONFLICT,
                    f"Elder response is already recorded for task: {task_id}",
                    409,
                )
            task["voice"] = "completed"
            response = self._elder_response_result(response_type, transcript, source="manual")
            self._merge_task_result_unlocked(task, response)
            self._touch_task(task)
            task["events"].append(
                {
                    "time": task["updatedAt"],
                    "step": "voice_result",
                    "stepV2": "WAITING_RESPONSE",
                    "message": "Elder response recorded.",
                }
            )
            response_event = self._response_events.get(task_id)
            copied = self._copy_task_for_response(task)
        if response_event:
            response_event.set()
        self._notify_task(task_id)
        self._audit_task(task_id, "elder_response")
        return copied

    def cancel_task(self, task_id: str, reason: str = "cancelled_by_request") -> dict:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise GatewayError(ErrorCode.TASK_NOT_FOUND, f"Task not found: {task_id}", 404)
            if task["status"] in TERMINAL_STATUSES:
                return deepcopy(task)
            task["status"] = "cancelled"
            task["statusV2"] = "CANCELLED"
            task["currentStep"] = "cancelled"
            task["current_step"] = "CANCELLED"
            self._touch_task(task)
            task["error"] = reason
            task["events"].append({"time": task["updatedAt"], "step": "cancelled", "stepV2": "CANCELLED", "message": reason})
        try:
            self.robot_service.safe_stop(source=f"cancel:{task_id}")
        except Exception as exc:
            self.logger.warning("stop after task cancel failed task_id=%s error=%s", task_id, exc)
        self._notify_task(task_id)
        self._audit_task(task_id, "cancelled")
        return self.get_task(task_id)

    def cancel_active_tasks(self, reason: str = "gateway_shutdown", wait_timeout_seconds: float = 5.0) -> list[dict]:
        with self._lock:
            task_ids = [
                task["taskId"]
                for task in self._tasks.values()
                if task["status"] not in TERMINAL_STATUSES
            ]
        cancelled = []
        for task_id in task_ids:
            cancelled.append(self.cancel_task(task_id, reason))
        self.wait_for_workers(wait_timeout_seconds)
        return cancelled

    def wait_for_workers(self, timeout_seconds: float = 5.0) -> list[str]:
        deadline = time.monotonic() + timeout_seconds
        current = threading.current_thread()
        while True:
            with self._lock:
                workers = [
                    (task_id, worker)
                    for task_id, worker in self._workers.items()
                    if worker is not current and worker.is_alive()
                ]
            if not workers:
                return []
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return [task_id for task_id, worker in workers if worker.is_alive()]
            for _, worker in workers:
                worker.join(timeout=min(0.05, remaining))

    def worker_count(self) -> int:
        with self._lock:
            return sum(1 for worker in self._workers.values() if worker.is_alive())

    def active_task(self) -> dict | None:
        with self._lock:
            pending = self._pending_tasks_unlocked()
            if pending:
                return self._copy_task_for_response(pending[0])
        return None

    def location_plans(self) -> dict:
        configured = self._configured_motion_plans()
        locations = {}
        for location, plan in LOCATION_MOTION_PLANS.items():
            locations[location] = {"source": "default", "plan": self._serialize_motion_plan(plan)}
        for location, plan in configured.items():
            locations[location] = {"source": "configured", "plan": self._serialize_motion_plan(plan)}
        aliases_by_location: dict[str, list[str]] = {}
        for alias, location in LOCATION_ALIASES.items():
            aliases_by_location.setdefault(location, []).append(alias)
        return {
            "locations": [
                {"location": location, "aliases": sorted(aliases_by_location.get(location, [])), **detail}
                for location, detail in sorted(locations.items(), key=lambda item: item[0])
            ],
            "aliases": [
                {"alias": alias, "location": location}
                for alias, location in sorted(LOCATION_ALIASES.items(), key=lambda item: item[0])
            ],
            "fallback": {
                "enabled": True,
                "source": "default",
                "plan": self._serialize_motion_plan(DEFAULT_MOTION_PLAN),
            },
        }

    def resolve_location(self, location: str) -> dict:
        key = self._location_key(location)
        configured = self._configured_motion_plan(key)
        if configured:
            return {
                "input": location,
                "location": key,
                "known": True,
                "fallback_used": False,
                "source": "configured",
                "plan": self._serialize_motion_plan(configured),
            }
        default_plan = LOCATION_MOTION_PLANS.get(key)
        if default_plan:
            return {
                "input": location,
                "location": key,
                "known": True,
                "fallback_used": False,
                "source": "default",
                "plan": self._serialize_motion_plan(default_plan),
            }
        return {
            "input": location,
            "location": key,
            "known": False,
            "fallback_used": True,
            "source": "fallback",
            "plan": self._serialize_motion_plan(DEFAULT_MOTION_PLAN),
        }

    def audit_entries(self, limit: int = 50) -> dict:
        entries = self._read_audit_entries(limit)
        return {
            "enabled": self.settings.task_audit_enabled,
            "path": self.settings.task_audit_log_path,
            "entries": entries,
        }

    def audit_entries_for_task(self, task_id: str, limit: int = 50) -> dict:
        entries = [
            entry
            for entry in self._read_audit_entries()
            if self._audit_entry_task_id(entry) == task_id
        ]
        return {
            "enabled": self.settings.task_audit_enabled,
            "path": self.settings.task_audit_log_path,
            "task_id": task_id,
            "entries": entries[-limit:],
        }

    def audit_entries_for_external_task(self, external_task_id: str, limit: int = 50) -> dict:
        entries = [
            entry
            for entry in self._read_audit_entries()
            if self._audit_entry_external_task_id(entry) == external_task_id
        ]
        task_id = self._audit_entry_task_id(entries[-1]) if entries else None
        return {
            "enabled": self.settings.task_audit_enabled,
            "path": self.settings.task_audit_log_path,
            "external_task_id": external_task_id,
            "task_id": task_id,
            "entries": entries[-limit:],
        }

    def restore_terminal_tasks_from_audit(self) -> int:
        return self._restore_terminal_tasks_from_audit()

    def _run_confirm_fall(self, task_id: str) -> None:
        try:
            self._wait_until_ready_for_dispatch(task_id)
            task = self.get_task(task_id)
            self._raise_if_cancelled(task_id)
            self._advance(task_id, "RECEIVED", "Fall event accepted.")
            self._raise_if_cancelled(task_id)
            self._advance(task_id, "PREFLIGHT", "Confirm fall preflight passed.")
            self._move_to_location(task_id, task["location"])
            self._raise_if_cancelled(task_id)
            self._advance(task_id, "ARRIVED", "Robot reached the first-stage target point.")
            self._capture_robot_view(task_id)
            self._raise_if_cancelled(task_id)
            voice_result = self.voice_service.ask_elder_status(task_id, task.get("source", {}).get("elderId"))
            self._advance(task_id, "VOICE_PROMPT", "Voice prompt sent.", voice_result)
            self._raise_if_cancelled(task_id)
            response = self._wait_for_elder_response(task_id)
            self._raise_if_cancelled(task_id)
            self._advance(task_id, "REPORTING", "Confirm fall result reporting.", response)
            self._finish(task_id, self._final_confirm_fall_result(task_id))
        except _TaskCancelled:
            return
        except Exception as exc:
            self._fail(task_id, exc)

    def _run_target_move(self, task_id: str) -> None:
        try:
            self._wait_until_ready_for_dispatch(task_id)
            task = self.get_task(task_id)
            self._raise_if_cancelled(task_id)
            self._advance(task_id, "receive_task", "Move task accepted.")
            self._move_to_location(task_id, task["location"])
            self._raise_if_cancelled(task_id)
            self._advance(task_id, "arrived", "Robot reached the first-stage target point.")
            self._finish(task_id)
        except _TaskCancelled:
            return
        except Exception as exc:
            self._fail(task_id, exc)

    def _move_to_location(self, task_id: str, location: str) -> None:
        self._raise_if_cancelled(task_id)
        self._advance(task_id, "MOVING", f"Moving toward location: {location}.")
        for vx, vy, wz, duration in self._motion_plan(location):
            self._raise_if_cancelled(task_id)
            self.robot_service.move(vx, vy, wz, duration, source=f"task:{task_id}")
            self._raise_if_cancelled(task_id)

    def _capture_robot_view(self, task_id: str) -> None:
        self._raise_if_cancelled(task_id)
        self._advance(task_id, "CAMERA_CHECK", "Robot camera snapshot requested.")
        try:
            evidence = self.camera_service.save_task_evidence(task_id)
        except Exception as exc:
            self._merge_result(
                task_id,
                {
                    "camera": "failed",
                    "robotCamera": {
                        "streamUrl": self.settings.camera_stream_url,
                        "snapshotUrl": None,
                        "snapshot": "failed",
                        "cameraAvailable": False,
                        "error": str(exc),
                    },
                    "cameraError": str(exc),
                    "observation": {
                        **self._observation(task_id),
                        "snapshot_url": None,
                        "camera_available": False,
                    },
                },
            )
            return
        self._raise_if_cancelled(task_id)
        camera_status = self.camera_service.status()
        self._merge_result(
            task_id,
            {
                "camera": camera_status["camera"],
                "robotCamera": {
                    "streamUrl": camera_status["stream_url"],
                    "snapshotUrl": self.settings.camera_snapshot_url,
                    "snapshot": "available",
                    "capturedAt": evidence["captured_at"],
                    "cameraAvailable": True,
                    "evidencePath": evidence["evidence_path"],
                    "source": evidence["source"],
                },
                "observation": {
                    **self._observation(task_id),
                    "snapshot_url": evidence["snapshot_url"],
                    "camera_available": True,
                },
            },
        )

    def _wait_for_elder_response(self, task_id: str) -> dict:
        self._raise_if_cancelled(task_id)
        response_event = threading.Event()
        with self._lock:
            self._response_events[task_id] = response_event
        self._advance(task_id, "WAITING_RESPONSE", "Waiting for elder response.")
        self._maybe_inject_mock_elder_response(task_id)
        deadline = time.monotonic() + max(0.0, self.settings.elder_response_timeout_seconds)
        try:
            with self._lock:
                task = self._tasks[task_id]
                response = task.get("result", {}).get("elderResponse")
                task_result = task.get("result") or {}
                current_voice = "failed" if task.get("voice") == "failed" or task_result.get("voiceDelivery") == "failed" else task.get("voice")
            if response:
                existing_result = self._elder_response_result(
                    response.get("responseType", "UNKNOWN"),
                    response.get("transcript"),
                    source=response.get("source", "manual"),
                )
                if response.get("transcript"):
                    existing_result["voiceResult"] = response["transcript"]
                if current_voice == "failed":
                    existing_result["voice"] = "failed"
                return existing_result
            if self.settings.mode == "mock" and (self.settings.mock_confirm_fall_outcome or "").strip().upper() == "NO_RESPONSE":
                result = self._elder_response_result("NO_RESPONSE", None, source="mock")
                if current_voice == "failed":
                    result["voice"] = "failed"
                self._merge_result(task_id, result)
                return result
            while time.monotonic() < deadline:
                self._raise_if_cancelled(task_id)
                with self._lock:
                    task = self._tasks[task_id]
                    response = task.get("result", {}).get("elderResponse")
                    if response:
                        existing_result = self._elder_response_result(
                            response.get("responseType", "UNKNOWN"),
                            response.get("transcript"),
                            source=response.get("source", "manual"),
                        )
                        if response.get("transcript"):
                            existing_result["voiceResult"] = response["transcript"]
                        if task.get("voice") == "failed":
                            existing_result["voice"] = "failed"
                        return existing_result
                response_event.wait(timeout=min(0.05, max(0.0, deadline - time.monotonic())))
            result = self._elder_response_result("NO_RESPONSE", None, source="timeout")
            with self._lock:
                task_result = self._tasks[task_id].get("result") or {}
                if self._tasks[task_id].get("voice") == "failed" or task_result.get("voiceDelivery") == "failed":
                    result["voice"] = "failed"
            self._merge_result(task_id, result)
            return result
        finally:
            with self._lock:
                self._response_events.pop(task_id, None)

    def _maybe_inject_mock_elder_response(self, task_id: str) -> None:
        if self.settings.mode != "mock":
            return
        outcome = (self.settings.mock_confirm_fall_outcome or "").strip().upper()
        if outcome in {"", "NO_RESPONSE"}:
            return
        if outcome not in {"SAFE", "NEED_HELP", "UNKNOWN"}:
            outcome = "UNKNOWN"
        try:
            self.record_elder_response(task_id, outcome, transcript=f"mock:{outcome.lower()}")
        except GatewayError:
            self.logger.warning("mock elder response injection skipped task_id=%s outcome=%s", task_id, outcome)

    def _elder_response_result(self, response_type: str, transcript: str | None, source: str) -> dict:
        response_type = response_type.strip().upper()
        if response_type not in CONFIRM_FALL_OUTCOMES:
            response_type = "UNKNOWN"
        no_response = response_type == "NO_RESPONSE"
        return {
            "voice": "waiting" if no_response else "completed",
            "voiceResult": "awaiting_response" if no_response else response_type,
            "needHelp": response_type == "NEED_HELP",
            "confirm": self._confirm_value_for_outcome(response_type),
            "outcome": response_type,
            "elderResponse": {
                "responseType": response_type,
                "transcript": transcript,
                "source": source,
                "recordedAt": _now_iso(),
            },
            "observation": {
                **self._observation_from_result({}),
                "response_type": response_type,
                "transcript": transcript,
                "voice_available": response_type != "NO_RESPONSE",
            },
        }

    def _final_confirm_fall_result(self, task_id: str) -> dict:
        with self._lock:
            result = deepcopy(self._tasks[task_id].get("result") or {})
        outcome = result.get("outcome") or "UNKNOWN"
        observation = self._observation_from_result(result)
        return {
            "outcome": outcome,
            "confirm": self._confirm_value_for_outcome(outcome),
            "observation": observation,
        }

    def _confirm_value_for_outcome(self, outcome: str) -> str:
        return {
            "SAFE": "elder_safe",
            "NEED_HELP": "need_help",
            "NO_RESPONSE": "elder_present",
            "UNKNOWN": "unknown",
        }.get(outcome, "unknown")

    def _observation(self, task_id: str) -> dict:
        with self._lock:
            result = deepcopy((self._tasks.get(task_id) or {}).get("result") or {})
        return self._observation_from_result(result)

    def _observation_from_result(self, result: dict) -> dict:
        observation = deepcopy(result.get("observation") or {})
        robot_camera = result.get("robotCamera") or {}
        elder_response = result.get("elderResponse") or {}
        return {
            "snapshot_url": observation.get("snapshot_url") or robot_camera.get("snapshotUrl"),
            "camera_available": observation.get("camera_available", robot_camera.get("cameraAvailable")),
            "voice_available": observation.get("voice_available"),
            "response_type": observation.get("response_type") or elder_response.get("responseType"),
            "transcript": observation.get("transcript") if observation.get("transcript") is not None else elder_response.get("transcript"),
        }

    def _advance(self, task_id: str, step: str, message: str, result: dict | None = None) -> None:
        with self._lock:
            task = self._tasks[task_id]
            if task["status"] in TERMINAL_STATUSES:
                return
            legacy_step = self._legacy_step_for(step)
            task["status"] = self._legacy_status_for_step(step)
            task["statusV2"] = self._unified_status_for_step(step)
            task["currentStep"] = legacy_step
            task["current_step"] = self._unified_step_for(step)
            self._touch_task(task)
            if step in {"robot_camera", "CAMERA_CHECK"}:
                task["camera"] = "starting"
            if step in {"voice_check", "VOICE_PROMPT", "WAITING_RESPONSE"} and not self._has_recorded_voice_result(task):
                task["voice"] = "waiting"
            for item in task["steps"]:
                if item["name"] == step or item.get("legacyName") == legacy_step:
                    item["status"] = "done"
                    item["time"] = task["updatedAt"]
            task["events"].append(
                {
                    "time": task["updatedAt"],
                    "step": legacy_step,
                    "stepV2": self._unified_step_for(step),
                    "message": message,
                }
            )
            if result:
                result = self._preserve_recorded_voice_result(task, result)
                self._merge_task_result_unlocked(task, result)
                if "camera" in result:
                    task["camera"] = result["camera"]
                if "voice" in result:
                    task["voice"] = result["voice"]
        self._notify_task(task_id)
        self._audit_task(task_id, step)

    def _finish(self, task_id: str, result: dict | None = None) -> None:
        should_notify = True
        with self._lock:
            task = self._tasks[task_id]
            if task["status"] in TERMINAL_STATUSES and task["status"] != "finished":
                return
            already_finished = task["status"] == "finished"
            task["status"] = "finished"
            task["statusV2"] = "COMPLETED"
            task["currentStep"] = "finished"
            task["current_step"] = "REPORTING"
            task["camera"] = task.get("camera") or task.get("result", {}).get("camera")
            task["voice"] = task.get("voice") or task.get("result", {}).get("voice")
            self._touch_task(task)
            for item in task["steps"]:
                if item["name"] == "REPORTING" or item["name"] == "finished":
                    item["status"] = "done"
                    item["time"] = task["updatedAt"]
            if result:
                self._merge_task_result_unlocked(task, result)
                if "camera" in result:
                    task["camera"] = result["camera"]
                if "voice" in result:
                    task["voice"] = result["voice"]
            if not already_finished or not any(event.get("step") == "finished" for event in task.get("events", [])):
                task["events"].append({"time": task["updatedAt"], "step": "finished", "stepV2": "REPORTING", "message": "Task finished."})
            should_notify = bool(result) or not already_finished
        if should_notify:
            self._notify_task(task_id)
        self._audit_task(task_id, "finished")

    def _fail(self, task_id: str, exc: Exception) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            if task["status"] == "cancelled":
                return
            task["status"] = "failed"
            task["statusV2"] = "FAILED"
            self._touch_task(task)
            task["error"] = str(exc)
            failure_result = self._failure_result(task, exc)
            self._merge_task_result_unlocked(task, failure_result)
            if "camera" in failure_result:
                task["camera"] = failure_result["camera"]
            if "voice" in failure_result:
                task["voice"] = failure_result["voice"]
            task["events"].append(
                {
                    "time": task["updatedAt"],
                    "step": task.get("currentStep"),
                    "stepV2": task.get("current_step"),
                    "message": str(exc),
                }
            )
        self.logger.exception("task failed task_id=%s", task_id)
        self._notify_task(task_id)
        self._audit_task(task_id, "failed")

    def _merge_result(self, task_id: str, result: dict) -> None:
        with self._lock:
            task = self._tasks[task_id]
            self._merge_task_result_unlocked(task, result)
            if "camera" in result:
                task["camera"] = result["camera"]
            if "voice" in result:
                task["voice"] = result["voice"]
            self._touch_task(task)
        self._notify_task(task_id)
        self._audit_task(task_id, "result_updated")

    def _merge_task_result_unlocked(self, task: dict, result: dict) -> None:
        if "observation" in result and isinstance(result.get("observation"), dict):
            current = task.setdefault("result", {}).setdefault("observation", {})
            if isinstance(current, dict):
                current.update({key: value for key, value in result["observation"].items() if value is not None})
                result = {key: value for key, value in result.items() if key != "observation"}
        task.setdefault("result", {}).update(result)

    def _ensure_no_active_task(self) -> None:
        active = self.active_task()
        if active:
            raise GatewayError(ErrorCode.CONTROL_BUSY, f"Robot task already running: {active['taskId']}", 409)

    def _wait_for_turn(self, task_id: str) -> None:
        while True:
            self._raise_if_cancelled(task_id)
            with self._lock:
                task = self._tasks.get(task_id)
                if task is None:
                    raise GatewayError(ErrorCode.TASK_NOT_FOUND, f"Task not found: {task_id}", 404)
                pending = self._pending_tasks_unlocked()
                first_task = pending[0] if pending else None
                first_task_id = (first_task.get("taskId") or first_task.get("task_id")) if first_task else None
            if first_task_id == task_id and not self.robot_service.control_lock.busy:
                return
            time.sleep(0.05)

    def _wait_until_ready_for_dispatch(self, task_id: str) -> None:
        while True:
            self._wait_for_turn(task_id)
            try:
                self.robot_service.ensure_ready_for_task_dispatch()
                return
            except GatewayError as exc:
                if exc.code != ErrorCode.CONTROL_BUSY:
                    raise
                time.sleep(0.05)

    def _build_task(
        self,
        task_id: str,
        task_name: str,
        priority: str,
        location: str,
        steps: list[str],
        source: dict,
        result: dict | None = None,
        workflow_steps: list[str] | None = None,
    ) -> dict:
        now = _now_iso()
        workflow_steps = workflow_steps or steps
        return {
            "taskId": task_id,
            "task_id": task_id,
            "robotId": self.robot_service.settings.robot_id,
            "task": task_name,
            "priority": priority,
            "status": "waiting",
            "statusV2": "QUEUED",
            "location": location,
            "currentStep": None,
            "current_step": None,
            "step": steps,
            "steps": [
                {
                    "name": step,
                    "legacyName": self._legacy_step_for(step),
                    "status": "pending",
                    "time": None,
                }
                for step in workflow_steps
            ],
            "source": source,
            "camera": "idle",
            "voice": "idle",
            "result": result or {},
            "error": None,
            "revision": 0,
            "queueSequence": self._next_task_sequence(),
            "events": [{"time": now, "step": "waiting", "stepV2": None, "message": "Task waiting."}],
            "createdAt": now,
            "updatedAt": now,
        }

    def _next_task_sequence(self) -> int:
        with self._lock:
            self._task_sequence += 1
            return self._task_sequence

    def _touch_task(self, task: dict) -> None:
        task["updatedAt"] = _now_iso()
        task["revision"] = int(task.get("revision", 0)) + 1

    def _store(self, task: dict) -> None:
        with self._lock:
            self._tasks[task["taskId"]] = task
        self._audit_task(task["taskId"], "created")

    def _index_fall_event(self, event_key: str, task_id: str) -> None:
        with self._lock:
            self._fall_event_index[event_key] = task_id

    def _index_external_task(self, external_task_key: str, task_id: str) -> None:
        with self._lock:
            self._external_task_index[external_task_key] = task_id

    def _restore_terminal_tasks_from_audit(self) -> int:
        with self._lock:
            if self._audit_restored:
                return 0
            self._audit_restored = True
        if not self.settings.task_audit_enabled:
            return 0
        restored = 0
        latest_by_task_id: dict[str, dict] = {}
        for entry in self._read_audit_entries():
            task_id = self._audit_entry_task_id(entry)
            task = entry.get("task") or {}
            if task_id and task:
                latest_by_task_id[task_id] = task
        with self._lock:
            for task_id, task in latest_by_task_id.items():
                restored_task = deepcopy(task)
                if restored_task.get("status") in TERMINAL_STATUSES | BLOCKED_STATUSES:
                    pass
                else:
                    restored_task["status"] = "failed"
                    restored_task["statusV2"] = "FAILED"
                    restored_task["error"] = "Task was interrupted by service restart and will not resume robot motion."
                    restored_task.setdefault("result", {})["errorCode"] = ErrorCode.SERVICE_RESTART_INTERRUPTED.value
                    restored_task["result"]["failureStep"] = restored_task.get("currentStep")
                    restored_task["updatedAt"] = _now_iso()
                    restored_task.setdefault("events", []).append(
                        {
                            "time": restored_task["updatedAt"],
                            "step": restored_task.get("currentStep"),
                            "stepV2": restored_task.get("current_step"),
                            "message": "SERVICE_RESTART_INTERRUPTED",
                        }
                    )
                restored_task.setdefault("queueSequence", self._next_task_sequence())
                restored_task.setdefault("statusV2", "BLOCKED" if restored_task.get("status") in BLOCKED_STATUSES else restored_task.get("status", "").upper())
                self._tasks[task_id] = restored_task
                source = task.get("source") or {}
                source_event_id = source.get("sourceEventId") or source.get("source_event_id")
                external_task_id = source.get("externalTaskId") or source.get("external_task_id")
                if source_event_id:
                    self._fall_event_index[source_event_id] = task_id
                if external_task_id:
                    self._external_task_index[external_task_id] = task_id
                restored += 1
        if restored:
            self.logger.info("restored %s terminal task(s) from audit log", restored)
        return restored

    def _attach_event_callback_if_needed(self, task_id: str, callback_url: str | None) -> bool:
        if not callback_url:
            return False
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            source = task.get("source") or {}
            if source.get("callbackUrl"):
                return False
            source["callbackUrl"] = callback_url
            task["source"] = source
            self._touch_task(task)
            task["events"].append(
                {
                    "time": task["updatedAt"],
                    "step": "callback_attached",
                    "message": "Callback URL attached by idempotent fall event replay.",
                }
            )
            return True

    def _attach_external_task_id_if_needed(self, task_id: str, external_task_id: str | None) -> bool:
        external_task_key = self._external_task_key(external_task_id)
        if not external_task_key:
            return False
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            source = task.get("source") or {}
            if source.get("externalTaskId"):
                self._external_task_index.setdefault(source["externalTaskId"], task_id)
                return False
            source["externalTaskId"] = external_task_key
            task["source"] = source
            self._external_task_index[external_task_key] = task_id
            self._touch_task(task)
            task["events"].append(
                {
                    "time": task["updatedAt"],
                    "step": "external_task_attached",
                    "message": "External health_new task ID attached by fall event replay.",
                }
            )
            return True

    def _task_id_for_fall_event(self, event_key: str) -> str | None:
        with self._lock:
            return self._fall_event_index.get(event_key)

    def _task_id_for_external_task(self, external_task_key: str) -> str | None:
        with self._lock:
            task_id = self._external_task_index.get(external_task_key)
            if task_id:
                return task_id
            for candidate in self._tasks.values():
                source = candidate.get("source") or {}
                if source.get("externalTaskId") == external_task_key:
                    task_id = candidate.get("task_id") or candidate.get("taskId")
                    if task_id:
                        self._external_task_index[external_task_key] = task_id
                    return task_id
        return None

    def _start_worker(self, task_id: str, target) -> None:
        worker = threading.Thread(target=self._run_worker, args=(task_id, target), name=f"go2-task-{task_id}", daemon=True)
        with self._lock:
            self._workers[task_id] = worker
        worker.start()

    def _run_worker(self, task_id: str, target) -> None:
        try:
            target(task_id)
        finally:
            with self._lock:
                if self._workers.get(task_id) is threading.current_thread():
                    self._workers.pop(task_id, None)

    def _motion_plan(self, location: str) -> list[tuple[float, float, float, float]]:
        key = self._location_key(location)
        configured = self._configured_motion_plan(key)
        if configured:
            return configured
        return LOCATION_MOTION_PLANS.get(key, DEFAULT_MOTION_PLAN)

    def _location_key(self, location: str) -> str:
        key = location.strip().lower().replace("-", "_").replace(" ", "_")
        return LOCATION_ALIASES.get(key, key)

    def _location_resolution_payload(self, location: str) -> dict:
        resolved = self.resolve_location(location)
        return {
            "input": resolved["input"],
            "location": resolved["location"],
            "known": resolved["known"],
            "fallbackUsed": resolved["fallback_used"],
            "source": resolved["source"],
            "plan": resolved["plan"],
        }

    def _configured_motion_plan(self, key: str) -> list[tuple[float, float, float, float]] | None:
        return self._configured_motion_plans().get(key)

    def _configured_motion_plans(self) -> dict[str, list[tuple[float, float, float, float]]]:
        if not self.settings.location_motion_plans_json:
            return {}
        try:
            plans = json.loads(self.settings.location_motion_plans_json)
            configured = {}
            for location, raw_plan in plans.items():
                key = self._location_key(location)
                plan = []
                for item in raw_plan:
                    if len(item) != 4:
                        raise ValueError("each motion step must contain vx, vy, wz, duration")
                    vx, vy, wz, duration = item
                    plan.append((float(vx), float(vy), float(wz), float(duration)))
                if plan:
                    configured[key] = plan
            return configured
        except Exception as exc:
            self.logger.warning("invalid GO2_LOCATION_MOTION_PLANS_JSON, falling back to defaults: %s", exc)
            return {}

    def _serialize_motion_plan(self, plan: list[tuple[float, float, float, float]]) -> list[dict]:
        return [
            {
                "vx": vx,
                "vy": vy,
                "wz": wz,
                "duration": duration,
            }
            for vx, vy, wz, duration in plan
        ]

    def _legacy_step_for(self, step: str | None) -> str | None:
        if step is None:
            return None
        return UNIFIED_STEP_TO_LEGACY.get(step, step)

    def _unified_step_for(self, step: str | None) -> str | None:
        if step is None:
            return None
        if step in CONFIRM_FALL_STEPS:
            return step
        for unified, legacy in UNIFIED_STEP_TO_LEGACY.items():
            if step == legacy:
                return unified
        return step.upper() if isinstance(step, str) else step

    def _legacy_status_for_step(self, step: str) -> str:
        legacy_step = self._legacy_step_for(step)
        if legacy_step in {"receive_event", "receive_task"}:
            return "running"
        if legacy_step == "moving":
            return "moving"
        if legacy_step == "arrived":
            return "arrived"
        if legacy_step in {"robot_camera", "voice_check"}:
            return "checking"
        if legacy_step == "finished":
            return "finished"
        return "running"

    def _unified_status_for_step(self, step: str) -> str:
        unified_step = self._unified_step_for(step)
        return "RUNNING"

    def _new_task_id(self) -> str:
        return f"task_{uuid.uuid4().hex[:12]}"

    def _new_trace_id(self) -> str:
        return f"trace_{uuid.uuid4().hex[:16]}"

    def _new_trace_id(self) -> str:
        return f"trace_{uuid.uuid4().hex[:16]}"

    def _raise_if_cancelled(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task["status"] == "cancelled":
                raise _TaskCancelled(task_id)

    def _fall_event_key(self, event: FallEventRequest) -> str | None:
        if event.source_event_id:
            return event.source_event_id.strip() or None
        return None

    def _external_task_key(self, external_task_id: str | None) -> str | None:
        if external_task_id:
            return external_task_id.strip() or None
        return None

    def _notify_task(self, task_id: str) -> None:
        try:
            self.feedback_service.publish_task_update(self.get_task(task_id))
        except Exception:
            self.logger.exception("failed to publish task update task_id=%s", task_id)

    def _audit_task(self, task_id: str, event: str) -> None:
        if not self.settings.task_audit_enabled:
            return
        try:
            task = self._get_task_for_audit(task_id)
            payload = {"auditEvent": event, "auditTime": _now_iso(), "task": task}
            path = Path(self.settings.task_audit_log_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        except Exception:
            self.logger.warning("failed to write task audit log task_id=%s event=%s", task_id, event, exc_info=True)

    def _get_task_for_audit(self, task_id: str) -> dict:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise GatewayError(ErrorCode.TASK_NOT_FOUND, f"Task not found: {task_id}", 404)
            return deepcopy(task)

    def _copy_task_for_response(self, task: dict) -> dict:
        copied = deepcopy(task)
        self._apply_legacy_confirm_fall_view(copied)
        task_id = copied.get("taskId") or copied.get("task_id")
        queue = self._queue_state_for_task_unlocked(task_id)
        copied["queue"] = queue
        copied["queuePosition"] = queue["position"]
        copied["queue_position"] = queue["position"]
        copied["queueSize"] = queue["size"]
        copied["queue_size"] = queue["size"]
        copied["queueHead"] = queue["head"]
        copied["queue_head"] = queue["head"]
        copied["blockedByTaskId"] = queue["blockedByTaskId"]
        copied["blocked_by_task_id"] = queue["blockedByTaskId"]
        copied["status_v2"] = copied.get("statusV2")
        copied["currentStepV2"] = copied.get("current_step")
        return copied

    def _apply_legacy_confirm_fall_view(self, task: dict) -> None:
        if task.get("task") != "confirm_fall":
            return
        legacy_order = task.get("step") or CONFIRM_FALL_LEGACY_STEPS
        compact = {
            name: {"name": name, "status": "pending", "time": None}
            for name in legacy_order
        }
        for item in task.get("steps") or []:
            legacy_name = item.get("legacyName") or self._legacy_step_for(item.get("name"))
            if legacy_name not in compact:
                continue
            if item.get("status") == "done":
                compact[legacy_name]["status"] = "done"
                compact[legacy_name]["time"] = item.get("time") or compact[legacy_name]["time"]
        task["steps"] = [compact[name] for name in legacy_order]
        if task.get("status") == "finished":
            task["statusV2"] = "COMPLETED"
            task["current_step"] = "finished"

    def _queue_state_for_task_unlocked(self, task_id: str | None) -> dict:
        pending_ids = [
            candidate.get("taskId") or candidate.get("task_id")
            for candidate in self._pending_tasks_unlocked()
        ]
        position = pending_ids.index(task_id) + 1 if task_id in pending_ids else None
        blocked_by = pending_ids[position - 2] if position and position > 1 else None
        return {
            "position": position,
            "size": len(pending_ids),
            "head": position == 1,
            "blockedByTaskId": blocked_by,
        }

    def _pending_tasks_unlocked(self) -> list[dict]:
        return sorted(
            (
                task
                for task in self._tasks.values()
                if task.get("status") not in TERMINAL_STATUSES | BLOCKED_STATUSES
            ),
            key=self._task_order_key,
        )

    def _task_order_key(self, task: dict) -> tuple[int, str, str]:
        return (
            self._queue_sequence(task),
            task.get("createdAt") or "",
            task.get("taskId") or task.get("task_id") or "",
        )

    def _queue_sequence(self, task: dict) -> int:
        try:
            return int(task.get("queueSequence") or 0)
        except (TypeError, ValueError):
            return 0

    def _fall_event_blocking_error(self, location_resolution: dict | None = None) -> GatewayError | None:
        if self.settings.read_only_mode:
            return GatewayError(ErrorCode.READ_ONLY_MODE, "Gateway is in read-only mode; motion is blocked.", 403)
        if self.settings.mode == "real" and location_resolution and location_resolution.get("fallbackUsed"):
            return GatewayError(
                ErrorCode.LOCATION_PLAN_NOT_VALIDATED,
                "Real mode requires a validated location motion plan; fallback motion is blocked.",
                409,
            )
        try:
            self.robot_service.ensure_ready_for_task_acceptance()
        except GatewayError as exc:
            if self.settings.mode == "real":
                return exc
            raise
        return None

    def _block_task_robot_offline(self, task_id: str, exc: GatewayError) -> None:
        with self._lock:
            task = self._tasks[task_id]
            task["status"] = "BLOCKED_ROBOT_OFFLINE"
            task["statusV2"] = "BLOCKED"
            task["currentStep"] = "waiting"
            task["current_step"] = "PREFLIGHT"
            task["error"] = exc.message
            self._touch_task(task)
            self._merge_task_result_unlocked(
                task,
                {
                    "errorCode": exc.code.value,
                    "errorMessage": exc.message,
                    "blockedReason": exc.message,
                },
            )
            task["events"].append(
                {
                    "time": task["updatedAt"],
                    "step": "BLOCKED_ROBOT_OFFLINE",
                    "stepV2": "PREFLIGHT",
                    "message": exc.message,
                }
            )
        self._notify_task(task_id)
        self._audit_task(task_id, "blocked_robot_offline")

    def _read_audit_entries(self, limit: int | None = None) -> list[dict]:
        path = Path(self.settings.task_audit_log_path)
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        if limit is not None:
            lines = lines[-limit:]
        entries = []
        for line in lines:
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                self.logger.warning("invalid task audit log line skipped path=%s", path)
        return entries

    def _audit_entry_task_id(self, entry: dict) -> str | None:
        task = entry.get("task") or {}
        return task.get("task_id") or task.get("taskId")

    def _audit_entry_external_task_id(self, entry: dict) -> str | None:
        task = entry.get("task") or {}
        source = task.get("source") or {}
        return source.get("externalTaskId") or source.get("external_task_id")

    def _status_for_step(self, step: str) -> str:
        return self._legacy_status_for_step(step)

    def _has_recorded_voice_result(self, task: dict) -> bool:
        return task.get("voice") == "completed" or task.get("result", {}).get("voiceResult") not in (None, "awaiting_response")

    def _preserve_recorded_voice_result(self, task: dict, result: dict) -> dict:
        if not self._has_recorded_voice_result(task):
            return result
        if result.get("voiceResult") != "awaiting_response" and result.get("voice") != "waiting":
            return result
        return {key: value for key, value in result.items() if key not in {"voiceResult", "voice"}}

    def _failure_result(self, task: dict, exc: Exception) -> dict:
        error_code = exc.code.value if isinstance(exc, GatewayError) else ErrorCode.INTERNAL_ERROR.value
        result = {
            "errorCode": error_code,
            "failureStep": task.get("currentStep"),
        }
        if task.get("currentStep") == "robot_camera":
            result.update(
                {
                    "camera": "failed",
                    "confirm": "unknown",
                    "outcome": "UNKNOWN",
                    "robotCamera": {
                        "streamUrl": self.settings.camera_stream_url,
                        "snapshotUrl": self.settings.camera_snapshot_url,
                        "snapshot": "failed",
                        "cameraAvailable": False,
                    },
                }
            )
        if task.get("currentStep") == "voice_check":
            result["voice"] = "failed"
        return result
