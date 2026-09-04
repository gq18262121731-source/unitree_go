from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def ok_response(message: str, data: Any | None = None, code: int = 0, request_id: str | None = None) -> dict:
    payload = {
        "success": True,
        "code": code,
        "message": message,
        "timestamp": now_iso(),
    }
    if request_id:
        payload["requestId"] = request_id
    if data is not None:
        payload["data"] = data
    return payload


def error_response(code: str, message: str, request_id: str | None = None, data: Any | None = None) -> dict:
    payload = {
        "success": False,
        "code": code,
        "message": message,
        "timestamp": now_iso(),
    }
    if request_id:
        payload["requestId"] = request_id
    if data is not None:
        payload["data"] = data
    return payload
