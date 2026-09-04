from __future__ import annotations

import json

from app.config import Settings
from tools.go2_follow_single_pulse import (
    CONFIRM_JOYSTICK_HANDOFF,
    CONFIRM_SCOPE,
    CONFIRM_SEND,
    main,
)


class FakeRealRobotService:
    def __init__(self) -> None:
        self.initialized = False
        self.closed = False
        self.moves: list[tuple[float, float, float, float, str]] = []
        self.stops: list[str] = []
        self.joystick_switches: list[bool] = []

    def initialize(self) -> None:
        self.initialized = True

    def close(self) -> None:
        self.closed = True

    def move(
        self,
        vx: float,
        vy: float,
        wz: float,
        duration: float,
        source: str = "api",
    ) -> dict:
        self.moves.append((vx, vy, wz, duration, source))
        return {"code": 0}

    def safe_stop(self, source: str = "api") -> int:
        self.stops.append(source)
        return 0

    def switch_joystick(self, enabled: bool, source: str = "api") -> dict:
        self.joystick_switches.append(enabled)
        return {"code": 0}

    def safe_switch_joystick(self, enabled: bool, source: str = "api") -> int:
        self.joystick_switches.append(enabled)
        return 0


def real_settings() -> Settings:
    return Settings(
        mode="real",
        control_enabled=True,
        read_only_mode=False,
        follow_simulation=False,
        follow_execution_enabled=True,
        phase7_motion_execution_enabled=True,
    )


def test_default_mode_is_dry_run_and_does_not_create_real_service(capsys) -> None:
    created = False

    def factory(_settings: Settings):
        nonlocal created
        created = True
        return FakeRealRobotService()

    exit_code = main(["--vx", "0.05"], service_factory=factory)
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert created is False
    assert output["mode"] == "dry_run"
    assert output["execution"]["execution_result"] == "sent"
    assert output["execution"]["robot_result"]["dry_run"] is True


def test_real_mode_requires_all_environment_gates(capsys) -> None:
    exit_code = main(
        ["--vx", "0.05", "--execute"],
        settings=Settings(),
        input_fn=lambda _prompt: CONFIRM_SCOPE,
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert output["ok"] is False
    assert "GO2_MODE must be real" in output["error"]
    assert "FOLLOW_EXECUTION_ENABLED must be true" in output["error"]
    assert "PHASE7_MOTION_EXECUTION_ENABLED must be true" in output["error"]


def test_real_forward_pulse_requires_two_confirmations(capsys) -> None:
    service = FakeRealRobotService()
    answers = iter([CONFIRM_SCOPE, CONFIRM_SEND])

    exit_code = main(
        ["--vx", "0.05", "--execute"],
        settings=real_settings(),
        input_fn=lambda _prompt: next(answers),
        service_factory=lambda _settings: service,
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert service.initialized is True
    assert service.moves == [(0.05, 0.0, 0.0, 0.10, "follow_executor")]
    assert service.stops == ["follow_single_pulse:finalize"]
    assert service.closed is True
    assert '"execution_result": "sent"' in output


def test_rejected_confirmation_never_initializes_service(capsys) -> None:
    service = FakeRealRobotService()

    exit_code = main(
        ["--wz", "0.10", "--execute"],
        settings=real_settings(),
        input_fn=lambda _prompt: "NO",
        service_factory=lambda _settings: service,
    )
    capsys.readouterr()

    assert exit_code == 2
    assert service.initialized is False
    assert service.moves == []


def test_stop_uses_safe_stop_and_never_moves(capsys) -> None:
    service = FakeRealRobotService()
    answers = iter([CONFIRM_SCOPE, CONFIRM_SEND])

    exit_code = main(
        ["--stop", "--execute"],
        settings=real_settings(),
        input_fn=lambda _prompt: next(answers),
        service_factory=lambda _settings: service,
    )
    capsys.readouterr()

    assert exit_code == 0
    assert service.moves == []
    assert service.stops == [
        "follow_executor:safety",
        "follow_single_pulse:finalize",
    ]


def test_real_pulse_with_joystick_handoff_requires_and_restores(capsys) -> None:
    service = FakeRealRobotService()
    answers = iter([CONFIRM_SCOPE, CONFIRM_SEND, CONFIRM_JOYSTICK_HANDOFF])

    exit_code = main(
        [
            "--vx",
            "0.10",
            "--duration",
            "0.5",
            "--temporary-joystick-handoff",
            "--execute",
        ],
        settings=real_settings(),
        input_fn=lambda _prompt: next(answers),
        service_factory=lambda _settings: service,
    )
    capsys.readouterr()

    assert exit_code == 0
    assert service.joystick_switches == [False, True]
    assert service.moves == [(0.10, 0.0, 0.0, 0.5, "follow_executor")]


def test_tool_rejects_motion_above_first_test_limit(capsys) -> None:
    exit_code = main(["--vx", "0.11"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert "must not exceed 0.1" in output["error"]


def test_dry_run_accepts_half_second_single_pulse(capsys) -> None:
    exit_code = main(["--vx", "0.05", "--duration", "0.5"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["requested"]["duration"] == 0.5
    assert output["execution"]["robot_result"]["dry_run"] is True


def test_tool_rejects_duration_over_half_second(capsys) -> None:
    exit_code = main(["--vx", "0.05", "--duration", "0.51"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert "between 0.05 and 0.50" in output["error"]


def test_visible_rotation_gate_allows_0p20_and_preserves_exact_command(capsys) -> None:
    exit_code = main(
        ["--wz", "0.20", "--duration", "0.50", "--rotation-gate", "visible"]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["requested"]["rotation_gate"] == "visible"
    assert output["requested"]["max_wz"] == 0.20
    assert output["execution"]["wz"] == 0.20


def test_visible_rotation_gate_rejects_value_above_0p20(capsys) -> None:
    exit_code = main(["--wz", "0.21", "--rotation-gate", "visible"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert "must not exceed 0.2" in output["error"]


def test_advanced_rotation_gate_allows_0p30(capsys) -> None:
    exit_code = main(["--wz", "-0.30", "--rotation-gate", "advanced"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["requested"]["rotation_gate"] == "advanced"
    assert output["execution"]["wz"] == -0.30


def test_rotation_gate_is_rejected_for_forward_pulse(capsys) -> None:
    exit_code = main(["--vx", "0.05", "--rotation-gate", "visible"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert "only valid with --wz" in output["error"]
