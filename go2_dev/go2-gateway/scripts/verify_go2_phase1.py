from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.providers.unitree.real_provider import RealGo2Provider, RealProviderConfig


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Unitree Go2 EDU + L1 Phase 5.1 read-only hardware report."
    )
    parser.add_argument("--sample-seconds", type=float, default=10.0)
    parser.add_argument("--discovery-seconds", type=float, default=3.0)
    parser.add_argument("--ping-count", type=int, default=4)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    config = RealProviderConfig(
        robot_ip=os.getenv("UNITREE_ROBOT_IP", "192.168.123.161"),
        network_interface=os.getenv("UNITREE_NETWORK_INTERFACE", "eth0"),
        domain_id=int(os.getenv("UNITREE_DOMAIN_ID", "0")),
        provider=os.getenv("ROBOT_PROVIDER", "mock").lower(),
        real_motion_enabled=_bool_env("REAL_MOTION_ENABLED", False),
    )
    report = RealGo2Provider(config).collect_report(
        sample_seconds=args.sample_seconds,
        discovery_seconds=args.discovery_seconds,
        ping_count=args.ping_count,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
