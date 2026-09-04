from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAILED: {message}")


def request_json(url: str, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 5.0) -> dict:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        with urlopen(Request(url, data=body, headers=headers, method=method), timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code} {method} {url}: {response_body}") from exc
    except URLError as exc:
        raise SystemExit(f"FAILED: cannot reach gateway {url}: {exc}") from exc


def request_bytes(url: str, timeout: float = 5.0) -> bytes:
    try:
        with urlopen(Request(url, method="GET"), timeout=timeout) as response:
            return response.read()
    except HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code} GET {url}: {response_body}") from exc
    except URLError as exc:
        raise SystemExit(f"FAILED: cannot reach gateway {url}: {exc}") from exc


class CallbackRecorder:
    def __init__(self, host: str) -> None:
        self._lock = threading.Lock()
        self.payloads: list[dict] = []
        self.server = self._build_server(host)
        self.thread = threading.Thread(target=self.server.serve_forever, name="field-callback", daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}/api/robot/callback"

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1.0)

    def wait_for_terminal(self, task_id: str, timeout_seconds: float) -> dict:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            with self._lock:
                for payload in self.payloads:
                    if payload.get("task_id") == task_id and payload.get("finished") is True:
                        return payload
            time.sleep(0.1)
        with self._lock:
            seen = list(self.payloads)
        raise SystemExit(f"FAILED: terminal callback not received for task_id={task_id}, seen={seen}")

    def _build_server(self, host: str) -> ThreadingHTTPServer:
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                payload = json.loads(body.decode("utf-8"))
                with recorder._lock:
                    recorder.payloads.append(payload)
                progress = payload.get("progress") or {}
                print(
                    f"callback task={payload.get('task_id')} rev={payload.get('revision')} "
                    f"status={payload.get('status')} step={payload.get('step')} "
                    f"finished={payload.get('finished')} progress={progress.get('percent')}",
                    flush=True,
                )
                self.send_response(204)
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:
                return

        return ThreadingHTTPServer((host, 0), Handler)


def wait_for_terminal_task(base_url: str, task_id: str, timeout_seconds: float, poll_seconds: float) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        task = request_json(f"{base_url}/api/tasks/{task_id}", timeout=5.0)["data"]
        if task["status"] in {"finished", "failed", "cancelled"}:
            return task
        time.sleep(poll_seconds)
    raise SystemExit(f"FAILED: task did not finish before timeout task_id={task_id}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run staged field acceptance checks against a running Go2 gateway. Defaults to non-motion checks."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8090")
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--task-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--require-dispatch-ready", action="store_true")
    parser.add_argument("--allow-readonly", action="store_true", help="Allow GO2_CONTROL_ENABLED=false during non-motion checks.")
    parser.add_argument("--exercise-camera", action="store_true", help="Fetch /api/camera/snapshot and validate JPEG bytes.")
    parser.add_argument("--allow-motion", action="store_true", help="Required before any task dispatch that can move the robot.")
    parser.add_argument("--dispatch-fall", action="store_true", help="Dispatch a confirm_fall task through /api/events/fall.")
    parser.add_argument("--expect-callback", action="store_true", help="Start a local health_new-style callback receiver and require the terminal callback.")
    parser.add_argument("--callback-host", default="127.0.0.1")
    parser.add_argument("--location", default="bedroom")
    parser.add_argument("--elder-id", default="field-elder-001")
    parser.add_argument("--source-event-id", default="field-camera-fall-001")
    parser.add_argument("--external-task-id", default="field-health-task-001")
    parser.add_argument("--need-help", action="store_true", help="Record a voice result after the task is accepted.")
    parser.add_argument("--dump-json", action="store_true")
    args = parser.parse_args()

    expect(not args.expect_callback or args.dispatch_fall, "--expect-callback requires --dispatch-fall")

    base_url = args.base_url.rstrip("/")
    health = request_json(f"{base_url}/health", timeout=args.timeout_seconds)["data"]
    status = request_json(f"{base_url}/api/status", timeout=args.timeout_seconds)["data"]
    preflight = request_json(f"{base_url}/api/preflight", timeout=args.timeout_seconds)["data"]
    capabilities = request_json(f"{base_url}/api/capabilities", timeout=args.timeout_seconds)["data"]
    location = request_json(
        f"{base_url}/api/locations/resolve?location={quote('bedroom')}",
        timeout=args.timeout_seconds,
    )["data"]
    chinese_location = request_json(
        f"{base_url}/api/locations/resolve?location={quote(chr(21351) + chr(23460))}",
        timeout=args.timeout_seconds,
    )["data"]
    camera = request_json(f"{base_url}/api/camera/status", timeout=args.timeout_seconds)["data"]
    voice = request_json(f"{base_url}/api/voice/status", timeout=args.timeout_seconds)["data"]
    feedback = request_json(f"{base_url}/api/feedback/status", timeout=args.timeout_seconds)["data"]

    expect(health["service"] == "go2-gateway", f"unexpected health payload: {health}")
    expect(status["robot_id"], f"status missing robot_id: {status}")
    expect(capabilities["gateway"]["sdk_wrapped"] is True, f"SDK wrapper flag changed: {capabilities}")
    expect("get_status" in capabilities["gateway"]["methods"], f"gateway methods incomplete: {capabilities}")
    expect(capabilities["events"]["fall"]["submit_url"] == "/api/events/fall", f"fall event URL changed: {capabilities}")
    expect(capabilities["tasks"]["urls"]["confirm_fall"] == "/api/tasks/confirm-fall", f"task URL changed: {capabilities}")
    expect(preflight["camera"]["sampled"] is False, f"preflight should not sample camera: {preflight}")
    expect(location["location"] == "bedroom" and location["known"] is True, f"bedroom resolve failed: {location}")
    expect(chinese_location["location"] == "bedroom" and chinese_location["known"] is True, f"Chinese bedroom resolve failed: {chinese_location}")
    expect("camera" in camera and "stream_url" in camera, f"camera status incomplete: {camera}")
    expect("ready" in voice and "mode" in voice, f"voice status incomplete: {voice}")
    expect("pending" in feedback and "worker_alive" in feedback, f"feedback status incomplete: {feedback}")

    next_action = preflight.get("next_action")
    if args.require_dispatch_ready:
        expect(preflight["dispatch_ready"] is True, f"gateway is not dispatch-ready: {preflight}")
    elif args.allow_readonly and next_action == "CONTROL_DISABLED":
        pass
    else:
        expect(
            preflight["dispatch_ready"] is True or next_action in {None, "dispatch"},
            f"gateway has a dispatch blocker: {preflight}",
        )

    if args.exercise_camera:
        snapshot = request_bytes(f"{base_url}/api/camera/snapshot", timeout=args.timeout_seconds)
        expect(snapshot.startswith(b"\xff\xd8") and snapshot.endswith(b"\xff\xd9"), "camera snapshot is not a JPEG")
        print(f"camera snapshot ok bytes={len(snapshot)}")

    task = None
    callback = CallbackRecorder(args.callback_host) if args.expect_callback else None
    if callback:
        callback.start()
        print(f"callback receiver listening url={callback.url}")
    try:
        if args.dispatch_fall:
            expect(args.allow_motion, "--dispatch-fall requires --allow-motion because it can move the robot")
            payload = {
                "event": "fall_detected",
                "elder_id": args.elder_id,
                "location": args.location,
                "confidence": 0.95,
                "source_event_id": args.source_event_id,
                "external_task_id": args.external_task_id,
            }
            if callback:
                payload["callback_url"] = callback.url
            accepted = request_json(f"{base_url}/api/events/fall", method="POST", payload=payload, timeout=args.timeout_seconds)["data"]
            task_id = accepted["task_id"]
            if args.need_help:
                request_json(
                    f"{base_url}/api/tasks/{task_id}/voice-result",
                    method="POST",
                    payload={"voice_result": "need_help", "need_help": True},
                    timeout=args.timeout_seconds,
                )
            task = wait_for_terminal_task(base_url, task_id, args.task_timeout_seconds, args.poll_seconds)
            result = request_json(f"{base_url}/api/tasks/external/{args.external_task_id}/result", timeout=args.timeout_seconds)["data"]
            expect(task["status"] == "finished", f"fall task did not finish successfully: {task}")
            expect(result["external_task_id"] == args.external_task_id, f"external result mismatch: {result}")
            expect(result["confirm"] == "elder_present", f"fall confirmation result changed: {result}")
            if callback:
                terminal_callback = callback.wait_for_terminal(task_id, args.task_timeout_seconds)
                expect(terminal_callback["status"] == "finished", f"terminal callback status changed: {terminal_callback}")
                expect(terminal_callback["external_task_id"] == args.external_task_id, f"terminal callback external task id changed: {terminal_callback}")
                expect(terminal_callback["source_event_id"] == args.source_event_id, f"terminal callback source event id changed: {terminal_callback}")
                expect(terminal_callback.get("progress", {}).get("percent") == 100, f"terminal callback progress changed: {terminal_callback}")
                print(f"terminal callback ok task_id={task_id} revision={terminal_callback.get('revision')}")
            print(
                "fall dispatch ok "
                f"task_id={task_id} status={task['status']} "
                f"external_task_id={args.external_task_id}"
            )
    finally:
        if callback:
            callback.stop()

    print(
        "field acceptance ok "
        f"mode={preflight.get('mode')} "
        f"online={status.get('online')} "
        f"battery={status.get('battery')} "
        f"dispatch_ready={preflight.get('dispatch_ready')} "
        f"next_action={next_action} "
        f"camera={camera.get('camera')} "
        f"voice_ready={voice.get('ready')} "
        f"feedback_pending={feedback.get('pending')}"
    )

    if args.dump_json:
        print(
            json.dumps(
                {
                    "health": health,
                    "status": status,
                    "preflight": preflight,
                    "capabilities": capabilities,
                    "location": location,
                    "chinese_location": chinese_location,
                    "camera": camera,
                    "voice": voice,
                    "feedback": feedback,
                    "task": task,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
