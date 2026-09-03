from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.follow import (
    FollowController,
    FollowControllerConfig,
    FollowOffset,
    FollowTargetPlanner,
    RealMotionSafetyLimit,
    UwbBearingSource,
    UwbBearingUnit,
    UwbInputConfig,
    UwbInputValidator,
)
from app.motion import (
    LidarSafetyConfig,
    LidarSafetyGuard,
    MotionArbiter,
    MotionArbiterConfig,
    MotionAuthority,
    RawUwbSample,
    RealFollowExecutionStatus,
    RealFollowExecutor,
    RealFollowExecutorConfig,
    SupervisedMotionLoop,
)


class MutableClock:
    def __init__(self, value: float = 10.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class FakeRobotService:
    def __init__(self) -> None:
        self.moves: list[dict[str, object]] = []
        self.stops: list[str] = []

    def move(
        self,
        vx: float,
        vy: float,
        wz: float,
        duration: float,
        source: str = "api",
    ) -> dict[str, object]:
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

    def safe_stop(self, source: str = "api") -> int:
        self.stops.append(source)
        return 0


def clear_cloud() -> list[tuple[float, float, float]]:
    return [(2.5, -1.0 + index * 0.1, 0.0) for index in range(21)]


def obstacle_cloud(distance: float) -> list[tuple[float, float, float]]:
    return clear_cloud() + [
        (distance, -0.05, 0.0),
        (distance, 0.0, 0.0),
        (distance, 0.05, 0.0),
    ]


def build_loop(clock: MutableClock) -> tuple[SupervisedMotionLoop, FakeRobotService]:
    service = FakeRobotService()
    loop = SupervisedMotionLoop(
        uwb_validator=UwbInputValidator(
            UwbInputConfig(
                bearing_source=UwbBearingSource.ORIENTATION_EST,
                bearing_unit=UwbBearingUnit.RADIANS,
                bearing_sign=1,
                bearing_zero_offset_rad=0.55,
                calibration_confirmed=True,
            )
        ),
        planner=FollowTargetPlanner(
            FollowOffset(back_distance=1.5, right_offset=0.5),
            lost_timeout_seconds=1.0,
            monotonic_clock=clock,
        ),
        controller=FollowController(
            FollowControllerConfig(
                max_vx=0.10,
                max_wz=0.30,
                simulation_mode=False,
                velocity_feedforward_enabled=False,
            )
        ),
        lidar_guard=LidarSafetyGuard(
            LidarSafetyConfig(
                stop_distance=0.80,
                slow_distance=1.40,
                roi_min_z=-0.25,
                clear_samples_required=3,
            ),
            monotonic_clock=clock,
        ),
        arbiter=MotionArbiter(
            MotionArbiterConfig(
                uwb_timeout_seconds=1.0,
                external_risk_timeout_seconds=2.0,
                require_external_risk_feed=True,
            ),
            monotonic_clock=clock,
        ),
        executor=RealFollowExecutor(
            service,  # type: ignore[arg-type]
            config=RealFollowExecutorConfig(
                execution_enabled=True,
                command_duration_seconds=0.10,
                max_frequency_hz=5.0,
            ),
            limits=RealMotionSafetyLimit(max_vx=0.10, max_vy=0.0, max_wz=0.30),
            monotonic_clock=clock,
        ),
        monotonic_clock=clock,
    )
    return loop, service


def fresh_non_fall(loop: SupervisedMotionLoop, now: float, second: int = 0) -> None:
    loop.ingest_risk_event(
        {
            "event_type": "NON_FALL",
            "timestamp": datetime(
                2026, 8, 23, 12, 0, second, tzinfo=timezone.utc
            ).isoformat(),
        },
        received_monotonic=now,
    )


def fresh_uwb(loop: SupervisedMotionLoop, now: float, *, distance: float = 2.0) -> None:
    loop.ingest_uwb(
        RawUwbSample(
            distance_est=distance,
            orientation_est=-0.55,
            enabled_from_app=1,
            error_state=0,
            sample_monotonic=now,
        )
    )


def prime_clear_lidar(loop: SupervisedMotionLoop, start: float) -> None:
    for index in range(3):
        loop.ingest_lidar(
            clear_cloud(),
            frame_id="cloud_base",
            sample_monotonic=start + index * 0.01,
        )


def authorize_ready_loop(
    loop: SupervisedMotionLoop, clock: MutableClock, *, distance: float = 2.0
) -> None:
    fresh_non_fall(loop, clock.value)
    fresh_uwb(loop, clock.value, distance=distance)
    prime_clear_lidar(loop, clock.value)
    clock.value += 0.03
    loop.arm_for_supervised_test()
    loop.authorize_resume()


def test_clear_cycle_dispatches_one_bounded_tenth_second_slice() -> None:
    clock = MutableClock()
    loop, service = build_loop(clock)
    authorize_ready_loop(loop, clock)

    result = loop.step()

    assert result.execution.status is RealFollowExecutionStatus.SENT
    assert result.decision.authority is MotionAuthority.FOLLOW
    assert service.moves == [
        {
            "vx": 0.10,
            "vy": 0.0,
            "wz": pytest.approx(-0.30),
            "duration": 0.10,
            "source": "phase7_motion_arbiter",
        }
    ]


def test_uwb_stale_stops_and_requires_explicit_new_resume() -> None:
    clock = MutableClock()
    loop, service = build_loop(clock)
    authorize_ready_loop(loop, clock)
    loop.step()

    clock.value += 1.05
    prime_clear_lidar(loop, clock.value)
    clock.value += 0.03
    stale = loop.step()
    assert (stale.decision.authority, stale.decision.reason) == (
        MotionAuthority.IDLE,
        "uwb_stale",
    )
    assert stale.execution.status is RealFollowExecutionStatus.STOPPED
    assert loop.executor.resume_authorized is False

    fresh_non_fall(loop, clock.value, second=2)
    fresh_uwb(loop, clock.value)
    prime_clear_lidar(loop, clock.value)
    clock.value += 0.03
    held = loop.step()
    assert held.execution.status is RealFollowExecutionStatus.RESUME_REQUIRED
    assert len(service.moves) == 1


def test_lidar_slow_scales_and_stop_preempts() -> None:
    clock = MutableClock()
    loop, service = build_loop(clock)
    authorize_ready_loop(loop, clock)

    clock.value += 0.01
    loop.ingest_lidar(
        obstacle_cloud(1.0), frame_id="cloud_base", sample_monotonic=clock.value
    )
    slowed = loop.step()
    assert slowed.execution.status is RealFollowExecutionStatus.SENT
    assert slowed.execution.vx == pytest.approx(0.035)

    clock.value += 0.21
    loop.ingest_lidar(
        obstacle_cloud(0.6), frame_id="cloud_base", sample_monotonic=clock.value
    )
    stopped = loop.step()
    assert stopped.decision.authority is MotionAuthority.LIDAR_STOP
    assert stopped.execution.status is RealFollowExecutionStatus.STOPPED
    assert loop.executor.resume_authorized is False
    assert service.stops[-1] == "phase7:lidar_stop:obstacle_in_stop_zone"


def test_fall_and_manual_takeover_preempt_and_latch_resume() -> None:
    clock = MutableClock()
    loop, service = build_loop(clock)
    authorize_ready_loop(loop, clock)
    loop.step()

    clock.value += 0.21
    loop.ingest_risk_event(
        {
            "event_type": "FALL_CONFIRMED",
            "confidence": 0.93,
            "timestamp": "2026-08-23T12:00:01+00:00",
            "incident_id": "fall-test-1",
        },
        received_monotonic=clock.value,
    )
    fall = loop.step()
    assert (fall.decision.authority, fall.decision.reason) == (
        MotionAuthority.EMERGENCY,
        "fall_incident_active",
    )
    assert fall.execution.status is RealFollowExecutionStatus.STOPPED
    assert loop.executor.resume_authorized is False

    second_clock = MutableClock()
    second, second_service = build_loop(second_clock)
    authorize_ready_loop(second, second_clock)
    second.step()
    second_clock.value += 0.21
    second.set_manual_takeover(True)
    manual = second.step()
    assert manual.decision.authority is MotionAuthority.MANUAL
    assert manual.execution.status is RealFollowExecutionStatus.STOPPED
    assert second.executor.resume_authorized is False
    assert len(service.moves) == 1
    assert len(second_service.moves) == 1


def test_invalid_uwb_and_missing_risk_feed_fail_closed() -> None:
    clock = MutableClock()
    loop, service = build_loop(clock)
    prime_clear_lidar(loop, clock.value)
    clock.value += 0.03
    loop.arm_for_supervised_test()
    loop.authorize_resume()

    no_risk = loop.step()
    assert (no_risk.decision.authority, no_risk.decision.reason) == (
        MotionAuthority.EMERGENCY,
        "risk_feed_not_ready",
    )
    assert no_risk.execution.status is RealFollowExecutionStatus.STOPPED

    fresh_non_fall(loop, clock.value)
    with pytest.raises(ValueError, match="not enabled"):
        loop.ingest_uwb(
            RawUwbSample(
                distance_est=2.0,
                orientation_est=-0.55,
                enabled_from_app=0,
                error_state=0,
                sample_monotonic=clock.value,
            )
        )
    invalid = loop.step()
    assert invalid.decision.stop_required is True
    assert invalid.decision.reason == "uwb_not_ready"
    assert service.moves == []
