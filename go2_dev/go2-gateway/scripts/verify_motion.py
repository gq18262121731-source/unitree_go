from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_settings
from scripts.adapter_factory import build_adapter


SAFETY_TEXT = """
Go2 motion verification
Confirm: clear 3m x 3m area, robot starts lying down, remote controller is powered on.
This script defaults to StandUp -> StopMove -> StandDown only.
"""


def main() -> None:
    print(SAFETY_TEXT)
    if input("Type GO2 to continue: ").strip() != "GO2":
        print("Cancelled.")
        return
    settings = load_settings()
    adapter = build_adapter(settings)
    adapter.initialize()
    try:
        print("StandUp:", adapter.stand_up())
        print("StopMove:", adapter.stop())
        print("StandDown:", adapter.stand_down())
        if input("Optional tiny move test? Type MOVE to run: ").strip() == "MOVE":
            try:
                print("Move:", adapter.move(0.05, 0.0, 0.0))
            finally:
                print("StopMove:", adapter.stop())
    finally:
        adapter.close()


if __name__ == "__main__":
    main()
