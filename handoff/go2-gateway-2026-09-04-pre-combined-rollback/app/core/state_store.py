from __future__ import annotations

import threading
from copy import deepcopy
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone().isoformat() if dt else None


class StateStore:
    def __init__(self, robot_id: str, stale_seconds: float) -> None:
        self.robot_id = robot_id
        self.stale_seconds = stale_seconds
        self._lock = threading.RLock()
        self._status = self._empty_status(online=False)

    def _empty_status(self, online: bool) -> dict:
        return {
            "robotId": self.robot_id,
            "online": online,
            "lastSeen": None,
            "stateStale": True,
            "motion": {
                "mode": None,
                "modeName": None,
                "gaitType": None,
                "velocityX": None,
                "velocityY": None,
                "yawSpeed": None,
                "bodyHeight": None,
            },
            "attitude": {"roll": None, "pitch": None, "yaw": None},
            "battery": {"percentage": None, "voltage": None, "current": None, "raw": {}},
            "camera": {"online": False, "lastFrameTime": None},
            "control": {
                "enabled": True,
                "busy": False,
                "lastCommand": None,
                "lastCommandTime": None,
            },
            "system": {"gatewayVersion": None, "sdkVersion": None},
        }

    def update_status(self, status: dict) -> None:
        with self._lock:
            previous = deepcopy(self._status)
            merged = self._empty_status(bool(status.get("online", False)))
            merged["camera"] = previous.get("camera", merged["camera"])
            merged["control"] = previous.get("control", merged["control"])
            merged["system"] = previous.get("system", merged["system"])
            self._deep_update(merged, status)
            if merged["online"] and not merged.get("lastSeen"):
                merged["lastSeen"] = iso(utc_now())
            merged["stateStale"] = self._is_stale_unlocked(merged)
            self._status = merged

    def update_camera(self, online: bool, frame_time: str | None) -> None:
        with self._lock:
            self._status["camera"] = {"online": online, "lastFrameTime": frame_time}

    def update_control(
        self,
        enabled: bool | None = None,
        busy: bool | None = None,
        last_command: str | None = None,
    ) -> None:
        with self._lock:
            if enabled is not None:
                self._status["control"]["enabled"] = enabled
            if busy is not None:
                self._status["control"]["busy"] = busy
            if last_command is not None:
                self._status["control"]["lastCommand"] = last_command
                self._status["control"]["lastCommandTime"] = iso(utc_now())

    def set_system(self, gateway_version: str, sdk_version: str) -> None:
        with self._lock:
            self._status["system"] = {"gatewayVersion": gateway_version, "sdkVersion": sdk_version}

    def snapshot(self) -> dict:
        with self._lock:
            status = deepcopy(self._status)
            status["stateStale"] = self._is_stale_unlocked(status)
            if status["stateStale"]:
                status["online"] = False
            return status

    def is_online_fresh(self) -> bool:
        status = self.snapshot()
        return bool(status["online"]) and not bool(status["stateStale"])

    def _is_stale_unlocked(self, status: dict) -> bool:
        last_seen = status.get("lastSeen")
        if not status.get("online") or not last_seen:
            return True
        try:
            last_dt = datetime.fromisoformat(last_seen)
        except ValueError:
            return True
        return (utc_now() - last_dt).total_seconds() > self.stale_seconds

    def _deep_update(self, target: dict, source: dict) -> None:
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                self._deep_update(target[key], value)
            else:
                target[key] = value
