from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings, load_settings
from app.core.control_owner import ControlOwner
from app.core.state_store import StateStore
from app.follow import (
    FollowExecutionStatus,
    FollowExecutor,
    FollowExecutorConfig,
    RealMotionSafetyLimit,
    SafetyState,
    VelocityCommand,
)
from app.gateway.go2_gateway import Go2Gateway
from app.services.robot_service import RobotService
from scripts.adapter_factory import build_adapter


CONFIRM_SCOPE = "GO2_SINGLE_PULSE"
CONFIRM_SEND = "SEND_ONCE"
CONFIRM_JOYSTICK_HANDOFF = "JOYSTICK_HANDOFF"


class PulseToolError(ValueError):
    pass


class MotionService(Protocol):
    def initialize(self) -> None: ...

    def close(self) -> None: ...

    def move(
        self,
        vx: float,
        vy: float,
        wz: float,
        duration: float,
        source: str = "api",
    ) -> dict: ...

    def safe_stop(self, source: str = "api") -> int: ...

    def switch_joystick(self, enabled: bool, source: str = "api") -> dict: ...

    def safe_switch_joystick(self, enabled: bool, source: str = "api") -> int: ...


@dataclass(frozen=True)
class PulseSpec:
    action: str
    vx: float
    wz: float
    duration: float
    temporary_joystick_handoff: bool
    safety_state: SafetyState
    rotation_gate: str
    max_wz: float


class DryRunRobotService:
    """In-memory service used by default; it cannot reach Unitree SDK."""

    def __init__(self) -> None:
        self.moves: list[dict[str, object]] = []
        self.stops: list[str] = []
        self.joystick_switches: list[bool] = []

    def initialize(self) -> None:
        return None

    def close(self) -> None:
        return None

    def move(
        self,
        vx: float,
        vy: float,
        wz: float,
        duration: float,
        source: str = "api",
    ) -> dict:
        self.moves.append(
            {
                "vx": vx,
                "vy": vy,
                "wz": wz,
                "duration": duration,
                "source": source,
            }
        )
        return {"code": 0, "dry_run": True}

    def safe_stop(self, source: str = "api") -> int:
        self.stops.append(source)
        return 0

    def switch_joystick(self, enabled: bool, source: str = "api") -> dict:
        self.joystick_switches.append(enabled)
        return {"code": 0}

    def safe_switch_joystick(self, enabled: bool, source: str = "api") -> int:
        self.joystick_switches.append(enabled)
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Send at most one 0.1-second Go2 follow test pulse. "
            "Default mode is dry-run and never initializes Unitree SDK."
        )
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--vx", type=float, help="Single forward/backward pulse, maximum absolute value 0.10 m/s.")
    action.add_argument("--wz", type=float, help="Single yaw pulse, maximum absolute value 0.15 rad/s.")
    action.add_argument("--stop", action="store_true", help="Send a stop request through RobotService.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Explicitly select the default no-hardware mode.")
    mode.add_argument("--execute", action="store_true", help="Allow one real pulse after all gates and confirmations.")
    parser.add_argument(
        "--duration",
        type=float,
        default=0.10,
        help="Single pulse duration in seconds, from 0.05 to 0.50 (default: %(default)s).",
    )
    parser.add_argument(
        "--temporary-joystick-handoff",
        action="store_true",
        help=(
            "Temporarily disable the native joystick for this pulse and "
            "restore it in a finally block. Requires a third confirmation."
        ),
    )
    parser.add_argument(
        "--rotation-gate",
        choices=("initial", "visible", "advanced"),
        default="initial",
        help=(
            "Explicit one-shot yaw limit: initial=0.15, visible=0.20, "
            "advanced=0.30 rad/s. Does not change production defaults."
        ),
    )
    return parser


def build_pulse_spec(args: argparse.Namespace) -> PulseSpec:
    if not math.isfinite(args.duration) or not 0.05 <= args.duration <= 0.50:
        raise PulseToolError("duration must be between 0.05 and 0.50 seconds")
    if args.stop:
        if args.rotation_gate != "initial":
            raise PulseToolError("--rotation-gate is only valid with --wz")
        return PulseSpec(
            action="stop",
            vx=0.0,
            wz=0.0,
            duration=args.duration,
            temporary_joystick_handoff=bool(args.temporary_joystick_handoff),
            safety_state=SafetyState.STOP_PLANNER_REQUEST,
            rotation_gate="initial",
            max_wz=0.15,
        )

    if args.vx is not None:
        if args.rotation_gate != "initial":
            raise PulseToolError("--rotation-gate is only valid with --wz")
        _validate_requested_value("vx", args.vx, 0.10)
        return PulseSpec(
            action="forward" if args.vx > 0.0 else "backward",
            vx=args.vx,
            wz=0.0,
            duration=args.duration,
            temporary_joystick_handoff=bool(args.temporary_joystick_handoff),
            safety_state=SafetyState.SAFE,
            rotation_gate="initial",
            max_wz=0.15,
        )

    gate_limits = {"initial": 0.15, "visible": 0.20, "advanced": 0.30}
    max_wz = gate_limits[args.rotation_gate]
    _validate_requested_value("wz", args.wz, max_wz)
    return PulseSpec(
        action="rotate_left" if args.wz > 0.0 else "rotate_right",
        vx=0.0,
        wz=args.wz,
        duration=args.duration,
        temporary_joystick_handoff=bool(args.temporary_joystick_handoff),
        safety_state=SafetyState.SAFE,
        rotation_gate=args.rotation_gate,
        max_wz=max_wz,
    )


def validate_real_execution_settings(settings: Settings) -> None:
    failures: list[str] = []
    if settings.mode != "real":
        failures.append("GO2_MODE must be real")
    if not settings.control_enabled:
        failures.append("GO2_CONTROL_ENABLED must be true")
    if settings.read_only_mode:
        failures.append("GO2_READ_ONLY_MODE must be false")
    if settings.follow_simulation:
        failures.append("FOLLOW_SIMULATION must be false")
    if not settings.follow_execution_enabled:
        failures.append("FOLLOW_EXECUTION_ENABLED must be true")
    if not settings.phase7_motion_execution_enabled:
        failures.append("PHASE7_MOTION_EXECUTION_ENABLED must be true")
    if failures:
        raise PulseToolError("; ".join(failures))


def create_real_robot_service(settings: Settings) -> RobotService:
    adapter = build_adapter(settings)
    gateway = Go2Gateway(adapter)
    state_store = StateStore(settings.robot_id, settings.state_stale_seconds)
    return RobotService(gateway, settings, state_store)


def run_pulse(
    spec: PulseSpec,
    *,
    execute_real: bool,
    settings: Settings,
    input_fn: Callable[[str], str] = input,
    service_factory: Callable[[Settings], MotionService] = create_real_robot_service,
) -> dict[str, object]:
    if execute_real:
        validate_real_execution_settings(settings)
        _confirm_real_pulse(spec, input_fn)
        service = service_factory(settings)
    else:
        service = DryRunRobotService()

    executor = FollowExecutor(
        service,  # type: ignore[arg-type]
        config=FollowExecutorConfig(
            execution_enabled=True,
            command_duration_seconds=spec.duration,
            temporary_joystick_handoff=spec.temporary_joystick_handoff,
        ),
        limits=RealMotionSafetyLimit(max_wz=spec.max_wz),
        control_owner_provider=lambda: ControlOwner.FOLLOW,
        emergency_stop_provider=lambda: False,
    )
    command = VelocityCommand(
        vx=spec.vx,
        vy=0.0,
        wz=spec.wz,
        safety_state=spec.safety_state,
        simulation_mode=False,
    )

    try:
        if execute_real:
            service.initialize()
        result = executor.execute(command)
    finally:
        if execute_real:
            try:
                service.safe_stop("follow_single_pulse:finalize")
            finally:
                service.close()

    return {
        "mode": "real" if execute_real else "dry_run",
        "action": spec.action,
        "requested": {
            "vx": spec.vx,
            "vy": 0.0,
            "wz": spec.wz,
            "duration": spec.duration,
            "temporary_joystick_handoff": spec.temporary_joystick_handoff,
            "rotation_gate": spec.rotation_gate,
            "max_wz": spec.max_wz,
        },
        "execution": result.to_dict(),
    }


def main(
    argv: list[str] | None = None,
    *,
    input_fn: Callable[[str], str] = input,
    settings: Settings | None = None,
    service_factory: Callable[[Settings], MotionService] = create_real_robot_service,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        spec = build_pulse_spec(args)
        active_settings = settings or load_settings()
        output = run_pulse(
            spec,
            execute_real=bool(args.execute),
            settings=active_settings,
            input_fn=input_fn,
            service_factory=service_factory,
        )
    except PulseToolError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:
        logging.getLogger(__name__).exception("single pulse failed")
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps({"ok": True, **output}, ensure_ascii=False, indent=2))
    return 0


def _confirm_real_pulse(
    spec: PulseSpec,
    input_fn: Callable[[str], str],
) -> None:
    print(
        "REAL GO2 SINGLE PULSE: clear 2m around the robot, disable Unitree LeadFollow, "
        "keep the remote controller ready, and verify the robot is standing."
    )
    print(
        f"Requested action={spec.action} vx={spec.vx} wz={spec.wz} "
        f"duration={spec.duration:.2f}s"
    )
    if input_fn(f"Type {CONFIRM_SCOPE} to accept the test scope: ").strip() != CONFIRM_SCOPE:
        raise PulseToolError("first confirmation rejected")
    if input_fn(f"Type {CONFIRM_SEND} to send exactly one pulse: ").strip() != CONFIRM_SEND:
        raise PulseToolError("second confirmation rejected")
    if spec.temporary_joystick_handoff:
        if (
            input_fn(
                f"Type {CONFIRM_JOYSTICK_HANDOFF} to temporarily disable and restore the joystick: "
            ).strip()
            != CONFIRM_JOYSTICK_HANDOFF
        ):
            raise PulseToolError("joystick handoff confirmation rejected")


def _validate_requested_value(name: str, value: float, limit: float) -> None:
    if not math.isfinite(value):
        raise PulseToolError(f"{name} must be finite")
    if math.isclose(value, 0.0, abs_tol=1e-12):
        raise PulseToolError(f"{name}=0 is not a motion pulse; use --stop")
    if abs(value) > limit:
        raise PulseToolError(f"absolute {name} must not exceed {limit}")


if __name__ == "__main__":
    raise SystemExit(main())
