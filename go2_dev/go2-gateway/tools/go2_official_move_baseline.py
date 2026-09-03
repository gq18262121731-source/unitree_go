#!/usr/bin/env python3
"""One-second official-style Go2 SportClient straight-motion baseline.

This diagnostic intentionally bypasses UWB, LiDAR, FollowController and the
gateway business layer. It sends exactly five Move(0.3, 0, 0) refreshes at
5 Hz, then calls StopMove exactly once from the cleanup path.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path


VX = 0.30
VY = 0.0
WZ = 0.0
FREQUENCY_HZ = 5.0
DURATION_SECONDS = 1.0
MOVE_CALL_LIMIT = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--interface", default="eth0")
    parser.add_argument("--domain", type=int, default=0)
    parser.add_argument("--output", type=Path)
    return parser


def run(interface: str, domain: int, output: Path | None) -> dict[str, object]:
    if not interface or any(character.isspace() for character in interface):
        raise ValueError("interface must be a non-empty interface name")
    if domain != 0:
        raise ValueError("official baseline is locked to DDS domain 0")

    from unitree_sdk2py.core.channel import ChannelFactoryInitialize
    from unitree_sdk2py.go2.sport.sport_client import SportClient

    ChannelFactoryInitialize(domain, interface)
    client = SportClient()
    client.SetTimeout(10.0)
    client.Init()

    move_records: list[dict[str, object]] = []
    stop_records: list[dict[str, object]] = []
    started = time.monotonic()
    stop_code: int | None = None
    try:
        period = 1.0 / FREQUENCY_HZ
        for index in range(MOVE_CALL_LIMIT):
            scheduled = started + index * period
            time.sleep(max(0.0, scheduled - time.monotonic()))
            called = time.monotonic()
            code = client.Move(VX, VY, WZ)
            returned = time.monotonic()
            move_records.append(
                {
                    "index": index + 1,
                    "called_monotonic": called,
                    "scheduled_offset_seconds": index * period,
                    "actual_offset_seconds": called - started,
                    "interval_seconds": (
                        None
                        if index == 0
                        else called - float(move_records[-1]["called_monotonic"])
                    ),
                    "vx": VX,
                    "vy": VY,
                    "wz": WZ,
                    "sdk_return_code": code,
                    "call_elapsed_seconds": returned - called,
                }
            )
            if code != 0:
                break
        time.sleep(max(0.0, started + DURATION_SECONDS - time.monotonic()))
    finally:
        stop_called = time.monotonic()
        stop_code = client.StopMove()
        stop_returned = time.monotonic()
        stop_records.append(
            {
                "called_monotonic": stop_called,
                "actual_offset_seconds": stop_called - started,
                "sdk_return_code": stop_code,
                "call_elapsed_seconds": stop_returned - stop_called,
            }
        )

    intervals = [
        float(record["interval_seconds"])
        for record in move_records
        if record["interval_seconds"] is not None
    ]
    report: dict[str, object] = {
        "phase": "7.2-C2_OFFICIAL_MOVE_BASELINE",
        "business_logic_bypassed": [
            "UWB",
            "LiDAR",
            "FollowController",
            "MotionArbiter",
            "external_risk_feed",
        ],
        "interface": interface,
        "domain": domain,
        "requested": {
            "vx": VX,
            "vy": VY,
            "wz": WZ,
            "frequency_hz": FREQUENCY_HZ,
            "duration_seconds": DURATION_SECONDS,
        },
        "move_calls": len(move_records),
        "stop_move_calls": len(stop_records),
        "all_move_codes_zero": all(
            record["sdk_return_code"] == 0 for record in move_records
        ),
        "stop_code_zero": stop_code == 0,
        "mean_move_interval_seconds": (
            sum(intervals) / len(intervals) if intervals else None
        ),
        "move_records": move_records,
        "stop_records": stop_records,
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return report


def main() -> int:
    args = build_parser().parse_args()
    if not args.execute:
        print(
            json.dumps(
                {
                    "ok": True,
                    "mode": "configuration_check_only",
                    "unitree_sdk_initialized": False,
                    "real_motion": "DISABLED",
                    "locked_profile": {
                        "vx": VX,
                        "vy": VY,
                        "wz": WZ,
                        "frequency_hz": FREQUENCY_HZ,
                        "duration_seconds": DURATION_SECONDS,
                        "move_call_limit": MOVE_CALL_LIMIT,
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    for prompt, expected in (
        ("Type OFFICIAL_MOVE_BASELINE: ", "OFFICIAL_MOVE_BASELINE"),
        ("Type VX_0.30_FOR_1.0_SECOND: ", "VX_0.30_FOR_1.0_SECOND"),
        ("Type REMOTE_OPERATOR_READY: ", "REMOTE_OPERATOR_READY"),
    ):
        if input(prompt).strip() != expected:
            print(json.dumps({"ok": False, "error": "confirmation failed"}))
            return 2

    try:
        report = run(args.interface, args.domain, args.output)
    except BaseException as exc:
        print(
            json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps({"ok": True, **report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
