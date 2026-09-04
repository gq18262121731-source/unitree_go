#!/usr/bin/env python3
"""Append a clearly-labelled Phase 7 test NON_FALL heartbeat JSONL stream.

This is a test fixture, not a production fall detector. It never imports the
Unitree SDK and has no robot-control surface.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--interval", type=float, default=0.50)
    parser.add_argument("--start-delay", type=float, default=0.0)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Truncate the test artifact before the start delay.",
    )
    return parser


def run(args: argparse.Namespace) -> dict[str, object]:
    for name in ("seconds", "interval", "start_delay"):
        value = float(getattr(args, name))
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(f"--{name.replace('_', '-')} must be finite and non-negative")
    if args.seconds <= 0.0 or args.interval <= 0.0:
        raise ValueError("--seconds and --interval must be greater than zero")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.reset:
        args.output.write_text("", encoding="utf-8")
    elif not args.output.exists():
        args.output.touch()
    time.sleep(args.start_delay)

    started = time.monotonic()
    write_count = max(1, int(math.ceil(args.seconds / args.interval)))
    count = 0
    for index in range(write_count):
        payload = {
            "event_type": "NON_FALL",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "test_fixture": True,
            "source": "phase7_test_risk_feed",
            "sequence": count + 1,
        }
        with args.output.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")
            stream.flush()
        count += 1
        if index + 1 < write_count:
            next_write = started + (index + 1) * args.interval
            time.sleep(max(0.0, next_write - time.monotonic()))

    return {
        "ok": True,
        "test_fixture": True,
        "robot_sdk_imported": False,
        "motion_calls": 0,
        "event_type": "NON_FALL",
        "count": count,
        "output": str(args.output),
    }


def main(argv: list[str] | None = None) -> int:
    try:
        result = run(build_parser().parse_args(argv))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
