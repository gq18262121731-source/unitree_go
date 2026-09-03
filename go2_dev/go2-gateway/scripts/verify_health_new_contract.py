from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.main import create_app


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAILED: {message}")


def wait_for_terminal_task(client: TestClient, task_id: str, timeout_seconds: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_task = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/tasks/{task_id}/status")
        expect(response.status_code == 200, f"task status returned {response.status_code}")
        last_task = response.json()["data"]
        if last_task["status"] in {"finished", "failed", "cancelled"}:
            return last_task
        time.sleep(0.05)
    raise SystemExit(f"FAILED: task {task_id} did not finish, last={last_task}")


def wait_for_feedback_sent(client: TestClient, minimum_sent: int, timeout_seconds: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_status = None
    while time.monotonic() < deadline:
        response = client.get("/api/feedback/status")
        expect(response.status_code == 200, f"feedback status returned {response.status_code}")
        last_status = response.json()["data"]
        if last_status["sent"] >= minimum_sent:
            return last_status
        time.sleep(0.05)
    raise SystemExit(f"FAILED: feedback sent count did not reach {minimum_sent}, last={last_status}")


def wait_for_compact_task_status(client: TestClient, task_id: str, timeout_seconds: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_status = None
    while time.monotonic() < deadline:
        response = client.get("/api/status")
        expect(response.status_code == 200, f"compact status returned {response.status_code}")
        last_status = response.json()["data"]
        if last_status.get("task_id") == task_id:
            return last_status
        time.sleep(0.05)
    raise SystemExit(f"FAILED: compact status did not expose active task {task_id}, last={last_status}")


class CallbackRecorder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.payloads: list[dict] = []
        self.server = self._build_server()
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

    def wait_for_terminal_payload(self, task_id: str, timeout_seconds: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            with self._lock:
                for payload in self.payloads:
                    if payload.get("task_id") == task_id and payload.get("finished") is True:
                        return payload
            time.sleep(0.05)
        with self._lock:
            seen = list(self.payloads)
        raise SystemExit(f"FAILED: callback did not receive terminal payload for {task_id}, seen={seen}")

    def wait_for_voice_payload(self, task_id: str, timeout_seconds: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            with self._lock:
                for payload in self.payloads:
                    if payload.get("task_id") == task_id and payload.get("voice") == "completed":
                        return payload
            time.sleep(0.05)
        with self._lock:
            seen = list(self.payloads)
        raise SystemExit(f"FAILED: callback did not receive voice payload for {task_id}, seen={seen}")

    def wait_for_payload_count(self, task_id: str, minimum_count: int, timeout_seconds: float = 5.0) -> list[dict]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            with self._lock:
                payloads = [payload for payload in self.payloads if payload.get("task_id") == task_id]
                if len(payloads) >= minimum_count:
                    return payloads
            time.sleep(0.05)
        with self._lock:
            seen = list(self.payloads)
        raise SystemExit(f"FAILED: callback did not receive {minimum_count} payloads for {task_id}, seen={seen}")

    def count_for(self, task_id: str) -> int:
        with self._lock:
            return sum(1 for payload in self.payloads if payload.get("task_id") == task_id)

    def revisions_for(self, task_id: str) -> list[int]:
        with self._lock:
            return [
                payload["revision"]
                for payload in self.payloads
                if payload.get("task_id") == task_id and payload.get("revision") is not None
            ]

    def _build_server(self) -> ThreadingHTTPServer:
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                payload = json.loads(body.decode("utf-8"))
                with recorder._lock:
                    recorder.payloads.append(payload)
                self.send_response(204)
                self.end_headers()

            def log_message(self, format: str, *args) -> None:
                return

        return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


def main() -> None:
    temp_dir = TemporaryDirectory()
    audit_path = Path(temp_dir.name) / "task-events.jsonl"
    settings = Settings(
        mode="mock",
        state_stale_seconds=2.0,
        task_audit_enabled=True,
        task_audit_log_path=str(audit_path),
    )
    app = create_app(settings)
    callback = CallbackRecorder()
    callback.start()

    try:
        with TestClient(app) as client:
            health = client.get("/health")
            expect(health.status_code == 200, f"health returned {health.status_code}")
            health_data = health.json()["data"]
            expect(health_data["service"] == "go2-gateway", f"health service changed: {health_data}")
            expect(health_data["ready"] is True, f"health ready changed: {health_data}")
            expect(health_data["controlEnabled"] is True, f"health controlEnabled changed: {health_data}")
            expect(health_data["activeTask"]["active"] is False, f"health active task should be idle: {health_data}")
            expect(health_data["feedback"]["pending"] == 0, f"health feedback pending changed: {health_data}")

            capabilities = client.get("/api/capabilities")
            expect(capabilities.status_code == 200, f"capabilities returned {capabilities.status_code}")
            capabilities_data = capabilities.json()["data"]
            expect(
                capabilities_data["tasks"]["confirm_fall_statuses"]
                == ["waiting", "running", "moving", "arrived", "checking", "finished", "failed", "cancelled"],
                f"confirm_fall status capability changed: {capabilities_data}",
            )
            expect(
                capabilities_data["tasks"]["terminal_statuses"] == ["finished", "failed", "cancelled"],
                f"terminal status capability changed: {capabilities_data}",
            )
            expect(
                capabilities_data["tasks"]["terminal_restore_from_audit"] is True,
                f"terminal restore capability changed: {capabilities_data}",
            )
            expect(
                capabilities_data["tasks"]["queueing"] == "fifo_waiting_tasks",
                f"task queueing capability changed: {capabilities_data}",
            )
            expect(
                capabilities_data["tasks"]["queue_fields"]
                == ["queue_position", "queue_size", "queue_head", "blocked_by_task_id", "queue"],
                f"task queue fields changed: {capabilities_data}",
            )
            expect(
                capabilities_data["tasks"]["urls"]["target_move"] == "/api/tasks/target-move",
                f"target_move capability URL changed: {capabilities_data}",
            )
            expect(
                capabilities_data["tasks"]["urls"]["confirm_fall"] == "/api/tasks/confirm-fall",
                f"confirm_fall capability URL changed: {capabilities_data}",
            )
            expect(
                capabilities_data["tasks"]["urls"]["external_lookup"] == "/api/tasks/external/{external_task_id}",
                f"external task lookup capability URL changed: {capabilities_data}",
            )
            expect(
                capabilities_data["tasks"]["urls"]["latest"] == "/api/tasks/latest",
                f"latest task capability URL changed: {capabilities_data}",
            )
            expect(
                capabilities_data["tasks"]["urls"]["queue"] == "/api/tasks/queue",
                f"task queue capability URL changed: {capabilities_data}",
            )
            expect(
                capabilities_data["tasks"]["urls"]["external_status"] == "/api/tasks/external/{external_task_id}/status",
                f"external task status capability URL changed: {capabilities_data}",
            )
            expect(
                capabilities_data["tasks"]["urls"]["external_result"] == "/api/tasks/external/{external_task_id}/result",
                f"external task result capability URL changed: {capabilities_data}",
            )
            expect(
                capabilities_data["tasks"]["urls"]["external_timeline"] == "/api/tasks/external/{external_task_id}/timeline",
                f"external task timeline capability URL changed: {capabilities_data}",
            )
            expect(
                capabilities_data["tasks"]["urls"]["external_audit_log"] == "/api/tasks/external/{external_task_id}/audit-log",
                f"external task audit log capability URL changed: {capabilities_data}",
            )
            expect(
                capabilities_data["tasks"]["urls"]["external_feedback_replay"] == "/api/tasks/external/{external_task_id}/feedback/replay",
                f"external feedback replay capability URL changed: {capabilities_data}",
            )
            expect(
                capabilities_data["tasks"]["urls"]["external_voice_result"] == "/api/tasks/external/{external_task_id}/voice-result",
                f"external voice result capability URL changed: {capabilities_data}",
            )
            expect(
                capabilities_data["tasks"]["urls"]["external_cancel"] == "/api/tasks/external/{external_task_id}/cancel",
                f"external cancel capability URL changed: {capabilities_data}",
            )
            expect(
                capabilities_data["tasks"]["urls"]["task_audit_log"] == "/api/tasks/{task_id}/audit-log",
                f"task audit log capability URL changed: {capabilities_data}",
            )
            expect(
                capabilities_data["tasks"]["urls"]["feedback_replay"] == "/api/tasks/{task_id}/feedback/replay",
                f"feedback replay capability URL changed: {capabilities_data}",
            )
            expect(
                capabilities_data["feedback"]["replay_url"] == "/api/tasks/{task_id}/feedback/replay",
                f"feedback replay status URL changed: {capabilities_data}",
            )
            expect(
                capabilities_data["status"]["health_url"] == "/health",
                f"health capability URL changed: {capabilities_data}",
            )
            expect(
                capabilities_data["status"]["preflight_url"] == "/api/preflight",
                f"preflight capability URL changed: {capabilities_data}",
            )
            expect(
                capabilities_data["events"]["fall"]["submit_url"] == "/api/events/fall",
                f"fall event capability URL changed: {capabilities_data}",
            )
            expect(
                "event_id" in capabilities_data["events"]["fall"]["idempotency_aliases"],
                f"fall event idempotency aliases changed: {capabilities_data}",
            )
            expect(
                capabilities_data["events"]["fall"]["external_task_id_idempotency"] is True,
                f"external task idempotency capability changed: {capabilities_data}",
            )
            expect(
                capabilities_data["voice"]["status_url"] == "/api/voice/status",
                f"voice status capability URL changed: {capabilities_data}",
            )
            expect(
                capabilities_data["tasks"]["location_resolve_url"] == "/api/locations/resolve?location={location}",
                f"location resolve capability URL changed: {capabilities_data}",
            )

            readiness = client.get("/api/readiness")
            expect(readiness.status_code == 200, f"readiness returned {readiness.status_code}")
            readiness_data = readiness.json()["data"]
            expect(readiness_data["ready"] is True, "robot is not ready in mock mode")
            expect(readiness_data["accepting_tasks"] is True, "robot should accept queued tasks in mock mode")
            expect(readiness_data["control_enabled"] is True, "mock mode should have control enabled")
            expect(readiness_data["last_error"] is None, "mock mode should not have a last_error")

            preflight = client.get("/api/preflight")
            expect(preflight.status_code == 200, f"preflight returned {preflight.status_code}: {preflight.text}")
            preflight_data = preflight.json()["data"]
            expect(preflight_data["dispatch_ready"] is True, f"preflight dispatch readiness changed: {preflight_data}")
            expect(preflight_data["dispatch_immediate_ready"] is True, f"preflight immediate readiness changed: {preflight_data}")
            expect(preflight_data["dispatch_accepting"] is True, f"preflight task acceptance changed: {preflight_data}")
            expect(preflight_data["camera"]["sampled"] is False, f"preflight should not sample camera: {preflight_data}")
            expect(preflight_data["checks"]["dispatch_accepting"]["ok"] is True, f"preflight dispatch acceptance changed: {preflight_data}")
            expect(preflight_data["checks"]["dispatch_idle"]["ok"] is True, f"preflight dispatch idle changed: {preflight_data}")
            expect(preflight_data["voice"]["ready"] is True, f"preflight voice readiness changed: {preflight_data}")
            expect(preflight_data["checks"]["voice_ready"]["ok"] is True, f"preflight voice check changed: {preflight_data}")

            status = client.get("/api/status")
            expect(status.status_code == 200, f"status returned {status.status_code}")
            status_data = status.json()["data"]
            expect(status_data["online"] is True, "robot should be online in mock mode")
            expect(status_data["battery"] == 78, "mock battery contract changed")
            expect(status_data["battery_detail"]["voltage"] == 31.2, f"status battery detail changed: {status_data}")
            expect(status_data["action"] == "mock-locomotion", f"status action changed: {status_data}")
            expect(status_data["busy"] is False, f"status busy flag changed: {status_data}")
            expect(status_data["control_enabled"] is True, f"status control flag changed: {status_data}")
            expect(status_data["state_stale"] is False, f"status stale flag changed: {status_data}")
            expect(status_data["last_seen"], f"status last_seen missing: {status_data}")

            location_resolve = client.get("/api/locations/resolve", params={"location": "卧室"})
            expect(location_resolve.status_code == 200, f"location resolve returned {location_resolve.status_code}")
            location_data = location_resolve.json()["data"]
            expect(location_data["location"] == "bedroom", f"Chinese bedroom alias changed: {location_data}")
            expect(location_data["known"] is True, f"Chinese bedroom alias should be known: {location_data}")

            target_move = client.post("/api/tasks/target-move", json={"location": "living_room"})
            expect(target_move.status_code == 200, f"target move returned {target_move.status_code}: {target_move.text}")
            target_move_task_id = target_move.json()["data"]["task_id"]
            target_move_finished = wait_for_terminal_task(client, target_move_task_id)
            expect(target_move_finished["task"] == "move_to_target", f"target move task type changed: {target_move_finished}")
            expect(target_move_finished["status"] == "finished", f"target move did not finish: {target_move_finished}")
            expect(target_move_finished["location"] == "living_room", f"target move location changed: {target_move_finished}")

            confirm_task = client.post(
                "/api/tasks/confirm-fall",
                json={
                    "task": "confirm_fall",
                    "elder_id": "task-elder-001",
                    "location": "bedroom",
                    "confidence": 0.93,
                    "source_event_id": "contract-confirm-task-source-001",
                    "camera_id": "contract-confirm-task-camera-01",
                    "taskId": "health-confirm-task-contract-001",
                },
            )
            expect(confirm_task.status_code == 200, f"confirm fall task returned {confirm_task.status_code}: {confirm_task.text}")
            confirm_task_id = confirm_task.json()["data"]["task_id"]
            confirm_task_finished = wait_for_terminal_task(client, confirm_task_id)
            expect(confirm_task_finished["task"] == "confirm_fall", f"confirm task type changed: {confirm_task_finished}")
            expect(confirm_task_finished["status"] == "finished", f"confirm task did not finish: {confirm_task_finished}")
            expect(
                confirm_task_finished["source"]["externalTaskId"] == "health-confirm-task-contract-001",
                f"confirm task external task id changed: {confirm_task_finished}",
            )
            confirm_lookup = client.get("/api/tasks/external/health-confirm-task-contract-001")
            expect(confirm_lookup.status_code == 200, f"confirm task external lookup returned {confirm_lookup.status_code}")
            expect(confirm_lookup.json()["data"]["task_id"] == confirm_task_id, "confirm task external lookup id changed")

            invalid_fall = client.post(
                "/api/events/fall",
                json={"event": "fall_detected", "elder_id": "001", "location": "bedroom", "confidence": 1.5},
                headers={"X-Request-ID": "contract-invalid-fall"},
            )
            expect(invalid_fall.status_code == 422, f"invalid fall event returned {invalid_fall.status_code}")
            invalid_body = invalid_fall.json()
            expect(invalid_body["code"] == "INVALID_REQUEST", f"invalid fall error code changed: {invalid_body}")
            expect(invalid_body["requestId"] == "contract-invalid-fall", f"invalid fall request id changed: {invalid_body}")
            expect(invalid_body["data"]["errors"], "invalid fall should include validation errors")

            task_count_before_blank_fall = len(client.get("/api/tasks").json()["data"])
            blank_fall = client.post(
                "/api/events/fall",
                json={"event": "fall_detected", "elder_id": "   ", "location": "", "confidence": 0.95},
            )
            expect(blank_fall.status_code == 422, f"blank fall event returned {blank_fall.status_code}")
            expect(blank_fall.json()["code"] == "INVALID_REQUEST", f"blank fall error code changed: {blank_fall.json()}")
            expect(
                len(client.get("/api/tasks").json()["data"]) == task_count_before_blank_fall,
                "blank fall event should not create a task",
            )

            fall_payload = {
                "event": "fall_detected",
                "elder_id": "001",
                "location": "bedroom",
                "confidence": 0.95,
                "source_event_id": "contract-camera-fall-001",
                "camera_id": "contract-fixed-camera-01",
                "external_task_id": "health-task-contract-001",
                "callback_url": callback.url,
            }
            missing_event = client.get("/api/events/fall/contract-camera-fall-missing")
            expect(missing_event.status_code == 200, f"missing fall event lookup returned {missing_event.status_code}")
            expect(missing_event.json()["data"]["received"] is False, "missing fall event lookup should not be received")

            created = client.post("/api/events/fall", json=fall_payload)
            expect(created.status_code == 200, f"fall event returned {created.status_code}: {created.text}")
            task_id = created.json()["data"]["task_id"]
            compact_task_status = wait_for_compact_task_status(client, task_id)
            expect(compact_task_status["elder_id"] == "001", "compact status elder_id changed")
            expect(compact_task_status["location"] == "bedroom", "compact status location changed")
            expect(compact_task_status["confidence"] == 0.95, "compact status confidence changed")
            expect(compact_task_status["source_event_id"] == "contract-camera-fall-001", "compact status source_event_id changed")
            expect(compact_task_status["camera_id"] == "contract-fixed-camera-01", "compact status camera_id changed")
            expect(compact_task_status["external_task_id"] == "health-task-contract-001", "compact status external_task_id changed")
            expect(
                [step["name"] for step in compact_task_status["steps"]] == [
                    "receive_event",
                    "moving",
                    "arrived",
                    "robot_camera",
                    "voice_check",
                    "finished",
                ],
                f"compact status steps changed: {compact_task_status}",
            )
            event_lookup = client.get("/api/events/fall/contract-camera-fall-001")
            expect(event_lookup.status_code == 200, f"fall event lookup returned {event_lookup.status_code}")
            event_lookup_data = event_lookup.json()["data"]
            expect(event_lookup_data["received"] is True, "fall event lookup did not report received")
            expect(event_lookup_data["task_id"] == task_id, f"fall event lookup task changed: {event_lookup_data}")

            finished = wait_for_terminal_task(client, task_id)
            expect(finished["task"] == "confirm_fall", "fall event did not create confirm_fall task")
            expect(finished["status"] == "finished", f"task ended as {finished['status']}")
            expect(finished["finished"] is True, "task status terminal flag changed")
            expect(finished["elder_id"] == "001", "task status elder_id changed")
            expect(finished["location"] == "bedroom", "task status location changed")
            expect(finished["confidence"] == 0.95, "task status confidence changed")
            expect(finished["source_event_id"] == "contract-camera-fall-001", "task status source_event_id changed")
            expect(finished["camera_id"] == "contract-fixed-camera-01", "task status camera_id changed")
            expect(finished["external_task_id"] == "health-task-contract-001", "task status external_task_id changed")
            expect(finished["camera"] == "ready", "robot camera was not ready")
            expect(finished["voice"] == "waiting", "voice prompt state should be waiting")
            expect(finished["steps"][-1]["name"] == "finished", f"task status steps changed: {finished}")

            summaries = client.get("/api/tasks/summary?limit=1")
            expect(summaries.status_code == 200, f"task summaries returned {summaries.status_code}: {summaries.text}")
            summary = summaries.json()["data"][0]
            expect(summary["task_id"] == task_id, f"task summary id changed: {summary}")
            expect(summary["finished"] is True, f"task summary terminal flag changed: {summary}")
            expect(summary["source_event_id"] == "contract-camera-fall-001", f"task summary source_event_id changed: {summary}")
            expect(summary["external_task_id"] == "health-task-contract-001", f"task summary external_task_id changed: {summary}")
            expect(summary["steps"][-1]["name"] == "finished", f"task summary steps changed: {summary}")
            expect(summary["progress"]["percent"] == 100, f"task summary progress changed: {summary}")
            expect(summary["queue_position"] is None, f"terminal summary queue position changed: {summary}")
            expect(summary["queue_size"] == 0, f"terminal summary queue size changed: {summary}")

            latest = client.get("/api/tasks/latest")
            expect(latest.status_code == 200, f"latest task returned {latest.status_code}: {latest.text}")
            latest_data = latest.json()["data"]
            expect(latest_data["exists"] is True, f"latest task missing: {latest_data}")
            expect(latest_data["task_id"] == task_id, f"latest task id changed: {latest_data}")
            expect(latest_data["status"] == "finished", f"latest task status changed: {latest_data}")
            expect(latest_data["external_task_id"] == "health-task-contract-001", f"latest external task id changed: {latest_data}")

            summaries_compat = client.get("/api/robot/tasks/summary?limit=1")
            expect(
                summaries_compat.status_code == 200,
                f"compat task summaries returned {summaries_compat.status_code}: {summaries_compat.text}",
            )
            expect(summaries_compat.json()["data"][0]["task_id"] == task_id, "compat task summary id changed")

            external_lookup = client.get("/api/tasks/external/health-task-contract-001")
            expect(external_lookup.status_code == 200, f"external task lookup returned {external_lookup.status_code}: {external_lookup.text}")
            external_lookup_data = external_lookup.json()["data"]
            expect(external_lookup_data["received"] is True, f"external task lookup did not report received: {external_lookup_data}")
            expect(external_lookup_data["task_id"] == task_id, f"external task lookup id changed: {external_lookup_data}")
            expect(
                external_lookup_data["task"]["source_event_id"] == "contract-camera-fall-001",
                f"external task lookup source event changed: {external_lookup_data}",
            )

            external_lookup_compat = client.get("/api/robot/tasks/external/health-task-contract-001")
            expect(
                external_lookup_compat.status_code == 200,
                f"compat external task lookup returned {external_lookup_compat.status_code}: {external_lookup_compat.text}",
            )
            expect(external_lookup_compat.json()["data"]["task_id"] == task_id, "compat external task lookup id changed")

            terminal_callback = callback.wait_for_terminal_payload(task_id)
            callback_statuses = {payload.get("status") for payload in callback.wait_for_payload_count(task_id, 6)}
            expect(
                {"waiting", "running", "moving", "arrived", "checking", "finished"}.issubset(callback_statuses),
                f"callback task status sequence changed: {callback_statuses}",
            )
            expect(terminal_callback["status"] == "finished", "callback terminal status changed")
            expect(terminal_callback["step"] == "finished", "callback terminal step changed")
            expect(terminal_callback["elder_id"] == "001", "callback elder_id changed")
            expect(terminal_callback["location"] == "bedroom", "callback location changed")
            expect(terminal_callback["source_event_id"] == "contract-camera-fall-001", "callback source_event_id changed")
            expect(terminal_callback["camera_id"] == "contract-fixed-camera-01", "callback camera_id changed")
            expect(terminal_callback["external_task_id"] == "health-task-contract-001", "callback external_task_id changed")
            expect(terminal_callback["location_resolution"]["location"] == "bedroom", "callback location resolution changed")
            expect(terminal_callback["location_resolution"]["fallbackUsed"] is False, "callback location fallback changed")
            expect(terminal_callback["steps"][-1]["name"] == "finished", "callback steps changed")
            expect(terminal_callback["progress"]["percent"] == 100, "callback terminal progress changed")
            expect(terminal_callback["queue_position"] is None, "callback terminal queue position changed")
            expect(terminal_callback["queue_size"] == 0, "callback terminal queue size changed")
            expect(terminal_callback["blocked_by_task_id"] is None, "callback blocked_by_task_id changed")
            expect(terminal_callback["result"]["confirm"] == "elder_present", "callback result changed")

            result = client.get(f"/api/tasks/{task_id}/result")
            expect(result.status_code == 200, f"task result returned {result.status_code}")
            result_data = result.json()["data"]
            expect(result_data["finished"] is True, "result should be terminal")
            expect(result_data["elder_id"] == "001", "result elder_id changed")
            expect(result_data["location"] == "bedroom", "result location changed")
            expect(result_data["confidence"] == 0.95, "result confidence changed")
            expect(result_data["source_event_id"] == "contract-camera-fall-001", "result source_event_id changed")
            expect(result_data["camera_id"] == "contract-fixed-camera-01", "result camera_id changed")
            expect(result_data["external_task_id"] == "health-task-contract-001", "result external_task_id changed")
            expect(result_data["queue_position"] is None, "result terminal queue position changed")
            expect(result_data["queue_size"] == 0, "result terminal queue size changed")
            expect(result_data["location_resolution"]["location"] == "bedroom", "result location resolution changed")
            expect(result_data["location_resolution"]["fallbackUsed"] is False, "result location fallback changed")
            expect(result_data["confirm"] == "elder_present", "confirm result changed")
            expect(result_data["robot_camera"]["streamUrl"] == "/api/camera/stream", "stream URL contract changed")
            expect(result_data["voice_result"] == "awaiting_response", "voice result should await elder response")
            external_status = client.get("/api/tasks/external/health-task-contract-001/status")
            expect(external_status.status_code == 200, f"external status returned {external_status.status_code}")
            external_status_data = external_status.json()["data"]
            expect(external_status_data["task_id"] == task_id, "external status task_id changed")
            expect(external_status_data["status"] == "finished", "external status value changed")
            expect(external_status_data["finished"] is True, "external status terminal flag changed")
            external_result = client.get("/api/tasks/external/health-task-contract-001/result")
            expect(external_result.status_code == 200, f"external result returned {external_result.status_code}")
            external_result_data = external_result.json()["data"]
            expect(external_result_data["task_id"] == task_id, "external result task_id changed")
            expect(external_result_data["finished"] is True, "external result terminal flag changed")
            external_timeline = client.get("/api/robot/tasks/external/health-task-contract-001/timeline")
            expect(external_timeline.status_code == 200, f"external timeline returned {external_timeline.status_code}")
            external_timeline_data = external_timeline.json()["data"]
            expect(external_timeline_data["task_id"] == task_id, "external timeline task_id changed")
            expect(external_timeline_data["finished"] is True, "external timeline terminal flag changed")
            expect(external_timeline_data["progress"]["percent"] == 100, "external timeline progress changed")

            voice = client.post(
                "/api/tasks/external/health-task-contract-001/voice-result",
                json={"voice_result": "need_help", "need_help": True},
            )
            expect(voice.status_code == 200, f"voice result returned {voice.status_code}")
            voice_task = voice.json()["data"]
            expect(voice_task["voice"] == "completed", "voice state was not completed")
            expect(voice_task["result"]["needHelp"] is True, "needHelp flag changed")
            voice_callback = callback.wait_for_voice_payload(task_id)
            expect(voice_callback["progress"]["percent"] == 100, "voice callback progress changed")
            expect(voice_callback["result"]["needHelp"] is True, "callback needHelp flag changed")
            latest_after_voice = client.get("/api/tasks/latest")
            expect(latest_after_voice.status_code == 200, f"latest after voice returned {latest_after_voice.status_code}")
            latest_after_voice_data = latest_after_voice.json()["data"]
            expect(latest_after_voice_data["task_id"] == task_id, f"latest after voice task changed: {latest_after_voice_data}")
            expect(latest_after_voice_data["voice"] == "completed", f"latest after voice state changed: {latest_after_voice_data}")
            expect(
                latest_after_voice_data["result"]["voiceResult"] == "need_help",
                f"latest after voice result changed: {latest_after_voice_data}",
            )
            callback_revisions = callback.revisions_for(task_id)
            expect(callback_revisions == sorted(callback_revisions), f"callback revisions are not FIFO: {callback_revisions}")
            feedback_status = wait_for_feedback_sent(client, len(callback_revisions))
            expect(feedback_status["failed"] == 0, f"feedback delivery failed unexpectedly: {feedback_status}")
            expect(feedback_status["last_success_at"] is not None, "feedback status missing last success")
            callback_count_before_replay = callback.count_for(task_id)
            replay_feedback = client.post(
                f"/api/tasks/{task_id}/feedback/replay",
                json={"callback_url": callback.url},
            )
            expect(
                replay_feedback.status_code == 200,
                f"feedback replay returned {replay_feedback.status_code}: {replay_feedback.text}",
            )
            replay_data = replay_feedback.json()["data"]
            expect(replay_data["queued"] is True, f"feedback replay was not queued: {replay_data}")
            expect(replay_data["revision"] == voice_task["revision"], f"feedback replay revision changed: {replay_data}")
            replay_payloads = callback.wait_for_payload_count(task_id, callback_count_before_replay + 1)
            replay_callback = replay_payloads[-1]
            expect(replay_callback["revision"] == voice_task["revision"], f"feedback replay callback revision changed: {replay_callback}")
            expect(replay_callback["result"]["needHelp"] is True, f"feedback replay callback payload changed: {replay_callback}")
            external_callback_count_before_replay = callback.count_for(task_id)
            external_replay_feedback = client.post(
                "/api/tasks/external/health-task-contract-001/feedback/replay",
                json={"callback_url": callback.url},
            )
            expect(
                external_replay_feedback.status_code == 200,
                f"external feedback replay returned {external_replay_feedback.status_code}: {external_replay_feedback.text}",
            )
            external_replay_data = external_replay_feedback.json()["data"]
            expect(external_replay_data["task_id"] == task_id, f"external replay task_id changed: {external_replay_data}")
            expect(external_replay_data["external_task_id"] == "health-task-contract-001", f"external replay ID changed: {external_replay_data}")
            expect(external_replay_data["queued"] is True, f"external feedback replay was not queued: {external_replay_data}")
            external_replay_payloads = callback.wait_for_payload_count(task_id, external_callback_count_before_replay + 1)
            expect(
                external_replay_payloads[-1]["revision"] == voice_task["revision"],
                f"external feedback replay callback revision changed: {external_replay_payloads[-1]}",
            )

            camel_event = {
                "event": "fall_detected",
                "elderId": "elder-camel-001",
                "location": "living_room",
                "confidence": 0.9,
                "sourceEventId": "contract-camera-fall-camel",
                "cameraId": "contract-fixed-camera-camel",
                "externalTaskId": "health-task-contract-camel",
            }
            camel_created = client.post("/api/robot/events/fall", json=camel_event)
            expect(camel_created.status_code == 200, f"camel fall event returned {camel_created.status_code}: {camel_created.text}")
            camel_task_id = camel_created.json()["data"]["task_id"]
            camel_finished = wait_for_terminal_task(client, camel_task_id)
            expect(camel_finished["elder_id"] == "elder-camel-001", f"camel task elder_id changed: {camel_finished}")
            expect(camel_finished["location"] == "living_room", f"camel task location changed: {camel_finished}")
            expect(camel_finished["source_event_id"] == "contract-camera-fall-camel", f"camel source_event_id changed: {camel_finished}")
            expect(camel_finished["camera_id"] == "contract-fixed-camera-camel", f"camel camera_id changed: {camel_finished}")
            expect(camel_finished["external_task_id"] == "health-task-contract-camel", f"camel external_task_id changed: {camel_finished}")
            camel_voice = client.post(
                f"/api/robot/tasks/{camel_task_id}/voice-result",
                json={"voiceResult": "no_help_needed", "needHelp": False},
            )
            expect(camel_voice.status_code == 200, f"camel voice result returned {camel_voice.status_code}")
            camel_voice_task = camel_voice.json()["data"]
            expect(camel_voice_task["result"]["voiceResult"] == "no_help_needed", "camel voiceResult changed")
            expect(camel_voice_task["result"]["needHelp"] is False, "camel needHelp changed")

            event_id_payload = {
                "event": "fall_detected",
                "elder_id": "elder-event-id-001",
                "location": "bedroom",
                "confidence": 0.88,
                "event_id": "contract-camera-event-id-alias",
            }
            event_id_created = client.post("/api/events/fall", json=event_id_payload)
            expect(event_id_created.status_code == 200, f"event_id fall event returned {event_id_created.status_code}: {event_id_created.text}")
            event_id_task_id = event_id_created.json()["data"]["task_id"]
            event_id_finished = wait_for_terminal_task(client, event_id_task_id)
            expect(
                event_id_finished["source_event_id"] == "contract-camera-event-id-alias",
                f"event_id alias was not normalized: {event_id_finished}",
            )
            event_id_replay = client.post("/api/events/fall", json={**event_id_payload, "eventId": "contract-camera-event-id-alias"})
            expect(event_id_replay.status_code == 200, f"event_id replay returned {event_id_replay.status_code}: {event_id_replay.text}")
            expect(event_id_replay.json()["data"]["task_id"] == event_id_task_id, "event_id replay created a second task")

            replay_payload = {
                "event": "fall_detected",
                "elder_id": "001",
                "location": "bedroom",
                "confidence": 0.95,
                "source_event_id": "contract-camera-fall-callback-replay",
                "camera_id": "contract-fixed-camera-01",
            }
            replay_first = client.post("/api/events/fall", json=replay_payload)
            expect(replay_first.status_code == 200, f"callback replay first event returned {replay_first.status_code}: {replay_first.text}")
            replay_task_id = replay_first.json()["data"]["task_id"]
            wait_for_terminal_task(client, replay_task_id)
            replay_second = client.post("/api/events/fall", json={**replay_payload, "callback_url": callback.url})
            expect(replay_second.status_code == 200, f"callback replay second event returned {replay_second.status_code}: {replay_second.text}")
            replay_task = replay_second.json()["data"]
            expect(replay_task["task_id"] == replay_task_id, "callback replay created a second task")
            expect(replay_task["source"]["callbackUrl"] == callback.url, "callback replay did not attach callbackUrl")
            replay_callback = callback.wait_for_terminal_payload(replay_task_id)
            expect(replay_callback["finished"] is True, f"callback replay did not publish terminal task: {replay_callback}")

            try:
                client.app.state.adapter.bad_camera = True
                camera_failure_payload = {
                    "event": "fall_detected",
                    "elder_id": "001",
                    "location": "bedroom",
                    "confidence": 0.95,
                    "source_event_id": "contract-camera-fall-bad-camera",
                    "camera_id": "contract-fixed-camera-01",
                    "callback_url": callback.url,
                }
                camera_failure = client.post("/api/events/fall", json=camera_failure_payload)
                expect(camera_failure.status_code == 200, f"bad camera fall event returned {camera_failure.status_code}: {camera_failure.text}")
                camera_task_id = camera_failure.json()["data"]["task_id"]
                camera_task = wait_for_terminal_task(client, camera_task_id)
                expect(camera_task["status"] == "finished", f"bad camera task should continue to terminal success: {camera_task}")
                expect(camera_task["camera"] == "failed", f"bad camera task did not expose camera failed: {camera_task}")
                expect(camera_task["result"]["robotCamera"]["snapshot"] == "failed", f"bad camera snapshot changed: {camera_task}")
                expect(camera_task["result"]["robotCamera"]["cameraAvailable"] is False, f"bad camera availability changed: {camera_task}")
                expect(camera_task["result"]["observation"]["camera_available"] is False, f"bad camera observation changed: {camera_task}")
                camera_callback = callback.wait_for_terminal_payload(camera_task_id)
                expect(camera_callback["status"] == "finished", f"bad camera callback status changed: {camera_callback}")
                expect(camera_callback["camera"] == "failed", f"bad camera callback did not expose camera failed: {camera_callback}")
                expect(camera_callback["result"]["observation"]["camera_available"] is False, f"bad camera callback observation changed: {camera_callback}")
                camera_result = client.get(f"/api/tasks/{camera_task_id}/result")
                expect(camera_result.status_code == 200, f"bad camera result returned {camera_result.status_code}")
                camera_result_data = camera_result.json()["data"]
                expect(camera_result_data["finished"] is True, "bad camera result should be terminal")
                expect(camera_result_data["error_code"] is None, f"bad camera should not fail the task: {camera_result_data}")
                expect(camera_result_data["failure_step"] is None, f"bad camera failure step changed: {camera_result_data}")
                expect(camera_result_data["robot_camera"]["snapshot"] == "failed", f"bad camera result snapshot changed: {camera_result_data}")
                expect(camera_result_data["observation"]["camera_available"] is False, f"bad camera result observation changed: {camera_result_data}")
            finally:
                client.app.state.adapter.bad_camera = False

            audit = client.get("/api/tasks/audit-log?limit=200")
            expect(audit.status_code == 200, f"audit log returned {audit.status_code}: {audit.text}")
            audit_data = audit.json()["data"]
            expect(audit_data["enabled"] is True, "audit log should be enabled")
            expect(audit_data["path"] == str(audit_path), f"audit log path changed: {audit_data['path']}")
            audit_entries = audit_data["entries"]
            audit_events = [entry["auditEvent"] for entry in audit_entries]
            audit_task_ids = [entry["task"]["task_id"] for entry in audit_entries]
            expect("finished" in audit_events, f"audit log missing finished event: {audit_events}")
            expect("result_updated" in audit_events, f"audit log missing camera observation update: {audit_events}")
            expect(task_id in audit_task_ids, f"audit log missing success task {task_id}: {audit_task_ids}")
            expect(camera_task_id in audit_task_ids, f"audit log missing bad camera task {camera_task_id}: {audit_task_ids}")

            task_audit = client.get(f"/api/tasks/{task_id}/audit-log?limit=200")
            expect(task_audit.status_code == 200, f"task audit log returned {task_audit.status_code}: {task_audit.text}")
            task_audit_data = task_audit.json()["data"]
            task_audit_events = [entry["auditEvent"] for entry in task_audit_data["entries"]]
            expect(task_audit_data["task_id"] == task_id, f"task audit id changed: {task_audit_data}")
            expect("created" in task_audit_events, f"task audit missing created event: {task_audit_events}")
            expect("finished" in task_audit_events, f"task audit missing finished event: {task_audit_events}")
            expect(
                task_audit_data["entries"][-1]["task"]["result"]["confirm"] == "elder_present",
                f"task audit final result changed: {task_audit_data['entries'][-1]}",
            )

            external_audit = client.get("/api/tasks/external/health-task-contract-001/audit-log?limit=200")
            expect(external_audit.status_code == 200, f"external audit log returned {external_audit.status_code}: {external_audit.text}")
            external_audit_data = external_audit.json()["data"]
            expect(external_audit_data["task_id"] == task_id, f"external audit task id changed: {external_audit_data}")
            expect(
                external_audit_data["entries"][-1]["task"]["source"]["externalTaskId"] == "health-task-contract-001",
                f"external audit task source changed: {external_audit_data['entries'][-1]}",
            )

            audit_compat = client.get("/api/robot/tasks/audit-log?limit=200")
            expect(
                audit_compat.status_code == 200,
                f"compat audit log returned {audit_compat.status_code}: {audit_compat.text}",
            )
            compat_task_ids = [entry["task"]["task_id"] for entry in audit_compat.json()["data"]["entries"]]
            expect(task_id in compat_task_ids, f"compat audit log missing success task {task_id}: {compat_task_ids}")
            expect(
                camera_task_id in compat_task_ids,
                f"compat audit log missing bad camera task {camera_task_id}: {compat_task_ids}",
            )
    finally:
        callback.stop()
        temp_dir.cleanup()

    print("health_new contract verification passed")


if __name__ == "__main__":
    main()
