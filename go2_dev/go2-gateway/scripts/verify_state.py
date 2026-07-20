from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_settings
from scripts.adapter_factory import build_adapter


def main() -> None:
    settings = load_settings()
    adapter = build_adapter(settings)
    adapter.initialize()
    if hasattr(adapter, "wait_for_status"):
        print(adapter.wait_for_status(timeout_seconds=10.0))
    else:
        print(adapter.get_status())


if __name__ == "__main__":
    main()
