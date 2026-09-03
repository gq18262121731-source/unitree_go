from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, TextIO


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.adapters.mock_adapter import MockGo2Adapter
from app.adapters.unitree_adapter import UnitreeGo2Adapter
from app.companion.exceptions import CompanionLifecycleError
from app.companion.lifecycle_service import CompanionLifecycleService
from app.config import Settings, load_settings
from app.core.state_store import StateStore
from app.gateway.go2_gateway import Go2Gateway
from app.services.robot_service import RobotService


CONFIRM_RUNTIME = "PHASE7_PERSISTENT_RUNTIME"
CONFIRM_WRITER = "EXCLUSIVE_MOTION_WRITER"
CONFIRM_OPERATOR = "REMOTE_OPERATOR_READY"
COMMANDS = {"START", "STOP", "RESUME", "STATUS", "EXIT"}


def create_robot_service(settings: Settings) -> RobotService:
    if settings.mode == "real":
        adapter = UnitreeGo2Adapter(
            settings.network_interface,
            settings.sdk_timeout_seconds,
            settings.robot_id,
            settings.robot_ip,
            settings.domain_id,
        )
    else:
        adapter = MockGo2Adapter(settings.robot_id)
    return RobotService(
        Go2Gateway(adapter),
        settings,
        StateStore(settings.robot_id, settings.state_stale_seconds),
    )


class CompanionConsole:
    """Thin CLI over the same lifecycle service used by the REST API."""

    def __init__(
        self,
        *,
        robot_service: RobotService,
        lifecycle: CompanionLifecycleService,
        input_fn: Callable[[str], str] = input,
        output: TextIO = sys.stdout,
    ) -> None:
        self.robot_service = robot_service
        self.lifecycle = lifecycle
        self.input_fn = input_fn
        self.output = output

    def run(self) -> int:
        try:
            self.robot_service.initialize()
            self.lifecycle.initialize()
            self.lifecycle.prepare()
            self._print_banner()
            self._print_status(self.lifecycle.status())
            while True:
                try:
                    raw = self.input_fn("companion> ")
                except (EOFError, KeyboardInterrupt):
                    self._write("\nEXIT requested")
                    return 0
                command = raw.strip().upper()
                if not command:
                    continue
                if command not in COMMANDS:
                    self._write(
                        "UNKNOWN_COMMAND: use START, STOP, RESUME, STATUS, or EXIT"
                    )
                    continue
                if command == "EXIT":
                    self._write("EXIT accepted; stopping and closing runtime")
                    return 0
                try:
                    if command == "START":
                        status = self.lifecycle.start()
                        self._write("START accepted")
                    elif command == "STOP":
                        status = self.lifecycle.stop()
                        self._write("STOP accepted; DDS inputs remain active")
                    elif command == "RESUME":
                        status = self.lifecycle.resume()
                        self._write("RESUME accepted")
                    else:
                        status = self.lifecycle.status()
                    self._print_status(status)
                except CompanionLifecycleError as exc:
                    self._write(f"{command}_REJECTED: {exc.code}: {exc.message}")
                    self._print_status(self.lifecycle.status())
        finally:
            self.lifecycle.close()
            self.robot_service.close()

    def _print_banner(self) -> None:
        self._write("=" * 56)
        self._write("Go2 Companion Supervisor")
        self._write("Persistent DDS inputs; motion defaults to IDLE")
        self._write("Commands: START | STOP | RESUME | STATUS | EXIT")
        self._write("=" * 56)

    def _print_status(self, status: dict[str, object]) -> None:
        uwb = _mapping(status.get("uwb"))
        lidar = _mapping(status.get("lidar"))
        risk = _mapping(status.get("risk"))
        motion = _mapping(status.get("motion"))
        runtime = _mapping(status.get("runtime"))
        control = _mapping(runtime.get("control"))
        robot_online = bool(status.get("robot_online"))
        inputs_ready = bool(runtime.get("inputs_started"))
        vx = _number(motion.get("vx"))
        wz = _number(motion.get("wz"))
        self._write(
            " | ".join(
                (
                    f"State={status.get('state', 'UNKNOWN')}",
                    f"Robot={'ONLINE' if robot_online else 'OFFLINE'}",
                    f"DDS={'READY' if robot_online and inputs_ready else 'NOT_READY'}",
                    f"UWB={'FRESH' if uwb.get('valid') else 'STALE'}",
                    f"LiDAR={lidar.get('state', 'STOP')}",
                    f"SportClient={'READY' if robot_online else 'NOT_READY'}",
                    f"Risk={risk.get('state', 'DISABLED')}",
                    f"Motion={'MOVING' if abs(vx) > 1e-6 or abs(wz) > 1e-6 else 'STOPPED'}",
                    f"vx={vx:.3f}",
                    f"wz={wz:.3f}",
                )
            )
        )
        self._write(
            json.dumps(
                {
                    "reason": status.get("reason"),
                    "resume_required": status.get("resume_required"),
                    "uwb": {
                        "distance_m": uwb.get("distance_m"),
                        "bearing_rad": uwb.get("bearing_rad"),
                        "age_ms": uwb.get("age_ms"),
                    },
                    "lidar": {
                        "nearest_distance_m": lidar.get("nearest_distance_m"),
                        "age_ms": lidar.get("age_ms"),
                        "reason": lidar.get("reason"),
                    },
                    "risk": {
                        "heartbeat_fresh": risk.get("heartbeat_fresh"),
                        "incident_id": risk.get("incident_id"),
                    },
                    "control": control,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

    def _write(self, message: str) -> None:
        print(message, file=self.output, flush=True)


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _number(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _confirm_real_runtime(input_fn: Callable[[str], str]) -> None:
    confirmations = (
        ("Type PHASE7_PERSISTENT_RUNTIME: ", CONFIRM_RUNTIME),
        ("Type EXCLUSIVE_MOTION_WRITER: ", CONFIRM_WRITER),
        ("Type REMOTE_OPERATOR_READY: ", CONFIRM_OPERATOR),
    )
    for prompt, expected in confirmations:
        if input_fn(prompt).strip() != expected:
            raise CompanionLifecycleError(
                "CONFIRMATION_FAILED",
                f"expected exact confirmation {expected}",
                403,
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Persistent Go2 companion CLI over CompanionLifecycleService. "
            "Startup never begins motion; START remains an explicit command."
        )
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Required in GO2_MODE=real before SDK initialization.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    if settings.mode == "real":
        if not args.execute:
            print(
                "REAL_RUNTIME_REJECTED: pass --execute for a separately approved session",
                file=sys.stderr,
            )
            return 2
        if not settings.phase7_require_external_risk_feed:
            print(
                "WARNING: RISK FEED DISABLED; fall preemption and RESUME are unavailable",
                file=sys.stderr,
            )
        try:
            _confirm_real_runtime(input)
        except CompanionLifecycleError as exc:
            print(f"REAL_RUNTIME_REJECTED: {exc}", file=sys.stderr)
            return 2

    robot_service = create_robot_service(settings)
    lifecycle = CompanionLifecycleService(
        robot_service=robot_service,
        settings=settings,
    )
    return CompanionConsole(
        robot_service=robot_service,
        lifecycle=lifecycle,
    ).run()


if __name__ == "__main__":
    raise SystemExit(main())
