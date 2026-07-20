from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_settings
from app.core.state_store import StateStore
from app.services.camera_service import CameraService
from scripts.adapter_factory import build_adapter


def main() -> None:
    settings = load_settings()
    adapter = build_adapter(settings)
    store = StateStore(settings.robot_id, settings.state_stale_seconds)
    adapter.initialize()
    try:
        jpeg = CameraService(adapter, store).snapshot()
        output = Path("go2_snapshot.jpg")
        output.write_bytes(jpeg)
        print(f"Saved {output.resolve()}")
    finally:
        adapter.close()


if __name__ == "__main__":
    main()
