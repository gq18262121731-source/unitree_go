from __future__ import annotations

from app.adapters.mock_adapter import MockGo2Adapter
from app.adapters.unitree_adapter import UnitreeGo2Adapter
from app.config import Settings


def build_adapter(settings: Settings):
    if settings.mode == "real":
        return UnitreeGo2Adapter(
            settings.network_interface,
            settings.sdk_timeout_seconds,
            settings.robot_id,
            settings.robot_ip,
            settings.domain_id,
        )
    return MockGo2Adapter(settings.robot_id)
