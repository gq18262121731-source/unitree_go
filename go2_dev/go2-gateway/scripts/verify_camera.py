from __future__ import annotations

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


def main() -> None:
    settings = load_settings()
    adapter = build_adapter(settings)
    gateway = Go2Gateway(adapter)
    store = StateStore(settings.robot_id, settings.state_stale_seconds)
    camera_service = CameraService(gateway, store, settings)
    gateway.connect()
    try:
        jpeg = camera_service.snapshot()
        output = Path("go2_snapshot.jpg")
        output.write_bytes(jpeg)
        print(f"Saved {output.resolve()}")
    finally:
        gateway.close()


if __name__ == "__main__":
    main()
