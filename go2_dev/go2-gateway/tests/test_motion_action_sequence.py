from __future__ import annotations

from pathlib import Path

import pytest

from app.motion.action_sequence import (
    MotionActionDispatcher,
    MotionSequence,
    MotionSequenceStep,
    load_motion_sequence,
)
from app.motion.scripted_motion import MotionActionResult


PHONE_DEMO = Path(__file__).resolve().parents[1] / "configs" / "phone_demo.yaml"


def action_result(action: str, value: float, *, completed: bool = True) -> MotionActionResult:
    return MotionActionResult(
        action=action,
        requested_value=value,
        actual_value=value if completed else 0.0,
        error=0.0 if completed else -value,
        unit="m",
        duration_s=1.0,
        completed=completed,
        reason="target_reached" if completed else "timeout",
        start_pose=None,
        end_pose=None,
    )


class FakeController:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.fail_action: str | None = None
        self.stops = 0

    def _motion(self, action: str, value: float) -> MotionActionResult:
        self.calls.append((action, value))
        return action_result(action, value, completed=action != self.fail_action)

    def forward(self, value: float) -> MotionActionResult:
        return self._motion("forward", value)

    def backward(self, value: float) -> MotionActionResult:
        return self._motion("backward", value)

    def move_left(self, value: float) -> MotionActionResult:
        return self._motion("move_left", value)

    def move_right(self, value: float) -> MotionActionResult:
        return self._motion("move_right", value)

    def turn_left(self, value: float) -> MotionActionResult:
        return self._motion("turn_left", value)

    def turn_right(self, value: float) -> MotionActionResult:
        return self._motion("turn_right", value)

    def turn_clockwise(self, value: float) -> MotionActionResult:
        return self._motion("turn_clockwise", value)

    def wait(self, value: float) -> MotionActionResult:
        return self._motion("wait", value)

    def stop(self) -> int:
        self.stops += 1
        self.calls.append(("stop", None))
        return 0

    def pose(self, **parameters) -> MotionActionResult:
        self.calls.append(("pose", parameters))
        return action_result("pose", float(parameters["duration_s"]))

    def play_audio(self, path: str) -> None:
        self.calls.append(("play_audio", path))

    def speak(self, text: str) -> None:
        self.calls.append(("speak", text))


def test_phone_demo_is_loaded_entirely_from_yaml() -> None:
    sequence = load_motion_sequence(PHONE_DEMO)

    assert sequence.name == "phone_demo"
    assert len(sequence.steps) == 14
    assert sequence.steps[0] == MotionSequenceStep("forward", {"distance_m": 0.8})
    assert sequence.steps[1] == MotionSequenceStep("turn_clockwise", {"angle_deg": 90})
    assert sequence.steps[-1] == MotionSequenceStep("speak", {"text": "演示完成"})


def test_phone_demo_dispatcher_preserves_yaml_order_without_skips() -> None:
    sequence = load_motion_sequence(PHONE_DEMO)
    controller = FakeController()

    result = MotionActionDispatcher(controller).execute(sequence)

    assert result.completed is True
    assert result.reason == "completed"
    assert all(step.status == "COMPLETED" for step in result.steps)
    assert controller.calls[0] == ("forward", 0.8)
    assert controller.calls[1] == ("turn_clockwise", 90.0)
    assert controller.calls[-1] == ("speak", "演示完成")


def test_dispatcher_stops_sequence_after_failed_action() -> None:
    controller = FakeController()
    controller.fail_action = "forward"
    sequence = MotionSequence(
        name="failure",
        steps=(
            MotionSequenceStep("forward", {"distance_m": 0.2}),
            MotionSequenceStep("turn_left", {"angle_deg": 30}),
        ),
    )

    result = MotionActionDispatcher(controller).execute(sequence)

    assert result.completed is False
    assert result.reason == "step_1_failed"
    assert controller.calls == [("forward", 0.2), ("stop", None)]
    assert controller.stops == 1


@pytest.mark.parametrize(
    "body",
    [
        "scripted_sequence:\n  name: bad\n  steps:\n    - action: fly\n      distance_m: 1\n",
        "scripted_sequence:\n  name: bad\n  steps:\n    - action: forward\n      distance_m: -1\n",
        "scripted_sequence:\n  name: bad\n  steps:\n    - action: forward\n      distance_m: .nan\n",
        "scripted_sequence:\n  name: bad\n  steps:\n    - action: wait\n      seconds: 1\n      extra: 2\n",
    ],
)
def test_invalid_sequence_is_rejected_before_dispatch(tmp_path, body: str) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(ValueError):
        load_motion_sequence(path)


def test_action_parameters_are_unit_specific(tmp_path) -> None:
    path = tmp_path / "wrong-unit.yaml"
    path.write_text(
        "scripted_sequence:\n"
        "  name: wrong-unit\n"
        "  steps:\n"
        "    - action: turn_left\n"
        "      distance_m: 90\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="parameters mismatch"):
        load_motion_sequence(path)


def test_stop_action_uses_same_dispatcher() -> None:
    controller = FakeController()
    sequence = MotionSequence(
        name="stop",
        steps=(MotionSequenceStep("stop", {}),),
    )

    result = MotionActionDispatcher(controller).execute(sequence)

    assert result.completed is True
    assert controller.stops == 1
