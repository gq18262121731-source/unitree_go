#!/usr/bin/env python3
"""Phase 7.2-C supervised UWB + LiDAR short-cycle Go2 follow runner.

The default invocation performs configuration validation only and does not
initialize Unitree SDK. Real execution requires explicit environment gates,
an external risk JSONL heartbeat, at least three typed confirmations, and an in-session
RESUME command. Safe cycles refresh the SDK velocity command at no more than
5 Hz; unsafe cycles issue StopMove and clear the resume authorization.
"""

from __future__ import annotations

import argparse
import json
import math
import queue
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings, load_settings
from app.core.control_owner import ControlOwner
from app.core.state_store import StateStore
from app.follow import (
    FollowController,
    FollowControllerConfig,
    FollowPlan,
    FollowTargetPlanner,
    RealMotionSafetyLimit,
    SafetyState,
    UwbBearingSource,
    UwbBearingUnit,
    UwbInputConfig,
    UwbInputValidator,
    VelocityCommand,
)
from app.gateway.go2_gateway import Go2Gateway
from app.motion import (
    LidarSafetyConfig,
    LidarSafetyGuard,
    MotionArbiter,
    MotionArbiterConfig,
    MotionAuthority,
    RealFollowExecutionStatus,
    RealFollowExecutor,
    RealFollowExecutorConfig,
    SupervisedMotionLoop,
)
from app.companion import (
    CompanionDemoConfig,
    CompanionSupervisor,
    FollowProfile,
    load_companion_demo_config,
)
from app.providers.unitree.phase7_input_stream import Phase7ReadonlyInputStream
from app.services.robot_service import RobotService
from scripts.adapter_factory import build_adapter


CONFIRM_SCOPE = "PHASE7_SUPERVISED_LOOP"
CONFIRM_REFRESH_MODE = "CONTINUOUS_5HZ_REFRESH"
CONFIRM_FIXED_GATE = "FIXED_VELOCITY_GATE"
CONFIRM_UWB_FOLLOW_GATE = "UWB_FOLLOW_LIVE_GATE"
CONFIRM_SAFETY_OPERATOR = "REMOTE_OPERATOR_READY"
DEFAULT_COMPANION_CONFIG = ROOT / "configs" / "companion_follow_demo.yaml"


class Phase72CError(ValueError):
    pass


class FixedVelocityController:
    """C2 test-only controller; UWB liveness and planner stops still apply."""

    def __init__(self, vx: float, wz: float) -> None:
        self.vx = vx
        self.wz = wz

    def reset_dynamic_state(self, _reason: str) -> None:
        return None

    def calculate_velocity(
        self,
        plan: FollowPlan,
        *,
        control_owner: ControlOwner,
        measurement_age_seconds: float,
        sample_monotonic: float,
    ) -> VelocityCommand:
        del measurement_age_seconds, sample_monotonic
        if control_owner is not ControlOwner.FOLLOW:
            return VelocityCommand(
                vx=0.0,
                vy=0.0,
                wz=0.0,
                safety_state=SafetyState.STOP_CONTROL_NOT_FOLLOW,
                simulation_mode=False,
            )
        if plan.stop_required:
            return VelocityCommand(
                vx=0.0,
                vy=0.0,
                wz=0.0,
                safety_state=SafetyState.STOP_PLANNER_REQUEST,
                simulation_mode=False,
            )
        return VelocityCommand(
            vx=self.vx,
            vy=0.0,
            wz=self.wz,
            safety_state=SafetyState.SAFE,
            simulation_mode=False,
        )


class JsonlRiskFeed:
    """Tail the stable external fall-event contract without inventing heartbeats."""

    def __init__(self, path: Path) -> None:
        self.path = path
        # Existing lines may be stale or replayed. Only events appended after
        # this supervised session starts are eligible to refresh the heartbeat.
        self._offset = path.stat().st_size
        self.accepted = 0
        self.rejected = 0

    def poll(self, loop: SupervisedMotionLoop, *, now_monotonic: float) -> None:
        try:
            if self.path.stat().st_size < self._offset:
                loop.set_emergency(True, reason="risk_feed_truncated")
                raise Phase72CError("risk event file was truncated during the session")
            with self.path.open("r", encoding="utf-8") as stream:
                stream.seek(self._offset)
                lines = stream.readlines()
                self._offset = stream.tell()
        except OSError as exc:
            loop.set_emergency(True, reason="risk_feed_read_error")
            raise Phase72CError(f"risk event file cannot be read: {exc}") from exc

        for line in lines:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError("risk event must be a JSON object")
                accepted = loop.ingest_risk_event(
                    payload,
                    received_monotonic=now_monotonic,
                )
                self.accepted += int(accepted)
                self.rejected += int(not accepted)
            except Exception as exc:
                self.rejected += 1
                loop.set_emergency(True, reason="invalid_risk_event")
                raise Phase72CError(f"invalid risk event: {exc}") from exc


class SafetyConsole:
    """Local human safety input; STOP is immediate and RESUME is conditional."""

    def __init__(self, input_fn: Callable[[str], str] = input) -> None:
        self._input_fn = input_fn
        self.commands: queue.Queue[str] = queue.Queue()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._read,
            name="phase7-safety-console",
            daemon=True,
        )
        self._thread.start()

    def drain(self) -> list[str]:
        items: list[str] = []
        while True:
            try:
                items.append(self.commands.get_nowait())
            except queue.Empty:
                return items

    def _read(self) -> None:
        while True:
            try:
                command = self._input_fn("").strip().upper()
            except (EOFError, KeyboardInterrupt):
                command = "STOP"
            if command:
                self.commands.put(command)
            if command == "EXIT":
                return


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Initialize the real robot after all gates and confirmations.",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=20.0,
        help="Bounded session duration; maximum 60 seconds (default: %(default)s).",
    )
    parser.add_argument(
        "--risk-events",
        type=Path,
        help="Append-only JSONL from the external risk module; required for --execute.",
    )
    parser.add_argument(
        "--max-sent-cycles",
        type=int,
        default=5,
        help=(
            "Hard cap on successful 5 Hz velocity refreshes; maximum 5 for "
            "C1, 15 for an explicitly confirmed live UWB follow gate, or 17 "
            "for an explicitly confirmed fixed-velocity gate."
        ),
    )
    parser.add_argument(
        "--uwb-follow-live",
        action="store_true",
        help=(
            "Enable the separately confirmed Phase 7.2-D UWB follow gate; "
            "allows at most 15 successful 5 Hz refreshes."
        ),
    )
    parser.add_argument(
        "--companion-config",
        type=Path,
        default=DEFAULT_COMPANION_CONFIG,
        help=(
            "Validated field profile used by --uwb-follow-live "
            "(default: %(default)s)."
        ),
    )
    parser.add_argument(
        "--fixed-vx",
        type=float,
        help="C2 fixed forward velocity; requires --fixed-wz.",
    )
    parser.add_argument(
        "--fixed-wz",
        type=float,
        help="C2 fixed yaw rate; requires --fixed-vx.",
    )
    parser.add_argument(
        "--max-execute-vx",
        type=float,
        default=0.10,
        help=(
            "Final executor forward-speed clamp in m/s; ordinary C1 remains "
            "within (0, 0.15], while --uwb-follow-live requires [0.20, 0.30] "
            "(default: %(default)s)."
        ),
    )
    parser.add_argument(
        "--max-execute-wz",
        type=float,
        default=0.30,
        help=(
            "Final executor yaw-rate clamp in rad/s; must be within (0, 0.30] "
            "(default: %(default)s)."
        ),
    )
    parser.add_argument("--output", type=Path)
    return parser


def validate_settings(settings: Settings) -> None:
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
    if not settings.phase7_require_external_risk_feed:
        failures.append("PHASE7_REQUIRE_EXTERNAL_RISK_FEED must be true")
    if failures:
        raise Phase72CError("; ".join(failures))


def create_robot_service(settings: Settings) -> RobotService:
    adapter = build_adapter(settings)
    return RobotService(
        Go2Gateway(adapter),
        settings,
        StateStore(settings.robot_id, settings.state_stale_seconds),
    )


def build_supervised_loop(
    service: RobotService,
    settings: Settings,
    *,
    max_execute_vx: float = 0.10,
    max_execute_wz: float = 0.30,
    fixed_velocity: tuple[float, float] | None = None,
    walking_speed_floor_enabled: bool = False,
    companion_config: CompanionDemoConfig | None = None,
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> SupervisedMotionLoop:
    profile = companion_config.follow if companion_config is not None else (
        FollowProfile(vx_max=max_execute_vx, wz_max=max_execute_wz)
        if walking_speed_floor_enabled
        else FollowProfile()
    )
    if companion_config is not None:
        max_execute_vx = profile.vx_max
        max_execute_wz = profile.wz_max
    lidar_config = (
        companion_config.lidar
        if companion_config is not None
        else LidarSafetyConfig(
            stop_distance=0.80,
            slow_distance=1.40,
            roi_min_z=-0.25,
            clear_samples_required=3,
        )
    )
    controller = (
        FixedVelocityController(*fixed_velocity)
        if fixed_velocity is not None
        else FollowController(
            profile.controller_config(simulation_mode=False)
            if walking_speed_floor_enabled
            else FollowControllerConfig(
                max_vx=max_execute_vx,
                max_wz=0.30,
                simulation_mode=False,
                velocity_feedforward_enabled=False,
            )
        )
    )
    return SupervisedMotionLoop(
        uwb_validator=UwbInputValidator(
            UwbInputConfig(
                bearing_source=UwbBearingSource(settings.uwb_bearing_source),
                bearing_unit=UwbBearingUnit(settings.uwb_bearing_unit),
                bearing_sign=settings.uwb_bearing_sign,
                bearing_zero_offset_rad=settings.uwb_bearing_zero_offset_rad,
                calibration_confirmed=True,
            )
        ),
        planner=FollowTargetPlanner(
            profile.follow_offset(),
            lost_timeout_seconds=profile.uwb_timeout_seconds,
            monotonic_clock=monotonic_clock,
        ),
        controller=controller,  # type: ignore[arg-type]
        lidar_guard=LidarSafetyGuard(lidar_config, monotonic_clock=monotonic_clock),
        arbiter=MotionArbiter(
            MotionArbiterConfig(
                uwb_timeout_seconds=profile.uwb_timeout_seconds,
                external_risk_timeout_seconds=2.0,
                require_external_risk_feed=True,
            ),
            monotonic_clock=monotonic_clock,
        ),
        executor=RealFollowExecutor(
            service,
            config=RealFollowExecutorConfig(
                execution_enabled=settings.phase7_motion_execution_enabled,
                command_duration_seconds=0.10,
                max_frequency_hz=5.0,
                continuous_velocity_refresh=True,
            ),
            limits=RealMotionSafetyLimit(
                max_vx=max_execute_vx,
                max_vy=0.0,
                max_wz=max_execute_wz,
            ),
            monotonic_clock=monotonic_clock,
        ),
        companion_supervisor=(
            CompanionSupervisor(
                config=(
                    companion_config.companion
                    if companion_config is not None
                    else None
                ),
                profile=profile,
                monotonic_clock=monotonic_clock,
            )
            if walking_speed_floor_enabled and fixed_velocity is None
            else None
        ),
        monotonic_clock=monotonic_clock,
    )


def cycle_record(result) -> dict[str, object]:
    plan = result.follow_plan
    candidate = result.candidate
    lidar = result.lidar
    decision = result.decision
    execution = result.execution
    companion = result.companion
    return {
        "cycle": result.cycle,
        "monotonic": result.now_monotonic,
        "uwb_age_seconds": result.uwb_age_seconds,
        "uwb_error": result.uwb_error,
        "follow_target": None
        if plan is None
        else {"x": plan.target_x, "y": plan.target_y, "state": plan.current_state.value},
        "candidate": None
        if candidate is None
        else {"vx": candidate.vx, "vy": candidate.vy, "wz": candidate.wz},
        "companion": None
        if companion is None
        else {
            "state": companion.state.value,
            "motion_mode": companion.motion_mode.value,
            "reason": companion.reason,
            "target_stationary": companion.target_stationary,
            "resume_required": companion.resume_required,
            "active_incident_id": companion.active_incident_id,
        },
        "lidar": None
        if lidar is None
        else {
            "level": lidar.level.value,
            "reason": lidar.reason,
            "nearest_distance": lidar.nearest_distance,
            "age_seconds": lidar.sample_age_seconds,
        },
        "arbiter": {
            "authority": decision.authority.value,
            "reason": decision.reason,
            "stop_required": decision.stop_required,
            "risk_state": decision.risk_state.value,
        },
        "execution": {
            "status": execution.status.value,
            "vx": execution.vx,
            "vy": execution.vy,
            "wz": execution.wz,
            "velocity_refresh": execution.status is RealFollowExecutionStatus.SENT,
            "duration_seconds": 0.0,
        },
    }


def run_real(
    *,
    settings: Settings,
    seconds: float,
    risk_path: Path,
    output: Path | None,
    max_sent_cycles: int = 5,
    max_execute_vx: float = 0.10,
    max_execute_wz: float = 0.30,
    fixed_vx: float | None = None,
    fixed_wz: float | None = None,
    uwb_follow_live: bool = False,
    companion_config: CompanionDemoConfig | None = None,
    input_fn: Callable[[str], str] = input,
    service_factory: Callable[[Settings], RobotService] = create_robot_service,
) -> dict[str, object]:
    if uwb_follow_live and companion_config is not None:
        max_execute_vx = companion_config.follow.vx_max
        max_execute_wz = companion_config.follow.wz_max
        settings = replace(
            settings,
            control_watchdog_seconds=companion_config.safety.watchdog_seconds,
        )
    validate_settings(settings)
    if not math.isfinite(seconds) or not 0.0 < seconds <= 60.0:
        raise Phase72CError("--seconds must be within (0, 60]")
    fixed_requested = fixed_vx is not None or fixed_wz is not None
    if fixed_requested and uwb_follow_live:
        raise Phase72CError("--uwb-follow-live cannot be combined with fixed velocity")
    if (fixed_vx is None) != (fixed_wz is None):
        raise Phase72CError("--fixed-vx and --fixed-wz must be provided together")
    max_cycle_cap = 17 if fixed_requested else (15 if uwb_follow_live else 5)
    if not 1 <= max_sent_cycles <= max_cycle_cap:
        raise Phase72CError(
            f"--max-sent-cycles must be within [1, {max_cycle_cap}]"
        )
    if not math.isfinite(max_execute_vx):
        raise Phase72CError("--max-execute-vx must be finite")
    if fixed_requested:
        if not 0.0 < max_execute_vx <= 0.30:
            raise Phase72CError(
                "--max-execute-vx must be within (0, 0.30] for fixed velocity"
            )
    elif uwb_follow_live and not 0.20 <= max_execute_vx <= 0.30:
        raise Phase72CError(
            "--max-execute-vx must be within [0.20, 0.30] for UWB follow"
        )
    elif not uwb_follow_live and not 0.0 < max_execute_vx <= 0.15:
        raise Phase72CError(
            "--max-execute-vx must be within (0, 0.15] for the C1 gate"
        )
    if not math.isfinite(max_execute_wz) or not 0.0 < max_execute_wz <= 0.30:
        raise Phase72CError("--max-execute-wz must be within (0, 0.30]")
    if fixed_requested:
        assert fixed_vx is not None and fixed_wz is not None
        if not all(math.isfinite(value) for value in (fixed_vx, fixed_wz)):
            raise Phase72CError("fixed velocity values must be finite")
        if not 0.0 <= fixed_vx <= max_execute_vx:
            raise Phase72CError("--fixed-vx must be within [0, --max-execute-vx]")
        if not -max_execute_wz <= fixed_wz <= max_execute_wz:
            raise Phase72CError(
                "--fixed-wz must be within the configured yaw-rate clamp"
            )
        if fixed_vx == 0.0 and fixed_wz == 0.0:
            raise Phase72CError("fixed velocity command must request motion")
    if not risk_path.is_file():
        raise Phase72CError("--risk-events must name an existing JSONL file")
    _confirm_real_session(
        input_fn,
        max_execute_vx=max_execute_vx,
        max_execute_wz=max_execute_wz,
        fixed_velocity=fixed_requested,
        uwb_follow_live=uwb_follow_live,
    )

    service = service_factory(settings)
    loop: SupervisedMotionLoop | None = None
    inputs: Phase7ReadonlyInputStream | None = None
    records: list[dict[str, object]] = []
    console = SafetyConsole(input_fn)
    risk = JsonlRiskFeed(risk_path)
    exit_requested = False
    resume_requested = False
    sent_cycles = 0
    started = time.monotonic()
    try:
        service.initialize()
        loop = build_supervised_loop(
            service,
            settings,
            max_execute_vx=max_execute_vx,
            max_execute_wz=max_execute_wz,
            fixed_velocity=(fixed_vx, fixed_wz) if fixed_requested else None,
            walking_speed_floor_enabled=uwb_follow_live,
            companion_config=(companion_config if uwb_follow_live else None),
        )
        if uwb_follow_live:
            loop.start_companion()
        inputs = Phase7ReadonlyInputStream(loop)
        inputs.start()
        loop.arm_for_supervised_test()
        console.start()
        print(
            "ARMED WITH RESUME HOLD. Type RESUME only after the live record shows "
            "FOLLOW + RESUME_REQUIRED. Type STOP or EXIT at any time.",
            flush=True,
        )

        next_cycle = time.monotonic()
        while not exit_requested and time.monotonic() - started < seconds:
            now = time.monotonic()
            risk.poll(loop, now_monotonic=now)
            for command in console.drain():
                if command in {"STOP", "EXIT"}:
                    loop.set_manual_takeover(True)
                    resume_requested = False
                    exit_requested = command == "EXIT"
                elif command == "RESUME":
                    loop.set_manual_takeover(False)
                    resume_requested = True
                else:
                    print(f"IGNORED COMMAND: {command}; use RESUME, STOP, or EXIT", flush=True)

            result = loop.step()
            record = cycle_record(result)
            records.append(record)
            print(json.dumps(record, ensure_ascii=False, separators=(",", ":")), flush=True)

            if (
                result.companion is not None
                and result.companion.state.value == "EMERGENCY_STOP"
                and result.companion.active_incident_id is not None
                and result.execution.status is RealFollowExecutionStatus.STOPPED
            ):
                # Acknowledge only after the executor has observed the
                # emergency decision and issued StopMove. The active incident
                # remains latched and motion remains prohibited in MONITORING.
                loop.acknowledge_fall(result.companion.active_incident_id)
                print("FALL STOP CONFIRMED; ENTERED MONITORING", flush=True)

            if result.execution.status is RealFollowExecutionStatus.SENT:
                sent_cycles += 1
                if sent_cycles >= max_sent_cycles:
                    loop.set_manual_takeover(True)
                    limit_stop = loop.step()
                    stop_record = cycle_record(limit_stop)
                    records.append(stop_record)
                    print(
                        json.dumps(
                            stop_record,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        flush=True,
                    )
                    print("C1 SENT-CYCLE LIMIT REACHED; STOPPED", flush=True)
                    exit_requested = True
                    continue

            if (
                resume_requested
                and result.decision.authority is MotionAuthority.FOLLOW
                and result.execution.status is RealFollowExecutionStatus.RESUME_REQUIRED
            ):
                loop.authorize_resume()
                resume_requested = False
                print("RESUME AUTHORIZED FOR NEXT SAFE CYCLE", flush=True)
            elif result.decision.stop_required:
                resume_requested = False

            next_cycle += 0.20
            time.sleep(max(0.0, next_cycle - time.monotonic()))
    finally:
        if loop is not None:
            loop.set_manual_takeover(True)
            loop.shutdown()
        if inputs is not None:
            inputs.close()
        service.safe_stop("phase7_2c:finalize")
        service.close()

    report = {
        "phase": "7.2-C",
        "real_motion": "EXPLICITLY_GATED",
        "command_mode": "CONTINUOUS_VELOCITY_REFRESH",
        "command_source": "FIXED_TEST" if fixed_requested else "UWB_FOLLOW",
        "fixed_command": (
            {"vx": fixed_vx, "vy": 0.0, "wz": fixed_wz}
            if fixed_requested
            else None
        ),
        "command_duration_seconds": None,
        "refresh_period_seconds": 0.20,
        "watchdog_timeout_seconds": settings.control_watchdog_seconds,
        "control_frequency_hz": 5.0,
        "limits": {
            "max_vx": max_execute_vx,
            "max_vy": 0.0,
            "max_abs_wz": max_execute_wz,
        },
        "walking_speed_policy": (
            {
                "minimum_walking_vx": (
                    companion_config.follow.walk_min
                    if companion_config is not None
                    else 0.20
                ),
                "follow_start_distance": (
                    companion_config.follow.follow_start_distance
                    if companion_config is not None
                    else 1.90
                ),
                "follow_stop_distance": (
                    companion_config.follow.follow_stop_distance
                    if companion_config is not None
                    else 1.70
                ),
                "bearing_deadband_degrees": (
                    math.degrees(companion_config.follow.bearing_deadband_radians)
                    if companion_config is not None
                    else 12.0
                ),
            }
            if uwb_follow_live
            else None
        ),
        "companion_framework": (
            {
                "name": "Go2 Companion Follow V1",
                "supervisor_enabled": True,
                "fall_resume_policy": "EXPLICIT_HUMAN_RESUME_ONLY",
                "field_config": (
                    companion_config.report() if companion_config is not None else None
                ),
            }
            if uwb_follow_live
            else None
        ),
        "max_sent_cycles": max_sent_cycles,
        "risk_events": {"accepted": risk.accepted, "rejected": risk.rejected},
        "inputs": inputs.diagnostics() if inputs is not None else None,
        "cycles": len(records),
        "sent_cycles": sum(
            record["execution"]["status"] == RealFollowExecutionStatus.SENT.value
            for record in records
        ),
        "records": records,
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _confirm_real_session(
    input_fn: Callable[[str], str],
    *,
    max_execute_vx: float = 0.10,
    max_execute_wz: float = 0.30,
    fixed_velocity: bool = False,
    uwb_follow_live: bool = False,
) -> None:
    print(
        "REAL GO2 SUPERVISED FOLLOW: UWB + cloud_base + external risk heartbeat "
        "are rechecked every cycle. Safe cycles refresh velocity without "
        f"StopMove; vx<={max_execute_vx:.2f}, "
        f"|wz|<={max_execute_wz:.2f}, vy=0.",
        flush=True,
    )
    confirmations = [
        ("Type PHASE7_SUPERVISED_LOOP: ", CONFIRM_SCOPE),
        ("Type CONTINUOUS_5HZ_REFRESH: ", CONFIRM_REFRESH_MODE),
    ]
    if fixed_velocity:
        confirmations.append(("Type FIXED_VELOCITY_GATE: ", CONFIRM_FIXED_GATE))
    if uwb_follow_live:
        confirmations.append(
            ("Type UWB_FOLLOW_LIVE_GATE: ", CONFIRM_UWB_FOLLOW_GATE)
        )
    confirmations.append(
        ("Type REMOTE_OPERATOR_READY: ", CONFIRM_SAFETY_OPERATOR)
    )
    for prompt, expected in confirmations:
        if input_fn(prompt).strip() != expected:
            raise Phase72CError("real supervised session confirmation failed")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    try:
        companion_config = load_companion_demo_config(args.companion_config)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    if not args.execute:
        print(
            json.dumps(
                {
                    "ok": True,
                    "mode": "configuration_check_only",
                    "unitree_sdk_initialized": False,
                    "real_motion": "DISABLED",
                    "companion_config": companion_config.report(),
                    "message": "Pass --execute only during a separately approved supervised gate.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.risk_events is None:
        print(json.dumps({"ok": False, "error": "--risk-events is required"}))
        return 2
    try:
        report = run_real(
            settings=settings,
            seconds=args.seconds,
            risk_path=args.risk_events,
            output=args.output,
            max_sent_cycles=args.max_sent_cycles,
            max_execute_vx=args.max_execute_vx,
            max_execute_wz=args.max_execute_wz,
            fixed_vx=args.fixed_vx,
            fixed_wz=args.fixed_wz,
            uwb_follow_live=args.uwb_follow_live,
            companion_config=(companion_config if args.uwb_follow_live else None),
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"ok": True, **report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
