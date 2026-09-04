from __future__ import annotations

import threading
import time

import pytest

from app.motion.manual_control import (
    LatestManualVelocityDispatcher,
    ManualControlConfig,
    ManualKeyboardController,
)


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class Service:
    def __init__(self) -> None:
        self.owner = None
        self.refreshes = []
        self.stops = []

    def acquire_exclusive_control(self, owner: str) -> None:
        assert self.owner is None
        self.owner = owner

    def release_exclusive_control(self, owner: str) -> None:
        assert self.owner == owner
        self.owner = None

    def refresh_velocity(self, vx, vy, wz, source="api"):
        assert source == self.owner
        self.refreshes.append((vx, vy, wz, source))
        return {"code": 0}

    def safe_stop(self, source="api"):
        self.stops.append(source)
        return 0


class FakeDispatcher:
    def __init__(self) -> None:
        self.submissions = []
        self.stops = []
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def submit(self, vx, vy, wz) -> None:
        self.submissions.append((vx, vy, wz))

    def submit_stop(self, *, reason: str) -> bool:
        self.stops.append(reason)
        return True

    def raise_if_failed(self) -> None:
        return None

    def wait_idle(self, _timeout_seconds: float) -> bool:
        return True

    def snapshot(self):
        return {
            "in_flight": False,
            "pending": False,
            "submitted": len(self.submissions),
            "dispatched": len(self.submissions),
            "replaced": 0,
            "failed": False,
        }

    def close(self) -> None:
        self.closed = True


def build_controller(
    service: Service, clock: Clock
) -> tuple[ManualKeyboardController, FakeDispatcher]:
    dispatcher = FakeDispatcher()
    controller = ManualKeyboardController(
        service,
        monotonic_clock=clock,
        dispatcher_factory=lambda _service, _source: dispatcher,
        start_background_threads=False,
    )
    return controller, dispatcher


def scan_and_tick(
    controller: ManualKeyboardController,
    clock: Clock,
    keys: set[str],
    seconds: float,
) -> None:
    cycles = int(seconds / 0.02)
    for _ in range(cycles):
        controller.update_pressed(keys)
        controller.control_tick()
        clock.now += 0.02


def test_holding_w_three_seconds_is_rate_limited_without_deadman_stop() -> None:
    service = Service()
    clock = Clock()
    controller, dispatcher = build_controller(service, clock)
    controller.acquire()

    scan_and_tick(controller, clock, {"W"}, 3.0)

    assert 14 <= len(dispatcher.submissions) <= 16
    assert set(dispatcher.submissions) == {(0.35, 0.0, 0.0)}
    assert dispatcher.stops == []


@pytest.mark.parametrize(
    ("key", "expected"),
    [("A", (0.0, 0.0, 0.55)), ("D", (0.0, 0.0, -0.55))],
)
def test_holding_turn_three_seconds_is_continuous(key, expected) -> None:
    service = Service()
    clock = Clock()
    controller, dispatcher = build_controller(service, clock)
    controller.acquire()

    scan_and_tick(controller, clock, {key}, 3.0)

    assert 14 <= len(dispatcher.submissions) <= 16
    assert set(dispatcher.submissions) == {expected}
    assert dispatcher.stops == []


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        ({"W", "A"}, (0.30, 0.0, 0.45)),
        ({"W", "D"}, (0.30, 0.0, -0.45)),
        ({"S", "A"}, (-0.25, 0.0, 0.45)),
        ({"S", "D"}, (-0.25, 0.0, -0.45)),
    ],
)
def test_combination_keys_generate_walk_and_turn(keys, expected) -> None:
    service = Service()
    clock = Clock()
    controller, dispatcher = build_controller(service, clock)
    controller.acquire()
    controller.update_pressed(keys)

    assert controller.control_tick() is True
    assert dispatcher.submissions == [expected]


def test_releasing_all_keys_immediately_schedules_stop_barrier() -> None:
    service = Service()
    clock = Clock()
    controller, dispatcher = build_controller(service, clock)
    controller.acquire()
    controller.update_pressed({"W"})
    controller.control_tick()

    controller.update_pressed(set())

    assert dispatcher.stops == ["keys_released"]


def test_deadman_monitors_keyboard_scan_health_not_key_repeat() -> None:
    service = Service()
    clock = Clock()
    controller, dispatcher = build_controller(service, clock)
    controller.acquire()
    controller.update_pressed({"W"})
    controller.control_tick()
    clock.now += 0.50

    controller.control_tick()

    assert dispatcher.stops == ["keyboard_scan_stale"]


def test_space_schedules_stop_and_keeps_manual_authority() -> None:
    service = Service()
    clock = Clock()
    controller, dispatcher = build_controller(service, clock)
    controller.acquire()
    controller.update_pressed({"D"})
    controller.control_tick()

    controller.stop(reason="manual_space")

    assert controller.active is True
    assert dispatcher.stops == ["manual_space"]


def test_release_stops_releases_only_shared_writer() -> None:
    service = Service()
    clock = Clock()
    controller, dispatcher = build_controller(service, clock)
    controller.acquire()
    controller.update_pressed({"Q"})
    controller.control_tick()

    controller.release(reason="manual_release")

    assert controller.active is False
    assert dispatcher.stops == ["manual_release"]
    assert dispatcher.closed is True
    assert service.owner is None
    assert service.stops[-1] == "wireless_manual:manual_release:final"


def test_emergency_arbiter_preempts_manual_before_dispatch() -> None:
    service = Service()
    clock = Clock()
    controller, dispatcher = build_controller(service, clock)
    controller.acquire()
    controller.arbiter.set_emergency(True, reason="test_emergency")
    controller.update_pressed({"W"})

    assert controller.control_tick() is False
    assert dispatcher.submissions == []
    assert dispatcher.stops == ["test_emergency"]


def test_one_second_actual_submission_count_is_capped_at_five_hz() -> None:
    service = Service()
    clock = Clock()
    controller, dispatcher = build_controller(service, clock)
    controller.acquire()

    scan_and_tick(controller, clock, {"W"}, 1.0)

    assert 4 <= len(dispatcher.submissions) <= 6


def test_slow_ack_retains_only_latest_pending_velocity() -> None:
    class BlockingService(Service):
        def __init__(self) -> None:
            super().__init__()
            self.started = threading.Event()
            self.release_ack = threading.Event()

        def refresh_velocity(self, vx, vy, wz, source="api"):
            self.started.set()
            self.release_ack.wait(2.0)
            return super().refresh_velocity(vx, vy, wz, source)

    service = BlockingService()
    service.acquire_exclusive_control("wireless_manual")
    dispatcher = LatestManualVelocityDispatcher(
        service, source="wireless_manual"
    )
    dispatcher.start()
    dispatcher.submit(0.35, 0.0, 0.0)
    assert service.started.wait(1.0)

    for index in range(100):
        dispatcher.submit(0.0, 0.0, -0.77 + index * 0.001)
    snapshot = dispatcher.snapshot()
    assert snapshot["in_flight"] is True
    assert snapshot["pending"] is True
    assert snapshot["replaced"] == 99

    service.release_ack.set()
    assert dispatcher.wait_idle(2.0)
    dispatcher.close()
    assert len(service.refreshes) == 2
    assert service.refreshes[-1][:3] == pytest.approx((0.0, 0.0, -0.671))
    service.release_exclusive_control("wireless_manual")


def test_manual_config_enforces_frozen_limits_and_key_whitelist() -> None:
    with pytest.raises(ValueError, match="W/S limit"):
        ManualControlConfig(forward_mps=0.50)
    with pytest.raises(ValueError, match="hard 0.20"):
        ManualControlConfig(lateral_mps=0.21)
    with pytest.raises(ValueError, match="hard 0.55"):
        ManualControlConfig(yaw_radps=0.56)
    service = Service()
    clock = Clock()
    controller, _dispatcher = build_controller(service, clock)
    controller.acquire()
    with pytest.raises(ValueError, match="W/S/A/D/Q/E"):
        controller.command("X")
