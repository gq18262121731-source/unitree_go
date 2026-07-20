from __future__ import annotations

from app.core.control_lock import ControlLock
from app.core.errors import GatewayError


def test_control_lock_rejects_parallel_command():
    lock = ControlLock()
    lock.acquire()
    try:
        try:
            lock.acquire()
        except GatewayError as exc:
            assert exc.code.value == "CONTROL_BUSY"
        else:
            raise AssertionError("second acquire should fail")
    finally:
        lock.release()


def test_stand_and_lie_down_endpoints(client):
    assert client.post("/api/robot/stand").status_code == 200
    assert client.post("/api/robot/lie-down").status_code == 200

