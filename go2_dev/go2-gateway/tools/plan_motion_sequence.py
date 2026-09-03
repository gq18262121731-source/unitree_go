from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.motion.action_sequence import load_motion_sequence
from app.motion.sequence_geometry import plan_sequence_geometry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline ideal-path clearance calculator.")
    parser.add_argument("sequence", type=Path)
    parser.add_argument("--margin-m", type=float, default=1.0)
    args = parser.parse_args(argv)
    if args.margin_m < 0:
        parser.error("--margin-m must be >= 0")
    sequence = load_motion_sequence(args.sequence)
    result = {
        "sequence": sequence.name,
        "frame": "start robot frame: +x forward, +y left",
        "clockwiseIsNegativeYaw": True,
        **plan_sequence_geometry(sequence).to_dict(args.margin_m),
        "warning": "Ideal center path only; margin must cover robot footprint, gait sway, odometry error and stopping drift.",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
