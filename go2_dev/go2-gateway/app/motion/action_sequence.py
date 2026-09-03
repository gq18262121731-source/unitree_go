from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Protocol

import yaml

from app.motion.scripted_motion import MotionActionResult


@dataclass(frozen=True)
class MotionSequenceStep:
    action: str
    parameters: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class MotionSequence:
    name: str
    steps: tuple[MotionSequenceStep, ...]


@dataclass(frozen=True)
class SequenceStepResult:
    index: int
    action: str
    status: str
    reason: str
    action_result: MotionActionResult | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "action": self.action,
            "status": self.status,
            "reason": self.reason,
            "result": self.action_result.to_dict() if self.action_result else None,
        }


@dataclass(frozen=True)
class SequenceExecutionResult:
    name: str
    completed: bool
    reason: str
    steps: tuple[SequenceStepResult, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "completed": self.completed,
            "reason": self.reason,
            "steps": [step.to_dict() for step in self.steps],
        }


class SequenceController(Protocol):
    def forward(self, distance_m: float) -> MotionActionResult: ...

    def backward(self, distance_m: float) -> MotionActionResult: ...

    def move_left(self, distance_m: float) -> MotionActionResult: ...

    def move_right(self, distance_m: float) -> MotionActionResult: ...

    def turn_left(self, angle_deg: float) -> MotionActionResult: ...

    def turn_right(self, angle_deg: float) -> MotionActionResult: ...

    def turn_clockwise(self, angle_deg: float) -> MotionActionResult: ...

    def wait(self, seconds: float) -> MotionActionResult: ...

    def stop(self) -> int: ...

    def pose(
        self,
        *,
        roll_deg: float,
        pitch_deg: float,
        yaw_deg: float,
        body_height_m: float,
        duration_s: float | None = None,
    ) -> MotionActionResult: ...

    def play_audio(self, path: str) -> None: ...

    def speak(self, text: str) -> None: ...


_ACTION_PARAMETERS: dict[str, tuple[str, ...]] = {
    "forward": ("distance_m",),
    "backward": ("distance_m",),
    "move_left": ("distance_m",),
    "move_right": ("distance_m",),
    "turn_left": ("angle_deg",),
    "turn_right": ("angle_deg",),
    "turn_clockwise": ("angle_deg",),
    "wait": ("seconds",),
    "stop": (),
    "pose": ("roll_deg", "pitch_deg", "yaw_deg", "body_height_m", "duration_s"),
    "play_audio": ("path",),
    "speak": ("text",),
}


def load_motion_sequence(path: str | Path) -> MotionSequence:
    source = Path(path)
    try:
        payload = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ValueError(f"cannot read motion sequence {source}: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != {"scripted_sequence"}:
        raise ValueError("sequence file must contain only a scripted_sequence mapping")
    section = payload["scripted_sequence"]
    if not isinstance(section, dict) or set(section) != {"name", "steps"}:
        raise ValueError("scripted_sequence must contain exactly name and steps")
    name = str(section["name"] or "").strip()
    raw_steps = section["steps"]
    if not name:
        raise ValueError("scripted_sequence.name must not be empty")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("scripted_sequence.steps must be a non-empty list")
    steps = tuple(_parse_step(index, item) for index, item in enumerate(raw_steps, start=1))
    return MotionSequence(name=name, steps=steps)


def _parse_step(index: int, item: object) -> MotionSequenceStep:
    if not isinstance(item, dict):
        raise ValueError(f"step {index} must be a mapping")
    action = str(item.get("action") or "").strip().lower()
    if action not in _ACTION_PARAMETERS:
        raise ValueError(f"step {index} has unknown action {action!r}")
    expected = set(_ACTION_PARAMETERS[action])
    actual = set(item) - {"action"}
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ValueError(
            f"step {index} action {action} parameters mismatch; "
            f"missing={missing}, unknown={unknown}"
        )
    parameters = {name: item[name] for name in _ACTION_PARAMETERS[action]}
    _validate_parameters(index, action, parameters)
    return MotionSequenceStep(action=action, parameters=parameters)


def _validate_parameters(index: int, action: str, parameters: dict[str, object]) -> None:
    if action in {"forward", "backward", "move_left", "move_right"}:
        _positive_number(parameters["distance_m"], index, "distance_m")
    elif action in {"turn_left", "turn_right", "turn_clockwise"}:
        _positive_number(parameters["angle_deg"], index, "angle_deg")
    elif action == "wait":
        _positive_number(parameters["seconds"], index, "seconds")
    elif action == "pose":
        for name in ("roll_deg", "pitch_deg", "yaw_deg", "body_height_m"):
            _finite_number(parameters[name], index, name)
        _positive_number(parameters["duration_s"], index, "duration_s")
    elif action == "play_audio":
        value = str(parameters["path"] or "").strip()
        if not value:
            raise ValueError(f"step {index} path must not be empty")
    elif action == "speak":
        value = str(parameters["text"] or "").strip()
        if not value or len(value) > 200:
            raise ValueError(f"step {index} text must contain 1 to 200 characters")


def _finite_number(value: object, index: int, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"step {index} {name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"step {index} {name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"step {index} {name} must be a finite number")
    return number


def _positive_number(value: object, index: int, name: str) -> float:
    number = _finite_number(value, index, name)
    if number <= 0:
        raise ValueError(f"step {index} {name} must be > 0")
    return number


class MotionActionDispatcher:
    """Execute validated sequence data through one controller surface."""

    def __init__(
        self,
        controller: SequenceController,
        *,
        step_callback: Callable[[SequenceStepResult], None] | None = None,
    ) -> None:
        self.controller = controller
        self._step_callback = step_callback

    def execute(self, sequence: MotionSequence) -> SequenceExecutionResult:
        results: list[SequenceStepResult] = []
        skipped = False
        for index, step in enumerate(sequence.steps, start=1):
            try:
                action_result = self.dispatch(step)
            except NotImplementedError as exc:
                skipped = True
                step_result = SequenceStepResult(
                    index=index,
                    action=step.action,
                    status="SKIPPED_NOT_IMPLEMENTED",
                    reason=str(exc),
                )
            except Exception as exc:
                self.controller.stop()
                step_result = SequenceStepResult(
                    index=index,
                    action=step.action,
                    status="FAILED",
                    reason=f"{type(exc).__name__}: {exc}",
                )
                results.append(step_result)
                self._emit(step_result)
                return SequenceExecutionResult(
                    name=sequence.name,
                    completed=False,
                    reason=f"step_{index}_failed",
                    steps=tuple(results),
                )
            else:
                failed = action_result is not None and not action_result.completed
                step_result = SequenceStepResult(
                    index=index,
                    action=step.action,
                    status="FAILED" if failed else "COMPLETED",
                    reason=(action_result.reason if action_result is not None else "completed"),
                    action_result=action_result,
                )
                if failed:
                    self.controller.stop()
                    results.append(step_result)
                    self._emit(step_result)
                    return SequenceExecutionResult(
                        name=sequence.name,
                        completed=False,
                        reason=f"step_{index}_failed",
                        steps=tuple(results),
                    )
            results.append(step_result)
            self._emit(step_result)
        return SequenceExecutionResult(
            name=sequence.name,
            completed=True,
            reason="completed_with_skips" if skipped else "completed",
            steps=tuple(results),
        )

    def dispatch(self, step: MotionSequenceStep) -> MotionActionResult | None:
        p = step.parameters
        if step.action == "forward":
            return self.controller.forward(float(p["distance_m"]))
        if step.action == "backward":
            return self.controller.backward(float(p["distance_m"]))
        if step.action == "move_left":
            return self.controller.move_left(float(p["distance_m"]))
        if step.action == "move_right":
            return self.controller.move_right(float(p["distance_m"]))
        if step.action == "turn_left":
            return self.controller.turn_left(float(p["angle_deg"]))
        if step.action == "turn_right":
            return self.controller.turn_right(float(p["angle_deg"]))
        if step.action == "turn_clockwise":
            return self.controller.turn_clockwise(float(p["angle_deg"]))
        if step.action == "wait":
            return self.controller.wait(float(p["seconds"]))
        if step.action == "stop":
            code = self.controller.stop()
            if code != 0:
                raise RuntimeError(f"StopMove failed, code={code}")
            return None
        if step.action == "pose":
            return self.controller.pose(
                roll_deg=float(p["roll_deg"]),
                pitch_deg=float(p["pitch_deg"]),
                yaw_deg=float(p["yaw_deg"]),
                body_height_m=float(p["body_height_m"]),
                duration_s=float(p["duration_s"]),
            )
        if step.action == "play_audio":
            self.controller.play_audio(str(p["path"]))
            return None
        if step.action == "speak":
            self.controller.speak(str(p["text"]))
            return None
        raise AssertionError(f"validated action has no dispatcher: {step.action}")

    def _emit(self, result: SequenceStepResult) -> None:
        if self._step_callback is not None:
            self._step_callback(result)
