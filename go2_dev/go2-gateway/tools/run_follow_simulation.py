from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from app.follow.simulation import FollowSimulation, standard_simulation_scenarios


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the isolated Go2 right-rear follow simulation. This tool does "
            "not initialize or call DDS, RobotService, Go2Gateway, or Unitree SDK2."
        )
    )
    parser.add_argument(
        "--scenario",
        default="all",
        help="Scenario name, or 'all' (default).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON report path. Parent directories are created.",
    )
    parser.add_argument(
        "--include-samples",
        action="store_true",
        help="Include the full time series in the JSON report.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    available = {scenario.name: scenario for scenario in standard_simulation_scenarios()}
    if args.scenario == "all":
        selected = list(available.values())
    elif args.scenario in available:
        selected = [available[args.scenario]]
    else:
        choices = ", ".join(sorted(available))
        raise SystemExit(f"unknown scenario {args.scenario!r}; choose one of: {choices}")

    simulation = FollowSimulation()
    results = [simulation.run(scenario) for scenario in selected]
    by_name = {result.scenario: result for result in results}
    checks: dict[str, bool] = {}
    if "stationary_front_3m" in by_name:
        checks["stationary_target_converges_within_0_12m"] = (
            by_name["stationary_front_3m"].final_target_error_norm < 0.12
        )
    if "too_close" in by_name:
        checks["too_close_stops"] = (
            by_name["too_close"].maximum_abs_vx == 0.0
            and by_name["too_close"].maximum_abs_wz == 0.0
        )
    if "uwb_dropout" in by_name:
        checks["uwb_dropout_stops"] = (
            by_name["uwb_dropout"].safety_counts.get("STOP_UWB_TIMEOUT", 0) > 0
        )
    if "manual_takeover" in by_name:
        checks["manual_takeover_stops"] = (
            by_name["manual_takeover"].safety_counts.get(
                "STOP_CONTROL_NOT_FOLLOW", 0
            )
            > 0
        )
    findings: list[str] = []
    if "moving_straight" in by_name:
        moving_error = by_name["moving_straight"].final_target_error_norm
        if moving_error > 0.5:
            findings.append(
                "The proportional controller has {:.3f} m steady-state lag "
                "while the target walks continuously at 0.15 m/s.".format(moving_error)
            )
    report = {
        "mode": "software_only",
        "hardware_access": False,
        "model": {
            "robot": "planar_unicycle",
            "uwb_frequency_hz": 5.0,
            "desired_person_position_in_robot_frame": {"x": 1.5, "y": 0.5},
            "controller_limits": {"max_vx": 0.3, "max_wz": 0.5},
        },
        "scenario_count": len(results),
        "checks": checks,
        "findings": findings,
        "results": [
            result.to_dict(include_samples=args.include_samples) for result in results
        ],
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
