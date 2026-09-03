from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .provider import UnitreeReadonlyProvider
from .sources.replay import JsonlReplaySource


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Go2 X EDU read-only status adapter")
    parser.add_argument("--source", choices=("replay", "ros2", "dds"), required=True)
    parser.add_argument("--input", help="JSONL file for --source replay")
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--interface", help="DDS network interface")
    parser.add_argument("--robot-ip", default="192.168.123.161")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.source == "replay":
        if not args.input:
            raise SystemExit("--input is required for replay")
        source = JsonlReplaySource(args.input)
    elif args.source == "ros2":
        from .sources.ros2 import Ros2ReadonlySource

        source = Ros2ReadonlySource()
    else:
        if not args.interface:
            raise SystemExit("--interface is required for DDS")
        from .sources.dds import Sdk2DdsReadonlySource

        source = Sdk2DdsReadonlySource(args.interface, args.robot_ip)

    report = UnitreeReadonlyProvider().collect(source, args.duration)
    text = json.dumps(
        report,
        indent=2 if args.pretty else None,
        ensure_ascii=False,
        sort_keys=True,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0 if not report["transport"]["errors"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
