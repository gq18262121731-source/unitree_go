from __future__ import annotations

import ast
from pathlib import Path


PROBE = Path(__file__).resolve().parents[2] / "tools" / "go2_uwb_readonly_probe.py"


def test_phase7_uwb_probe_remains_subscriber_only() -> None:
    source = PROBE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(PROBE))
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert "DataReader" in imported_names
    assert "ChannelPublisher" not in imported_names
    assert "DataWriter" not in imported_names
    assert "SportClient" not in source
    assert "StopMove" not in source
    assert ".Move(" not in source


def test_phase7_uwb_probe_records_monotonic_receive_timing() -> None:
    source = PROBE.read_text(encoding="utf-8")

    assert "receive_monotonic=received_monotonic" in source
    assert "maximum_uwb_receive_gap_seconds" in source
