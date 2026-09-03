from __future__ import annotations

import time

from app.core.watchdog import ControlWatchdog


class StopRecorder:
    def __init__(self) -> None:
        self.count = 0

    def stop(self) -> int:
        self.count += 1
        return 0


def test_watchdog_triggers_stop():
    recorder = StopRecorder()
    watchdog = ControlWatchdog(recorder, timeout_seconds=0.05)
    watchdog.start()
    try:
        watchdog.arm()
        deadline = time.monotonic() + 0.50
        while recorder.count == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        watchdog.stop()

    assert recorder.count >= 1


def test_bounded_ack_wait_does_not_compete_with_inflight_motion_rpc():
    recorder = StopRecorder()
    watchdog = ControlWatchdog(recorder, timeout_seconds=0.05)
    watchdog.start()
    try:
        watchdog.arm()
        watchdog.begin_ack_wait(0.20)
        time.sleep(0.12)
        assert recorder.count == 0
        watchdog.end_ack_wait()
        deadline = time.monotonic() + 0.30
        while recorder.count == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        watchdog.stop()

    assert recorder.count >= 1


def test_emergency_stop_is_available(client):
    before = client.app.state.adapter.stop_count

    response = client.post("/api/robot/emergency-stop")

    assert response.status_code == 200
    assert client.app.state.adapter.stop_count > before
    assert client.get("/api/robot/status").json()["data"]["control"]["lastCommand"] == "EMERGENCY_STOP"
