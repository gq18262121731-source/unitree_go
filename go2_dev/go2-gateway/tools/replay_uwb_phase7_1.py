from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from app.follow import (
    FollowController,
    FollowControllerConfig,
    FollowTargetPlanner,
    UwbBearingSource,
    UwbBearingUnit,
    UwbInputConfig,
    UwbInputValidator,
)
from app.motion import (
    LidarSafetyConfig,
    LidarSafetyGuard,
    MotionArbiter,
    MotionArbiterConfig,
    RealFollowExecutionStatus,
    RealFollowExecutor,
)
from app.core.control_owner import ControlOwner


class DryRunRobotService:
    def __init__(self) -> None:
        self.moves: list[dict[str, object]] = []
        self.stops: list[str] = []

    def move(self, *args, **kwargs):
        self.moves.append({"args": args, "kwargs": kwargs})
        raise AssertionError("dry-run replay must never call move")

    def safe_stop(self, source: str = "api") -> int:
        self.stops.append(source)
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Replay a real go2_uwb_readonly_probe JSONL capture through the "
            "Phase 7 planner/controller/arbiter with real motion disabled."
        )
    )
    parser.add_argument("capture", type=Path, help="probe JSONL capture")
    parser.add_argument(
        "--bearing-source",
        choices=[item.value for item in UwbBearingSource],
        default=os.getenv("UWB_BEARING_SOURCE", "orientation_est"),
    )
    parser.add_argument(
        "--bearing-unit",
        choices=[item.value for item in UwbBearingUnit],
        default=os.getenv("UWB_BEARING_UNIT", "radians"),
    )
    parser.add_argument(
        "--bearing-sign",
        type=int,
        choices=[-1, 1],
        default=int(os.getenv("UWB_BEARING_SIGN", "1")),
    )
    parser.add_argument(
        "--bearing-zero-offset-rad",
        type=float,
        default=float(os.getenv("UWB_BEARING_ZERO_OFFSET_RAD", "0.55")),
    )
    parser.add_argument(
        "--confirm-calibration",
        action="store_true",
        help=(
            "Confirm distance plus orientation_est bearing unit/sign/offset "
            "from the physical Phase 7.1 test."
        ),
    )
    parser.add_argument("--output", type=Path, help="optional JSON report path")
    return parser


def load_capture(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    samples: list[dict[str, Any]] = []
    probe_result = None
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_number} is not valid JSON") from exc
        if event.get("event") == "uwb_sample":
            samples.append(event)
        elif event.get("event") == "probe_result":
            probe_result = event
    return samples, probe_result


def clear_cloud() -> list[tuple[float, float, float]]:
    return [(2.5, -1.0 + index * 0.1, 0.0) for index in range(21)]


def replay(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    samples, probe_result = load_capture(args.capture)
    baseline_checks = {
        "probe_result_present": probe_result is not None,
        "dds_baseline_ok": bool(probe_result and probe_result.get("dds_baseline_ok")),
        "uwb_writer_discovered": bool(probe_result and probe_result.get("uwb_writer_discovered")),
        "uwb_samples_received": bool(probe_result and probe_result.get("uwb_samples_received")),
        "samples_present": bool(samples),
    }
    if not all(baseline_checks.values()):
        report = {
            "phase": "7.1-B",
            "mode": "dry_run",
            "verdict": "FAIL_CAPTURE_BASELINE",
            "baseline_checks": baseline_checks,
            "motion_calls": 0,
        }
        return report, 2

    input_config = UwbInputConfig(
        bearing_source=UwbBearingSource(args.bearing_source),
        bearing_unit=UwbBearingUnit(args.bearing_unit),
        bearing_sign=args.bearing_sign,
        bearing_zero_offset_rad=args.bearing_zero_offset_rad,
        calibration_confirmed=args.confirm_calibration,
    )
    validator = UwbInputValidator(input_config)
    planner = FollowTargetPlanner(lost_timeout_seconds=2.0)
    controller = FollowController(
        FollowControllerConfig(
            simulation_mode=False,
            velocity_feedforward_enabled=False,
        )
    )
    arbiter = MotionArbiter(
        MotionArbiterConfig(require_external_risk_feed=False)
    )
    lidar = LidarSafetyGuard(LidarSafetyConfig(clear_samples_required=1))
    service = DryRunRobotService()
    executor = RealFollowExecutor(service)  # type: ignore[arg-type]
    state_counts: Counter[str] = Counter()
    authority_counts: Counter[str] = Counter()
    execution_counts: Counter[str] = Counter()
    rejected: list[dict[str, object]] = []
    commands: list[dict[str, float | int | str]] = []
    receive_times: list[float] = []
    timeout_evidence: list[dict[str, float | bool | str]] = []
    last_sample_time = None

    def evaluate_timeout(*, timeout_at: float, source: str, gap_seconds: float) -> bool:
        timeout_plan = planner.check_target_liveness(now_monotonic=timeout_at)
        timeout_command = controller.calculate_velocity(
            timeout_plan,
            control_owner=ControlOwner.FOLLOW,
            measurement_age_seconds=2.0,
            sample_monotonic=timeout_at,
        )
        fresh_lidar = lidar.update(
            clear_cloud(), frame_id="base_link", sample_monotonic=timeout_at
        )
        timeout_decision = arbiter.decide(
            follow_command=timeout_command,
            uwb_age_seconds=2.0,
            lidar=fresh_lidar,
            now_monotonic=timeout_at,
        )
        timeout_execution = executor.execute(timeout_decision)
        zero_motion = (
            timeout_decision.stop_required
            and timeout_decision.vx == 0.0
            and timeout_decision.vy == 0.0
            and timeout_decision.wz == 0.0
            and timeout_execution.status is RealFollowExecutionStatus.DISABLED
        )
        authority_counts[timeout_decision.authority.value] += 1
        execution_counts[timeout_execution.status.value] += 1
        timeout_evidence.append(
            {
                "source": source,
                "gap_seconds": gap_seconds,
                "timeout_at": timeout_at,
                "zero_motion": zero_motion,
                "decision_reason": timeout_decision.reason,
            }
        )
        return zero_motion

    for event in samples:
        raw_sample = event.get("sample")
        if not isinstance(raw_sample, dict):
            rejected.append({"sequence": event.get("sequence"), "reason": "missing sample object"})
            continue
        receive_time = event.get("receive_monotonic", event.get("timestamp"))
        try:
            observation = validator.normalize(
                distance_est=float(raw_sample.get("distance_est")),
                orientation_est=float(raw_sample.get("orientation_est")),
                sample_monotonic=float(receive_time),
                enabled_from_app=int(raw_sample.get("enabled_from_app")),
                error_state=int(raw_sample.get("error_state")),
            )
        except (TypeError, ValueError) as exc:
            rejected.append({"sequence": event.get("sequence"), "reason": str(exc)})
            continue

        if (
            last_sample_time is not None
            and observation.sample_monotonic - last_sample_time >= 2.0
        ):
            evaluate_timeout(
                timeout_at=last_sample_time + 2.0,
                source="captured_receive_gap",
                gap_seconds=observation.sample_monotonic - last_sample_time,
            )

        receive_times.append(observation.sample_monotonic)
        last_sample_time = observation.sample_monotonic
        plan = planner.process_measurement(
            observation.distance_metres,
            observation.bearing_radians,
            sample_monotonic=observation.sample_monotonic,
        )
        velocity = controller.calculate_velocity(
            plan,
            control_owner=ControlOwner.FOLLOW,
            measurement_age_seconds=0.0,
            sample_monotonic=observation.sample_monotonic,
        )
        lidar_decision = lidar.update(
            clear_cloud(),
            frame_id="base_link",
            sample_monotonic=observation.sample_monotonic,
        )
        decision = arbiter.decide(
            follow_command=velocity,
            uwb_age_seconds=0.0,
            lidar=lidar_decision,
            now_monotonic=observation.sample_monotonic,
        )
        execution = executor.execute(decision)
        state_counts[plan.current_state.value] += 1
        authority_counts[decision.authority.value] += 1
        execution_counts[execution.status.value] += 1
        commands.append(
            {
                "sequence": int(event.get("sequence", len(commands) + 1)),
                "distance_metres": observation.distance_metres,
                "bearing_radians": observation.bearing_radians,
                "vx": decision.vx,
                "wz": decision.wz,
                "authority": decision.authority.value,
            }
        )

    final_timeout_zero_motion = False
    if last_sample_time is not None:
        final_timeout_zero_motion = evaluate_timeout(
            timeout_at=last_sample_time + 2.0,
            source="end_of_capture_timeout",
            gap_seconds=2.0,
        )

    gaps = [later - earlier for earlier, later in zip(receive_times, receive_times[1:])]
    captured_timeouts = [
        item for item in timeout_evidence if item["source"] == "captured_receive_gap"
    ]
    valid_commands = bool(commands) and not rejected
    dry_run_safe = len(service.moves) == 0 and all(
        status == RealFollowExecutionStatus.DISABLED.value
        for status in execution_counts
    )
    checks = {
        **baseline_checks,
        "calibration_confirmed": bool(args.confirm_calibration),
        "all_samples_valid": valid_commands,
        "dropout_produces_zero_motion": (
            final_timeout_zero_motion
            and all(bool(item["zero_motion"]) for item in captured_timeouts)
        ),
        "real_executor_remained_disabled": dry_run_safe,
        "move_count_zero": len(service.moves) == 0,
    }
    if all(checks.values()):
        verdict, exit_code = "PASS_DRY_RUN", 0
    elif not args.confirm_calibration:
        verdict, exit_code = "HOLD_CALIBRATION_NOT_CONFIRMED", 3
    else:
        verdict, exit_code = "FAIL_DRY_RUN", 2

    vx_values = [float(item["vx"]) for item in commands]
    wz_values = [float(item["wz"]) for item in commands]

    def direction_summary(items: list[dict[str, float | int | str]]) -> dict[str, object]:
        if not items:
            return {"count": 0}
        bearings = [float(item["bearing_radians"]) for item in items]
        angular_commands = [float(item["wz"]) for item in items]
        return {
            "count": len(items),
            "mean_bearing_radians": sum(bearings) / len(bearings),
            "mean_wz": sum(angular_commands) / len(angular_commands),
            "min_wz": min(angular_commands),
            "max_wz": max(angular_commands),
        }

    front_commands = [item for item in commands if abs(float(item["bearing_radians"])) <= 0.15]
    left_commands = [item for item in commands if float(item["bearing_radians"]) > 0.15]
    right_commands = [item for item in commands if float(item["bearing_radians"]) < -0.15]
    control_direction_summary = {
        "front": direction_summary(front_commands),
        "left": direction_summary(left_commands),
        "right": direction_summary(right_commands),
    }

    desired_pose_target_ok = False
    desired_pose_command_ok = False
    desired_pose_check: dict[str, object] = {"evaluated": False}
    if args.confirm_calibration:
        desired_distance = math.hypot(
            planner.offset.back_distance, planner.offset.right_offset
        )
        desired_bearing = math.atan2(
            planner.offset.right_offset, planner.offset.back_distance
        )
        desired_raw_orientation = (
            desired_bearing / input_config.bearing_sign
            - input_config.bearing_zero_offset_rad
        )
        desired_observation = UwbInputValidator(input_config).normalize(
            distance_est=desired_distance,
            orientation_est=desired_raw_orientation,
            sample_monotonic=1.0,
            enabled_from_app=1,
            error_state=0,
        )
        desired_planner = FollowTargetPlanner(offset=planner.offset)
        desired_plan = desired_planner.process_measurement(
            desired_observation.distance_metres,
            desired_observation.bearing_radians,
            sample_monotonic=desired_observation.sample_monotonic,
        )
        desired_controller = FollowController(
            FollowControllerConfig(
                simulation_mode=False,
                velocity_feedforward_enabled=False,
            )
        )
        desired_command = desired_controller.calculate_velocity(
            desired_plan,
            control_owner=ControlOwner.FOLLOW,
            measurement_age_seconds=0.0,
            sample_monotonic=desired_observation.sample_monotonic,
        )
        desired_pose_target_ok = (
            abs(desired_plan.target_x) <= 1e-9
            and abs(desired_plan.target_y) <= 1e-9
        )
        desired_pose_command_ok = (
            abs(desired_command.vx) <= 1e-9
            and abs(desired_command.wz) <= 1e-9
        )
        desired_pose_check = {
            "evaluated": True,
            "back_distance": planner.offset.back_distance,
            "right_offset": planner.offset.right_offset,
            "distance_metres": desired_distance,
            "bearing_radians": desired_bearing,
            "raw_orientation_est": desired_raw_orientation,
            "target_x": desired_plan.target_x,
            "target_y": desired_plan.target_y,
            "vx": desired_command.vx,
            "wz": desired_command.wz,
        }
    direction_gate_applicable = all(
        int(control_direction_summary[name]["count"]) > 0
        for name in ("front", "left", "right")
    )
    direction_gate_checks = {
        "front_has_right_rear_correction": (
            direction_gate_applicable
            and float(control_direction_summary["front"]["mean_wz"]) < 0.0
        ),
        "left_wz_positive": (
            direction_gate_applicable
            and float(control_direction_summary["left"]["mean_wz"]) > 0.0
        ),
        "right_wz_negative": (
            direction_gate_applicable
            and float(control_direction_summary["right"]["mean_wz"]) < 0.0
        ),
        "desired_pose_target_error_near_zero": (
            desired_pose_target_ok
        ),
        "desired_pose_command_near_zero": (
            desired_pose_command_ok
        ),
    }
    direction_gate_passed = direction_gate_applicable and all(
        direction_gate_checks.values()
    )
    report = {
        "phase": "7.1-B",
        "mode": "dry_run",
        "verdict": verdict,
        "capture": str(args.capture.resolve()),
        "calibration": {
            "bearing_source": args.bearing_source,
            "bearing_unit": args.bearing_unit,
            "bearing_sign": args.bearing_sign,
            "bearing_zero_offset_rad": args.bearing_zero_offset_rad,
            "confirmed": bool(args.confirm_calibration),
        },
        "checks": checks,
        "sample_count": len(samples),
        "accepted_sample_count": len(commands),
        "rejected_samples": rejected,
        "maximum_receive_gap_seconds": max(gaps, default=None),
        "captured_dropout_count": len(captured_timeouts),
        "timeout_evidence": timeout_evidence,
        "planner_state_counts": dict(state_counts),
        "arbiter_authority_counts": dict(authority_counts),
        "executor_status_counts": dict(execution_counts),
        "command_summary": {
            "min_vx": min(vx_values, default=None),
            "max_vx": max(vx_values, default=None),
            "min_wz": min(wz_values, default=None),
            "max_wz": max(wz_values, default=None),
            "positive_vx_count": sum(value > 0.0 for value in vx_values),
            "zero_vx_count": sum(math.isclose(value, 0.0, abs_tol=1e-12) for value in vx_values),
            "positive_wz_count": sum(value > 0.0 for value in wz_values),
            "negative_wz_count": sum(value < 0.0 for value in wz_values),
        },
        "control_direction_summary": control_direction_summary,
        "desired_right_rear_pose_check": desired_pose_check,
        "control_direction_gate": {
            "applicable": direction_gate_applicable,
            "checks": direction_gate_checks,
            "passed": direction_gate_passed,
            "zero_command_tolerance": 1e-9,
        },
        "phase7_1c_entry_ready": (
            verdict == "PASS_DRY_RUN" and direction_gate_passed
        ),
        "motion_calls": len(service.moves),
        "notes": [
            "LiDAR input is a synthetic CLEAR cloud for UWB replay only.",
            "External risk feed is disabled only for this pre-integration dry-run.",
            "This report does not satisfy Phase 7.1-C LiDAR physical calibration.",
        ],
    }
    return report, exit_code


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report, exit_code = replay(args)
    except (OSError, ValueError) as exc:
        report = {
            "phase": "7.1-B",
            "mode": "dry_run",
            "verdict": "FAIL_INPUT",
            "error": str(exc),
            "motion_calls": 0,
        }
        exit_code = 2
    rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    print(rendered)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
