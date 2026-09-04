from __future__ import annotations

"""Replay real Phase 7 UWB captures through the complete disabled motion chain.

This tool is offline-only.  It opens files, creates no DDS entity, and keeps
``RealFollowExecutor`` disabled.  Arbiter output is reported as a candidate
final command; executed velocity always remains zero.
"""

import argparse
import json
import math
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.control_owner import ControlOwner
from app.follow import (
    FollowController,
    FollowControllerConfig,
    FollowOffset,
    FollowTargetPlanner,
    SafetyState,
    UwbBearingSource,
    UwbBearingUnit,
    UwbInputConfig,
    UwbInputValidator,
    VelocityCommand,
)
from app.motion import (
    LidarSafetyConfig,
    LidarSafetyDecision,
    LidarSafetyGuard,
    LidarSafetyLevel,
    MotionArbiter,
    MotionArbiterConfig,
    MotionAuthority,
    RealFollowExecutionStatus,
    RealFollowExecutor,
    RealFollowExecutorConfig,
    RiskState,
)
from tools.replay_uwb_phase7_1 import load_capture


REAL_MOTION_ENABLED = False
DDS_PUBLISHER_COUNT = 0
PLANNER_TIMEOUT_SECONDS = 2.0
ARBITER_UWB_TIMEOUT_SECONDS = 1.0
RISK_TIMEOUT_SECONDS = 2.0


class DisabledRobotService:
    """Fail loudly if a disabled offline replay ever reaches a dispatch API."""

    def __init__(self) -> None:
        self.moves: list[dict[str, object]] = []
        self.stops: list[str] = []

    def move(self, *args: object, **kwargs: object) -> None:
        self.moves.append({"args": args, "kwargs": kwargs})
        raise AssertionError("REAL MOTION DISABLED: offline replay reached move")

    def safe_stop(self, source: str = "api") -> int:
        self.stops.append(source)
        raise AssertionError("offline disabled executor must not call RobotService")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "captures",
        nargs="+",
        type=Path,
        help="one or more saved go2_uwb_readonly_probe JSONL files",
    )
    parser.add_argument(
        "--lidar-state",
        choices=[item.value for item in LidarSafetyLevel],
        default=LidarSafetyLevel.CLEAR.value,
        help="simulated LiDAR state for the historical UWB timeline",
    )
    parser.add_argument("--back-distance", type=float, default=1.5)
    parser.add_argument("--right-offset", type=float, default=0.5)
    parser.add_argument("--bearing-zero-offset-rad", type=float, default=0.55)
    parser.add_argument("--slow-distance", type=float, default=1.40)
    parser.add_argument("--stop-distance", type=float, default=0.80)
    parser.add_argument("--roi-min-z", type=float, default=-0.25)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    for path in args.captures:
        if not path.is_file():
            raise ValueError(f"capture does not exist: {path}")
    if args.back_distance <= 0.0 or args.right_offset < 0.0:
        raise ValueError("follow offsets are invalid")
    if args.stop_distance <= 0.0 or args.stop_distance >= args.slow_distance:
        raise ValueError("LiDAR thresholds must satisfy 0 < STOP < SLOW")


def _input_config(args: argparse.Namespace) -> UwbInputConfig:
    return UwbInputConfig(
        bearing_source=UwbBearingSource.ORIENTATION_EST,
        bearing_unit=UwbBearingUnit.RADIANS,
        bearing_sign=1,
        bearing_zero_offset_rad=args.bearing_zero_offset_rad,
        calibration_confirmed=True,
    )


def _lidar_config(args: argparse.Namespace) -> LidarSafetyConfig:
    return LidarSafetyConfig(
        slow_distance=args.slow_distance,
        stop_distance=args.stop_distance,
        roi_min_z=args.roi_min_z,
        clear_samples_required=1,
    )


def _clear_cloud() -> list[tuple[float, float, float]]:
    return [(2.5, -1.0 + index * 0.1, 0.0) for index in range(21)]


def _cloud_for_level(level: LidarSafetyLevel) -> list[tuple[float, float, float]]:
    cloud = _clear_cloud()
    if level is LidarSafetyLevel.SLOW:
        cloud.extend([(1.0, -0.05, 0.0), (1.0, 0.0, 0.0), (1.0, 0.05, 0.0)])
    elif level is LidarSafetyLevel.STOP:
        cloud.extend([(0.6, -0.05, 0.0), (0.6, 0.0, 0.0), (0.6, 0.05, 0.0)])
    return cloud


def _lidar_decision(
    level: LidarSafetyLevel,
    *,
    at: float,
    config: LidarSafetyConfig,
) -> LidarSafetyDecision:
    guard = LidarSafetyGuard(config)
    if level is not LidarSafetyLevel.CLEAR:
        guard.update(_clear_cloud(), frame_id="base_link", sample_monotonic=at - 0.001)
    return guard.update(
        _cloud_for_level(level), frame_id="base_link", sample_monotonic=at
    )


class RiskHeartbeat:
    def __init__(self, arbiter: MotionArbiter) -> None:
        self.arbiter = arbiter
        self.sequence = 0
        self.base_timestamp = datetime(2026, 8, 23, tzinfo=timezone.utc)

    def refresh(self, at: float) -> None:
        self.sequence += 1
        timestamp = self.base_timestamp + timedelta(microseconds=self.sequence)
        accepted = self.arbiter.ingest_risk_event(
            {"event_type": "NON_FALL", "timestamp": timestamp.isoformat()},
            received_monotonic=at,
        )
        if not accepted:
            raise RuntimeError("fresh offline NON_FALL heartbeat was rejected")


def _record(
    *,
    capture: Path,
    event_kind: str,
    sequence: int | None,
    sample_monotonic: float,
    timestamp: object,
    observation: object | None,
    plan: object,
    command: VelocityCommand,
    lidar: LidarSafetyDecision,
    decision: object,
    execution: object,
    uwb_valid: bool,
) -> dict[str, object]:
    distance = getattr(observation, "distance_metres", None)
    bearing = getattr(observation, "bearing_radians", None)
    return {
        "capture": str(capture.resolve()),
        "event_kind": event_kind,
        "sequence": sequence,
        "timestamp": timestamp,
        "sample_monotonic": sample_monotonic,
        "uwb_distance": distance,
        "uwb_bearing": bearing,
        "uwb_valid": uwb_valid,
        "planner_state": getattr(plan, "current_state").value,
        "follow_target_x": getattr(plan, "target_x"),
        "follow_target_y": getattr(plan, "target_y"),
        "candidate_vx": command.vx,
        "candidate_vy": command.vy,
        "candidate_wz": command.wz,
        "lidar_state": lidar.level.value,
        "arbiter_authority": getattr(decision, "authority").value,
        "final_vx": getattr(decision, "vx"),
        "final_vy": getattr(decision, "vy"),
        "final_wz": getattr(decision, "wz"),
        "execution_enabled": REAL_MOTION_ENABLED,
        "executor_status": getattr(execution, "status").value,
        "executed_vx": getattr(execution, "vx"),
        "executed_vy": getattr(execution, "vy"),
        "executed_wz": getattr(execution, "wz"),
        "resume_authorized": False,
        "risk_state": getattr(decision, "risk_state").value,
        "reason": getattr(decision, "reason"),
    }


def replay_capture(
    path: Path,
    *,
    args: argparse.Namespace,
    service: DisabledRobotService,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    samples, probe_result = load_capture(path)
    if probe_result is None:
        raise ValueError(f"capture has no probe_result: {path}")
    required_baseline = (
        probe_result.get("dds_baseline_ok"),
        probe_result.get("uwb_writer_discovered"),
        probe_result.get("uwb_samples_received"),
    )
    if not all(required_baseline):
        raise ValueError(f"capture baseline failed: {path}")

    validator = UwbInputValidator(_input_config(args))
    planner = FollowTargetPlanner(
        FollowOffset(
            back_distance=args.back_distance,
            right_offset=args.right_offset,
        ),
        lost_timeout_seconds=PLANNER_TIMEOUT_SECONDS,
    )
    controller = FollowController(
        FollowControllerConfig(simulation_mode=False, velocity_feedforward_enabled=False)
    )
    arbiter = MotionArbiter(
        MotionArbiterConfig(
            uwb_timeout_seconds=ARBITER_UWB_TIMEOUT_SECONDS,
            external_risk_timeout_seconds=RISK_TIMEOUT_SECONDS,
            require_external_risk_feed=True,
        )
    )
    heartbeat = RiskHeartbeat(arbiter)
    executor = RealFollowExecutor(
        service,  # type: ignore[arg-type]
        config=RealFollowExecutorConfig(execution_enabled=False),
    )
    lidar_level = LidarSafetyLevel(args.lidar_state)
    lidar_config = _lidar_config(args)
    timeline: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    last_at: float | None = None
    captured_dropouts = 0

    def append_timeout(at: float, *, kind: str, gap: float) -> None:
        nonlocal captured_dropouts
        if kind == "captured_dropout_timeout":
            captured_dropouts += 1
        heartbeat.refresh(at)
        plan = planner.check_target_liveness(now_monotonic=at)
        command = controller.calculate_velocity(
            plan,
            control_owner=ControlOwner.FOLLOW,
            measurement_age_seconds=PLANNER_TIMEOUT_SECONDS,
            sample_monotonic=at,
        )
        lidar = _lidar_decision(lidar_level, at=at, config=lidar_config)
        decision = arbiter.decide(
            follow_command=command,
            uwb_age_seconds=PLANNER_TIMEOUT_SECONDS,
            lidar=lidar,
            now_monotonic=at,
        )
        execution = executor.execute(decision)
        row = _record(
            capture=path,
            event_kind=kind,
            sequence=None,
            sample_monotonic=at,
            timestamp=at,
            observation=None,
            plan=plan,
            command=command,
            lidar=lidar,
            decision=decision,
            execution=execution,
            uwb_valid=False,
        )
        row["receive_gap_seconds"] = gap
        timeline.append(row)

    for event in samples:
        raw = event.get("sample")
        if not isinstance(raw, dict):
            rejected.append({"sequence": event.get("sequence"), "reason": "missing sample"})
            continue
        receive_at = event.get("receive_monotonic", event.get("timestamp"))
        try:
            at = float(receive_at)
            observation = validator.normalize(
                distance_est=float(raw.get("distance_est")),
                orientation_est=float(raw.get("orientation_est")),
                sample_monotonic=at,
                enabled_from_app=int(raw.get("enabled_from_app")),
                error_state=int(raw.get("error_state")),
            )
        except (TypeError, ValueError) as exc:
            rejected.append({"sequence": event.get("sequence"), "reason": str(exc)})
            continue

        if last_at is not None and at - last_at >= PLANNER_TIMEOUT_SECONDS:
            append_timeout(
                last_at + PLANNER_TIMEOUT_SECONDS,
                kind="captured_dropout_timeout",
                gap=at - last_at,
            )

        heartbeat.refresh(at)
        plan = planner.process_measurement(
            observation.distance_metres,
            observation.bearing_radians,
            sample_monotonic=at,
        )
        command = controller.calculate_velocity(
            plan,
            control_owner=ControlOwner.FOLLOW,
            measurement_age_seconds=0.0,
            sample_monotonic=at,
        )
        lidar = _lidar_decision(lidar_level, at=at, config=lidar_config)
        decision = arbiter.decide(
            follow_command=command,
            uwb_age_seconds=0.0,
            lidar=lidar,
            now_monotonic=at,
        )
        execution = executor.execute(decision)
        timeline.append(
            _record(
                capture=path,
                event_kind="uwb_sample",
                sequence=int(event.get("sequence", len(timeline) + 1)),
                sample_monotonic=at,
                timestamp=event.get("timestamp", at),
                observation=observation,
                plan=plan,
                command=command,
                lidar=lidar,
                decision=decision,
                execution=execution,
                uwb_valid=True,
            )
        )
        last_at = at

    if last_at is not None:
        append_timeout(
            last_at + PLANNER_TIMEOUT_SECONDS,
            kind="end_of_capture_timeout",
            gap=PLANNER_TIMEOUT_SECONDS,
        )

    return timeline, {
        "capture": str(path.resolve()),
        "sample_count": len(samples),
        "accepted_sample_count": sum(row["event_kind"] == "uwb_sample" for row in timeline),
        "rejected_samples": rejected,
        "captured_dropout_count": captured_dropouts,
    }


def _desired_pose_scenario(args: argparse.Namespace) -> dict[str, object]:
    offset = FollowOffset(back_distance=args.back_distance, right_offset=args.right_offset)
    distance = math.hypot(offset.back_distance, offset.right_offset)
    bearing = math.atan2(offset.right_offset, offset.back_distance)
    raw_orientation = bearing - args.bearing_zero_offset_rad
    observation = UwbInputValidator(_input_config(args)).normalize(
        distance_est=distance,
        orientation_est=raw_orientation,
        sample_monotonic=1.0,
        enabled_from_app=1,
        error_state=0,
    )
    plan = FollowTargetPlanner(offset).process_measurement(
        observation.distance_metres,
        observation.bearing_radians,
        sample_monotonic=1.0,
    )
    command = FollowController(
        FollowControllerConfig(simulation_mode=False, velocity_feedforward_enabled=False)
    ).calculate_velocity(
        plan,
        control_owner=ControlOwner.FOLLOW,
        measurement_age_seconds=0.0,
        sample_monotonic=1.0,
    )
    return {
        "distance_m": distance,
        "bearing_rad": bearing,
        "target_x": plan.target_x,
        "target_y": plan.target_y,
        "candidate_vx": command.vx,
        "candidate_vy": command.vy,
        "candidate_wz": command.wz,
        "passed": all(
            math.isclose(value, 0.0, abs_tol=1e-9)
            for value in (plan.target_x, plan.target_y, command.vx, command.vy, command.wz)
        ),
    }


def _pick(
    timeline: Iterable[dict[str, object]], predicate: Any
) -> dict[str, object] | None:
    return next((row for row in timeline if predicate(row)), None)


def _arbitration_scenarios(
    args: argparse.Namespace,
    *,
    source_row: dict[str, object],
    service: DisabledRobotService,
) -> dict[str, object]:
    command = VelocityCommand(
        vx=float(source_row["candidate_vx"]),
        vy=float(source_row["candidate_vy"]),
        wz=float(source_row["candidate_wz"]),
        safety_state=SafetyState.SAFE,
        simulation_mode=False,
    )
    lidar_config = _lidar_config(args)
    lidar_results: dict[str, object] = {}
    for index, level in enumerate(LidarSafetyLevel, 1):
        now = 100.0 + index
        arbiter = MotionArbiter(monotonic_clock=lambda: now)
        RiskHeartbeat(arbiter).refresh(now)
        lidar = _lidar_decision(level, at=now, config=lidar_config)
        decision = arbiter.decide(
            follow_command=command,
            uwb_age_seconds=0.0,
            lidar=lidar,
            now_monotonic=now,
        )
        lidar_results[level.value] = {
            "authority": decision.authority.value,
            "reason": decision.reason,
            "final_vx": decision.vx,
            "final_vy": decision.vy,
            "final_wz": decision.wz,
            "passed": (
                decision.authority is MotionAuthority.FOLLOW
                and math.isclose(decision.vx, command.vx, abs_tol=1e-12)
                if level is LidarSafetyLevel.CLEAR
                else (
                    decision.authority is MotionAuthority.FOLLOW
                    and math.isclose(
                        decision.vx,
                        command.vx * lidar_config.slow_speed_scale,
                        abs_tol=1e-12,
                    )
                    if level is LidarSafetyLevel.SLOW
                    else decision.stop_required
                    and decision.vx == decision.vy == decision.wz == 0.0
                )
            ),
        }

    now = 200.0
    arbiter = MotionArbiter(monotonic_clock=lambda: now)
    heartbeat = RiskHeartbeat(arbiter)
    heartbeat.refresh(now)
    clear_lidar = _lidar_decision(LidarSafetyLevel.CLEAR, at=now, config=lidar_config)
    before = arbiter.decide(
        follow_command=command,
        uwb_age_seconds=0.0,
        lidar=clear_lidar,
        now_monotonic=now,
    )
    arbiter.ingest_risk_event(
        {
            "event_type": "FALL_CONFIRMED",
            "confidence": 0.93,
            "timestamp": "2026-08-23T16:00:00+08:00",
            "incident_id": "phase7-2a-fall-001",
        },
        received_monotonic=now + 0.1,
    )
    fall = arbiter.decide(
        follow_command=command,
        uwb_age_seconds=0.0,
        lidar=clear_lidar,
        now_monotonic=now + 0.1,
    )
    continued = arbiter.decide(
        follow_command=command,
        uwb_age_seconds=0.0,
        lidar=clear_lidar,
        now_monotonic=now + 0.2,
    )
    executor = RealFollowExecutor(
        service,  # type: ignore[arg-type]
        config=RealFollowExecutorConfig(execution_enabled=False),
    )
    fall_execution = executor.execute(fall)
    return {
        "lidar": lidar_results,
        "fall_preemption": {
            "before_authority": before.authority.value,
            "fall_authority": fall.authority.value,
            "fall_reason": fall.reason,
            "fall_risk_state": fall.risk_state.value,
            "fall_final": [fall.vx, fall.vy, fall.wz],
            "continued_authority": continued.authority.value,
            "continued_final": [continued.vx, continued.vy, continued.wz],
            "executor_status": fall_execution.status.value,
            "resume_authorized_after": executor.resume_authorized,
            "passed": (
                fall.authority is MotionAuthority.EMERGENCY
                and fall.risk_state is RiskState.PAUSED_BY_FALL
                and fall.vx == fall.vy == fall.wz == 0.0
                and continued.authority is MotionAuthority.EMERGENCY
                and continued.vx == continued.vy == continued.wz == 0.0
                and fall_execution.status is RealFollowExecutionStatus.DISABLED
                and not executor.resume_authorized
            ),
        },
    }


def run(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    _validate_args(args)
    service = DisabledRobotService()
    timeline: list[dict[str, object]] = []
    captures: list[dict[str, object]] = []
    for path in args.captures:
        rows, summary = replay_capture(path, args=args, service=service)
        timeline.extend(rows)
        captures.append(summary)

    far = _pick(
        timeline,
        lambda row: row["event_kind"] == "uwb_sample"
        and float(row["uwb_distance"]) > 2.5
        and float(row["candidate_vx"]) > 0.0,
    )
    too_close = _pick(
        timeline,
        lambda row: row["event_kind"] == "uwb_sample"
        and float(row["uwb_distance"]) < 1.0
        and float(row["candidate_vx"]) == 0.0,
    )
    left = _pick(
        timeline,
        lambda row: row["event_kind"] == "uwb_sample"
        and float(row["uwb_bearing"]) > 0.5
        and float(row["candidate_wz"]) > 0.0,
    )
    right = _pick(
        timeline,
        lambda row: row["event_kind"] == "uwb_sample"
        and float(row["uwb_bearing"]) < -0.3
        and float(row["candidate_wz"]) < 0.0,
    )
    dropout_rows = [
        row for row in timeline if row["event_kind"] == "captured_dropout_timeout"
    ]
    desired = _desired_pose_scenario(args)
    scenarios = (
        _arbitration_scenarios(args, source_row=far, service=service)
        if far is not None
        else {"lidar": {}, "fall_preemption": {"passed": False}}
    )

    checks = {
        "real_historical_far_positive_vx": far is not None,
        "desired_pose_near_zero_command": bool(desired["passed"]),
        "real_historical_too_close_zero_forward": too_close is not None,
        "real_historical_left_positive_wz": left is not None,
        "real_historical_right_negative_wz": right is not None,
        "real_captured_dropout_zero_motion": bool(dropout_rows)
        and all(
            row["reason"] == "uwb_stale"
            and row["final_vx"] == row["final_vy"] == row["final_wz"] == 0.0
            and row["resume_authorized"] is False
            for row in dropout_rows
        ),
        "lidar_clear_passes_candidate": bool(
            scenarios.get("lidar", {}).get("CLEAR", {}).get("passed")
        ),
        "lidar_slow_scales_candidate": bool(
            scenarios.get("lidar", {}).get("SLOW", {}).get("passed")
        ),
        "lidar_stop_zeroes_candidate": bool(
            scenarios.get("lidar", {}).get("STOP", {}).get("passed")
        ),
        "fall_confirmed_latches_zero_motion": bool(
            scenarios.get("fall_preemption", {}).get("passed")
        ),
        "executor_disabled_all_timeline_rows": all(
            row["execution_enabled"] is False
            and row["executor_status"] == RealFollowExecutionStatus.DISABLED.value
            and row["executed_vx"] == row["executed_vy"] == row["executed_wz"] == 0.0
            for row in timeline
        ),
        "move_count_zero": len(service.moves) == 0,
        "safe_stop_count_zero": len(service.stops) == 0,
        "dds_publishers_zero": DDS_PUBLISHER_COUNT == 0,
    }
    verdict = "PASS_OFFLINE_CONTROL_MAPPING" if all(checks.values()) else "HOLD_OFFLINE_CONTROL_MAPPING"
    report: dict[str, object] = {
        "phase": "7.2-A",
        "mode": "offline_control_mapping",
        "banner": ["REAL MOTION = DISABLED", "OFFLINE FILE REPLAY", "DDS PUBLISHERS = 0"],
        "verdict": verdict,
        "configuration": {
            "uwb": {
                "bearing_source": "orientation_est",
                "unit": "radians",
                "sign": 1,
                "zero_offset_rad": args.bearing_zero_offset_rad,
            },
            "follow": {
                "back_distance_m": args.back_distance,
                "right_offset_m": args.right_offset,
            },
            "lidar_candidate": {
                "slow_distance_m": args.slow_distance,
                "stop_distance_m": args.stop_distance,
                "roi_min_z_m": args.roi_min_z,
            },
            "planner_timeout_seconds": PLANNER_TIMEOUT_SECONDS,
            "arbiter_uwb_timeout_seconds": ARBITER_UWB_TIMEOUT_SECONDS,
            "external_risk_feed_required": True,
            "execution_enabled": False,
        },
        "captures": captures,
        "checks": checks,
        "mapping_evidence": {
            "far": far,
            "desired_pose": desired,
            "too_close": too_close,
            "left": left,
            "right": right,
            "captured_dropouts": dropout_rows,
        },
        "arbitration_scenarios": scenarios,
        "timeline_summary": {
            "row_count": len(timeline),
            "event_kind_counts": dict(Counter(str(row["event_kind"]) for row in timeline)),
            "authority_counts": dict(Counter(str(row["arbiter_authority"]) for row in timeline)),
            "executor_status_counts": dict(Counter(str(row["executor_status"]) for row in timeline)),
        },
        "timeline": timeline,
        "live_uwb_control_dry_run": "WAITING_FOR_ROBOT",
        "real_motion": "CLOSED",
        "motion_calls": len(service.moves),
        "safe_stop_calls": len(service.stops),
        "dds_publishers": DDS_PUBLISHER_COUNT,
    }
    return report, 0 if verdict.startswith("PASS") else 2


def main(argv: list[str] | None = None) -> int:
    print("PHASE 7.2-A OFFLINE CONTROL MAPPING", flush=True)
    print("REAL MOTION = DISABLED", flush=True)
    print("DDS PUBLISHERS = 0", flush=True)
    args = build_parser().parse_args(argv)
    try:
        report, exit_code = run(args)
    except (OSError, RuntimeError, ValueError) as exc:
        report = {
            "phase": "7.2-A",
            "mode": "offline_control_mapping",
            "verdict": "FAIL_INPUT",
            "error": str(exc),
            "real_motion": "CLOSED",
            "motion_calls": 0,
            "dds_publishers": 0,
        }
        exit_code = 2
    rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered + "\n", encoding="utf-8")
    print(json.dumps({key: report.get(key) for key in ("verdict", "real_motion", "motion_calls", "dds_publishers")}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
