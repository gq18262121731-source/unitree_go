from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_settings
from app.core.state_store import StateStore
from app.gateway.go2_gateway import Go2Gateway
from app.services.robot_service import RobotService
from scripts.adapter_factory import build_adapter


SAFETY_TEXT = """
Go2 motion verification
Confirm: clear 3m x 3m area, robot starts lying down, remote controller is powered on.
This script uses RobotService safety guards and defaults to StandUp -> StopMove -> StandDown only.
"""


def main() -> None:
    print(SAFETY_TEXT)
    if input("Type GO2 to continue: ").strip() != "GO2":
        print("Cancelled.")
        return
    settings = load_settings()
    adapter = build_adapter(settings)
    gateway = Go2Gateway(adapter)
    state_store = StateStore(settings.robot_id, settings.state_stale_seconds)
    robot_service = RobotService(gateway, settings, state_store)
    robot_service.initialize()
    try:
        print("StandUp:", robot_service.stand()["code"])
        print("StopMove:", robot_service.stop()["code"])
        if input("Optional tiny move test? Type MOVE to run: ").strip() == "MOVE":
            print("Move:", robot_service.move(0.05, 0.0, 0.0, settings.min_move_duration, source="verify_motion")["code"])
        print("StandDown:", robot_service.lie_down()["code"])
    finally:
        robot_service.close()


if __name__ == "__main__":
    main()
