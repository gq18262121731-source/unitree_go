from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAILED: {message}")


def read_json(request: Request, timeout: float = 5.0) -> dict:
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise SystemExit(f"FAILED: cannot reach gateway: {exc}") from exc


def get_json(url: str, timeout: float = 5.0) -> dict:
    return read_json(Request(url, method="GET"), timeout=timeout)


def post_json(url: str, payload: dict, timeout: float = 5.0) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return read_json(request, timeout=timeout)


class CallbackRecorder:
    def __init__(self, host: str) -> None:
        self._lock = threading.Lock()
        self.payloads: list[dict] = []
        self.server = self._build_server(host)
        self.thread = threading.Thread(target=self.server.serve_forever, name="health-new-callback", daemon=True)

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

    def wait_for(self, task_id: str, predicate, description: str, timeout_seconds: float) -> dict:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            with self._lock:
                for payload in self.payloads:
                    if payload.get("task_id") == task_id and predicate(payload):
                        return payload
            time.sleep(0.1)
        with self._lock:
            seen = list(self.payloads)
        raise SystemExit(f"FAILED: callback did not receive {description} for {task_id}, seen={seen}")

    def revisions_for(self, task_id: str) -> list[int]:
        with self._lock:
            return [
                payload["revision"]
                for payload in self.payloads
                if payload.get("task_id") == task_id and payload.get("revision") is not None
            ]

    def _build_server(self, host: str) -> ThreadingHTTPServer:
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                payload = json.loads(body.decode("utf-8"))
                with recorder._lock:
                    recorder.payloads.append(payload)
                result = payload.get("result") or {}
                progress = payload.get("progress") or {}
                print(
                    f"callback task={payload.get('task_id')} rev={payload.get('revision')} status={payload.get('status')} "
                    f"step={payload.get('step')} camera={payload.get('camera')} "
                    f"voice={payload.get('voice')} confirm={result.get('confirm')} "
                    f"progress={progress.get('percent')} finished={payload.get('finished')}",
                    flush=True,
                )
                self.send_response(204)
                self.end_headers()

            def log_message(self, format: str, *args) -> None:
                return

        return ThreadingHTTPServer((host, 0), Handler)


def wait_for_terminal_task(base_url: str, task_id: str, timeout_seconds: float, poll_seconds: float) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_task = None
    while time.monotonic() < deadline:
        response = get_json(f"{base_url}/api/tasks/{task_id}/status")
        last_task = response["data"]
        print(
            f"poll task={task_id} rev={last_task.get('revision')} status={last_task.get('status')} "
            f"step={last_task.get('step')} camera={last_task.get('camera')} voice={last_task.get('voice')}",
            flush=True,
        )
        if last_task["status"] in {"finished", "failed", "cancelled"}:
            return last_task
        time.sleep(poll_seconds)
    raise SystemExit(f"FAILED: task {task_id} did not finish, last={last_task}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify a running Go2 gateway fall-confirmation loop over HTTP.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8090")
    parser.add_argument(
        "--dispatch-mode",
        choices=["task", "event"],
        default="task",
        help="Use the health_new robot-task endpoint or the raw fall-event endpoint.",
    )
    parser.add_argument("--callback-host", default="127.0.0.1")
    parser.add_argument("--elder-id", default="001")
    parser.add_argument("--location", default="bedroom")
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--source-event-id", default=f"running-gateway-fall-{int(time.time())}")
    parser.add_argument("--camera-id", default="running-fixed-camera-01")
    parser.add_argument("--external-task-id", default="")
    parser.add_argument("--voice-result", default="need_help")
    parser.add_argument("--need-help", action="store_true")
    parser.add_argument("--skip-voice-result", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    callback = CallbackRecorder(args.callback_host)
    callback.start()

    try:
        health = get_json(f"{base_url}/health")["data"]
        expect(health["service"] == "go2-gateway", f"unexpected health service: {health}")
        expect(health["ready"] is True, f"gateway health is not ready: {health}")
        expect(health["controlEnabled"] is True, f"gateway control is disabled: {health}")
        expect(health["activeTask"]["active"] is False, f"gateway has an active task before test: {health}")
        print(
            f"health ok ready={health.get('ready')} robotOnline={health.get('robotOnline')} "
            f"active={health.get('activeTask', {}).get('active')} feedbackPending={health.get('feedback', {}).get('pending')}",
            flush=True,
        )

        capabilities = get_json(f"{base_url}/api/capabilities")["data"]
        expect(
            capabilities["events"]["fall"]["submit_url"] == "/api/events/fall",
            f"fall event capability changed: {capabilities}",
        )
        expect(
            "event_id" in capabilities["events"]["fall"]["idempotency_aliases"],
            f"fall event idempotency aliases changed: {capabilities}",
        )
        expect(
            capabilities["tasks"]["urls"]["status"] == "/api/tasks/{task_id}/status",
            f"task status capability changed: {capabilities}",
        )
        expect(
            capabilities["tasks"]["urls"]["summary"] == "/api/tasks/summary",
            f"task summary capability changed: {capabilities}",
        )
        expect(
            capabilities["tasks"]["urls"]["external_lookup"] == "/api/tasks/external/{external_task_id}",
            f"external task lookup capability changed: {capabilities}",
        )
        expect(
            capabilities["tasks"]["urls"]["external_status"] == "/api/tasks/external/{external_task_id}/status",
            f"external task status capability changed: {capabilities}",
        )
        expect(
            capabilities["tasks"]["urls"]["external_result"] == "/api/tasks/external/{external_task_id}/result",
            f"external task result capability changed: {capabilities}",
        )
        expect(
            capabilities["tasks"]["urls"]["external_timeline"] == "/api/tasks/external/{external_task_id}/timeline",
            f"external task timeline capability changed: {capabilities}",
        )
        expect(
            capabilities["tasks"]["urls"]["external_feedback_replay"] == "/api/tasks/external/{external_task_id}/feedback/replay",
            f"external feedback replay capability changed: {capabilities}",
        )
        expect(
            capabilities["tasks"]["urls"]["external_voice_result"] == "/api/tasks/external/{external_task_id}/voice-result",
            f"external voice result capability changed: {capabilities}",
        )
        expect(
            capabilities["tasks"]["urls"]["external_cancel"] == "/api/tasks/external/{external_task_id}/cancel",
            f"external cancel capability changed: {capabilities}",
        )
        expect(
            capabilities["tasks"]["urls"]["confirm_fall"] == "/api/tasks/confirm-fall",
            f"confirm fall task capability changed: {capabilities}",
        )
        expect(
            capabilities["tasks"]["urls"]["feedback_replay"] == "/api/tasks/{task_id}/feedback/replay",
            f"feedback replay capability changed: {capabilities}",
        )
        expect(
            capabilities["status"]["health_url"] == "/health",
            f"health capability changed: {capabilities}",
        )
        expect(
            capabilities["status"]["preflight_url"] == "/api/preflight",
            f"preflight capability changed: {capabilities}",
        )
        expect(
            capabilities["voice"]["status_url"] == "/api/voice/status",
            f"voice status capability changed: {capabilities}",
        )
        expect(
            capabilities["tasks"]["location_resolve_url"] == "/api/locations/resolve?location={location}",
            f"location resolve capability changed: {capabilities}",
        )
        print(
            f"capabilities ok fall={capabilities['events']['fall']['submit_url']} "
            f"task_status={capabilities['tasks']['urls']['status']}",
            flush=True,
        )

        readiness = get_json(f"{base_url}/api/readiness")["data"]
        expect(readiness["ready"] is True, f"gateway is not ready: {readiness}")
        expect(readiness["control_enabled"] is True, f"gateway control is disabled: {readiness}")
        print(
            f"readiness ok robot={readiness.get('robot_id')} "
            f"online={readiness.get('online')} initialized={readiness.get('initialized')}",
            flush=True,
        )

        location_resolve = get_json(f"{base_url}/api/locations/resolve?location=%E5%8D%A7%E5%AE%A4")["data"]
        expect(location_resolve["location"] == "bedroom", f"Chinese bedroom alias changed: {location_resolve}")
        expect(location_resolve["known"] is True, f"Chinese bedroom alias should be known: {location_resolve}")
        print(
            f"location resolve ok input={location_resolve.get('input')} "
            f"location={location_resolve.get('location')} source={location_resolve.get('source')}",
            flush=True,
        )

        preflight = get_json(f"{base_url}/api/preflight")["data"]
        expect(preflight["dispatch_ready"] is True, f"gateway preflight is not dispatch ready: {preflight}")
        expect(preflight["camera"]["sampled"] is False, f"preflight sampled camera unexpectedly: {preflight}")
        expect("voice" in preflight, f"preflight missing voice status: {preflight}")
        expect("voice_ready" in preflight["checks"], f"preflight missing voice readiness check: {preflight}")
        print(
            f"preflight ok dispatch_ready={preflight.get('dispatch_ready')} "
            f"voice_ready={preflight.get('checks', {}).get('voice_ready', {}).get('ok')} "
            f"next_action={preflight.get('next_action')}",
            flush=True,
        )

        payload = {
            "elder_id": args.elder_id,
            "location": args.location,
            "confidence": args.confidence,
            "source_event_id": args.source_event_id,
            "camera_id": args.camera_id,
            "callback_url": callback.url,
        }
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
        print(f"created task={task_id} dispatch_mode={args.dispatch_mode} callback_url={callback.url}", flush=True)

        finished = wait_for_terminal_task(base_url, task_id, args.timeout_seconds, args.poll_seconds)
        expect(finished["task"] == "confirm_fall", f"unexpected task type: {finished}")
        expect(finished["status"] == "finished", f"task did not finish cleanly: {finished}")
        expect(finished["camera"] == "ready", f"camera not ready: {finished}")

        terminal_callback = callback.wait_for(
            task_id,
            lambda payload: payload.get("finished") is True,
            "terminal callback",
            args.timeout_seconds,
        )
        expect(terminal_callback["status"] == "finished", f"bad terminal callback: {terminal_callback}")
        expect(terminal_callback.get("revision") is not None, f"terminal callback missing revision: {terminal_callback}")
        expect(terminal_callback.get("elder_id") == args.elder_id, f"terminal callback elder_id changed: {terminal_callback}")
        expect(terminal_callback.get("location") == args.location, f"terminal callback location changed: {terminal_callback}")
        expect(terminal_callback.get("confidence") == args.confidence, f"terminal callback confidence changed: {terminal_callback}")
        expect(
            terminal_callback.get("source_event_id") == args.source_event_id,
            f"terminal callback source_event_id changed: {terminal_callback}",
        )
        expect(terminal_callback.get("camera_id") == args.camera_id, f"terminal callback camera_id changed: {terminal_callback}")
        if args.external_task_id:
            expect(
                terminal_callback.get("external_task_id") == args.external_task_id,
                f"terminal callback external_task_id changed: {terminal_callback}",
            )
        expect(terminal_callback.get("progress", {}).get("percent") == 100, f"terminal callback missing progress: {terminal_callback}")

        result = get_json(f"{base_url}/api/tasks/{task_id}/result")["data"]
        expect(result["finished"] is True, f"result not terminal: {result}")
        expect(result.get("revision", -1) >= terminal_callback.get("revision", -1), f"result revision moved backward: {result} {terminal_callback}")
        expect(result["confirm"] == "elder_present", f"unexpected confirm result: {result}")
        expect(result["robot_camera"]["streamUrl"], f"missing robot camera stream URL: {result}")
        if args.external_task_id:
            expect(result["external_task_id"] == args.external_task_id, f"result external_task_id changed: {result}")
            external_status = get_json(f"{base_url}/api/tasks/external/{args.external_task_id}/status")["data"]
            expect(external_status["task_id"] == task_id, f"external status task_id changed: {external_status}")
            expect(external_status["status"] == "finished", f"external status changed: {external_status}")
            external_result = get_json(f"{base_url}/api/tasks/external/{args.external_task_id}/result")["data"]
            expect(external_result["task_id"] == task_id, f"external result task_id changed: {external_result}")
            expect(external_result["finished"] is True, f"external result not terminal: {external_result}")
            external_timeline = get_json(f"{base_url}/api/tasks/external/{args.external_task_id}/timeline")["data"]
            expect(external_timeline["task_id"] == task_id, f"external timeline task_id changed: {external_timeline}")
            expect(external_timeline["progress"]["percent"] == 100, f"external timeline progress changed: {external_timeline}")
            print(
                f"external task lookup ok external_task_id={args.external_task_id} "
                f"status={external_status.get('status')} progress={external_timeline.get('progress', {}).get('percent')}",
                flush=True,
            )

        camera_status = get_json(f"{base_url}/api/camera/status")["data"]
        expect(camera_status["camera"] == "ready", f"camera status was not ready after task: {camera_status}")
        expect(camera_status["online"] is True, f"camera status did not report online: {camera_status}")
        print(
            f"camera ok status={camera_status.get('camera')} last_frame={camera_status.get('last_frame_time')}",
            flush=True,
        )

        if not args.skip_voice_result:
            voice_url = f"{base_url}/api/tasks/{task_id}/voice-result"
            if args.external_task_id:
                voice_url = f"{base_url}/api/tasks/external/{args.external_task_id}/voice-result"
            voice = post_json(
                voice_url,
                {"voice_result": args.voice_result, "need_help": args.need_help},
            )["data"]
            expect(voice["voice"] == "completed", f"voice result not recorded: {voice}")
            voice_callback = callback.wait_for(
                task_id,
                lambda payload: payload.get("voice") == "completed",
                "voice-result callback",
                args.timeout_seconds,
            )
            expect(voice_callback["result"].get("voiceResult") == args.voice_result, f"bad voice callback: {voice_callback}")
            expect(voice_callback.get("revision", -1) > terminal_callback.get("revision", -1), f"voice callback revision did not advance: {voice_callback}")
            expect(voice_callback.get("progress", {}).get("percent") == 100, f"voice callback missing progress: {voice_callback}")

        callback_revisions = callback.revisions_for(task_id)
        expect(callback_revisions == sorted(callback_revisions), f"callback revisions are not FIFO: {callback_revisions}")
        feedback_status = get_json(f"{base_url}/api/feedback/status")["data"]
        expect(
            feedback_status["sent"] >= len(callback_revisions),
            f"feedback status sent count did not cover callbacks: {feedback_status} revisions={callback_revisions}",
        )
        expect(feedback_status["failed"] == 0, f"feedback status reported failures: {feedback_status}")
        print(
            f"feedback ok sent={feedback_status.get('sent')} failed={feedback_status.get('failed')} "
            f"pending={feedback_status.get('pending')}",
            flush=True,
        )

        print("running gateway fall loop verification passed")
    finally:
        callback.stop()


if __name__ == "__main__":
    main()
