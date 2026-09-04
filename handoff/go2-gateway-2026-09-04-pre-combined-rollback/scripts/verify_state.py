from __future__ import annotations

import sys
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_settings
from app.gateway.go2_gateway import Go2Gateway
from scripts.adapter_factory import build_adapter


def main() -> None:
    settings = load_settings()
    adapter = build_adapter(settings)
    gateway = Go2Gateway(adapter)
    gateway.connect()
    try:
        deadline = time.monotonic() + 10.0
        status = gateway.get_status()
        while not status.get("online") and time.monotonic() < deadline:
            time.sleep(0.2)
            status = gateway.get_status()
        print(status)
    finally:
        gateway.close()


if __name__ == "__main__":
    main()
