from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import Settings
from tools.go2_supervised_follow_phase7_2c import (
    CONFIRM_SAFETY_OPERATOR,
    CONFIRM_SCOPE,
    CONFIRM_FIXED_GATE,
    CONFIRM_REFRESH_MODE,
    CONFIRM_UWB_FOLLOW_GATE,
    FixedVelocityController,
    JsonlRiskFeed,
    Phase72CError,
    _confirm_real_session,
    build_supervised_loop,
    main,
    build_parser,
    run_real,
    validate_settings,
)


class FakeLoop:
    def __init__(self) -> None:
        self.events: list[tuple[dict[str, object], float]] = []
        self.emergencies: list[tuple[bool, str]] = []

    def ingest_risk_event(
        self, payload: dict[str, object], *, received_monotonic: float
    ) -> bool:
        self.events.append((payload, received_monotonic))
        return True

    def set_emergency(self, active: bool, *, reason: str) -> None:
        self.emergencies.append((active, reason))


def real_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "mode": "real",
        "control_enabled": True,
        "read_only_mode": False,
        "follow_simulation": False,
        "follow_execution_enabled": True,
        "phase7_motion_execution_enabled": True,
        "phase7_require_external_risk_feed": True,
    }
    values.update(overrides)
    return Settings(**values)


def test_default_entrypoint_does_not_initialize_hardware(capsys) -> None:
    assert main([]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["unitree_sdk_initialized"] is False
    assert output["real_motion"] == "DISABLED"


def test_c1_sent_cycle_cap_defaults_to_five() -> None:
    args = build_parser().parse_args([])
    assert args.max_sent_cycles == 5
    assert args.max_execute_vx == pytest.approx(0.10)
    assert args.max_execute_wz == pytest.approx(0.30)


def test_supervised_tool_enables_validated_walking_floor_and_yaw_clamp() -> None:
    loop = build_supervised_loop(  # type: ignore[arg-type]
        object(),
        real_settings(),
        max_execute_vx=0.20,
        max_execute_wz=0.05,
        walking_speed_floor_enabled=True,
    )

    assert loop.controller.config.max_vx == pytest.approx(0.20)
    assert loop.controller.config.walking_speed_floor_enabled is True
    assert loop.controller.config.minimum_walking_vx == pytest.approx(0.20)
    assert loop.controller.config.distance_deadband_enabled is True
    assert loop.controller.config.follow_start_distance == pytest.approx(1.90)
    assert loop.controller.config.follow_stop_distance == pytest.approx(1.70)
    assert loop.controller.config.bearing_deadband_radians == pytest.approx(
        0.20943951023931956
    )
    assert loop.controller.config.forward_start_error == pytest.approx(0.25)
    assert loop.controller.config.forward_stop_error == pytest.approx(0.10)
    assert loop.executor.limits.max_vx == pytest.approx(0.20)
    assert loop.executor.limits.max_wz == pytest.approx(0.05)
    assert loop.executor.config.continuous_velocity_refresh is True
    assert loop.companion_supervisor is not None


def test_fixed_velocity_gate_uses_explicit_command_and_stricter_limits() -> None:
    loop = build_supervised_loop(  # type: ignore[arg-type]
        object(),
        real_settings(),
        max_execute_vx=0.15,
        max_execute_wz=0.20,
        fixed_velocity=(0.15, 0.0),
    )

    assert isinstance(loop.controller, FixedVelocityController)
    assert loop.controller.vx == pytest.approx(0.15)
    assert loop.controller.wz == pytest.approx(0.0)
    assert loop.executor.limits.max_vx == pytest.approx(0.15)
    assert loop.executor.limits.max_wz == pytest.approx(0.20)


def test_real_settings_require_every_motion_and_risk_gate() -> None:
    validate_settings(real_settings())
    with pytest.raises(Phase72CError, match="PHASE7_MOTION_EXECUTION_ENABLED"):
        validate_settings(real_settings(phase7_motion_execution_enabled=False))
    with pytest.raises(Phase72CError, match="PHASE7_REQUIRE_EXTERNAL_RISK_FEED"):
        validate_settings(real_settings(phase7_require_external_risk_feed=False))


def test_risk_feed_ignores_existing_lines_and_accepts_only_new_append(
    tmp_path: Path,
) -> None:
    path = tmp_path / "risk.jsonl"
    path.write_text(
        '{"event_type":"NON_FALL","timestamp":"2026-08-23T11:00:00+08:00"}\n',
        encoding="utf-8",
    )
    feed = JsonlRiskFeed(path)
    loop = FakeLoop()

    feed.poll(loop, now_monotonic=10.0)  # type: ignore[arg-type]
    assert loop.events == []

    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            '{"event_type":"NON_FALL","timestamp":"2026-08-23T11:00:01+08:00"}\n'
        )
    feed.poll(loop, now_monotonic=10.1)  # type: ignore[arg-type]
    assert len(loop.events) == 1
    assert feed.accepted == 1


def test_risk_feed_truncation_latches_emergency(tmp_path: Path) -> None:
    path = tmp_path / "risk.jsonl"
    path.write_text("some existing data\n", encoding="utf-8")
    feed = JsonlRiskFeed(path)
    path.write_text("", encoding="utf-8")
    loop = FakeLoop()

    with pytest.raises(Phase72CError, match="truncated"):
        feed.poll(loop, now_monotonic=10.0)  # type: ignore[arg-type]
    assert loop.emergencies == [(True, "risk_feed_truncated")]


def test_real_session_requires_all_three_exact_confirmations() -> None:
    answers = iter([CONFIRM_SCOPE, CONFIRM_REFRESH_MODE, CONFIRM_SAFETY_OPERATOR])
    _confirm_real_session(lambda _prompt: next(answers))

    wrong = iter([CONFIRM_SCOPE, "WRONG"])
    with pytest.raises(Phase72CError, match="confirmation failed"):
        _confirm_real_session(lambda _prompt: next(wrong))


def test_fixed_gate_requires_its_additional_exact_confirmation() -> None:
    answers = iter(
        [
            CONFIRM_SCOPE,
            CONFIRM_REFRESH_MODE,
            CONFIRM_FIXED_GATE,
            CONFIRM_SAFETY_OPERATOR,
        ]
    )
    _confirm_real_session(lambda _prompt: next(answers), fixed_velocity=True)


def test_live_uwb_gate_requires_its_additional_exact_confirmation() -> None:
    answers = iter(
        [
            CONFIRM_SCOPE,
            CONFIRM_REFRESH_MODE,
            CONFIRM_UWB_FOLLOW_GATE,
            CONFIRM_SAFETY_OPERATOR,
        ]
    )
    _confirm_real_session(lambda _prompt: next(answers), uwb_follow_live=True)


def test_cycle_caps_remain_separate_for_follow_and_fixed_gates(
    tmp_path: Path,
) -> None:
    missing_risk = tmp_path / "not-created.jsonl"
    with pytest.raises(Phase72CError, match=r"within \[1, 5\]"):
        run_real(
            settings=real_settings(),
            seconds=10.0,
            risk_path=missing_risk,
            output=None,
            max_sent_cycles=6,
        )
    with pytest.raises(Phase72CError, match=r"within \[1, 17\]"):
        run_real(
            settings=real_settings(),
            seconds=10.0,
            risk_path=missing_risk,
            output=None,
            max_sent_cycles=18,
            max_execute_vx=0.15,
            max_execute_wz=0.20,
            fixed_vx=0.15,
            fixed_wz=0.0,
        )
    with pytest.raises(Phase72CError, match=r"within \[1, 15\]"):
        run_real(
            settings=real_settings(),
            seconds=10.0,
            risk_path=missing_risk,
            output=None,
            max_sent_cycles=16,
            max_execute_vx=0.30,
            max_execute_wz=0.30,
            uwb_follow_live=True,
        )
