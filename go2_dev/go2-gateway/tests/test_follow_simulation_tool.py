from __future__ import annotations

import json

from tools.run_follow_simulation import main


def test_simulation_tool_writes_hardware_free_report(tmp_path, capsys) -> None:
    output = tmp_path / "report.json"

    code = main(["--scenario", "equilibrium", "--output", str(output)])

    assert code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["mode"] == "software_only"
    assert report["hardware_access"] is False
    assert report["scenario_count"] == 1
    assert report["results"][0]["scenario"] == "equilibrium"
    assert "samples" not in report["results"][0]
    assert json.loads(capsys.readouterr().out) == report
