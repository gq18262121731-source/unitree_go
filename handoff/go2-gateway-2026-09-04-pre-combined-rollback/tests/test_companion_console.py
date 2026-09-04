from __future__ import annotations

from io import StringIO

from app.companion.exceptions import CompanionLifecycleError
from tools.go2_companion_console import CompanionConsole


class FakeRobotService:
    def __init__(self) -> None:
        self.initialized = 0
        self.closed = 0

    def initialize(self) -> None:
        self.initialized += 1

    def close(self) -> None:
        self.closed += 1


class FakeLifecycle:
    def __init__(self) -> None:
        self.state = "IDLE"
        self.initialized = 0
        self.prepared = 0
        self.closed = 0
        self.commands: list[str] = []

    def initialize(self) -> None:
        self.initialized += 1

    def prepare(self) -> dict[str, object]:
        self.prepared += 1
        return self.status()

    def start(self) -> dict[str, object]:
        self.commands.append("START")
        self.state = "FOLLOWING"
        return self.status()

    def stop(self) -> dict[str, object]:
        self.commands.append("STOP")
        self.state = "IDLE"
        return self.status()

    def resume(self) -> dict[str, object]:
        self.commands.append("RESUME")
        if self.state != "WAIT_RESUME":
            raise CompanionLifecycleError(
                "COMPANION_STATE_CONFLICT", "not waiting for resume", 409
            )
        self.state = "FOLLOWING"
        return self.status()

    def status(self) -> dict[str, object]:
        return {
            "state": self.state,
            "reason": "test",
            "resume_required": self.state == "WAIT_RESUME",
            "robot_online": True,
            "uwb": {"valid": True, "distance_m": 1.8, "bearing_rad": 0.1},
            "lidar": {"valid": True, "state": "CLEAR"},
            "risk": {"state": "NORMAL", "heartbeat_fresh": True},
            "motion": {"vx": 0.0, "wz": 0.0},
            "runtime": {
                "inputs_started": True,
                "control": {
                    "distance_mode": "HOLD_TOO_CLOSE",
                    "distance_reason": "distance_stop_threshold",
                },
            },
        }

    def close(self) -> None:
        self.closed += 1


def _input_from(commands: list[str]):
    iterator = iter(commands)
    return lambda _prompt: next(iterator)


def test_console_start_stop_status_and_exit_share_one_lifecycle() -> None:
    robot = FakeRobotService()
    lifecycle = FakeLifecycle()
    output = StringIO()
    console = CompanionConsole(
        robot_service=robot,  # type: ignore[arg-type]
        lifecycle=lifecycle,  # type: ignore[arg-type]
        input_fn=_input_from(["status", "start", "stop", "exit"]),
        output=output,
    )

    assert console.run() == 0
    assert lifecycle.commands == ["START", "STOP"]
    assert lifecycle.initialized == 1
    assert lifecycle.prepared == 1
    assert lifecycle.closed == 1
    assert robot.initialized == 1
    assert robot.closed == 1
    assert "STOP accepted; DDS inputs remain active" in output.getvalue()
    assert '"distance_mode":"HOLD_TOO_CLOSE"' in output.getvalue()


def test_console_reports_resume_rejection_without_exiting() -> None:
    robot = FakeRobotService()
    lifecycle = FakeLifecycle()
    output = StringIO()
    console = CompanionConsole(
        robot_service=robot,  # type: ignore[arg-type]
        lifecycle=lifecycle,  # type: ignore[arg-type]
        input_fn=_input_from(["resume", "exit"]),
        output=output,
    )

    assert console.run() == 0
    assert "RESUME_REJECTED: COMPANION_STATE_CONFLICT" in output.getvalue()
    assert lifecycle.closed == 1
    assert robot.closed == 1


def test_console_displays_disabled_risk_mode_explicitly() -> None:
    robot = FakeRobotService()
    lifecycle = FakeLifecycle()
    original_status = lifecycle.status

    def disabled_status() -> dict[str, object]:
        status = original_status()
        status["risk"] = {
            "state": "DISABLED",
            "heartbeat_fresh": False,
        }
        return status

    lifecycle.status = disabled_status  # type: ignore[method-assign]
    output = StringIO()
    console = CompanionConsole(
        robot_service=robot,  # type: ignore[arg-type]
        lifecycle=lifecycle,  # type: ignore[arg-type]
        input_fn=_input_from(["status", "exit"]),
        output=output,
    )

    assert console.run() == 0
    assert "Risk=DISABLED" in output.getvalue()
