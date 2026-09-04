from __future__ import annotations

import argparse
import os
import time
from typing import Any

import httpx


SCENARIOS = {"safe", "need-help", "no-response", "camera-failure", "callback-failure", "robot-blocked"}


def _post(client: httpx.Client, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post(path, json=payload)
    response.raise_for_status()
    return response.json()["data"]


def _get(client: httpx.Client, path: str) -> dict[str, Any]:
    response = client.get(path)
    response.raise_for_status()
    return response.json()["data"]


def _wait_for_step(client: httpx.Client, task_id: str, step: str, timeout: float = 5.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = _get(client, f"/api/robot/tasks/{task_id}")
        if last.get("current_step") == step or last.get("status") in {"finished", "failed", "cancelled", "BLOCKED_ROBOT_OFFLINE"}:
            return last
        time.sleep(0.1)
    return last or {}


def _wait_finished(client: httpx.Client, task_id: str, timeout: float = 10.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = _get(client, f"/api/robot/tasks/{task_id}")
        if last.get("status") in {"finished", "failed", "cancelled", "BLOCKED_ROBOT_OFFLINE"}:
            return last
        time.sleep(0.1)
    return last or {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a confirm_fall demo against a running go2-gateway.")
    parser.add_argument("--base-url", default=os.getenv("GO2_GATEWAY_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="safe")
    parser.add_argument("--elder-id", default="elder-demo-001")
    parser.add_argument("--location", default="bedroom")
    args = parser.parse_args()

    source_event_id = f"demo-{args.scenario}-{int(time.time())}"
    callback_url = "http://127.0.0.1:9/callback" if args.scenario == "callback-failure" else None
    location = "unknown_demo_room" if args.scenario == "robot-blocked" else args.location
    payload = {
        "event": "fall_detected",
        "elder_id": args.elder_id,
        "location": location,
        "confidence": 0.96,
        "source_event_id": source_event_id,
        "external_task_id": f"health-{source_event_id}",
        "callback_url": callback_url,
    }

    with httpx.Client(base_url=args.base_url, timeout=10.0) as client:
        task = _post(client, "/api/robot/events/fall", payload)
        task_id = task["task_id"]
        print(f"created task: {task_id}")
        if args.scenario in {"safe", "need-help"}:
            _wait_for_step(client, task_id, "WAITING_RESPONSE")
            response_type = "SAFE" if args.scenario == "safe" else "NEED_HELP"
            task = _post(
                client,
                f"/api/robot/tasks/{task_id}/elder-response",
                {
                    "response_type": response_type,
                    "transcript": "demo response",
                },
            )
            print(f"elder response accepted: {task['result'].get('outcome')}")
        elif args.scenario == "camera-failure":
            print("Set the gateway mock adapter camera to fail before running this scenario for a full camera-failure demo.")
        elif args.scenario == "no-response":
            print("Use GO2_MOCK_CONFIRM_FALL_OUTCOME=NO_RESPONSE and a short GO2_ELDER_RESPONSE_TIMEOUT_SECONDS.")
        elif args.scenario == "robot-blocked":
            print("Use GO2_MODE=real or GO2_READ_ONLY_MODE=true to observe BLOCKED without motion.")

        final_task = _wait_finished(client, task_id)
        result = final_task.get("result") or {}
        print(
            {
                "task_id": task_id,
                "status": final_task.get("status"),
                "status_v2": final_task.get("status_v2") or final_task.get("statusV2"),
                "outcome": result.get("outcome"),
                "observation": result.get("observation"),
            }
        )


if __name__ == "__main__":
    main()
