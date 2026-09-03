from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone

import httpx

from app.config import Settings


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


class HealthNewFeedbackService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = logging.getLogger("go2_gateway.feedback")
        self._queue: queue.Queue[tuple[dict, str] | None] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._worker_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._closed = False
        self._enqueued = 0
        self._sent = 0
        self._failed = 0
        self._dropped = 0
        self._last_success_at: str | None = None
        self._last_failure_at: str | None = None
        self._last_error: str | None = None
        self._delivery_lock = threading.Lock()
        self._sequence_by_task: dict[str, int] = {}
        self._deliveries_by_task: dict[str, list[dict]] = {}

    def publish_task_update(self, task: dict) -> None:
        self.publish_task_update_to(task)

    def publish_task_update_to(self, task: dict, callback_url: str | None = None) -> bool:
        callback_url = callback_url or self._callback_url(task)
        if not callback_url:
            return False
        with self._worker_lock:
            if self._closed:
                self.logger.warning("health_new feedback dropped after close task_id=%s", task.get("task_id") or task.get("taskId"))
                self._record_dropped()
                return False
            self._ensure_worker_locked()
        delivery = self._create_delivery(task, callback_url)
        queued_task = deepcopy(task)
        queued_task["_callbackDelivery"] = {
            "callback_id": delivery["callback_id"],
            "sequence": delivery["sequence"],
            "message": self._task_message(task),
            "occurred_at": delivery["occurred_at"],
        }
        self._record_enqueued()
        self._queue.put((queued_task, callback_url))
        return True

    def close(self) -> None:
        worker: threading.Thread | None
        with self._worker_lock:
            self._closed = True
            worker = self._worker
        if worker is None:
            return
        self._queue.join()
        self._queue.put(None)
        worker.join(timeout=5.0)

    def status(self) -> dict:
        with self._worker_lock:
            worker_alive = bool(self._worker and self._worker.is_alive())
            closed = self._closed
        with self._stats_lock:
            with self._delivery_lock:
                delivery_count = sum(len(items) for items in self._deliveries_by_task.values())
            return {
                "configured": bool(self.settings.health_new_callback_url),
                "default_callback_url": self.settings.health_new_callback_url or None,
                "token_configured": bool(self.settings.health_new_callback_token),
                "timeout_seconds": self.settings.health_new_callback_timeout_seconds,
                "retries": self.settings.health_new_callback_retries,
                "retry_delay_seconds": self.settings.health_new_callback_retry_delay_seconds,
                "worker_alive": worker_alive,
                "closed": closed,
                "pending": self._queue.qsize(),
                "enqueued": self._enqueued,
                "sent": self._sent,
                "failed": self._failed,
                "dropped": self._dropped,
                "last_success_at": self._last_success_at,
                "last_failure_at": self._last_failure_at,
                "last_error": self._last_error,
                "delivery_tracking": True,
                "delivery_count": delivery_count,
            }

    def callback_url_for(self, task: dict) -> str:
        return self._callback_url(task)

    def deliveries_for_task(self, task_id: str) -> dict:
        with self._delivery_lock:
            deliveries = deepcopy(self._deliveries_by_task.get(task_id, []))
        return {"task_id": task_id, "deliveries": deliveries}

    def _post_task_update(self, task: dict, callback_url: str | None = None) -> None:
        callback_url = callback_url or self._callback_url(task)
        if not callback_url:
            return
        source = task.get("source") or {}
        queue_state = task.get("queue") or {}
        delivery = task.get("_callbackDelivery") or {}
        task_id = task.get("task_id") or task.get("taskId")
        callback_id = delivery.get("callback_id") or f"cb_{uuid.uuid4().hex[:16]}"
        sequence = delivery.get("sequence") or self._next_sequence(task_id)
        occurred_at = delivery.get("occurred_at") or _now_iso()
        message = delivery.get("message") or self._task_message(task)
        payload = {
            "callback_id": callback_id,
            "sequence": sequence,
            "task_id": task_id,
            "robot_id": task.get("robotId"),
            "elder_id": source.get("elderId"),
            "location": task.get("location") or source.get("location"),
            "confidence": source.get("confidence"),
            "source_event_id": source.get("sourceEventId"),
            "camera_id": source.get("cameraId"),
            "external_task_id": source.get("externalTaskId"),
            "trace_id": source.get("traceId") or task.get("traceId"),
            "location_resolution": source.get("locationResolution") or task.get("result", {}).get("locationResolution"),
            "task": task.get("task"),
            "status": task.get("status"),
            "status_v2": self._unified_status(task),
            "legacy_status": task.get("status"),
            "revision": task.get("revision"),
            "queue_position": queue_state.get("position", task.get("queuePosition")),
            "queue_size": queue_state.get("size", task.get("queueSize")),
            "queue_head": queue_state.get("head", task.get("queueHead")),
            "blocked_by_task_id": queue_state.get("blockedByTaskId", task.get("blockedByTaskId")),
            "queue": queue_state,
            "step": task.get("current_step") or task.get("currentStep"),
            "legacy_step": task.get("currentStep"),
            "message": message,
            "occurred_at": occurred_at,
            "steps": task.get("steps", []),
            "progress": self._task_progress(task),
            "camera": task.get("camera"),
            "voice": task.get("voice"),
            "source": task.get("source"),
            "result": task.get("result"),
            "outcome": (task.get("result") or {}).get("outcome"),
            "observation": (task.get("result") or {}).get("observation"),
            "error": task.get("error"),
            "finished": task.get("status") in {"finished", "failed", "cancelled", "BLOCKED", "BLOCKED_ROBOT_OFFLINE"},
            "updated_at": task.get("updatedAt"),
        }
        headers = {}
        if self.settings.health_new_callback_token:
            headers["Authorization"] = f"Bearer {self.settings.health_new_callback_token}"
        attempts = max(1, self.settings.health_new_callback_retries + 1)
        for attempt in range(1, attempts + 1):
            self._record_delivery_attempt(task_id, callback_id, attempt)
            try:
                with httpx.Client(timeout=self.settings.health_new_callback_timeout_seconds) as client:
                    response = client.post(callback_url, json=payload, headers=headers)
                    status_code = getattr(response, "status_code", None)
                    if status_code is not None:
                        self._record_delivery_http_status(task_id, callback_id, status_code)
                    response.raise_for_status()
                self._record_success()
                self._record_delivery_success(task_id, callback_id)
                return
            except Exception as exc:
                self._record_delivery_error(task_id, callback_id, exc)
                if attempt >= attempts:
                    self._record_failure(exc)
                    self.logger.warning("health_new feedback failed task_id=%s error=%s", payload["task_id"], exc)
                    return
                time.sleep(self.settings.health_new_callback_retry_delay_seconds)

    def _create_delivery(self, task: dict, callback_url: str) -> dict:
        task_id = task.get("task_id") or task.get("taskId")
        delivery = {
            "callback_id": f"cb_{uuid.uuid4().hex[:16]}",
            "sequence": self._next_sequence(task_id),
            "task_id": task_id,
            "callback_url": callback_url,
            "status": "queued",
            "http_status_code": None,
            "error": None,
            "attempts": 0,
            "created_at": _now_iso(),
            "occurred_at": task.get("updatedAt") or _now_iso(),
            "sent_at": None,
            "last_attempt_at": None,
        }
        with self._delivery_lock:
            self._deliveries_by_task.setdefault(task_id, []).append(deepcopy(delivery))
        return delivery

    def _next_sequence(self, task_id: str) -> int:
        with self._delivery_lock:
            sequence = self._sequence_by_task.get(task_id, 0) + 1
            self._sequence_by_task[task_id] = sequence
            return sequence

    def _update_delivery(self, task_id: str, callback_id: str, **changes) -> None:
        with self._delivery_lock:
            for delivery in self._deliveries_by_task.get(task_id, []):
                if delivery.get("callback_id") == callback_id:
                    delivery.update(changes)
                    return

    def _record_delivery_attempt(self, task_id: str, callback_id: str, attempt: int) -> None:
        self._update_delivery(task_id, callback_id, status="sending", attempts=attempt, last_attempt_at=_now_iso())

    def _record_delivery_http_status(self, task_id: str, callback_id: str, status_code: int) -> None:
        self._update_delivery(task_id, callback_id, http_status_code=status_code)

    def _record_delivery_success(self, task_id: str, callback_id: str) -> None:
        self._update_delivery(task_id, callback_id, status="sent", error=None, sent_at=_now_iso())

    def _record_delivery_error(self, task_id: str, callback_id: str, exc: Exception) -> None:
        self._update_delivery(task_id, callback_id, status="failed", error=str(exc))

    def _callback_url(self, task: dict) -> str:
        source = task.get("source") or {}
        return source.get("callbackUrl") or self.settings.health_new_callback_url

    def _ensure_worker_locked(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._run_worker, name="go2-feedback", daemon=True)
        self._worker.start()

    def _run_worker(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                task, callback_url = item
                self._post_task_update(task, callback_url)
            finally:
                self._queue.task_done()

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

    def _task_message(self, task: dict) -> str:
        events = task.get("events") or []
        if events:
            return events[-1].get("message") or ""
        return ""

    def _unified_status(self, task: dict) -> str:
        status_v2 = task.get("statusV2")
        if status_v2:
            return status_v2
        status = task.get("status")
        if status == "finished":
            return "COMPLETED"
        if status == "failed":
            return "FAILED"
        if status == "cancelled":
            return "CANCELLED"
        if status in {"BLOCKED", "BLOCKED_ROBOT_OFFLINE"}:
            return "BLOCKED"
        if status == "waiting":
            return "QUEUED"
        return "RUNNING"

    def _record_enqueued(self) -> None:
        with self._stats_lock:
            self._enqueued += 1

    def _record_success(self) -> None:
        with self._stats_lock:
            self._sent += 1
            self._last_success_at = _now_iso()
            self._last_error = None

    def _record_failure(self, exc: Exception) -> None:
        with self._stats_lock:
            self._failed += 1
            self._last_failure_at = _now_iso()
            self._last_error = str(exc)

    def _record_dropped(self) -> None:
        with self._stats_lock:
            self._dropped += 1
