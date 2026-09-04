from __future__ import annotations

from app.adapters.base import RobotAdapter
from app.gateway.go2_gateway import Go2Gateway


class RecordingAdapter(RobotAdapter):
    sdk_version = "test-sdk"

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def initialize(self) -> None:
        self.calls.append(("initialize",))

    def close(self) -> None:
        self.calls.append(("close",))

    def is_initialized(self) -> bool:
        self.calls.append(("is_initialized",))
        return True

    def get_status(self) -> dict:
        self.calls.append(("get_status",))
        return {"online": True}

    def stand_up(self) -> int:
        self.calls.append(("stand_up",))
        return 0

    def stand_down(self) -> int:
        self.calls.append(("stand_down",))
        return 0

    def sit(self) -> int:
        self.calls.append(("sit",))
        return 0

    def stop(self) -> int:
        self.calls.append(("stop",))
        return 0

    def move(self, vx: float, vy: float, wz: float) -> int:
        self.calls.append(("move", vx, vy, wz))
        return 0

    def get_camera_jpeg(self) -> bytes:
        self.calls.append(("get_camera_jpeg",))
        return b"\xff\xd8\xff\xd9"


def test_gateway_wraps_required_go2_capabilities():
    adapter = RecordingAdapter()
    gateway = Go2Gateway(adapter)

    gateway.connect()
    assert gateway.get_status() == {"online": True}
    assert gateway.stand() == 0
    assert gateway.sit() == 0
    assert gateway.stop() == 0
    assert gateway.move(0.1, 0.0, 0.05) == 0
    assert gateway.get_camera() == b"\xff\xd8\xff\xd9"

    assert adapter.calls == [
        ("initialize",),
        ("get_status",),
        ("stand_up",),
        ("sit",),
        ("stop",),
        ("move", 0.1, 0.0, 0.05),
        ("get_camera_jpeg",),
    ]


def test_gateway_exposes_extended_lie_down_and_lifecycle_methods():
    adapter = RecordingAdapter()
    gateway = Go2Gateway(adapter)

    assert gateway.sdk_version == "test-sdk"
    assert gateway.is_initialized() is True
    assert gateway.lie_down() == 0
    gateway.close()

    assert adapter.calls == [
        ("is_initialized",),
        ("stand_down",),
        ("close",),
    ]
