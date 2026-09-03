from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import math
from pathlib import Path
from threading import Event
import time
import pytest

from app.companion.exceptions import CompanionLifecycleError
from app.companion.lifecycle_service import CompanionLifecycleService
from app.companion.models import CompanionStatus
from app.companion.config_loader import load_companion_demo_config
from app.companion.runtime import CompanionRuntime
from app.config import Settings


class FakeRobotService:
    def __init__(self, *, online: bool = True) -> None:
        self.online = online
        self.stops: list[str] = []
        self.acceptance_checks = 0
        self.exclusive_owner: str | None = None
        self.refreshes: list[tuple[float, float, float, str]] = []

    def safe_stop(self, source: str = "api") -> int:
        self.stops.append(source)
        return 0

    def refresh_velocity(
        self, vx: float, vy: float, wz: float, source: str = "api"
    ) -> dict[str, int]:
        self.refreshes.append((vx, vy, wz, source))
        return {"code": 0}

    def status(self) -> dict[str, object]:
        return {"online": self.online}

    def ensure_ready_for_task_acceptance(self, source: str = "api") -> None:
        self.acceptance_checks += 1
        if self.exclusive_owner is not None and self.exclusive_owner != source:
            from app.core.errors import ErrorCode, GatewayError

            raise GatewayError(ErrorCode.CONTROL_BUSY, "exclusive control", 409)
        if not self.online:
            from app.core.errors import ErrorCode, GatewayError

            raise GatewayError(ErrorCode.ROBOT_OFFLINE, "Robot is offline.", 503)

    def acquire_exclusive_control(self, owner: str) -> None:
        if self.exclusive_owner not in (None, owner):
            from app.core.errors import ErrorCode, GatewayError

            raise GatewayError(ErrorCode.CONTROL_BUSY, "exclusive control", 409)
        self.exclusive_owner = owner

    def release_exclusive_control(self, owner: str) -> None:
        if self.exclusive_owner == owner:
            self.exclusive_owner = None


class FakeRuntime:
    def __init__(
        self,
        *,
        state: str = "IDLE",
        wait_error: CompanionLifecycleError | None = None,
    ) -> None:
        self.current_state = state
        self.wait_error = wait_error
        self.start_inputs_count = 0
        self.activate_count = 0
        self.stop_reasons: list[str] = []
        self.close_count = 0
        self.resume_count = 0

    def start_inputs(self) -> None:
        self.start_inputs_count += 1

    def wait_until_ready(self, timeout_seconds: float) -> None:
        assert timeout_seconds > 0
        if self.wait_error is not None:
            raise self.wait_error

    def activate(self) -> None:
        self.activate_count += 1
        self.current_state = "FOLLOWING"

    def stop(self, *, reason: str = "companion_stopped") -> None:
        self.stop_reasons.append(reason)
        self.current_state = "IDLE"

    def close(self) -> None:
        self.close_count += 1
        self.current_state = "IDLE"

    def resume(self) -> None:
        if self.current_state != "WAIT_RESUME":
            raise AssertionError("test runtime resumed from invalid state")
        self.resume_count += 1
        self.current_state = "FOLLOWING"

    def state(self) -> str:
        return self.current_state

    def snapshot(self) -> CompanionStatus:
        return CompanionStatus(
            state=self.current_state,
            reason="test",
            incident_id=None,
            resume_required=self.current_state == "WAIT_RESUME",
            runtime_active=self.current_state != "IDLE",
            robot_online=True,
        )


def build_service(
    runtime: FakeRuntime,
    *,
    robot: FakeRobotService | None = None,
    factory_counter: list[int] | None = None,
) -> CompanionLifecycleService:
    robot = robot or FakeRobotService()

    def factory() -> FakeRuntime:
        if factory_counter is not None:
            factory_counter.append(1)
        return runtime

    return CompanionLifecycleService(
        robot_service=robot,  # type: ignore[arg-type]
        settings=Settings(mode="mock", task_audit_enabled=False),
        runtime_factory=factory,
    )


def test_start_from_idle_and_second_start_are_idempotent() -> None:
    runtime = FakeRuntime()
    created: list[int] = []
    service = build_service(runtime, factory_counter=created)

    first = service.start()
    second = service.start()

    assert first["state"] == "FOLLOWING"
    assert second["state"] == "FOLLOWING"
    assert created == [1]
    assert runtime.start_inputs_count == 1
    assert runtime.activate_count == 1


def test_stop_is_global_and_idempotent() -> None:
    runtime = FakeRuntime()
    robot = FakeRobotService()
    service = build_service(runtime, robot=robot)
    service.start()
    runtime.current_state = "EMERGENCY_STOP"

    stopped = service.stop()
    stopped_again = service.stop()

    assert stopped["state"] == "IDLE"
    assert stopped_again["state"] == "IDLE"
    assert runtime.stop_reasons == ["api_stop"]
    assert robot.stops[-1] == "companion:stop_idempotent"
    assert robot.exclusive_owner is None


def test_stop_keeps_runtime_inputs_and_next_start_reuses_runtime() -> None:
    runtime = FakeRuntime()
    robot = FakeRobotService()
    created: list[int] = []
    service = build_service(runtime, robot=robot, factory_counter=created)

    service.prepare()
    first = service.start()
    stopped = service.stop()
    second = service.start()

    assert first["state"] == "FOLLOWING"
    assert stopped["state"] == "IDLE"
    assert second["state"] == "FOLLOWING"
    assert created == [1]
    assert runtime.start_inputs_count == 1
    assert runtime.activate_count == 2
    assert runtime.close_count == 0


def test_close_releases_persistent_runtime() -> None:
    runtime = FakeRuntime()
    robot = FakeRobotService()
    service = build_service(runtime, robot=robot)
    service.prepare()

    service.close()

    assert runtime.close_count == 1
    assert service.status()["state"] == "IDLE"
    assert robot.exclusive_owner is None


def test_start_holds_exclusive_motion_control_until_stop() -> None:
    runtime = FakeRuntime()
    robot = FakeRobotService()
    service = build_service(runtime, robot=robot)

    service.start()

    assert robot.exclusive_owner == "phase7_motion_arbiter"
    service.stop()
    assert robot.exclusive_owner is None


def test_resume_requires_wait_resume_and_rechecks_control() -> None:
    runtime = FakeRuntime()
    robot = FakeRobotService()
    service = build_service(runtime, robot=robot)

    with pytest.raises(CompanionLifecycleError, match="WAIT_RESUME"):
        service.resume()

    service.start()
    runtime.current_state = "WAIT_RESUME"
    resumed = service.resume()

    assert resumed["state"] == "FOLLOWING"
    assert runtime.resume_count == 1
    assert robot.acceptance_checks >= 2


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("UWB_NOT_READY", "uwb unavailable"),
        ("LIDAR_NOT_READY", "lidar unavailable"),
    ],
)
def test_failed_start_stops_and_releases_runtime(code: str, message: str) -> None:
    runtime = FakeRuntime(
        wait_error=CompanionLifecycleError(code, message, 503)
    )
    service = build_service(runtime)

    with pytest.raises(CompanionLifecycleError) as exc_info:
        service.start()

    assert exc_info.value.code == code
    assert runtime.stop_reasons == ["start_failed"]
    assert service.status()["state"] == "IDLE"
    assert service.robot_service.exclusive_owner is None


def test_service_restart_stops_existing_runtime_and_never_restores_following() -> None:
    runtime = FakeRuntime()
    robot = FakeRobotService()
    service = build_service(runtime, robot=robot)
    service.start()

    service.initialize()

    status = service.status()
    assert status["state"] == "IDLE"
    assert status["motion"] == {
        "vx": 0.0,
        "vy": 0.0,
        "wz": 0.0,
        "authority": "IDLE",
    }
    assert runtime.close_count == 1
    assert "companion:service_startup_idle" not in robot.stops


def test_concurrent_start_creates_only_one_runtime() -> None:
    runtime = FakeRuntime()
    created: list[int] = []
    service = build_service(runtime, factory_counter=created)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: service.start(), range(2)))

    assert {item["state"] for item in results} <= {"STARTING", "FOLLOWING"}
    assert service.status()["state"] == "FOLLOWING"
    assert created == [1]


def test_active_robot_task_blocks_start() -> None:
    runtime = FakeRuntime()
    service = CompanionLifecycleService(
        robot_service=FakeRobotService(),  # type: ignore[arg-type]
        settings=Settings(mode="mock", task_audit_enabled=False),
        runtime_factory=lambda: runtime,
        active_task_provider=lambda: {"task_id": "busy"},
    )

    with pytest.raises(CompanionLifecycleError) as exc_info:
        service.start()

    assert exc_info.value.code == "CONTROL_BUSY"
    assert runtime.start_inputs_count == 0


def test_stop_preempts_a_start_waiting_for_inputs() -> None:
    entered = Event()
    released = Event()

    class BlockingRuntime(FakeRuntime):
        def wait_until_ready(self, timeout_seconds: float) -> None:
            entered.set()
            released.wait(timeout=timeout_seconds)

        def stop(self, *, reason: str = "companion_stopped") -> None:
            released.set()
            super().stop(reason=reason)

    runtime = BlockingRuntime()
    robot = FakeRobotService()
    service = build_service(runtime, robot=robot)

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending_start = executor.submit(service.start)
        assert entered.wait(timeout=1.0)
        stopped = service.stop()
        with pytest.raises(CompanionLifecycleError):
            pending_start.result(timeout=1.0)

    assert stopped["state"] == "IDLE"
    assert "companion:api_stop_immediate" in robot.stops
    assert runtime.stop_reasons == ["api_stop"]


def test_runtime_worker_failure_stops_motion_and_latches_safe_stop() -> None:
    class CrashLoop:
        def __init__(self) -> None:
            self.emergencies: list[tuple[bool, str]] = []
            self.shutdown_count = 0

        def set_emergency(self, active: bool, *, reason: str) -> None:
            self.emergencies.append((active, reason))

        def shutdown(self) -> None:
            self.shutdown_count += 1

    class Inputs:
        def start(self) -> None:
            return None

        def close(self) -> None:
            return None

        def diagnostics(self) -> dict[str, object]:
            return {}

    class ExplodingRiskFeed:
        def poll(self, loop, *, now_monotonic: float) -> None:
            raise RuntimeError("risk feed failed")

        def diagnostics(self) -> dict[str, object]:
            return {}

    robot = FakeRobotService()
    loop = CrashLoop()
    config = load_companion_demo_config(
        Path(__file__).resolve().parents[1]
        / "configs"
        / "companion_follow_demo.yaml"
    )
    runtime = CompanionRuntime(
        robot_service=robot,  # type: ignore[arg-type]
        settings=Settings(mode="mock", task_audit_enabled=False),
        config=config,
        loop=loop,  # type: ignore[arg-type]
        inputs=Inputs(),
        risk_feed=ExplodingRiskFeed(),
    )

    runtime._run()

    assert runtime.failed is True
    assert loop.emergencies == [(True, "companion_runtime_failed")]
    assert loop.shutdown_count == 1
    assert robot.stops[-1] == "companion:runtime_failed"


def test_runtime_stop_keeps_input_worker_alive_until_close() -> None:
    robot = FakeRobotService()
    config = load_companion_demo_config(
        Path(__file__).resolve().parents[1]
        / "configs"
        / "companion_follow_demo.yaml"
    )
    runtime = CompanionRuntime(
        robot_service=robot,  # type: ignore[arg-type]
        settings=Settings(mode="mock", task_audit_enabled=False),
        config=config,
    )

    runtime.start_inputs()
    runtime.wait_until_ready(1.0)
    runtime.activate()
    time.sleep(0.05)
    runtime.stop(reason="test_pause")
    paused = runtime.snapshot()

    assert paused.state == "IDLE"
    assert paused.runtime_active is False
    assert paused.resume_required is False
    assert paused.runtime["inputs_started"] is True
    assert paused.runtime["worker_alive"] is True

    runtime.activate()
    assert runtime.snapshot().state == "FOLLOWING"
    runtime.close()
    closed = runtime.snapshot()
    assert closed.runtime["inputs_started"] is False
    assert closed.runtime["worker_alive"] is False


def test_unclean_real_restart_requires_explicit_stop_acknowledgement(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "companion-state.json"
    settings = Settings(
        mode="real",
        control_enabled=True,
        read_only_mode=False,
        follow_simulation=False,
        follow_execution_enabled=True,
        phase7_motion_execution_enabled=True,
        phase7_require_external_risk_feed=True,
        companion_risk_events_path=str(tmp_path / "risk.jsonl"),
        companion_state_path=str(state_path),
        max_vx=0.30,
        max_wz=0.35,
        task_audit_enabled=False,
    )
    first_runtime = FakeRuntime()
    first = CompanionLifecycleService(
        robot_service=FakeRobotService(),  # type: ignore[arg-type]
        settings=settings,
        runtime_factory=lambda: first_runtime,
    )
    first.initialize()
    first.start()
    assert state_path.exists()

    restarted = CompanionLifecycleService(
        robot_service=FakeRobotService(),  # type: ignore[arg-type]
        settings=settings,
        runtime_factory=lambda: FakeRuntime(),
    )
    restarted.initialize()

    assert restarted.status()["state"] == "IDLE"
    assert restarted.status()["resume_required"] is True
    with pytest.raises(CompanionLifecycleError) as exc_info:
        restarted.start()
    assert exc_info.value.code == "SERVICE_RESTART_INTERRUPTED"

    acknowledged = restarted.stop()
    assert acknowledged["state"] == "IDLE"
    assert acknowledged["resume_required"] is False


def test_real_follow_only_mode_allows_start_without_risk_attachment(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime()
    settings = Settings(
        mode="real",
        control_enabled=True,
        read_only_mode=False,
        follow_simulation=False,
        follow_execution_enabled=True,
        phase7_motion_execution_enabled=True,
        phase7_require_external_risk_feed=False,
        companion_risk_events_path="",
        companion_state_path=str(tmp_path / "companion-state.json"),
        max_vx=0.30,
        max_wz=0.30,
        task_audit_enabled=False,
    )
    service = CompanionLifecycleService(
        robot_service=FakeRobotService(),  # type: ignore[arg-type]
        settings=settings,
        runtime_factory=lambda: runtime,
    )
    service.initialize()

    started = service.start()

    assert started["state"] == "FOLLOWING"
    status = service.status()
    assert status["configuration"]["risk_feed_mode"] == "DISABLED"
    assert status["configuration"]["fall_preemption_available"] is False


def test_companion_http_contract_uses_one_mock_runtime(client) -> None:
    idle = client.get("/api/v1/robot/companion/status")
    assert idle.status_code == 200
    assert idle.json()["data"]["state"] == "IDLE"
    assert idle.json()["data"]["configuration"] == {
        "target_distance_m": 1.75,
        "target_bearing_rad": pytest.approx(math.radians(18.435)),
        "control_frequency_hz": 5.0,
        "motion_limits_aligned": True,
        "vx_max_mps": 0.3,
        "gateway_max_vx_mps": 0.3,
        "walk_min_mps": 0.2,
        "wz_max_radps": 0.3,
        "gateway_max_wz_radps": 0.3,
        "vy_mps": 0.0,
        "risk_feed_mode": "MOCK",
        "fall_preemption_available": True,
    }

    started = client.post("/api/v1/robot/companion/start")
    assert started.status_code == 200
    assert started.json()["data"]["state"] == "FOLLOWING"
    assert started.json()["data"]["uwb"]["valid"] is True
    assert started.json()["data"]["lidar"]["valid"] is True

    second = client.post("/api/v1/robot/companion/start")
    assert second.status_code == 200
    assert second.json()["data"]["state"] == "FOLLOWING"

    forged_move = client.post(
        "/api/robot/move",
        json={
            "vx": 0.1,
            "vy": 0.0,
            "wz": 0.0,
            "duration": 0.05,
            "controlSource": "phase7_motion_arbiter",
        },
    )
    assert forged_move.status_code == 409
    assert forged_move.json()["code"] == "CONTROL_BUSY"

    reconnect = client.post("/api/connection/reconnect")
    assert reconnect.status_code == 409
    assert reconnect.json()["code"] == "CONTROL_BUSY"

    stopped = client.post("/api/v1/robot/companion/stop")
    assert stopped.status_code == 200
    assert stopped.json()["data"]["state"] == "IDLE"
    assert stopped.json()["data"]["motion"]["vx"] == 0.0

    invalid_resume = client.post("/api/v1/robot/companion/resume")
    assert invalid_resume.status_code == 409
    assert invalid_resume.json()["code"] == "COMPANION_STATE_CONFLICT"
