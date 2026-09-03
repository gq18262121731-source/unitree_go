from __future__ import annotations

import argparse
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def post_json(url: str, payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return read_json(request)


def get_json(url: str) -> dict:
    return read_json(Request(url, method="GET"))


def read_json(request: Request) -> dict:
    try:
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise SystemExit(f"FAILED: cannot reach gateway: {exc}") from exc


def print_result(label: str, result: dict) -> None:
    nested_result = result.get("result") or {}
    source = result.get("source") or {}
    print(
        f"{label}: status={result.get('status')} revision={result.get('revision')} "
        f"external_task_id={result.get('external_task_id') or source.get('externalTaskId')} "
        f"confirm={result.get('confirm') or nested_result.get('confirm')} camera={result.get('camera')} "
        f"voice={result.get('voice')} error_code={result.get('error_code') or nested_result.get('errorCode')} "
        f"failure_step={result.get('failure_step') or nested_result.get('failureStep')} finished={result.get('finished')}"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate a health_new fall event for the Go2 gateway.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8090")
    parser.add_argument(
        "--dispatch-mode",
        choices=["task", "event"],
        default="task",
        help="Use the health_new robot-task endpoint or the raw fall-event endpoint.",
    )
    parser.add_argument("--elder-id", default="001")
    parser.add_argument("--location", default="bedroom")
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--source-event-id", default="demo-fall-001")
    parser.add_argument("--external-task-id", default="")
    parser.add_argument("--callback-url", default=None)
    parser.add_argument("--voice-result", default="need_help")
    parser.add_argument("--need-help", action="store_true")
    parser.add_argument("--skip-preflight", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    if not args.skip_preflight:
        preflight = get_json(f"{base_url}/api/preflight")["data"]
        print(
            f"preflight dispatch_ready={preflight['dispatch_ready']} next_action={preflight['next_action']} "
            f"camera={preflight['camera']['camera']} feedback_pending={preflight['feedback']['pending']}"
        )
        if not preflight["dispatch_ready"]:
            raise SystemExit(f"Gateway is not dispatch-ready: {preflight['next_action']}")

    connection = get_json(f"{base_url}/api/connection")["data"]
    print(
        f"connection online={connection['online']} initialized={connection['initialized']} "
        f"ip={connection['ip']} iface={connection['network_interface']}"
    )
    readiness = get_json(f"{base_url}/api/readiness")["data"]
    print(
        f"readiness ready={readiness['ready']} busy={readiness['busy']} "
        f"state_stale={readiness['state_stale']}"
    )

    payload = {
        "elder_id": args.elder_id,
        "location": args.location,
        "confidence": args.confidence,
        "source_event_id": args.source_event_id,
    }
    if args.callback_url:
        payload["callback_url"] = args.callback_url
    if args.external_task_id:
        if args.dispatch_mode == "task":
            payload["taskId"] = args.external_task_id
        else:
            payload["external_task_id"] = args.external_task_id
    if args.dispatch_mode == "task":
        payload["task"] = "confirm_fall"
        submit_url = f"{base_url}/api/tasks/confirm-fall"
    else:
        payload["event"] = "fall_detected"
        submit_url = f"{base_url}/api/events/fall"

    created = post_json(submit_url, payload)["data"]
    task_id = created["task_id"]
    created_source = created.get("source") or {}
    print(
        f"created task: {task_id} dispatch_mode={args.dispatch_mode} "
        f"endpoint={submit_url} external_task_id={created_source.get('externalTaskId')}"
    )

    deadline = time.monotonic() + args.timeout_seconds
    last_status = None
    while time.monotonic() < deadline:
        task = get_json(f"{base_url}/api/tasks/{task_id}/status")["data"]
        status = task["status"]
        if status != last_status:
            progress = task.get("progress") or {}
            print(
                f"status={status} rev={task.get('revision')} step={task.get('step')} "
                f"external_task_id={task.get('external_task_id')} "
                f"progress={progress.get('percent')} camera={task.get('camera')} voice={task.get('voice')}"
            )
            last_status = status
        if status in {"finished", "failed", "cancelled"}:
            result = get_json(f"{base_url}/api/tasks/{task_id}/result")["data"]
            print_result("result", result)
            latest = get_json(f"{base_url}/api/tasks/latest")["data"]
            print_result("latest", latest)
            if status == "finished" and args.voice_result:
                voice_url = f"{base_url}/api/tasks/{task_id}/voice-result"
                if args.external_task_id:
                    voice_url = f"{base_url}/api/tasks/external/{args.external_task_id}/voice-result"
                post_json(
                    voice_url,
                    {"voice_result": args.voice_result, "need_help": args.need_help},
                )
                result = get_json(f"{base_url}/api/tasks/{task_id}/result")["data"]
                print_result("result_after_voice", result)
                latest = get_json(f"{base_url}/api/tasks/latest")["data"]
                print_result("latest_after_voice", latest)
            return
        time.sleep(args.poll_seconds)

    raise SystemExit(f"Timed out waiting for task {task_id}")


if __name__ == "__main__":
    main()
