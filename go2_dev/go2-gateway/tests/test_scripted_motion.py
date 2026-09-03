from __future__ import annotations

import math
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.adapters.unitree_adapter import UnitreeGo2Adapter
from app.motion.scripted_motion import (
    HARD_MAX_VX_MPS,
    HARD_MAX_VY_MPS,
    HARD_MAX_WZ_RADPS,
    MotionPose,
    ScriptedMotionConfig,
    ScriptedMotionController,
    forward_progress,
    lateral_progress,
    load_scripted_motion_config,
    wrap_to_pi,
)


class SimClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.on_sleep = None

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        if self.on_sleep is not None:
            self.on_sleep(seconds)


class FakeRobotService:
    def __init__(self, clock: SimClock, *, yaw: float = 0.0) -> None:
        self.clock = clock
        self.x = 0.0
        self.y = 0.0
        self.yaw = yaw
        self.received = clock.now
        self.velocity = (0.0, 0.0, 0.0)
        self.moves: list[tuple[float, float, float]] = []
        self.stops = 0
        self.stop_code = 0
        self.emergency_stops = 0
        self.owner = None
        self.freeze_motion = False
        self.update_timestamp = True
        self.fail_move = False
        self.no_state = False
        self.pose_calls: list[dict[str, float]] = []
        self.pose_resets = 0
        self.audio_files: list[str] = []
        self.speech: list[str] = []
        clock.on_sleep = self.integrate

    def acquire_exclusive_control(self, owner: str) -> None:
        if self.owner not in (None, owner):
            raise RuntimeError("busy")
        self.owner = owner

    def release_exclusive_control(self, owner: str) -> None:
        if self.owner == owner:
            self.owner = None

    def refresh_velocity(self, vx: float, vy: float, wz: float, source: str = "api") -> dict:
        self.moves.append((vx, vy, wz))
        self.velocity = (vx, vy, wz)
        if self.fail_move:
            raise RuntimeError("move failed")
        return {"code": 0}

    def safe_stop(self, source: str = "api") -> int:
        self.stops += 1
        self.velocity = (0.0, 0.0, 0.0)
        return self.stop_code

    def emergency_stop(self, source: str = "api") -> dict:
        self.emergency_stops += 1
        self.safe_stop(source)
        return {"code": 0}

    def get_motion_state(self) -> dict | None:
        if self.no_state:
            return None
        return {
            "x": self.x,
            "y": self.y,
            "yaw": self.yaw,
            "received_monotonic": self.received,
            "source": "fake_sport_mode_state",
        }

    def apply_pose(self, **parameters) -> dict:
        parameters.pop("source", None)
        self.pose_calls.append(parameters)
        return {"code": 0}

    def reset_pose(self, source: str = "api") -> dict:
        self.pose_resets += 1
        return {"code": 0}

    def play_audio_file(self, path: str, source: str = "api") -> dict:
        self.audio_files.append(path)
        return {"code": 0}

    def speak(self, text: str, source: str = "api") -> dict:
        self.speech.append(text)
        return {"code": 0}

    def integrate(self, seconds: float) -> None:
        if not self.freeze_motion:
            vx, vy, wz = self.velocity
            self.x += (vx * math.cos(self.yaw) - vy * math.sin(self.yaw)) * seconds
            self.y += (vx * math.sin(self.yaw) + vy * math.cos(self.yaw)) * seconds
            self.yaw = wrap_to_pi(self.yaw + wz * seconds)
        if self.update_timestamp:
            self.received = self.clock.now


def build_controller(
    *,
    yaw: float = 0.0,
    config: ScriptedMotionConfig | None = None,
) -> tuple[ScriptedMotionController, FakeRobotService, SimClock]:
    clock = SimClock()
    service = FakeRobotService(clock, yaw=yaw)
    controller = ScriptedMotionController(
        service,
        config or ScriptedMotionConfig(),
        clock=clock.monotonic,
        sleep=clock.sleep,
    )
    return controller, service, clock


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, 0.0),
        (math.pi, -math.pi),
        (-math.pi, -math.pi),
        (3.0 * math.pi, -math.pi),
    ],
)
def test_wrap_to_pi(value: float, expected: float) -> None:
    assert wrap_to_pi(value) == pytest.approx(expected)


def test_yaw_delta_positive_179_to_negative_179() -> None:
    delta = wrap_to_pi(math.radians(-179) - math.radians(179))
    assert math.degrees(delta) == pytest.approx(2.0)


def test_yaw_delta_negative_179_to_positive_179() -> None:
    delta = wrap_to_pi(math.radians(179) - math.radians(-179))
    assert math.degrees(delta) == pytest.approx(-2.0)


@pytest.mark.parametrize("target", [90.0, 105.0])
def test_turn_accumulates_target_across_closed_loop(target: float) -> None:
    controller, service, _ = build_controller(yaw=math.radians(179.0))

    result = controller.turn_left(target)

    assert result.completed is True
    assert result.actual_value == pytest.approx(target, abs=3.0)
    assert len(service.moves) > 2
    assert all(move[2] > 0 for move in service.moves)


def test_forward_progress_uses_start_yaw_projection() -> None:
    start = MotionPose(1.0, 2.0, math.pi / 2.0, 0.0)
    current = MotionPose(1.0, 2.8, math.pi / 2.0, 0.1)
    assert forward_progress(start, current) == pytest.approx(0.8)


def test_lateral_progress_uses_start_yaw_projection() -> None:
    start = MotionPose(1.0, 2.0, math.pi / 2.0, 0.0)
    current = MotionPose(0.4, 2.0, math.pi / 2.0, 0.1)
    assert lateral_progress(start, current) == pytest.approx(0.6)


def test_forward_distance_target_reached_and_stop_called() -> None:
    controller, service, _ = build_controller(yaw=0.7)

    result = controller.forward(0.4)

    assert result.completed is True
    assert result.reason == "target_reached"
    assert result.actual_value == pytest.approx(0.4, abs=0.03)
    assert service.stops >= 2


def test_lateral_target_reached_in_start_frame() -> None:
    controller, service, _ = build_controller(yaw=math.pi / 3.0)

    result = controller.move_right(0.3)

    assert result.completed is True
    assert result.actual_value == pytest.approx(0.3, abs=0.03)
    assert all(move[1] < 0 for move in service.moves)


def test_timeout_returns_failure_and_stops() -> None:
    config = replace(
        ScriptedMotionConfig(), timeout_scale=0.2, timeout_margin_s=0.0
    )
    controller, service, _ = build_controller(config=config)
    service.freeze_motion = True

    result = controller.forward(0.2)

    assert result.completed is False
    assert result.reason == "timeout"
    assert service.stops >= 2


def test_stale_state_returns_failure_and_stops() -> None:
    controller, service, _ = build_controller(
        config=replace(ScriptedMotionConfig(), state_timeout_s=0.1)
    )
    service.update_timestamp = False

    result = controller.forward(0.3)

    assert result.completed is False
    assert result.reason == "state_stale"
    assert service.stops >= 2


def test_move_exception_always_stops() -> None:
    controller, service, _ = build_controller()
    service.fail_move = True

    result = controller.forward(0.3)

    assert result.completed is False
    assert result.reason.startswith("exception:RuntimeError:move failed")
    assert service.stops >= 2


def test_speed_clamp_applies_hard_configured_limits() -> None:
    controller, service, _ = build_controller()

    controller._refresh(10.0, -10.0, 10.0, "test")

    assert service.moves[-1] == (
        HARD_MAX_VX_MPS,
        -HARD_MAX_VY_MPS,
        HARD_MAX_WZ_RADPS,
    )


def test_keyboard_interrupt_cleanup_calls_stop() -> None:
    controller, service, clock = build_controller()

    def interrupt(_seconds: float) -> None:
        raise KeyboardInterrupt

    clock.on_sleep = interrupt
    with pytest.raises(KeyboardInterrupt):
        controller.forward(0.3)
    assert service.stops >= 2


@pytest.mark.parametrize("value", [0.0, -0.1, float("nan"), float("inf")])
def test_zero_negative_or_nonfinite_distance_rejected(value: float) -> None:
    controller, service, _ = build_controller()
    with pytest.raises(ValueError):
        controller.forward(value)
    assert service.moves == []


@pytest.mark.parametrize("value", [0.0, -90.0, float("nan"), 721.0])
def test_invalid_angle_rejected(value: float) -> None:
    controller, service, _ = build_controller()
    with pytest.raises(ValueError):
        controller.turn_left(value)
    assert service.moves == []


def test_move_is_refreshed_repeatedly_at_control_rate() -> None:
    controller, service, _ = build_controller()

    result = controller.forward(0.8)

    assert result.completed is True
    assert len(service.moves) >= 10
    assert len(set(service.moves)) >= 2  # nominal and reduced-speed phases


def test_stopmove_on_successful_turn_completion() -> None:
    controller, service, _ = build_controller()
    before = service.stops

    result = controller.turn_right(30.0)

    assert result.completed is True
    assert service.stops > before
    assert service.velocity == (0.0, 0.0, 0.0)


def test_clockwise_alias_uses_negative_yaw_velocity() -> None:
    controller, service, _ = build_controller()

    result = controller.turn_clockwise(30.0)

    assert result.completed is True
    assert result.action == "turn_clockwise"
    assert all(move[2] < 0 for move in service.moves)


def test_rotation_stall_stops_early_without_waiting_for_timeout() -> None:
    controller, service, clock = build_controller()
    service.freeze_motion = True

    result = controller.turn_left(30.0)

    assert result.completed is False
    assert result.reason == "rotation_stalled"
    assert result.duration_s < 2.0
    assert clock.now == pytest.approx(1.0)
    assert service.stops >= 2


def test_translation_stall_stops_early_without_waiting_for_timeout() -> None:
    controller, service, clock = build_controller()
    service.freeze_motion = True

    result = controller.move_right(0.5)

    assert result.completed is False
    assert result.reason == "translation_stalled"
    assert result.duration_s < 2.0
    assert clock.now == pytest.approx(1.0)
    assert service.stops >= 2


def test_state_unavailable_fails_closed_by_default() -> None:
    controller, service, _ = build_controller()
    service.no_state = True

    result = controller.forward(0.2)

    assert result.completed is False
    assert result.reason == "state_unavailable"
    assert service.moves == []


def test_explicit_time_fallback_is_labelled_unmeasured() -> None:
    controller, service, _ = build_controller(
        config=replace(ScriptedMotionConfig(), allow_time_fallback=True)
    )
    service.no_state = True

    result = controller.forward(0.2)

    assert result.completed is True
    assert result.actual_value is None
    assert result.error is None
    assert result.measurement_source == "estimated_time_fallback"
    assert result.reason == "time_fallback_completed_unmeasured"


def test_time_fallback_never_masks_stale_or_invalid_state() -> None:
    config = replace(ScriptedMotionConfig(), allow_time_fallback=True, state_timeout_s=0.1)
    controller, service, clock = build_controller(config=config)
    service.received = clock.now - 1.0
    stale = controller.forward(0.2)
    assert stale.completed is False
    assert stale.reason == "state_stale"
    assert service.moves == []

    service.received = clock.now
    service.x = float("nan")
    invalid = controller.forward(0.2)
    assert invalid.completed is False
    assert invalid.reason == "invalid_pose"
    assert service.moves == []


def test_preflight_stop_failure_never_sends_velocity() -> None:
    controller, service, _ = build_controller()
    service.stop_code = -1

    result = controller.forward(0.2)

    assert result.completed is False
    assert result.reason == "stop_failed"
    assert service.moves == []


def test_emergency_stop_latches_abort() -> None:
    controller, service, _ = build_controller()
    assert controller.emergency_stop() == 0

    result = controller.forward(0.2)

    assert result.completed is False
    assert result.reason == "action_aborted"
    assert service.emergency_stops == 1


def test_config_load_and_hard_limit_validation(tmp_path) -> None:
    valid = load_scripted_motion_config("configs/scripted_motion.yaml")
    assert valid.control_rate_hz == 5.0
    with pytest.raises(ValueError, match="hard limit"):
        replace(valid, max_vx_mps=0.31).validate()
    with pytest.raises(ValueError, match="hard limit"):
        replace(valid, max_vy_mps=0.31).validate()


def test_unitree_adapter_motion_snapshot_uses_existing_sport_state(monkeypatch) -> None:
    adapter = UnitreeGo2Adapter("eth0", 1.0, "go2")
    monkeypatch.setattr("app.adapters.unitree_adapter.time.monotonic", lambda: 12.5)
    msg = SimpleNamespace(
        position=[1.2, -0.4, 0.3],
        imu_state=SimpleNamespace(rpy=[0.0, 0.0, 1.1], quaternion=[]),
        stamp=SimpleNamespace(sec=10, nanosec=20),
    )

    adapter._on_sport_state(msg)
    state = adapter.get_motion_state()

    assert state is not None
    assert state["x"] == pytest.approx(1.2)
    assert state["y"] == pytest.approx(-0.4)
    assert state["yaw"] == pytest.approx(1.1)
    assert state["received_monotonic"] == pytest.approx(12.5)


def test_wait_uses_interruptible_clock_and_stopmove() -> None:
    controller, service, _ = build_controller()

    result = controller.wait(0.5)

    assert result.completed is True
    assert result.actual_value == pytest.approx(0.5)
    assert service.stops >= 2


def test_pose_uses_high_level_radians_then_restores_neutral_and_stops() -> None:
    controller, service, _ = build_controller()

    result = controller.pose(
        roll_deg=-6,
        pitch_deg=14,
        yaw_deg=0,
        body_height_m=-0.08,
        duration_s=1.5,
    )

    assert result.completed is True
    assert result.reason == "pose_completed"
    assert service.pose_calls == [
        {
            "roll_rad": pytest.approx(math.radians(-6)),
            "pitch_rad": pytest.approx(math.radians(14)),
            "yaw_rad": pytest.approx(0.0),
            "body_height_m": pytest.approx(-0.08),
        }
    ]
    assert service.pose_resets == 1
    assert service.stops >= 3


def test_audio_and_speech_use_service_capabilities_while_stopped() -> None:
    controller, service, _ = build_controller()

    controller.play_audio("assets/demo_end.wav")
    controller.speak("演示完成")

    assert service.audio_files == ["assets/demo_end.wav"]
    assert service.speech == ["演示完成"]
    assert service.stops >= 6


@pytest.mark.parametrize(
    "parameters",
    [
        {"roll_deg": -21, "pitch_deg": 0, "yaw_deg": 0, "body_height_m": 0},
        {"roll_deg": 0, "pitch_deg": 21, "yaw_deg": 0, "body_height_m": 0},
        {"roll_deg": 0, "pitch_deg": 0, "yaw_deg": 21, "body_height_m": 0},
        {"roll_deg": 0, "pitch_deg": 0, "yaw_deg": 0, "body_height_m": -0.19},
        {"roll_deg": 0, "pitch_deg": 0, "yaw_deg": 0, "body_height_m": 0.04},
    ],
)
def test_pose_rejects_values_outside_conservative_envelope(parameters) -> None:
    controller, service, _ = build_controller()
    with pytest.raises(ValueError):
        controller.pose(**parameters)
    assert service.pose_calls == []
