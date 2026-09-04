from __future__ import annotations

import json
import math

import pytest

from app.webrtc.follow_target_forwarder import (
    FollowTargetForwardConfig,
    FollowTargetState,
    Go2UwbFollowTargetSource,
    UdpFollowTargetForwarder,
)


class FakeRuntime:
    def __init__(self) -> None:
        self.now = 100.0
        self.received_monotonic = 100.0
        self.source_timestamp_ms = 1_787_892_000_123
        self.fields: dict[str, object] = {
            "distance_est": 1.5,
            "orientation_est": math.radians(15.0) - 0.55,
            "enabled_from_app": 1,
            "error_state": 0,
        }
        self.source_keys = list(self.fields)
        self.connected = True
        self.uwb_switch = True

    def get_uwb_snapshot(self) -> dict[str, object]:
        return {
            "fields": dict(self.fields),
            "received_monotonic": self.received_monotonic,
            "received_timestamp_ms": self.source_timestamp_ms,
            "sample_count": 1,
            "source_keys": list(self.source_keys),
            "topic": "rt/uwbstate",
        }

    def status(self) -> dict[str, object]:
        return {
            "connected": self.connected,
            "connectionCount": 1 if self.connected else 0,
            "sportStateReady": self.connected,
            "multipleState": {"uwbSwitch": self.uwb_switch},
        }


class FakeSocket:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.blocking: bool | None = None
        self.packets: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False

    def setblocking(self, value: bool) -> None:
        self.blocking = value

    def sendto(self, payload: bytes, destination: tuple[str, int]) -> int:
        if self.fail:
            raise OSError("simulated UDP failure")
        self.packets.append((payload, destination))
        return len(payload)

    def close(self) -> None:
        self.closed = True


class StaticSource:
    def __init__(self, state: FollowTargetState) -> None:
        self.state = state

    def current_state(self) -> FollowTargetState:
        return self.state


def make_source(
    runtime: FakeRuntime,
    *,
    monitoring_active: bool = True,
    follow_active: bool = True,
) -> Go2UwbFollowTargetSource:
    source = Go2UwbFollowTargetSource(
        runtime,
        bearing_sign=1,
        bearing_zero_offset_rad=0.55,
        stale_seconds=1.0,
        allow_missing_error_state=False,
        monitoring_active=monitoring_active,
        clock=lambda: runtime.now,
    )
    source.set_follow_active(follow_active)
    return source


def make_config() -> FollowTargetForwardConfig:
    return FollowTargetForwardConfig(
        enabled=True,
        host="192.168.8.10",
        port=8766,
        hz=20.0,
    )


def test_real_uwb_bearing_and_distance_are_sent_in_protocol_convention() -> None:
    runtime = FakeRuntime()
    udp = FakeSocket()
    sender = UdpFollowTargetForwarder(
        make_config(),
        make_source(runtime),
        socket_factory=lambda *_args: udp,
        wall_clock=lambda: 1_787_892_000.130,
    )

    sent = sender.send_once()
    payload = json.loads(udp.packets[0][0])

    assert udp.blocking is False
    assert udp.packets[0][1] == ("192.168.8.10", 8766)
    assert sent.target_valid is True
    assert payload["schema_version"] == "go2_follow_target.v1"
    assert payload["sequence"] == 1
    assert payload["source_connected"] is True
    assert payload["bearing_deg"] == pytest.approx(-15.0)
    assert payload["distance_m"] == pytest.approx(1.5)
    assert payload["source_timestamp_ms"] == 1_787_892_000_123
    assert payload["sent_timestamp_ms"] == 1_787_892_000_130


def test_real_uwb_state_can_use_lightweight_runtime_status() -> None:
    runtime = FakeRuntime()
    source = make_source(runtime)
    snapshot = runtime.get_uwb_snapshot()
    status = {
        "connected": True,
        "connectionCount": 1,
        "uwb": {
            "topic": snapshot["topic"],
            "sampleCount": snapshot["sample_count"],
            "fields": snapshot["fields"],
            "sourceKeys": snapshot["source_keys"],
            "receivedMonotonic": snapshot["received_monotonic"],
            "receivedTimestampMs": snapshot["received_timestamp_ms"],
        },
    }

    state = source.current_state_from_runtime_status(status)

    assert state.target_valid is True
    assert state.distance_m == pytest.approx(1.5)
    assert state.bearing_deg == pytest.approx(-15.0)
    assert state.source_timestamp_ms == runtime.source_timestamp_ms


def test_invalid_new_target_does_not_reuse_previous_valid_measurement() -> None:
    runtime = FakeRuntime()
    source = make_source(runtime)
    assert source.current_state().target_valid is True

    runtime.fields["distance_est"] = math.nan
    state = source.current_state()

    assert state.target_valid is False
    assert state.bearing_deg is None
    assert state.distance_m is None


def test_one_hundred_updates_leave_only_latest_state_for_sender() -> None:
    runtime = FakeRuntime()
    udp = FakeSocket()
    sender = UdpFollowTargetForwarder(
        make_config(),
        make_source(runtime),
        socket_factory=lambda *_args: udp,
    )
    for index in range(100):
        runtime.fields["distance_est"] = 1.0 + index / 100.0
        runtime.source_timestamp_ms += 1

    sender.send_once()
    payload = json.loads(udp.packets[0][0])

    assert len(udp.packets) == 1
    assert payload["distance_m"] == pytest.approx(1.99)
    assert payload["source_timestamp_ms"] == 1_787_892_000_223


def test_udp_send_failure_does_not_escape_and_next_send_recovers() -> None:
    sockets = [FakeSocket(fail=True), FakeSocket()]
    sender = UdpFollowTargetForwarder(
        make_config(),
        StaticSource(FollowTargetState(target_valid=False)),
        socket_factory=lambda *_args: sockets.pop(0),
    )

    sender.send_once()
    sender.send_once()
    status = sender.debug_status()

    assert status["send_error_count"] == 1
    assert status["send_count"] == 1
    assert status["latest_sequence"] == 2


def test_go2_source_failure_sends_invalid_instead_of_stopping_sender() -> None:
    class BrokenSource:
        def current_state(self) -> FollowTargetState:
            raise RuntimeError("simulated UWB read failure")

    udp = FakeSocket()
    sender = UdpFollowTargetForwarder(
        make_config(),
        BrokenSource(),
        socket_factory=lambda *_args: udp,
    )

    state = sender.send_once()

    assert state.target_valid is False
    assert json.loads(udp.packets[0][0])["target_valid"] is False


def test_monitoring_active_with_fresh_uwb_is_valid_when_follow_inactive() -> None:
    runtime = FakeRuntime()
    state = make_source(runtime, follow_active=False).current_state()

    assert state.monitoring_active is True
    assert state.follow_active is False
    assert state.target_valid is True
    assert state.bearing_deg == pytest.approx(-15.0)
    assert state.distance_m == pytest.approx(1.5)


def test_monitoring_active_with_fresh_uwb_is_valid_when_follow_active() -> None:
    runtime = FakeRuntime()
    state = make_source(runtime, follow_active=True).current_state()

    assert state.monitoring_active is True
    assert state.follow_active is True
    assert state.target_valid is True


def test_monitoring_inactive_forces_target_invalid() -> None:
    runtime = FakeRuntime()
    state = make_source(
        runtime,
        monitoring_active=False,
        follow_active=True,
    ).current_state()

    assert state.monitoring_active is False
    assert state.follow_active is True
    assert state.target_valid is False
    assert state.bearing_deg is None
    assert state.distance_m is None


def test_missing_fields_do_not_create_relative_coordinates() -> None:
    runtime = FakeRuntime()
    state = make_source(runtime).current_state()

    assert state.target_valid is True
    assert state.relative_x_m is None
    assert state.relative_y_m is None


def test_stale_or_disconnected_source_fails_closed() -> None:
    runtime = FakeRuntime()
    source = make_source(runtime)
    runtime.now += 1.0
    assert source.current_state().target_valid is False

    runtime.now = 100.0
    runtime.connected = False
    disconnected = source.current_state()
    assert disconnected.target_valid is False
    assert disconnected.source_connected is False


def test_disconnected_source_keeps_udp_heartbeat_with_explicit_source_state() -> None:
    runtime = FakeRuntime()
    runtime.connected = False
    udp = FakeSocket()
    sender = UdpFollowTargetForwarder(
        make_config(),
        make_source(runtime),
        socket_factory=lambda *_args: udp,
    )

    sender.send_once()
    sender.send_once()
    payload = json.loads(udp.packets[-1][0])

    assert len(udp.packets) == 2
    assert payload["sequence"] == 2
    assert payload["target_valid"] is False
    assert payload["source_connected"] is False
    assert payload["bearing_deg"] is None
    assert payload["distance_m"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("distance_est", math.nan),
        ("distance_est", -0.01),
        ("orientation_est", math.nan),
        ("orientation_est", math.inf),
    ],
)
def test_invalid_distance_or_orientation_forces_target_invalid(
    field: str, value: float
) -> None:
    runtime = FakeRuntime()
    runtime.fields[field] = value

    state = make_source(runtime, follow_active=False).current_state()

    assert state.monitoring_active is True
    assert state.follow_active is False
    assert state.target_valid is False
