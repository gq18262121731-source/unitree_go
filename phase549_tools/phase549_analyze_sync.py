#!/usr/bin/env python3
"""Analyze the two synchronized Phase 5.4.9 L1 ROS 2 bags."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "phase548_tools"))
from phase548_analyze_segments import read_segment


def main():
    if len(sys.argv) != 3:
        raise SystemExit(
            "usage: phase549_analyze_sync.py BAG_ROOT OUTPUT_JSON"
        )

    root = Path(sys.argv[1])
    output = Path(sys.argv[2])
    paths = {
        "level_static": (
            root / "phase549_20260729_171243_level_static_utlidar"
        ),
        "pitch_nose_down_hold": (
            root
            / "phase549_20260729_175059_pitch_nose_down_hold_utlidar"
        ),
    }
    result = {
        "phase": "5.4.9",
        "segments": {
            label: read_segment(path) for label, path in paths.items()
        },
    }
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "segments": list(paths)}, indent=2))


if __name__ == "__main__":
    main()
