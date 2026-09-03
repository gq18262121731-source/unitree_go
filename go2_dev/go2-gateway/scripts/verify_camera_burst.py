from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_settings
from app.core.state_store import StateStore
from app.gateway.go2_gateway import Go2Gateway
from app.services.camera_service import CameraService
from scripts.adapter_factory import build_adapter


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture multiple read-only Go2 camera snapshots.")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--output-dir", type=Path, default=Path.home() / "go2-camera-test")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.count < 1:
        raise SystemExit("--count must be at least 1")

    settings = load_settings()
    adapter = build_adapter(settings)
    gateway = Go2Gateway(adapter)
    store = StateStore(settings.robot_id, settings.state_stale_seconds)
    service = CameraService(gateway, store, settings)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    gateway.connect()
    try:
        for index in range(1, args.count + 1):
            jpeg = service.snapshot()
            output = args.output_dir / f"go2_snapshot_{index:02d}.jpg"
            output.write_bytes(jpeg)
            print(f"Saved {output}")
    finally:
        gateway.close()


if __name__ == "__main__":
    main()
