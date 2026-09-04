from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from app.follow.experimental_controller import FollowControlAlgorithm
from app.follow.simulation import FollowSimulation, standard_simulation_scenarios


TRACKING_SCENARIOS = (
    "stationary_front_3m",
    "moving_straight",
    "moving_straight_noisy",
    "moving_left_turn",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare hardware-free right-rear follow control algorithms."
    )
    parser.add_argument("--output", type=Path, help="Optional JSON report path.")
    return parser


def build_report() -> dict[str, object]:
    scenarios = {scenario.name: scenario for scenario in standard_simulation_scenarios()}
    comparisons: list[dict[str, object]] = []
    for algorithm in FollowControlAlgorithm:
        simulation = FollowSimulation(algorithm=algorithm)
        results = {
            name: simulation.run(scenarios[name])
            for name in (
                *TRACKING_SCENARIOS,
                "too_close",
                "uwb_dropout",
                "manual_takeover",
            )
        }
        tracking_errors = {
            name: results[name].final_target_error_norm for name in TRACKING_SCENARIOS
        }
        minimum_tracking_distance = min(
            results[name].minimum_true_distance for name in TRACKING_SCENARIOS
        )
        safety_passed = (
            results["too_close"].maximum_abs_vx == 0.0
            and results["too_close"].maximum_abs_wz == 0.0
            and results["uwb_dropout"].safety_counts.get("STOP_UWB_TIMEOUT", 0) > 0
            and results["manual_takeover"].safety_counts.get(
                "STOP_CONTROL_NOT_FOLLOW", 0
            )
            > 0
            and all(result.maximum_abs_vx <= 0.3 for result in results.values())
            and all(result.maximum_abs_wz <= 0.5 for result in results.values())
        )
        conservative_tracking_passed = (
            safety_passed
            and tracking_errors["stationary_front_3m"] < 0.10
            and minimum_tracking_distance >= 1.50
        )
        comparisons.append(
            {
                "algorithm": algorithm.value,
                "tracking_errors_m": tracking_errors,
                "tracking_error_sum_m": sum(tracking_errors.values()),
                "minimum_tracking_distance_m": minimum_tracking_distance,
                "maximum_abs_vx": max(
                    result.maximum_abs_vx for result in results.values()
                ),
                "maximum_abs_wz": max(
                    result.maximum_abs_wz for result in results.values()
                ),
                "safety_passed": safety_passed,
                "conservative_tracking_passed": conservative_tracking_passed,
            }
        )

    candidates = [
        item for item in comparisons if item["conservative_tracking_passed"]
    ]
    recommended = min(
        candidates,
        key=lambda item: (
            item["tracking_errors_m"]["moving_straight_noisy"],
            item["tracking_error_sum_m"],
        ),
    )
    return {
        "mode": "software_only_algorithm_comparison",
        "hardware_access": False,
        "production_controller_modified": False,
        "selection_policy": (
            "Pass every safety gate, keep stationary error below 0.10 m and "
            "minimum tracking distance at or above 1.50 m, then minimize noisy "
            "straight-walk error."
        ),
        "recommended_algorithm": recommended["algorithm"],
        "comparisons": comparisons,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_report()
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
