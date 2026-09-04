from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.config import Settings
from app.follow import SafetyState, VelocityCommand
from app.motion import (
    ExternalRiskEvent,
    ExternalRiskEventType,
    LidarSafetyConfig,
    LidarSafetyDecision,
    LidarSafetyGuard,
    LidarSafetyLevel,
    MotionArbiter,
    MotionArbiterConfig,
    MotionAuthority,
    RealFollowExecutionStatus,
    RealFollowExecutor,
    RealFollowExecutorConfig,
    RiskState,
)


class MutableClock:
    def __init__(self, value: float = 10.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class FakeRobotService:
    def __init__(self) -> None:
        self.moves: list[dict[str, object]] = []
        self.refreshes: list[dict[str, object]] = []
        self.stops: list[str] = []

    def move(
        self,
        vx: float,
        vy: float,
        wz: float,
        duration: float,
        source: str = "api",
    ) -> dict:
        self.moves.append(
            {
                "vx": vx,
                "vy": vy,
                "wz": wz,
                "duration": duration,
                "source": source,
            }
        )
        return {"code": 0}

    def refresh_velocity(
        self,
        vx: float,
        vy: float,
        wz: float,
        source: str = "api",
    ) -> dict:
        self.refreshes.append(
            {"vx": vx, "vy": vy, "wz": wz, "source": source}
        )
        return {"code": 0}

    def safe_stop(self, source: str = "api") -> int:
        self.stops.append(source)
        return 0


def command(
    vx: float = 0.3,
    wz: float = 0.5,
    *,
    simulation: bool = False,
    safety_state: SafetyState = SafetyState.SAFE,
) -> VelocityCommand:
    return VelocityCommand(
        vx=vx,
        vy=0.0,
        wz=wz,
        safety_state=safety_state,
        simulation_mode=simulation,
    )


def lidar_decision(
    level: LidarSafetyLevel = LidarSafetyLevel.CLEAR,
    *,
    speed_scale: float = 1.0,
    reason: str = "test",
) -> LidarSafetyDecision:
    return LidarSafetyDecision(
        level=level,
        stop_required=level is LidarSafetyLevel.STOP,
        speed_scale=speed_scale,
        reason=reason,
        nearest_distance=None,
        roi_point_count=0,
        sample_age_seconds=0.0,
        frame_id="base_link",
    )


def arbiter_ready(clock: MutableClock | None = None) -> MotionArbiter:
    source_clock = clock or MutableClock()
    arbiter = MotionArbiter(monotonic_clock=source_clock)
    arbiter.ingest_risk_event(
        {
            "event_type": "NON_FALL",
            "timestamp": "2026-08-21T16:00:00+08:00",
        },
        received_monotonic=source_clock.value,
    )
    return arbiter


def clear_cloud() -> list[tuple[float, float, float]]:
    return [(2.5, -1.0 + index * 0.1, 0.0) for index in range(21)]


def cloud_with_obstacle(distance: float) -> list[tuple[float, float, float]]:
    return clear_cloud() + [
        (distance, -0.05, 0.0),
        (distance, 0.0, 0.0),
        (distance, 0.05, 0.0),
    ]


def test_external_fall_contract_accepts_stable_payload() -> None:
    event = ExternalRiskEvent.from_payload(
        {
            "event_type": "FALL_CONFIRMED",
            "confidence": 0.93,
            "timestamp": "2026-08-21T16:00:00+08:00",
            "incident_id": "fall-001",
        }
    )

    assert event.event_type is ExternalRiskEventType.FALL_CONFIRMED
    assert event.incident_id == "fall-001"
    assert event.timestamp.utcoffset() is not None


def test_fall_suspected_contract_stops_before_confirmation() -> None:
    clock = MutableClock()
    arbiter = arbiter_ready(clock)
    suspected = ExternalRiskEvent.from_payload(
        {
            "event_type": "FALL_SUSPECTED",
            "confidence": 0.72,
            "timestamp": "2026-08-21T16:00:00+08:00",
            "incident_id": "fall-suspected-001",
        }
    )
    assert arbiter.ingest_risk_event(suspected, received_monotonic=10.1)
    decision = arbiter.decide(
        follow_command=command(),
        uwb_age_seconds=0.1,
        lidar=lidar_decision(),
        now_monotonic=10.2,
    )
    assert decision.authority is MotionAuthority.EMERGENCY
    assert decision.stop_required


@pytest.mark.parametrize(
    "payload",
    [
        {"event_type": "FALL_CONFIRMED", "confidence": 0.9, "timestamp": "2026-08-21T16:00:00"},
        {"event_type": "FALL_CONFIRMED", "confidence": 1.1, "timestamp": "2026-08-21T16:00:00+08:00", "incident_id": "fall-1"},
        {"event_type": "UNKNOWN", "timestamp": "2026-08-21T16:00:00+08:00"},
    ],
)
def test_external_fall_contract_rejects_unsafe_payload(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        ExternalRiskEvent.from_payload(payload)


def test_fall_is_idempotent_latched_and_requires_matching_recovery() -> None:
    arbiter = arbiter_ready()
    fall = ExternalRiskEvent(
        event_type=ExternalRiskEventType.FALL_CONFIRMED,
        confidence=0.93,
        timestamp=datetime(2026, 8, 21, 16, 0, tzinfo=timezone.utc),
        incident_id="fall-001",
    )

    assert arbiter.ingest_risk_event(fall, received_monotonic=10.1) is True
    assert arbiter.ingest_risk_event(fall, received_monotonic=10.2) is False
    stopped = arbiter.decide(
        follow_command=command(),
        uwb_age_seconds=0.1,
        lidar=lidar_decision(),
        now_monotonic=10.2,
    )

    assert stopped.authority is MotionAuthority.EMERGENCY
    assert stopped.risk_state is RiskState.PAUSED_BY_FALL
    assert stopped.active_incident_id == "fall-001"
    arbiter.acknowledge_fall("fall-001")
    with pytest.raises(ValueError, match="RECOVERY_CONFIRMED"):
        arbiter.clear_fall("fall-001")
    arbiter.ingest_risk_event(
        ExternalRiskEvent(
            event_type=ExternalRiskEventType.RECOVERY_CONFIRMED,
            timestamp=datetime(2026, 8, 21, 16, 1, tzinfo=timezone.utc),
            incident_id="fall-001",
        ),
        received_monotonic=10.3,
    )
    arbiter.clear_fall("fall-001")
    assert arbiter.risk_state is RiskState.NORMAL


def test_non_fall_heartbeat_cannot_unlock_fall_and_recovery_must_match() -> None:
    arbiter = arbiter_ready()
    arbiter.ingest_risk_event(
        {
            "event_type": "FALL_CONFIRMED",
            "confidence": 0.95,
            "timestamp": "2026-08-21T16:00:01+00:00",
            "incident_id": "fall-locked",
        },
        received_monotonic=10.1,
    )
    arbiter.acknowledge_fall("fall-locked")
    arbiter.ingest_risk_event(
        {"event_type": "NON_FALL", "timestamp": "2026-08-21T16:00:02+00:00"},
        received_monotonic=10.2,
    )
    with pytest.raises(ValueError, match="RECOVERY_CONFIRMED"):
        arbiter.clear_fall("fall-locked")
    with pytest.raises(ValueError, match="does not match"):
        arbiter.ingest_risk_event(
            {
                "event_type": "RECOVERY_CONFIRMED",
                "timestamp": "2026-08-21T16:00:03+00:00",
                "incident_id": "different-fall",
            },
            received_monotonic=10.3,
        )


def test_replayed_non_fall_does_not_refresh_the_risk_feed() -> None:
    clock = MutableClock()
    arbiter = arbiter_ready(clock)

    accepted = arbiter.ingest_risk_event(
        {"event_type": "NON_FALL", "timestamp": "2026-08-21T16:00:00+08:00"},
        received_monotonic=11.0,
    )
    decision = arbiter.decide(
        follow_command=command(),
        uwb_age_seconds=0.1,
        lidar=lidar_decision(),
        now_monotonic=12.0,
    )

    assert accepted is False
    assert (decision.authority, decision.reason) == (
        MotionAuthority.EMERGENCY,
        "risk_feed_stale",
    )


def test_motion_priority_is_emergency_manual_lidar_then_follow() -> None:
    arbiter = arbiter_ready()
    stop_lidar = lidar_decision(LidarSafetyLevel.STOP, reason="chair")

    arbiter.set_manual_takeover(True)
    manual = arbiter.decide(
        follow_command=command(), uwb_age_seconds=0.1, lidar=stop_lidar, now_monotonic=10.1
    )
    arbiter.set_emergency(True, reason="estop")
    emergency = arbiter.decide(
        follow_command=command(), uwb_age_seconds=0.1, lidar=stop_lidar, now_monotonic=10.1
    )
    arbiter.set_emergency(False)
    arbiter.set_manual_takeover(False)
    lidar_stop = arbiter.decide(
        follow_command=command(), uwb_age_seconds=0.1, lidar=stop_lidar, now_monotonic=10.1
    )
    follow = arbiter.decide(
        follow_command=command(),
        uwb_age_seconds=0.1,
        lidar=lidar_decision(),
        now_monotonic=10.1,
    )

    assert manual.authority is MotionAuthority.MANUAL
    assert emergency.authority is MotionAuthority.EMERGENCY
    assert lidar_stop.authority is MotionAuthority.LIDAR_STOP
    assert follow.authority is MotionAuthority.FOLLOW


def test_risk_feed_and_uwb_timeout_fail_closed() -> None:
    clock = MutableClock()
    no_feed = MotionArbiter(monotonic_clock=clock)
    not_ready = no_feed.decide(
        follow_command=command(), uwb_age_seconds=0.1, lidar=lidar_decision(), now_monotonic=10.0
    )
    arbiter = arbiter_ready(clock)
    stale_feed = arbiter.decide(
        follow_command=command(), uwb_age_seconds=0.1, lidar=lidar_decision(), now_monotonic=12.0
    )
    arbiter.ingest_risk_event(
        {"event_type": "NON_FALL", "timestamp": "2026-08-21T16:00:02+08:00"},
        received_monotonic=12.0,
    )
    stale_uwb = arbiter.decide(
        follow_command=command(), uwb_age_seconds=1.0, lidar=lidar_decision(), now_monotonic=12.1
    )

    assert (not_ready.authority, not_ready.reason) == (MotionAuthority.EMERGENCY, "risk_feed_not_ready")
    assert (stale_feed.authority, stale_feed.reason) == (MotionAuthority.EMERGENCY, "risk_feed_stale")
    assert (stale_uwb.authority, stale_uwb.reason) == (MotionAuthority.IDLE, "uwb_stale")


def test_supervised_preintegration_can_disable_only_the_external_feed_gate() -> None:
    arbiter = MotionArbiter(
        MotionArbiterConfig(require_external_risk_feed=False)
    )

    decision = arbiter.decide(
        follow_command=command(),
        uwb_age_seconds=0.1,
        lidar=lidar_decision(),
        now_monotonic=10.0,
    )

    assert decision.authority is MotionAuthority.FOLLOW


def test_phase7_environment_gates_are_safe_by_default() -> None:
    settings = Settings()

    assert settings.phase7_motion_execution_enabled is False
    assert settings.phase7_require_external_risk_feed is True


def test_lidar_requires_clearance_confirmation_and_latches_after_stop() -> None:
    clock = MutableClock()
    guard = LidarSafetyGuard(monotonic_clock=clock)

    first = guard.update(clear_cloud(), frame_id="base_link", sample_monotonic=10.0)
    second = guard.update(clear_cloud(), frame_id="base_link", sample_monotonic=10.1)
    third = guard.update(clear_cloud(), frame_id="base_link", sample_monotonic=10.2)
    stopped = guard.update(cloud_with_obstacle(0.5), frame_id="base_link", sample_monotonic=10.3)
    recovering = guard.update(clear_cloud(), frame_id="base_link", sample_monotonic=10.4)
    guard.update(clear_cloud(), frame_id="base_link", sample_monotonic=10.5)
    recovered = guard.update(clear_cloud(), frame_id="base_link", sample_monotonic=10.6)

    assert first.level is LidarSafetyLevel.STOP
    assert second.level is LidarSafetyLevel.STOP
    assert third.level is LidarSafetyLevel.CLEAR
    assert (stopped.level, stopped.reason) == (LidarSafetyLevel.STOP, "obstacle_in_stop_zone")
    assert recovering.reason == "clearance_confirmation_pending"
    assert recovered.level is LidarSafetyLevel.CLEAR


def test_lidar_slow_scales_follow_and_stale_or_unknown_frame_stops() -> None:
    guard = LidarSafetyGuard(
        LidarSafetyConfig(clear_samples_required=1),
        monotonic_clock=MutableClock(),
    )
    guard.update(clear_cloud(), frame_id="base_link", sample_monotonic=10.0)
    slow = guard.update(cloud_with_obstacle(0.9), frame_id="base_link", sample_monotonic=10.1)
    arbiter = arbiter_ready()
    slowed = arbiter.decide(
        follow_command=command(vx=0.2, wz=0.4),
        uwb_age_seconds=0.1,
        lidar=slow,
        now_monotonic=10.1,
    )
    stale = guard.evaluate(now_monotonic=10.5)
    unknown = guard.update(clear_cloud(), frame_id="utlidar_lidar", sample_monotonic=10.6)

    assert slow.level is LidarSafetyLevel.SLOW
    assert slowed.authority is MotionAuthority.FOLLOW
    assert slowed.vx == pytest.approx(0.07)
    assert slowed.wz == pytest.approx(0.14)
    assert (stale.level, stale.reason) == (LidarSafetyLevel.STOP, "lidar_stale")
    assert (unknown.level, unknown.reason) == (LidarSafetyLevel.STOP, "untrusted_lidar_frame")


def test_lidar_slow_requires_consecutive_clear_samples_before_full_speed() -> None:
    guard = LidarSafetyGuard(monotonic_clock=MutableClock())
    guard.update(clear_cloud(), frame_id="base_link", sample_monotonic=10.0)
    guard.update(clear_cloud(), frame_id="base_link", sample_monotonic=10.1)
    guard.update(clear_cloud(), frame_id="base_link", sample_monotonic=10.2)

    slow = guard.update(
        cloud_with_obstacle(0.9), frame_id="base_link", sample_monotonic=10.3
    )
    first_clear = guard.update(
        clear_cloud(), frame_id="base_link", sample_monotonic=10.4
    )
    interrupted = guard.update(
        cloud_with_obstacle(0.9), frame_id="base_link", sample_monotonic=10.5
    )
    second_first_clear = guard.update(
        clear_cloud(), frame_id="base_link", sample_monotonic=10.6
    )
    second_clear = guard.update(
        clear_cloud(), frame_id="base_link", sample_monotonic=10.7
    )
    third_clear = guard.update(
        clear_cloud(), frame_id="base_link", sample_monotonic=10.8
    )

    assert slow.level is LidarSafetyLevel.SLOW
    assert (first_clear.level, first_clear.reason) == (
        LidarSafetyLevel.SLOW,
        "slow_clearance_confirmation_pending",
    )
    assert interrupted.reason == "obstacle_in_slow_zone"
    assert second_first_clear.level is LidarSafetyLevel.SLOW
    assert second_clear.level is LidarSafetyLevel.SLOW
    assert third_clear.level is LidarSafetyLevel.CLEAR


def test_real_executor_is_disabled_and_unarmed_by_default() -> None:
    service = FakeRobotService()
    executor = RealFollowExecutor(service)  # type: ignore[arg-type]
    decision = arbiter_ready().decide(
        follow_command=command(), uwb_age_seconds=0.1, lidar=lidar_decision(), now_monotonic=10.1
    )

    result = executor.execute(decision)

    assert result.status is RealFollowExecutionStatus.DISABLED
    assert service.moves == []
    assert service.stops == []


def test_real_executor_requires_explicit_arm_and_resume_then_clamps() -> None:
    service = FakeRobotService()
    executor = RealFollowExecutor(
        service,  # type: ignore[arg-type]
        config=RealFollowExecutorConfig(execution_enabled=True),
        monotonic_clock=MutableClock(),
    )
    arbiter = arbiter_ready()
    decision = arbiter.decide(
        follow_command=command(), uwb_age_seconds=0.1, lidar=lidar_decision(), now_monotonic=10.1
    )

    unarmed = executor.execute(decision)
    executor.arm_for_supervised_test()
    next_decision = arbiter.decide(
        follow_command=command(), uwb_age_seconds=0.1, lidar=lidar_decision(), now_monotonic=10.2
    )
    held = executor.execute(next_decision)
    executor.authorize_resume()
    sent = executor.execute(
        arbiter.decide(
            follow_command=command(), uwb_age_seconds=0.1, lidar=lidar_decision(), now_monotonic=10.3
        )
    )

    assert unarmed.status is RealFollowExecutionStatus.NOT_ARMED
    assert held.status is RealFollowExecutionStatus.RESUME_REQUIRED
    assert sent.status is RealFollowExecutionStatus.SENT
    assert service.moves == [
        {
            "vx": 0.10,
            "vy": 0.0,
            "wz": 0.15,
            "duration": 0.10,
            "source": "phase7_motion_arbiter",
        }
    ]


def test_preemption_stops_and_requires_human_resume_before_follow_restarts() -> None:
    service = FakeRobotService()
    executor = RealFollowExecutor(
        service,  # type: ignore[arg-type]
        config=RealFollowExecutorConfig(execution_enabled=True),
        monotonic_clock=MutableClock(),
    )
    executor.arm_for_supervised_test()
    executor.authorize_resume()
    arbiter = arbiter_ready()
    first = executor.execute(
        arbiter.decide(
            follow_command=command(0.05, 0.1),
            uwb_age_seconds=0.1,
            lidar=lidar_decision(),
            now_monotonic=10.1,
        )
    )
    arbiter.set_manual_takeover(True)
    preempted = executor.execute(
        arbiter.decide(
            follow_command=command(),
            uwb_age_seconds=0.1,
            lidar=lidar_decision(),
            now_monotonic=10.2,
        )
    )
    arbiter.set_manual_takeover(False)
    held = executor.execute(
        arbiter.decide(
            follow_command=command(),
            uwb_age_seconds=0.1,
            lidar=lidar_decision(),
            now_monotonic=10.3,
        )
    )

    assert first.status is RealFollowExecutionStatus.SENT
    assert preempted.status is RealFollowExecutionStatus.STOPPED
    assert held.status is RealFollowExecutionStatus.RESUME_REQUIRED
    assert service.stops == ["phase7:manual:manual_takeover"]
    assert len(service.moves) == 1


def test_continuous_refresh_does_not_stop_between_safe_cycles() -> None:
    service = FakeRobotService()
    clock = MutableClock(10.0)
    executor = RealFollowExecutor(
        service,  # type: ignore[arg-type]
        config=RealFollowExecutorConfig(
            execution_enabled=True,
            continuous_velocity_refresh=True,
        ),
        monotonic_clock=clock,
    )
    executor.arm_for_supervised_test()
    executor.authorize_resume()
    arbiter = arbiter_ready()

    first = executor.execute(
        arbiter.decide(
            follow_command=command(0.05, 0.1),
            uwb_age_seconds=0.1,
            lidar=lidar_decision(),
            now_monotonic=10.0,
        )
    )
    clock.value = 10.21
    second = executor.execute(
        arbiter.decide(
            follow_command=command(0.05, 0.1),
            uwb_age_seconds=0.1,
            lidar=lidar_decision(),
            now_monotonic=10.21,
        )
    )

    assert first.status is RealFollowExecutionStatus.SENT
    assert second.status is RealFollowExecutionStatus.SENT
    assert len(service.refreshes) == 2
    assert service.moves == []
    assert service.stops == []

    arbiter.set_manual_takeover(True)
    stopped = executor.execute(
        arbiter.decide(
            follow_command=command(),
            uwb_age_seconds=0.1,
            lidar=lidar_decision(),
            now_monotonic=10.3,
        )
    )
    assert stopped.status is RealFollowExecutionStatus.STOPPED
    assert service.stops == ["phase7:manual:manual_takeover"]


def test_real_executor_never_dispatches_above_five_hz() -> None:
    service = FakeRobotService()
    clock = MutableClock(10.0)
    executor = RealFollowExecutor(
        service,  # type: ignore[arg-type]
        config=RealFollowExecutorConfig(execution_enabled=True),
        monotonic_clock=clock,
    )
    executor.arm_for_supervised_test()
    executor.authorize_resume()
    arbiter = arbiter_ready()

    first = executor.execute(
        arbiter.decide(
            follow_command=command(),
            uwb_age_seconds=0.1,
            lidar=lidar_decision(),
            now_monotonic=10.0,
        )
    )
    clock.value = 10.1
    limited = executor.execute(
        arbiter.decide(
            follow_command=command(),
            uwb_age_seconds=0.1,
            lidar=lidar_decision(),
            now_monotonic=10.1,
        )
    )

    assert first.status is RealFollowExecutionStatus.SENT
    assert limited.status is RealFollowExecutionStatus.RATE_LIMITED
    assert len(service.moves) == 1
