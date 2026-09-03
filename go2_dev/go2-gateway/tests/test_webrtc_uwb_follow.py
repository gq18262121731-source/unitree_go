from __future__ import annotations

import math
import threading
import time

import pytest

from app.companion.config import FollowProfile
from app.webrtc.uwb_follow import (
    WirelessUwbFollowConfig,
    WirelessUwbFollowSession,
    _LatestVelocityDispatcher,
    load_wireless_uwb_follow_config,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 10.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        # Let the production latest-command worker run while logical time is
        # advanced without a real wall-clock wait. A tiny deterministic yield
        # avoids test-only starvation on a busy Windows runner.
        time.sleep(0.001)


class FakeRuntime:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.sample_count = 0
        self.stale = False
        self.orientation = 0.0
        self.distance = 2.2
        self.video_failure_at = None
        self.video_dropout_windows = []
        self.sport_failure_at = None
        self.sport_dropout_windows = []
        self.sport_sample_count = 0
        self.dropout_windows = []
        self.last_received = clock.now - 0.01

    def status(self) -> dict:
        sport_ready = not (
            (
                self.sport_failure_at is not None
                and self.clock.now >= self.sport_failure_at
            )
            or any(
                start <= self.clock.now < end
                for start, end in self.sport_dropout_windows
            )
        )
        if sport_ready:
            self.sport_sample_count += 1
        return {
            "connected": True,
            "connectionCount": 1,
            "sportStateReady": sport_ready,
            "stateSampleCounts": {"rt/sportmodestate": self.sport_sample_count},
            "videoReady": not (
                (
                    self.video_failure_at is not None
                    and self.clock.now >= self.video_failure_at
                )
                or any(
                    start <= self.clock.now < end
                    for start, end in self.video_dropout_windows
                )
            ),
            "lowState": {"fresh": True},
            "multipleState": {"uwbSwitch": True},
        }

    def get_uwb_snapshot(self) -> dict:
        in_dropout = any(
            start <= self.clock.now < end for start, end in self.dropout_windows
        )
        if not in_dropout:
            self.sample_count += 1
            self.last_received = self.clock.now - (1.0 if self.stale else 0.01)
        return {
            "fields": {
                "distance_est": self.distance,
                "orientation_est": self.orientation,
                "enabled_from_app": 1,
                "error_state": None,
            },
            "received_monotonic": self.last_received,
            "sample_count": self.sample_count,
            "source_keys": [
                "distance_est",
                "orientation_est",
                "enabled_from_app",
            ],
            "topic": "rt/uwbstate",
        }


class OneShotTimestampAnomalyRuntime(FakeRuntime):
    def __init__(self, clock: FakeClock, *, kind: str) -> None:
        super().__init__(clock)
        self.kind = kind
        self.snapshot_calls = 0
        self.first_control_timestamp = None

    def get_uwb_snapshot(self) -> dict:
        snapshot = super().get_uwb_snapshot()
        self.snapshot_calls += 1
        if self.snapshot_calls == 2:
            self.first_control_timestamp = snapshot["received_monotonic"]
        elif self.snapshot_calls == 3:
            assert self.first_control_timestamp is not None
            snapshot["received_monotonic"] = (
                self.first_control_timestamp
                if self.kind == "duplicate"
                else self.first_control_timestamp - 0.05
            )
        return snapshot


class StaticSampleRuntime(FakeRuntime):
    def get_uwb_snapshot(self) -> dict:
        if self.sample_count == 0:
            self.sample_count = 1
            self.last_received = self.clock.now - 0.01
        return {
            "fields": {
                "distance_est": 2.2,
                "orientation_est": self.orientation,
                "enabled_from_app": 1,
                "error_state": None,
            },
            "received_monotonic": self.last_received,
            "sample_count": self.sample_count,
            "source_keys": [
                "distance_est",
                "orientation_est",
                "enabled_from_app",
            ],
            "topic": "rt/uwbstate",
        }


class SequenceRuntime(FakeRuntime):
    def __init__(
        self,
        clock: FakeClock,
        *,
        distances: list[float],
        orientations: list[float] | None = None,
    ) -> None:
        super().__init__(clock)
        self._distances = distances
        self._orientations = orientations or [0.0] * len(distances)
        assert len(self._distances) == len(self._orientations)
        self._sequence_index = -1

    def get_uwb_snapshot(self) -> dict:
        self._sequence_index = min(
            self._sequence_index + 1,
            len(self._distances) - 1,
        )
        self.distance = self._distances[self._sequence_index]
        self.orientation = self._orientations[self._sequence_index]
        return super().get_uwb_snapshot()


class FakeService:
    def __init__(self, clock: FakeClock | None = None) -> None:
        self.clock = clock
        self.owner = None
        self.commands = []
        self.stops = []

    def acquire_exclusive_control(self, owner: str) -> None:
        assert self.owner is None
        self.owner = owner

    def release_exclusive_control(self, owner: str) -> None:
        assert self.owner == owner
        self.owner = None

    def refresh_velocity(self, vx, vy, wz, source="api") -> dict:
        assert source == self.owner
        self.commands.append(
            (vx, vy, wz, source, None if self.clock is None else self.clock.now)
        )
        return {"code": 0}

    def safe_stop(self, source="api") -> int:
        self.stops.append(source)
        return 0


def config(**overrides) -> WirelessUwbFollowConfig:
    values = {
        "duration_seconds": 1.0,
        "control_rate_hz": 5.0,
        "uwb_stale_timeout_seconds": 0.75,
        "max_vx_mps": 0.504,
        "max_wz_radps": 1.10,
        "normal_max_wz_radps": 0.90,
        "alignment_enter_error_deg": 45.0,
        "alignment_exit_error_deg": 30.0,
        "alignment_turn_speed_radps": 1.10,
        "allow_missing_error_state": True,
    }
    values.update(overrides)
    return WirelessUwbFollowConfig(**values)


def v21_profile() -> FollowProfile:
    return FollowProfile(
        target_distance=1.35,
        follow_start_distance=1.20,
        follow_stop_distance=1.05,
        bearing_deadband_radians=math.radians(5.0),
        walk_min=0.24,
        vx_max=0.504,
        wz_max=1.10,
        kx=0.40,
    )


def test_three_minute_config_is_externalized_and_bounded() -> None:
    observed = load_wireless_uwb_follow_config(
        "configs/webrtc_uwb_follow_3min.yaml"
    )

    assert observed.duration_seconds == 180.0
    assert observed.control_rate_hz == 4.0
    assert observed.uwb_stale_timeout_seconds == 1.0
    assert observed.max_vx_mps == 0.504
    assert observed.max_wz_radps == 1.10
    assert observed.normal_max_wz_radps == 0.90
    assert observed.alignment_enter_error_deg == 45.0
    assert observed.alignment_exit_error_deg == 30.0
    assert observed.alignment_turn_speed_radps == 1.10
    assert observed.require_uwb_switch is False
    assert observed.uwb_fault_escalation_seconds == 5.0
    assert observed.full_speed_distance_m == 2.0
    assert observed.distance_speed_curve_exponent == 1.4
    assert observed.turn_slowdown_start_error_deg == 30.0
    assert observed.turn_slowdown_min_scale == 0.65
    assert observed.auto_recover_uwb_stale is True
    assert observed.recover_max_age_seconds == 0.50
    assert observed.recover_consecutive_samples == 3
    assert observed.recover_min_duration_seconds == 0.50


def test_wireless_follow_runs_bounded_then_stops_and_releases_owner() -> None:
    clock = FakeClock()
    runtime = FakeRuntime(clock)
    service = FakeService()
    session = WirelessUwbFollowSession(
        runtime,
        service,
        FollowProfile(),
        config(),
        bearing_sign=1,
        bearing_zero_offset_rad=0.0,
        cancel_event=threading.Event(),
        monotonic_clock=clock,
        sleep=clock.sleep,
    )

    result = session.run()

    assert result.completed is True
    assert result.reason == "duration_complete"
    assert result.commands_sent > 0
    assert result.missing_error_state_samples > 0
    assert service.stops
    assert service.owner is None
    assert all(0.0 <= item[0] <= 0.504 for item in service.commands)
    assert all(abs(item[2]) <= 1.10 for item in service.commands)


def test_final_motion_telemetry_is_emitted_every_control_cycle() -> None:
    clock = FakeClock()
    runtime = FakeRuntime(clock)
    service = FakeService()
    progress = []
    session = WirelessUwbFollowSession(
        runtime,
        service,
        FollowProfile(),
        config(),
        bearing_sign=1,
        bearing_zero_offset_rad=0.0,
        cancel_event=threading.Event(),
        monotonic_clock=clock,
        sleep=clock.sleep,
        progress_callback=progress.append,
    )

    result = session.run()

    cycle_rows = [row for row in progress if "cycle" in row]
    assert len(cycle_rows) == result.cycles
    assert [row["cycle"] for row in cycle_rows] == list(
        range(1, result.cycles + 1)
    )
    assert all("vx" in row and "wz" in row for row in cycle_rows)


def test_duplicate_uwb_timestamp_drops_one_frame_without_ending_session() -> None:
    clock = FakeClock()
    runtime = OneShotTimestampAnomalyRuntime(clock, kind="duplicate")
    service = FakeService()
    progress = []
    session = WirelessUwbFollowSession(
        runtime,
        service,
        FollowProfile(),
        config(),
        bearing_sign=1,
        bearing_zero_offset_rad=0.0,
        cancel_event=threading.Event(),
        monotonic_clock=clock,
        sleep=clock.sleep,
        progress_callback=progress.append,
    )

    result = session.run()

    assert result.completed is True
    assert result.reason == "duration_complete"
    assert result.duplicate_samples_dropped == 1
    assert result.out_of_order_samples_dropped == 0
    assert result.commands_sent >= 2
    assert any(
        row.get("event") == "UWB_SAMPLE_DROPPED"
        and row.get("kind") == "duplicate"
        for row in progress
    )


def test_out_of_order_uwb_timestamp_drops_one_frame_without_ending_session() -> None:
    clock = FakeClock()
    runtime = OneShotTimestampAnomalyRuntime(clock, kind="out_of_order")
    service = FakeService()
    session = WirelessUwbFollowSession(
        runtime,
        service,
        FollowProfile(),
        config(),
        bearing_sign=1,
        bearing_zero_offset_rad=0.0,
        cancel_event=threading.Event(),
        monotonic_clock=clock,
        sleep=clock.sleep,
    )

    result = session.run()

    assert result.completed is True
    assert result.reason == "duration_complete"
    assert result.duplicate_samples_dropped == 0
    assert result.out_of_order_samples_dropped == 1
    assert result.commands_sent >= 2


def test_wireless_follow_stale_fault_latches_stop_and_does_not_resume() -> None:
    clock = FakeClock()
    runtime = FakeRuntime(clock)
    service = FakeService()
    progress = []

    def observe(row: dict[str, object]) -> None:
        progress.append(row)
        runtime.stale = True

    session = WirelessUwbFollowSession(
        runtime,
        service,
        FollowProfile(),
        config(duration_seconds=2.0),
        bearing_sign=1,
        bearing_zero_offset_rad=0.0,
        cancel_event=threading.Event(),
        monotonic_clock=clock,
        sleep=clock.sleep,
        progress_callback=observe,
    )

    result = session.run()

    assert progress
    assert result.completed is False
    assert result.reason == "uwb_stale"
    # The latest-only dispatcher may discard the first still-pending Move when
    # the next cycle already proves the UWB sample stale.
    assert result.commands_sent in {0, 1}
    assert service.stops
    assert service.owner is None


def test_uwb_stale_pauses_then_debounced_recovery_replans_and_resumes() -> None:
    clock = FakeClock()
    runtime = FakeRuntime(clock)
    runtime.dropout_windows = [(10.50, 12.00)]
    service = FakeService(clock)
    progress = []
    session = WirelessUwbFollowSession(
        runtime,
        service,
        FollowProfile(),
        config(
            duration_seconds=4.0,
            control_rate_hz=4.0,
            uwb_stale_timeout_seconds=1.0,
            auto_recover_uwb_stale=True,
            recover_max_age_seconds=0.50,
            recover_consecutive_samples=3,
            recover_min_duration_seconds=0.50,
        ),
        bearing_sign=1,
        bearing_zero_offset_rad=0.0,
        cancel_event=threading.Event(),
        monotonic_clock=clock,
        sleep=clock.sleep,
        progress_callback=progress.append,
    )

    result = session.run()

    assert result.completed is True
    assert result.reason == "duration_complete"
    assert result.uwb_dropout_count == 1
    assert result.auto_recovery_count == 1
    assert result.last_dropout_duration_seconds is not None
    assert result.last_dropout_duration_seconds >= 2.0
    assert any("stop_barrier" in item for item in service.stops)
    assert any(row.get("event") == "UWB_STALE_STOP" for row in progress)
    assert sum(
        row.get("event") == "UWB_RECOVERY_PROGRESS" for row in progress
    ) >= 3
    assert any(row.get("event") == "UWB_RECOVERED" for row in progress)
    command_times = [item[4] for item in service.commands]
    assert any(value < 11.25 for value in command_times)
    assert not any(11.25 <= value < 12.50 for value in command_times)
    assert any(value >= 12.50 for value in command_times)


def test_sport_state_stale_pauses_then_recovers_same_worker() -> None:
    clock = FakeClock()
    runtime = FakeRuntime(clock)
    runtime.sport_dropout_windows = [(10.50, 12.00)]
    service = FakeService(clock)
    progress = []
    session = WirelessUwbFollowSession(
        runtime,
        service,
        FollowProfile(),
        config(
            duration_seconds=4.0,
            control_rate_hz=4.0,
            auto_recover_uwb_stale=True,
            recover_consecutive_samples=3,
            recover_min_duration_seconds=0.50,
        ),
        bearing_sign=1,
        bearing_zero_offset_rad=0.0,
        cancel_event=threading.Event(),
        monotonic_clock=clock,
        sleep=clock.sleep,
        progress_callback=progress.append,
    )

    result = session.run()

    assert result.completed is True
    assert result.reason == "duration_complete"
    assert result.uwb_dropout_count == 0
    assert result.auto_recovery_count == 0
    assert result.sport_state_dropout_count == 1
    assert result.sport_state_auto_recovery_count == 1
    assert any(row.get("event") == "SPORT_STATE_STALE_STOP" for row in progress)
    assert sum(
        row.get("event") == "SPORT_STATE_RECOVERY_PROGRESS"
        for row in progress
    ) >= 3
    assert any(row.get("event") == "SPORT_STATE_RECOVERED" for row in progress)
    command_times = [item[4] for item in service.commands]
    assert any(value < 10.50 for value in command_times)
    assert not any(10.75 <= value < 12.50 for value in command_times)
    assert any(value >= 12.50 for value in command_times)


def test_prolonged_uwb_stale_escalates_once_but_keeps_session_waiting() -> None:
    clock = FakeClock()
    runtime = FakeRuntime(clock)
    runtime.dropout_windows = [(10.50, 30.00)]
    service = FakeService(clock)
    progress = []
    session = WirelessUwbFollowSession(
        runtime,
        service,
        FollowProfile(),
        config(
            duration_seconds=7.0,
            control_rate_hz=4.0,
            uwb_stale_timeout_seconds=1.0,
            auto_recover_uwb_stale=True,
            uwb_fault_escalation_seconds=5.0,
        ),
        bearing_sign=1,
        bearing_zero_offset_rad=0.0,
        cancel_event=threading.Event(),
        monotonic_clock=clock,
        sleep=clock.sleep,
        progress_callback=progress.append,
    )

    result = session.run()

    assert result.completed is True
    assert result.reason == "duration_complete"
    assert result.uwb_dropout_count == 1
    assert result.auto_recovery_count == 0
    assert result.uwb_stale_escalation_count == 1
    escalation = [
        row for row in progress if row.get("event") == "UWB_STALE_ESCALATED"
    ]
    assert len(escalation) == 1
    assert escalation[0]["session_action"] == "KEEP_WAITING"


def test_unconfirmed_stop_barrier_is_not_retried_by_session_cleanup() -> None:
    class UnconfirmedStopService(FakeService):
        def safe_stop(self, source="api") -> int:
            self.stops.append(source)
            return -1

    clock = FakeClock()
    runtime = FakeRuntime(clock)
    runtime.dropout_windows = [(10.50, 30.00)]
    service = UnconfirmedStopService(clock)
    session = WirelessUwbFollowSession(
        runtime,
        service,
        FollowProfile(),
        config(
            duration_seconds=3.0,
            control_rate_hz=4.0,
            uwb_stale_timeout_seconds=1.0,
            auto_recover_uwb_stale=True,
        ),
        bearing_sign=1,
        bearing_zero_offset_rad=0.0,
        cancel_event=threading.Event(),
        monotonic_clock=clock,
        sleep=clock.sleep,
    )

    result = session.run()

    assert result.completed is False
    assert "StopMove barrier was not acknowledged" in result.reason
    assert len(service.stops) == 1


def test_video_stale_for_five_seconds_stops_then_recovers_same_worker() -> None:
    clock = FakeClock()
    runtime = FakeRuntime(clock)
    runtime.video_dropout_windows = [(10.40, 15.40)]
    service = FakeService(clock)
    progress = []
    session = WirelessUwbFollowSession(
        runtime,
        service,
        FollowProfile(),
        config(duration_seconds=6.8),
        bearing_sign=1,
        bearing_zero_offset_rad=0.0,
        cancel_event=threading.Event(),
        monotonic_clock=clock,
        sleep=clock.sleep,
        progress_callback=progress.append,
    )

    result = session.run()

    assert result.completed is True
    assert result.reason == "duration_complete"
    assert any(row.get("event") == "VIDEO_STALE_STOP" for row in progress)
    assert any(row.get("event") == "VIDEO_RECOVERED" for row in progress)
    assert any("stop_barrier" in item for item in service.stops)
    command_times = [item[4] for item in service.commands]
    assert any(value < 10.40 for value in command_times)
    # One RPC that was already in flight before the stale edge may finish
    # after logical time advances. No new stream of Move requests is allowed.
    assert sum(10.40 <= value < 15.40 for value in command_times) <= 1
    assert any(value >= 15.40 for value in command_times)


def test_wireless_follow_operator_cancel_stops_without_commands() -> None:
    clock = FakeClock()
    runtime = FakeRuntime(clock)
    service = FakeService()
    cancelled = threading.Event()
    cancelled.set()
    session = WirelessUwbFollowSession(
        runtime,
        service,
        FollowProfile(),
        config(),
        bearing_sign=1,
        bearing_zero_offset_rad=0.0,
        cancel_event=cancelled,
        monotonic_clock=clock,
        sleep=clock.sleep,
    )

    result = session.run()

    assert result.completed is False
    assert result.reason == "operator_stop"
    assert result.commands_sent == 0
    assert service.stops


def test_abnormal_target_bearing_rotates_without_forward_motion() -> None:
    clock = FakeClock()
    runtime = FakeRuntime(clock)
    runtime.orientation = 2.5
    service = FakeService()
    session = WirelessUwbFollowSession(
        runtime,
        service,
        FollowProfile(),
        config(),
        bearing_sign=1,
        bearing_zero_offset_rad=0.0,
        cancel_event=threading.Event(),
        monotonic_clock=clock,
        sleep=clock.sleep,
    )

    result = session.run()

    assert result.completed is True
    assert service.commands
    assert all(item[0] == 0.0 for item in service.commands)
    assert all(abs(item[2]) == 1.10 for item in service.commands)
    assert any(abs(item[2]) > 0.0 for item in service.commands)


def test_alignment_hysteresis_turns_first_then_enables_forward_follow() -> None:
    clock = FakeClock()
    runtime = FakeRuntime(clock)
    runtime.orientation = 2.5
    progress = []

    class AligningService(FakeService):
        def refresh_velocity(self, vx, vy, wz, source="api") -> dict:
            result = super().refresh_velocity(vx, vy, wz, source)
            if vx == 0.0 and wz > 0.0:
                runtime.orientation -= 0.55
            return result

    service = AligningService()
    session = WirelessUwbFollowSession(
        runtime,
        service,
        FollowProfile(),
        config(duration_seconds=2.0),
        bearing_sign=1,
        bearing_zero_offset_rad=0.0,
        cancel_event=threading.Event(),
        monotonic_clock=clock,
        sleep=clock.sleep,
        progress_callback=progress.append,
    )

    result = session.run()

    assert result.completed is True
    assert service.commands[0][0] == 0.0
    assert service.commands[0][2] == 1.10
    assert any(item[0] > 0.0 for item in service.commands)
    assert any(item["alignment_mode"] is True for item in progress)
    assert any(item["alignment_mode"] is False for item in progress)


def test_alignment_speed_cannot_exceed_final_wireless_wz_limit() -> None:
    with pytest.raises(ValueError, match="no greater than max_wz_radps"):
        config(
            max_wz_radps=0.90,
            normal_max_wz_radps=0.90,
            alignment_turn_speed_radps=1.10,
        ).validate()


def test_medium_bearing_error_walks_and_turns_without_alignment() -> None:
    clock = FakeClock()
    runtime = FakeRuntime(clock)
    profile = v21_profile()
    runtime.orientation = profile.target_bearing_radians + math.radians(30.0)
    service = FakeService()
    progress = []
    session = WirelessUwbFollowSession(
        runtime,
        service,
        profile,
        config(),
        bearing_sign=1,
        bearing_zero_offset_rad=0.0,
        cancel_event=threading.Event(),
        monotonic_clock=clock,
        sleep=clock.sleep,
        progress_callback=progress.append,
    )

    result = session.run()

    assert result.completed is True
    assert service.commands
    assert any(command[0] > 0.0 and abs(command[2]) > 0.0 for command in service.commands)
    assert all(abs(command[2]) <= 0.90 for command in service.commands)
    assert all(row["alignment_mode"] is False for row in progress if "cycle" in row)


def test_v21_distance_curve_reaches_cap_and_slows_medium_turns() -> None:
    clock = FakeClock()
    session = WirelessUwbFollowSession(
        FakeRuntime(clock),
        FakeService(),
        v21_profile(),
        config(),
        bearing_sign=1,
        bearing_zero_offset_rad=0.0,
        cancel_event=threading.Event(),
        monotonic_clock=clock,
        sleep=clock.sleep,
    )

    near = session._shape_forward_speed(
        0.24, distance_m=1.45, bearing_error_deg=0.0
    )
    middle = session._shape_forward_speed(
        0.24, distance_m=1.70, bearing_error_deg=0.0
    )
    far = session._shape_forward_speed(
        0.24, distance_m=2.00, bearing_error_deg=0.0
    )
    turning = session._shape_forward_speed(
        0.24, distance_m=2.00, bearing_error_deg=40.0
    )

    assert 0.29 <= near <= 0.30
    assert 0.37 <= middle <= 0.39
    assert far == pytest.approx(0.504)
    assert 0.0 < turning < far


def test_five_degree_deadband_suppresses_only_small_yaw_correction() -> None:
    clock = FakeClock()
    runtime = FakeRuntime(clock)
    profile = v21_profile()
    runtime.orientation = profile.target_bearing_radians + math.radians(4.9)
    service = FakeService()
    session = WirelessUwbFollowSession(
        runtime,
        service,
        profile,
        config(),
        bearing_sign=1,
        bearing_zero_offset_rad=0.0,
        cancel_event=threading.Event(),
        monotonic_clock=clock,
        sleep=clock.sleep,
    )

    result = session.run()

    assert result.completed is True
    assert service.commands
    assert all(command[0] > 0.0 for command in service.commands)
    assert all(command[2] == 0.0 for command in service.commands)

    clock = FakeClock()
    runtime = FakeRuntime(clock)
    runtime.orientation = profile.target_bearing_radians + math.radians(5.1)
    service = FakeService()
    session = WirelessUwbFollowSession(
        runtime,
        service,
        profile,
        config(),
        bearing_sign=1,
        bearing_zero_offset_rad=0.0,
        cancel_event=threading.Event(),
        monotonic_clock=clock,
        sleep=clock.sleep,
    )

    session.run()

    assert service.commands
    assert any(abs(command[2]) > 0.0 for command in service.commands)


def test_alignment_hysteresis_enters_above_45_and_exits_at_30() -> None:
    clock = FakeClock()
    runtime = FakeRuntime(clock)
    profile = v21_profile()
    runtime.orientation = profile.target_bearing_radians + math.radians(46.0)
    service = FakeService()
    progress = []

    def observe(row: dict[str, object]) -> None:
        progress.append(row)
        cycle = row.get("cycle")
        if cycle == 1:
            runtime.orientation = (
                profile.target_bearing_radians + math.radians(35.0)
            )
        elif cycle == 2:
            runtime.orientation = (
                profile.target_bearing_radians + math.radians(30.0)
            )

    session = WirelessUwbFollowSession(
        runtime,
        service,
        profile,
        config(),
        bearing_sign=1,
        bearing_zero_offset_rad=0.0,
        cancel_event=threading.Event(),
        monotonic_clock=clock,
        sleep=clock.sleep,
        progress_callback=observe,
    )

    session.run()

    cycle_rows = [row for row in progress if "cycle" in row]
    assert cycle_rows[0]["alignment_mode"] is True
    assert cycle_rows[1]["alignment_mode"] is True
    assert cycle_rows[2]["alignment_mode"] is False
    assert cycle_rows[0]["vx"] == 0.0
    assert cycle_rows[0]["wz"] == pytest.approx(1.10)


def test_latest_command_dispatcher_replaces_pending_command_without_queue() -> None:
    class BlockingService(FakeService):
        def __init__(self) -> None:
            super().__init__()
            self.first_started = threading.Event()
            self.release_first = threading.Event()
            self.second_done = threading.Event()

        def refresh_velocity(self, vx, vy, wz, source="api") -> dict:
            self.commands.append((vx, vy, wz, source, None))
            if len(self.commands) == 1:
                self.first_started.set()
                assert self.release_first.wait(1.0)
            else:
                self.second_done.set()
            return {"code": 0}

    service = BlockingService()
    dispatcher = _LatestVelocityDispatcher(service, source="wireless_uwb_follow")
    dispatcher.start()
    dispatcher.submit(0.10, 0.0, 0.10)
    assert service.first_started.wait(1.0)
    dispatcher.submit(0.20, 0.0, 0.20)
    dispatcher.submit(0.30, 0.0, 0.30)
    service.release_first.set()
    assert service.second_done.wait(1.0)
    snapshot = dispatcher.snapshot()
    dispatcher.close()

    assert [(row[0], row[2]) for row in service.commands] == [
        (0.10, 0.10),
        (0.30, 0.30),
    ]
    assert snapshot["replaced"] == 1
    assert snapshot["dispatched"] == 2


def test_same_uwb_timestamp_is_not_republished_each_control_cycle() -> None:
    clock = FakeClock()
    runtime = StaticSampleRuntime(clock)
    service = FakeService()
    session = WirelessUwbFollowSession(
        runtime,
        service,
        FollowProfile(),
        config(uwb_stale_timeout_seconds=1.0),
        bearing_sign=1,
        bearing_zero_offset_rad=0.0,
        cancel_event=threading.Event(),
        monotonic_clock=clock,
        sleep=clock.sleep,
    )

    result = session.run()

    assert result.completed is False
    assert result.reason == "uwb_stale"
    assert result.cycles >= 4
    assert result.commands_sent == 1
    assert len(service.commands) == 1


def test_too_close_hold_stops_once_then_resumes_without_restarting_session() -> None:
    clock = FakeClock()
    runtime = SequenceRuntime(
        clock,
        # The first value is consumed by preflight; the remaining six values
        # are the exact control-cycle regression sequence.
        distances=[1.50, 1.50, 1.03, 1.10, 1.19, 1.21, 1.50],
    )
    service = FakeService(clock)
    progress = []
    owner_during_progress = []

    def observe(row: dict[str, object]) -> None:
        progress.append(row)
        owner_during_progress.append(service.owner)

    session = WirelessUwbFollowSession(
        runtime,
        service,
        v21_profile(),
        config(duration_seconds=1.5, control_rate_hz=4.0),
        bearing_sign=1,
        bearing_zero_offset_rad=0.0,
        cancel_event=threading.Event(),
        monotonic_clock=clock,
        sleep=clock.sleep,
        progress_callback=observe,
    )

    result = session.run()

    cycle_rows = [row for row in progress if "cycle" in row]
    assert [row["distance_m"] for row in cycle_rows] == pytest.approx(
        [1.50, 1.03, 1.10, 1.19, 1.21, 1.50]
    )
    assert cycle_rows[0]["vx"] > 0.0
    assert [row["vx"] for row in cycle_rows[1:4]] == [0.0, 0.0, 0.0]
    assert all(row["wz"] == 0.0 for row in cycle_rows[1:4])
    assert cycle_rows[4]["vx"] > 0.0
    assert cycle_rows[5]["vx"] > 0.0
    assert [row["motion_state"] for row in cycle_rows[1:4]] == [
        "FOLLOW_HOLD_TOO_CLOSE",
        "FOLLOW_HOLD_TOO_CLOSE",
        "FOLLOW_HOLD_TOO_CLOSE",
    ]
    assert cycle_rows[4]["motion_state"] == "FOLLOW_TRACKING"
    assert sum(row.get("event") == "COMPANION_HOLD_ENTER" for row in progress) == 1
    assert sum(row.get("event") == "COMPANION_HOLD_EXIT" for row in progress) == 1
    assert sum(
        row.get("event") == "COMPANION_MOTION_RESUMED" for row in progress
    ) == 1
    assert not any(
        row.get("event") == "COMPANION_SESSION_STOPPED" for row in progress
    )
    assert all(
        row.get("state") == "FOLLOWING" for row in cycle_rows
    )
    assert all(owner == "wireless_uwb_follow" for owner in owner_during_progress)
    assert sum("stop_barrier" in source for source in service.stops) == 1
    assert result.completed is True
    assert result.reason == "duration_complete"


def test_too_close_hold_recovers_directly_into_rear_alignment() -> None:
    clock = FakeClock()
    profile = v21_profile()
    rear_bearing = profile.target_bearing_radians + math.radians(60.0)
    runtime = SequenceRuntime(
        clock,
        distances=[1.50, 1.50, 1.03, 1.10, 1.21],
        orientations=[0.0, 0.0, 0.0, 0.0, rear_bearing],
    )
    service = FakeService(clock)
    progress = []
    session = WirelessUwbFollowSession(
        runtime,
        service,
        profile,
        config(duration_seconds=1.0, control_rate_hz=4.0),
        bearing_sign=1,
        bearing_zero_offset_rad=0.0,
        cancel_event=threading.Event(),
        monotonic_clock=clock,
        sleep=clock.sleep,
        progress_callback=progress.append,
    )

    result = session.run()

    cycle_rows = [row for row in progress if "cycle" in row]
    recovery = cycle_rows[-1]
    assert recovery["distance_m"] == pytest.approx(1.21)
    assert recovery["motion_state"] == "FOLLOW_TRACKING"
    assert recovery["alignment_mode"] is True
    assert recovery["vx"] == 0.0
    assert abs(recovery["wz"]) == pytest.approx(1.10)
    assert any(row.get("event") == "COMPANION_HOLD_EXIT" for row in progress)
    resumed = next(
        row for row in progress if row.get("event") == "COMPANION_MOTION_RESUMED"
    )
    assert resumed["vx"] == 0.0
    assert abs(float(resumed["wz"])) == pytest.approx(1.10)
    assert result.completed is True


def test_too_close_stop_barrier_precedes_latest_recovery_move() -> None:
    class BlockingService(FakeService):
        def __init__(self) -> None:
            super().__init__()
            self.operations = []
            self.first_started = threading.Event()
            self.release_first = threading.Event()
            self.recovery_done = threading.Event()

        def refresh_velocity(self, vx, vy, wz, source="api") -> dict:
            self.operations.append(("MOVE", vx, wz))
            if len(self.operations) == 1:
                self.first_started.set()
                assert self.release_first.wait(1.0)
            else:
                self.recovery_done.set()
            return {"code": 0}

        def safe_stop(self, source="api") -> int:
            self.operations.append(("STOP", 0.0, 0.0))
            return 0

    service = BlockingService()
    dispatcher = _LatestVelocityDispatcher(service, source="wireless_uwb_follow")
    dispatcher.start()
    dispatcher.submit(0.30, 0.0, 0.10)
    assert service.first_started.wait(1.0)
    assert dispatcher.submit_stop() is True
    assert dispatcher.submit_stop() is False
    assert dispatcher.submit(0.24, 0.0, 0.55) is False
    service.release_first.set()
    deadline = time.monotonic() + 1.0
    while (
        dispatcher.snapshot()["stop_state"] != "STOP_CONFIRMED"
        and time.monotonic() < deadline
    ):
        time.sleep(0.001)
    assert dispatcher.snapshot()["stop_state"] == "STOP_CONFIRMED"
    assert dispatcher.submit_stop() is False
    assert dispatcher.submit(0.24, 0.0, 0.55) is True
    assert service.recovery_done.wait(1.0)
    snapshot = dispatcher.snapshot()
    dispatcher.close()

    assert service.operations == [
        ("MOVE", 0.30, 0.10),
        ("STOP", 0.0, 0.0),
        ("MOVE", 0.24, 0.55),
    ]
    assert snapshot["stop_submitted"] == 1
    assert snapshot["stop_dispatched"] == 1
