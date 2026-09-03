from __future__ import annotations

import json
from urllib.request import Request, urlopen

from scripts.verify_real_acceptance import CallbackRecorder


def _post_json(url: str, payload: dict) -> None:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=2.0) as response:
        assert response.status == 204


def test_field_acceptance_callback_recorder_waits_for_terminal_payload():
    recorder = CallbackRecorder("127.0.0.1")
    recorder.start()
    try:
        _post_json(recorder.url, {"task_id": "task-field-001", "finished": False, "status": "moving"})
        _post_json(
            recorder.url,
            {
                "task_id": "task-field-001",
                "finished": True,
                "status": "finished",
                "revision": 6,
            },
        )

        payload = recorder.wait_for_terminal("task-field-001", 1.0)

        assert payload["status"] == "finished"
        assert payload["revision"] == 6
    finally:
        recorder.stop()
