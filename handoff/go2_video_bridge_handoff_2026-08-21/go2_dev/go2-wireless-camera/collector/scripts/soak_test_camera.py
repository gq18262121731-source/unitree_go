from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_settings
from app.frame_store import LatestFrameStore
from app.unitree_camera import CameraCollector


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a read-only Go2 camera soak test.")
    parser.add_argument("--duration-seconds", type=int, default=600)
    parser.add_argument("--report", type=Path, default=Path.home() / "go2-wireless-test" / "soak-report.json")
    args = parser.parse_args()

    settings = load_settings()
    store = LatestFrameStore()
    collector = CameraCollector(settings, store)
    collector.start()
    start = time.monotonic()
    try:
        while time.monotonic() - start < args.duration_seconds:
            time.sleep(min(10, args.duration_seconds - (time.monotonic() - start)))
            stats = collector.snapshot_stats()
            frame = store.status(settings.frame_stale_seconds, time.monotonic())
            mem_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            print(
                "captureFps={captureFps:.2f} frameCount={frameCount} sdkErrors={sdkErrorCount} "
                "longestGapMs={longestFrameGapMs:.1f} reconnects={reconnectCount} "
                "frameAgeMs={frameAgeMs} memoryKb={memoryKb}".format(
                    **stats,
                    frameAgeMs=frame.get("frameAgeMs"),
                    memoryKb=mem_kb,
                )
            )
        report = {
            "durationSeconds": args.duration_seconds,
            "collector": collector.snapshot_stats(),
            "frame": store.status(settings.frame_stale_seconds, time.monotonic()),
            "networkInterface": settings.network_interface,
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"report={args.report}")
    finally:
        collector.stop()


if __name__ == "__main__":
    main()
