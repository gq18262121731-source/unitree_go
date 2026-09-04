from __future__ import annotations

import json
from pathlib import Path

from tools.phase7_test_risk_feed import build_parser, run


def test_fixture_writes_only_labelled_non_fall_events(
    tmp_path: Path,
) -> None:
    path = tmp_path / "risk.jsonl"
    args = build_parser().parse_args(
        [
            "--output",
            str(path),
            "--seconds",
            "0.03",
            "--interval",
            "0.01",
            "--reset",
        ]
    )

    result = run(args)
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    assert result["motion_calls"] == 0
    assert len(lines) >= 2
    assert {line["event_type"] for line in lines} == {"NON_FALL"}
    assert all(line["test_fixture"] is True for line in lines)
