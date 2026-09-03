from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from app.companion import (
    CompanionEvent,
    CompanionEventType,
    CompanionMotionMode,
    CompanionState,
    CompanionStateMachine,
    CompanionSupervisor,
    FollowProfile,
)
from app.config import Settings
from app.core.control_owner import ControlOwner
from app.follow import FollowController, FollowTargetPlanner, UwbObservation
from app.motion import (
    ExternalRiskEvent,
    ExternalRiskEventType,
    MotionAuthority,
    RawUwbSample,
)
from tools.go2_supervised_follow_phase7_2c import build_supervised_loop


class MutableClock:
    def __init__(self, value: float = 10.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class FakeRobotService:
    def __init__(self) -> None:
        self.refreshes: list[tuple[float, float, float]] = []
        self.stops: list[str] = []

    def refresh_velocity(
        self, vx: float, vy: float, wz: float, source: str = "api"
    ) -> dict[str, int]:
        self.refreshes.append((vx, vy, wz))
        return {"code": 0}

    def safe_stop(self, source: str = "api") -> int:
        self.stops.append(source)
        return 0


def event(event_type: CompanionEventType, at: float) -> CompanionEvent:
    return CompanionEvent(
        event_type=event_type,
        monotonic_time=at,
        reason=f"test_{event_type.value.lower()}",
    )


def test_state_machine_covers_follow_stop_loss_obstacle_and_fall_paths() -> None:
    machine = CompanionStateMachine(monotonic_clock=lambda: 0.0)

    assert machine.dispatch(event(CompanionEventType.START, 0.0))
    assert machine.state is CompanionState.FOLLOWING
    assert machine.dispatch(event(CompanionEventType.PERSON_STATIONARY, 1.0))
    assert machine.dispatch(event(CompanionEventType.VIEW_REQUIRED, 1.1))
    assert machine.dispatch(event(CompanionEventType.VIEW_ALIGNED, 1.2))
    assert machine.state is CompanionState.HOLD
    assert machine.dispatch(event(CompanionEventType.PERSON_MOVING, 2.0))

    assert machine.dispatch(event(CompanionEventType.OBSTACLE_DETECTED, 2.1))
    assert machine.state is CompanionState.OBSTACLE_STOP
    assert machine.dispatch(event(CompanionEventType.OBSTACLE_CLEARED, 2.2))
    assert machine.dispatch(event(CompanionEventType.TARGET_LOST, 3.0))
    assert machine.dispatch(event(CompanionEventType.FAILSAFE_COMMITTED, 3.1))
    assert machine.state is CompanionState.SAFE_STOP
    assert machine.dispatch(event(CompanionEventType.TARGET_REACQUIRED, 3.2))

    assert machine.dispatch(event(CompanionEventType.FALL_SUSPECTED, 4.0))
    assert machine.state is CompanionState.FALL_SUSPECTED
    assert machine.dispatch(event(CompanionEventType.FALL_DISMISSED, 4.1))
    assert machine.dispatch(event(CompanionEventType.FALL_CONFIRMED, 5.0))
    assert machine.state is CompanionState.EMERGENCY_STOP
    assert not machine.dispatch(event(CompanionEventType.RESUME, 5.1))
    assert machine.dispatch(event(CompanionEventType.EMERGENCY_ACKNOWLEDGED, 5.2))
    assert machine.dispatch(event(CompanionEventType.RECOVERY_DETECTED, 5.3))
    assert machine.dispatch(event(CompanionEventType.RECOVERY_STABLE, 6.0))
    assert machine.state is CompanionState.WAIT_RESUME
    assert machine.dispatch(event(CompanionEventType.RESUME, 6.1))
    assert machine.state is CompanionState.FOLLOWING


def test_follow_profile_applies_distance_hysteresis_gait_floor_and_bearing_deadband() -> None:
    profile = FollowProfile()
    planner = FollowTargetPlanner(profile.follow_offset())
    controller = FollowController(profile.controller_config(simulation_mode=False))
    desired = profile.desired_bearing_radians

    def command(distance: float, bearing: float = desired):
        plan = planner.process_measurement(distance, bearing)
        return controller.calculate_velocity(
            plan,
            control_owner=ControlOwner.FOLLOW,
            measurement_age_seconds=0.0,
        )

    assert command(1.75).vx == 0.0
    assert command(1.90).vx == pytest.approx(0.20)
    assert command(1.72).vx == pytest.approx(0.20)
    assert command(1.69).vx == 0.0
    assert command(1.90, desired + math.radians(10.0)).wz == 0.0
    assert command(1.90, desired + math.radians(15.0)).wz > 0.0


def test_stationary_target_enters_hold_and_motion_returns_to_following() -> None:
    clock = MutableClock(0.0)
    profile = FollowProfile()
    planner = FollowTargetPlanner(profile.follow_offset(), monotonic_clock=clock)
    supervisor = CompanionSupervisor(profile=profile, monotonic_clock=clock)
    supervisor.start(now_monotonic=0.0)

    def observe(at: float, distance: float, bearing: float) -> None:
        clock.value = at
        plan = planner.process_measurement(
            distance,
            bearing,
            sample_monotonic=at,
        )
        supervisor.ingest_uwb(
            UwbObservation(
                distance_metres=distance,
                bearing_radians=bearing,
                sample_monotonic=at,
                enabled_from_app=1,
                error_state=0,
            ),
            plan,
        )

    desired = profile.desired_bearing_radians
    observe(0.0, 1.60, desired)
    observe(1.50, 1.61, desired + math.radians(1.0))
    assert supervisor.state is CompanionState.PERSON_STOPPED
    observe(1.60, 1.60, desired)
    assert supervisor.state is CompanionState.VIEW_ADJUST
    observe(1.70, 1.60, 0.0)
    assert supervisor.state is CompanionState.HOLD
    assert supervisor.snapshot().motion_mode is CompanionMotionMode.HOLD

    observe(1.80, 1.80, 0.0)
    assert supervisor.state is CompanionState.FOLLOWING


def test_fall_path_is_latched_until_recovery_and_explicit_resume() -> None:
    clock = MutableClock(10.0)
    supervisor = CompanionSupervisor(monotonic_clock=clock)
    supervisor.start()
    fall = ExternalRiskEvent(
        event_type=ExternalRiskEventType.FALL_CONFIRMED,
        confidence=0.95,
        timestamp=datetime(2026, 8, 24, tzinfo=timezone.utc),
        incident_id="fall-001",
    )

    supervisor.ingest_risk_event(fall, received_monotonic=10.1)
    assert supervisor.state is CompanionState.EMERGENCY_STOP
    assert supervisor.snapshot().active_incident_id == "fall-001"
    assert not supervisor.resume(now_monotonic=10.2)

    assert supervisor.acknowledge_emergency(now_monotonic=10.3)
    supervisor.ingest_risk_event(
        ExternalRiskEvent(
            event_type=ExternalRiskEventType.RECOVERY_CONFIRMED,
            timestamp=datetime(2026, 8, 24, 0, 1, tzinfo=timezone.utc),
            incident_id="fall-001",
        ),
        received_monotonic=10.4,
    )
    assert supervisor.state is CompanionState.RECOVERING
    assert supervisor.mark_recovery_stable(now_monotonic=11.0)
    assert supervisor.state is CompanionState.WAIT_RESUME
    assert supervisor.snapshot().resume_required is True
    assert supervisor.resume(now_monotonic=11.1)
    assert supervisor.state is CompanionState.FOLLOWING
    assert supervisor.snapshot().active_incident_id is None


def test_supervised_loop_keeps_fall_latched_until_wait_resume() -> None:
    clock = MutableClock(10.0)
    service = FakeRobotService()
    settings = Settings(
        mode="real",
        control_enabled=True,
        read_only_mode=False,
        follow_simulation=False,
        follow_execution_enabled=True,
        phase7_motion_execution_enabled=True,
        phase7_require_external_risk_feed=True,
    )
    loop = build_supervised_loop(
        service,  # type: ignore[arg-type]
        settings,
        max_execute_vx=0.20,
        max_execute_wz=0.30,
        walking_speed_floor_enabled=True,
        monotonic_clock=clock,
    )
    loop.start_companion()
    loop.ingest_risk_event(
        {"event_type": "NON_FALL", "timestamp": "2026-08-24T12:00:00+08:00"},
        received_monotonic=10.0,
    )
    desired = FollowProfile().desired_bearing_radians
    loop.ingest_uwb(
        RawUwbSample(
            distance_est=1.90,
            orientation_est=desired - settings.uwb_bearing_zero_offset_rad,
            enabled_from_app=1,
            error_state=0,
            sample_monotonic=10.0,
        )
    )
    clear_cloud = [(2.5, -1.0 + index * 0.1, 0.0) for index in range(21)]
    for index in range(3):
        loop.ingest_lidar(
            clear_cloud,
            frame_id="cloud_base",
            sample_monotonic=10.0 + index * 0.01,
        )
    clock.value = 10.02
    assert loop.step().decision.authority is MotionAuthority.FOLLOW

    loop.ingest_risk_event(
        {
            "event_type": "FALL_CONFIRMED",
            "confidence": 0.95,
            "timestamp": "2026-08-24T12:00:01+08:00",
            "incident_id": "fall-loop-001",
        },
        received_monotonic=10.03,
    )
    clock.value = 10.03
    emergency = loop.step()
    assert emergency.companion is not None
    assert emergency.companion.state is CompanionState.EMERGENCY_STOP
    assert emergency.decision.authority is MotionAuthority.EMERGENCY

    loop.acknowledge_fall("fall-loop-001")
    loop.ingest_risk_event(
        {
            "event_type": "RECOVERY_CONFIRMED",
            "timestamp": "2026-08-24T12:00:02+08:00",
            "incident_id": "fall-loop-001",
        },
        received_monotonic=10.04,
    )
    waiting = loop.step(now_monotonic=10.04)
    assert waiting.companion is not None
    assert waiting.companion.state is CompanionState.WAIT_RESUME
    assert waiting.decision.stop_required is True

    loop.arm_for_supervised_test()
    loop.authorize_resume()
    resumed = loop.step(now_monotonic=10.05)
    assert resumed.companion is not None
    assert resumed.companion.state is CompanionState.FOLLOWING
    assert resumed.decision.authority is MotionAuthority.FOLLOW
