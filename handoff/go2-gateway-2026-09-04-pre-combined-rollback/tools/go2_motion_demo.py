from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.adapters.mock_adapter import MockGo2Adapter
from app.adapters.unitree_adapter import UnitreeGo2Adapter
from app.config import Settings, load_settings
from app.core.state_store import StateStore
from app.gateway.go2_gateway import Go2Gateway
from app.motion.scripted_motion import (
    MotionActionResult,
    ScriptedMotionController,
    load_scripted_motion_config,
)
from app.motion.action_sequence import (
    MotionActionDispatcher,
    MotionSequence,
    SequenceStepResult,
    load_motion_sequence,
)
from app.services.robot_service import RobotService


CONFIRM_WRITER = "EXCLUSIVE_MOTION_WRITER"
CONFIRM_AREA = "OPEN_AREA_REMOTE_READY"
CONFIRM_PHONE_DEMO = "PHONE_DEMO_APPROVED"
CONFIRM_SEQUENCE = "SCRIPTED_SEQUENCE_APPROVED"
CONFIRM_APP_CLOSED = "UNITREE_APP_CLOSED"
DEFAULT_CONFIG = ROOT / "configs" / "scripted_motion.yaml"
DEFAULT_PHONE_DEMO = ROOT / "configs" / "phone_demo.yaml"


def create_robot_service(settings: Settings, transport: str = "sdk2") -> RobotService:
    if settings.mode != "real":
        adapter = MockGo2Adapter(settings.robot_id)
    elif transport == "webrtc":
        from app.adapters.webrtc_motion_backend import WebRTCMotionBackend
        from app.webrtc.go2_wireless_runtime import Go2WirelessRuntime

        runtime = Go2WirelessRuntime(
            settings.robot_ip,
            aes_key=os.environ.get("GO2_AES_KEY", "").strip() or None,
            command_timeout_seconds=settings.sdk_timeout_seconds,
            state_stale_seconds=settings.state_stale_seconds,
            enable_video=False,
        )
        adapter = WebRTCMotionBackend(
            runtime,
            settings.robot_id,
            close_runtime=True,
        )
    else:
        adapter = UnitreeGo2Adapter(
            settings.network_interface,
            settings.sdk_timeout_seconds,
            settings.robot_id,
            settings.robot_ip,
            settings.domain_id,
        )
    return RobotService(
        Go2Gateway(adapter),
        settings,
        StateStore(settings.robot_id, settings.state_stale_seconds),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Closed-loop Go2 PC scripted motion over SDK2 or WebRTC SportModeState."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--demo", choices=("phone_demo",))
    mode.add_argument("--sequence", type=Path, help="Run a validated scripted-sequence YAML file.")
    mode.add_argument(
        "--action",
        choices=(
            "forward",
            "backward",
            "left",
            "right",
            "turn_left",
            "turn_right",
            "turn_clockwise",
            "wait",
        ),
    )
    parser.add_argument("--value", type=float, help="Distance metres, angle degrees, or wait seconds.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--transport",
        choices=("sdk2", "webrtc"),
        default="sdk2",
        help="Real-mode movement transport; mock mode ignores this option.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Required in GO2_MODE=real before SDK initialization.",
    )
    parser.add_argument(
        "--allow-phone-demo",
        action="store_true",
        help="Additional gate for the long phone sequence after T1/T2/T3 pass.",
    )
    parser.add_argument(
        "--allow-sequence",
        action="store_true",
        help="Additional gate for a custom sequence after staged real trials pass.",
    )
    return parser


def _companion_is_idle(settings: Settings) -> tuple[bool, str]:
    path = Path(settings.companion_state_path).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, f"companion lifecycle state file is missing: {path}"
    except (OSError, ValueError, TypeError) as exc:
        return False, f"cannot verify companion lifecycle state {path}: {exc}"
    state = str(payload.get("state") or "UNKNOWN").upper() if isinstance(payload, dict) else "UNKNOWN"
    return state == "IDLE", state


def _confirm_real_session(
    settings: Settings,
    *,
    sequence_confirmation: str | None,
    transport: str = "sdk2",
    input_fn: Callable[[str], str] = input,
) -> None:
    idle, detail = _companion_is_idle(settings)
    if not idle:
        raise RuntimeError(
            "COMPANION_NOT_CONFIRMED_IDLE: run Companion STOP -> IDLE first; "
            f"observed={detail}"
        )
    confirmations = [
        (f"Type {CONFIRM_WRITER}: ", CONFIRM_WRITER),
        (f"Type {CONFIRM_AREA}: ", CONFIRM_AREA),
    ]
    if transport == "webrtc":
        confirmations.insert(1, (f"Type {CONFIRM_APP_CLOSED}: ", CONFIRM_APP_CLOSED))
    for prompt, expected in confirmations:
        if input_fn(prompt).strip() != expected:
            raise RuntimeError(f"confirmation failed; expected exact text {expected}")
    if sequence_confirmation is not None:
        if input_fn(f"Type {sequence_confirmation}: ").strip() != sequence_confirmation:
            raise RuntimeError(
                f"confirmation failed; expected exact text {sequence_confirmation}"
            )


def _print_progress(payload: dict[str, object]) -> None:
    print(
        f"Progress {payload['action']}: {float(payload['progress']):.3f} / "
        f"{float(payload['target']):.3f}",
        flush=True,
    )


def _print_result(result: MotionActionResult) -> None:
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), flush=True)


def _run_action(controller: ScriptedMotionController, action: str, value: float) -> MotionActionResult:
    actions = {
        "forward": controller.forward,
        "backward": controller.backward,
        "left": controller.move_left,
        "right": controller.move_right,
        "turn_left": controller.turn_left,
        "turn_right": controller.turn_right,
        "turn_clockwise": controller.turn_clockwise,
        "wait": controller.wait,
    }
    print(f"Starting {action} {value:.3f}", flush=True)
    result = actions[action](value)
    _print_result(result)
    return result


def _print_sequence_step(result: SequenceStepResult) -> None:
    print(
        f"STEP {result.index}: {result.action}: {result.status}: {result.reason}",
        flush=True,
    )
    if result.action_result is not None:
        _print_result(result.action_result)


def run_sequence(controller: ScriptedMotionController, sequence: MotionSequence) -> bool:
    print(f"Starting sequence {sequence.name} ({len(sequence.steps)} steps)", flush=True)
    dispatcher = MotionActionDispatcher(controller, step_callback=_print_sequence_step)
    result = dispatcher.execute(sequence)
    print(
        json.dumps(
            {
                "sequence": result.name,
                "completed": result.completed,
                "reason": result.reason,
                "steps_executed": len(result.steps),
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return result.completed


def run_interactive(controller: ScriptedMotionController) -> int:
    print("Go2 Scripted Motion")
    print("Commands: F m | B m | L m | R m | TL deg | TR/TCW deg | WAIT s | STOP | STATUS | EXIT")
    aliases = {
        "F": "forward",
        "B": "backward",
        "L": "left",
        "R": "right",
        "TL": "turn_left",
        "TR": "turn_right",
        "TCW": "turn_clockwise",
        "WAIT": "wait",
    }
    while True:
        try:
            raw = input("motion> ").strip()
        except (EOFError, KeyboardInterrupt):
            controller.emergency_stop()
            print("\nEMERGENCY_STOP; exiting")
            return 130
        if not raw:
            continue
        parts = raw.split()
        command = parts[0].upper()
        if command == "EXIT":
            controller.stop()
            return 0
        if command == "STOP":
            print(f"StopMove code={controller.stop()}")
            continue
        if command == "STATUS":
            print(json.dumps(controller.status(), ensure_ascii=False, indent=2))
            continue
        if command not in aliases or len(parts) != 2:
            print("INVALID_COMMAND")
            continue
        try:
            result = _run_action(controller, aliases[command], float(parts[1]))
        except ValueError as exc:
            print(f"REJECTED: {exc}")
            continue
        if not result.completed:
            print(f"ACTION_FAILED: {result.reason}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action and args.value is None:
        print("--value is required with --action", file=sys.stderr)
        return 2
    settings = load_settings()
    phone_demo = args.demo == "phone_demo"
    if phone_demo and not args.allow_phone_demo:
        print(
            "PHONE_DEMO_REJECTED: pass --allow-phone-demo only after all staged real-trial gates pass",
            file=sys.stderr,
        )
        return 2
    if args.sequence and not args.allow_sequence:
        print(
            "SEQUENCE_REJECTED: pass --allow-sequence only after staged real-trial gates pass",
            file=sys.stderr,
        )
        return 2
    try:
        config = load_scripted_motion_config(args.config)
        sequence_path = DEFAULT_PHONE_DEMO if phone_demo else args.sequence
        sequence = load_motion_sequence(sequence_path) if sequence_path else None
    except ValueError as exc:
        print(f"CONFIG_REJECTED: {exc}", file=sys.stderr)
        return 2
    if settings.mode == "real":
        if not args.execute:
            print("REAL_MOTION_REJECTED: pass --execute", file=sys.stderr)
            return 2
        try:
            _confirm_real_session(
                settings,
                transport=args.transport,
                sequence_confirmation=(
                    CONFIRM_PHONE_DEMO
                    if phone_demo
                    else CONFIRM_SEQUENCE
                    if sequence is not None
                    else None
                ),
            )
        except RuntimeError as exc:
            print(f"REAL_MOTION_REJECTED: {exc}", file=sys.stderr)
            return 2

    service = create_robot_service(settings, args.transport)
    controller = ScriptedMotionController(
        service,
        config,
        progress_callback=_print_progress,
    )
    try:
        service.initialize()
        with controller:
            if sequence is not None:
                return 0 if run_sequence(controller, sequence) else 1
            if args.action:
                return 0 if _run_action(controller, args.action, args.value).completed else 1
            return run_interactive(controller)
    except KeyboardInterrupt:
        controller.emergency_stop()
        print("EMERGENCY_STOP: KeyboardInterrupt", file=sys.stderr)
        return 130
    except Exception as exc:
        controller.emergency_stop()
        print(f"SCRIPTED_MOTION_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        controller.stop()
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
