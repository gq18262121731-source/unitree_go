from __future__ import annotations

import json

from tools.compare_follow_algorithms import main


def test_comparison_recommends_conservative_feedforward(tmp_path, capsys) -> None:
    output = tmp_path / "comparison.json"

    code = main(["--output", str(output)])

    assert code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["hardware_access"] is False
    assert report["production_controller_modified"] is False
    assert report["recommended_algorithm"] == "velocity_feedforward"
    assert all(item["safety_passed"] for item in report["comparisons"])
    assert json.loads(capsys.readouterr().out) == report
