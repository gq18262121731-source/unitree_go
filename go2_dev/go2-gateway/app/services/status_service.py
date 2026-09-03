from __future__ import annotations

from typing import Protocol

from app.config import Settings
from app.gateway.go2_gateway import Go2Gateway
from app.services.robot_service import RobotService


class TaskStatusProvider(Protocol):
    def active_task(self) -> dict | None:
        ...


class RobotStatusService:
    def __init__(
        self,
        robot_service: RobotService,
        task_service: TaskStatusProvider,
        settings: Settings,
        gateway: Go2Gateway,
    ) -> None:
        self.robot_service = robot_service
        self.task_service = task_service
        self.settings = settings
        self.gateway = gateway

    def compact_status(self) -> dict:
        status = self.robot_service.status()
        active_task = self.task_service.active_task()
        active_steps = None if active_task is None else self._compact_steps(active_task)
        battery = status.get("battery", {})
        control = status.get("control", {})
        motion = status.get("motion", {})

        return {
            "robot_id": status.get("robotId"),
            "online": bool(status.get("online")),
            "ip": self.settings.robot_ip,
            "battery": battery.get("percentage"),
            "battery_detail": {
                "percentage": battery.get("percentage"),
                "voltage": battery.get("voltage"),
                "current": battery.get("current"),
                "raw": battery.get("raw", {}),
            },
            "mode": "task" if active_task else self._mode_name(status),
            "action": control.get("lastCommand") or motion.get("modeName") or "unknown",
            "action_updated_at": control.get("lastCommandTime"),
            "busy": bool(control.get("busy")),
            "control_enabled": bool(control.get("enabled", self.settings.control_enabled)),
            "state_stale": bool(status.get("stateStale")),
            "last_seen": status.get("lastSeen"),
            "task": None if active_task is None else active_task.get("task"),
            "task_id": None if active_task is None else active_task.get("taskId"),
            "status": None if active_task is None else active_task.get("status"),
            "revision": None if active_task is None else active_task.get("revision"),
            "queue_position": None if active_task is None else active_task.get("queuePosition") or active_task.get("queue_position"),
            "queue_size": None if active_task is None else active_task.get("queueSize") or active_task.get("queue_size"),
            "queue_head": None if active_task is None else active_task.get("queueHead") if active_task.get("queueHead") is not None else active_task.get("queue_head"),
            "blocked_by_task_id": None if active_task is None else active_task.get("blockedByTaskId") or active_task.get("blocked_by_task_id"),
            "queue": None if active_task is None else active_task.get("queue"),
            "step": None if active_task is None else active_task.get("currentStep"),
            "steps": active_steps,
            "progress": None if active_task is None else self._task_progress({**active_task, "steps": active_steps or []}),
            "finished": None if active_task is None else self._task_finished(active_task),
            **self._task_source_fields(active_task),
            "camera": status.get("camera", {}).get("online"),
            "voice": None if active_task is None else active_task.get("voice"),
            "error": self._error(status),
            "last_error": self.robot_service.last_connection_error,
            "detail": status,
        }

    def detailed_status(self) -> dict:
        status = self.robot_service.status()
        status["activeTask"] = self.task_service.active_task()
        return status

    def connection_status(self) -> dict:
        status = self.robot_service.status()
        return {
            "robot_id": status.get("robotId"),
            "online": bool(status.get("online")),
            "ip": self.settings.robot_ip,
            "network_interface": self.settings.network_interface,
            "mode": self.settings.mode,
            "initialized": self.gateway.is_initialized(),
            "sdk_version": self.gateway.sdk_version,
            "last_seen": status.get("lastSeen"),
            "state_stale": bool(status.get("stateStale")),
            "error": self._error(status),
            "last_error": self.robot_service.last_connection_error,
        }

    def readiness(self) -> dict:
        status = self.robot_service.status()
        active_task = self.task_service.active_task()
        initialized = self.gateway.is_initialized()
        dds = status.get("dds") or self.gateway.dds_diagnostics()
        online = bool(status.get("online"))
        dds_state_available = bool(dds.get("ddsStateAvailable", online))
        state_stale = bool(status.get("stateStale"))
        busy = bool(status.get("control", {}).get("busy"))
        control_enabled = bool(status.get("control", {}).get("enabled", self.settings.control_enabled))
        motion_ready = initialized and dds_state_available and online and control_enabled and not state_stale
        ready = motion_ready and not busy and active_task is None
        accepting_tasks = motion_ready

        return {
            "ready": ready,
            "accepting_tasks": accepting_tasks,
            "dds_initialized": initialized,
            "dds_state_available": dds_state_available,
            "motion_ready": motion_ready,
            "robot_id": status.get("robotId"),
            "online": online,
            "ip": self.settings.robot_ip,
            "initialized": initialized,
            "control_enabled": control_enabled,
            "state_stale": state_stale,
            "busy": busy,
            "active_task": active_task,
            "error": self._readiness_error(status, initialized, control_enabled, busy, active_task),
            "acceptance_error": self._acceptance_error(status, initialized, control_enabled),
            "last_error": self.robot_service.last_connection_error,
        }

    def _mode_name(self, status: dict) -> str:
        if not status.get("online"):
            return "offline"
        if status.get("stateStale"):
            return "stale"
        if status.get("control", {}).get("busy"):
            return "control"
        return "idle"

    def _error(self, status: dict) -> str | None:
        if self.settings.mode == "real" and status.get("dds", {}).get("ddsInitialized") and not status.get("dds", {}).get("ddsStateAvailable"):
            return "DDS_NOT_READY"
        if not status.get("online"):
            return "ROBOT_OFFLINE"
        if status.get("stateStale"):
            return "ROBOT_STATE_STALE"
        return None

    def _readiness_error(self, status: dict, initialized: bool, control_enabled: bool, busy: bool, active_task: dict | None) -> str | None:
        if not initialized:
            return "SDK_NOT_INITIALIZED"
        if self.settings.mode == "real" and status.get("dds", {}).get("ddsInitialized") and not status.get("dds", {}).get("ddsStateAvailable"):
            return "DDS_NOT_READY"
        if not status.get("online"):
            return "ROBOT_OFFLINE"
        if not control_enabled:
            return "CONTROL_DISABLED"
        if status.get("stateStale"):
            return "ROBOT_STATE_STALE"
        if busy or active_task is not None:
            return "CONTROL_BUSY"
        return None

    def _acceptance_error(self, status: dict, initialized: bool, control_enabled: bool) -> str | None:
        if not initialized:
            return "SDK_NOT_INITIALIZED"
        if self.settings.mode == "real" and status.get("dds", {}).get("ddsInitialized") and not status.get("dds", {}).get("ddsStateAvailable"):
            return "DDS_NOT_READY"
        if not status.get("online"):
            return "ROBOT_OFFLINE"
        if not control_enabled:
            return "CONTROL_DISABLED"
        if status.get("stateStale"):
            return "ROBOT_STATE_STALE"
        return None

    def _compact_steps(self, task: dict) -> list[dict]:
        raw_steps = task.get("steps") or []
        legacy_order = task.get("step") or []
        if task.get("task") != "confirm_fall" or not legacy_order:
            return raw_steps

        compact = {
            name: {
                "name": name,
                "status": "pending",
                "time": None,
            }
            for name in legacy_order
        }
        for item in raw_steps:
            legacy_name = item.get("legacyName") or item.get("name")
            if legacy_name not in compact:
                continue
            if item.get("status") == "done":
                compact[legacy_name]["status"] = "done"
                compact[legacy_name]["time"] = item.get("time") or compact[legacy_name]["time"]
        return [compact[name] for name in legacy_order]

    def _task_source_fields(self, task: dict | None) -> dict:
        source = task.get("source") if task else {}
        source = source or {}
        return {
            "elder_id": source.get("elderId"),
            "location": None if task is None else task.get("location") or source.get("location"),
            "confidence": source.get("confidence"),
            "source_event_id": source.get("sourceEventId"),
            "camera_id": source.get("cameraId"),
            "external_task_id": source.get("externalTaskId"),
            "location_resolution": None
            if task is None
            else source.get("locationResolution") or task.get("result", {}).get("locationResolution"),
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
        return task.get("status") in {"finished", "failed", "cancelled", "BLOCKED_ROBOT_OFFLINE"}
