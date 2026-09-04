from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.follow import (
    FollowExecutionStatus,
    FollowExecutor,
    FollowExecutorConfig,
    FollowTargetPlanner,
    RealMotionSafetyLimit,
    SafetyState,
    VelocityCommand,
)
from app.navigation.models import ControlOwner


TIMESTAMP = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


class FakeRobotService:
    def __init__(self) -> None:
        self.moves: list[dict[str, object]] = []
        self.stops: list[str] = []
        self.joystick_switches: list[bool] = []
        self.fail_move = False

    def move(
        self,
        vx: float,
        vy: float,
        wz: float,
        duration: float,
        source: str = "api",
    ) -> dict:
        if self.fail_move:
            raise RuntimeError("move failed")
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

    def switch_joystick(self, enabled: bool, source: str = "api") -> dict:
        self.joystick_switches.append(enabled)
        return {"code": 0}

    def safe_switch_joystick(self, enabled: bool, source: str = "api") -> int:
        self.joystick_switches.append(enabled)
        return 0


class MutableClock:
    def __init__(self, value: float = 10.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def command(
    *,
    vx: float = 0.3,
    vy: float = 0.0,
    wz: float = 0.5,
    safety_state: SafetyState = SafetyState.SAFE,
    simulation_mode: bool = False,
) -> VelocityCommand:
    return VelocityCommand(
        vx=vx,
        vy=vy,
        wz=wz,
        safety_state=safety_state,
        simulation_mode=simulation_mode,
    )


def plan(*, distance: float = 3.0, yaw: float = 0.0, timestamp: datetime = TIMESTAMP):
    return FollowTargetPlanner().process_measurement(
        distance,
        yaw,
        sample_monotonic=10.0,
        timestamp=timestamp,
    )


def executor(
    service: FakeRobotService,
    *,
    enabled: bool = True,
    simulation_owner: ControlOwner = ControlOwner.FOLLOW,
    emergency: bool = False,
    single_command_only: bool = True,
    clock: MutableClock | None = None,
) -> FollowExecutor:
    return FollowExecutor(
        service,  # type: ignore[arg-type]
        config=FollowExecutorConfig(
            execution_enabled=enabled,
            single_command_only=single_command_only,
        ),
        limits=RealMotionSafetyLimit(),
        control_owner_provider=lambda: simulation_owner,
        emergency_stop_provider=lambda: emergency,
        monotonic_clock=clock or MutableClock(),
        wall_clock=lambda: TIMESTAMP,
    )


def test_execution_is_disabled_by_default() -> None:
    service = FakeRobotService()
    follow_executor = FollowExecutor(
        service,  # type: ignore[arg-type]
        config=FollowExecutorConfig.from_settings(
            Settings(follow_execution_enabled=False)
        ),
        control_owner_provider=lambda: ControlOwner.FOLLOW,
    )

    result = follow_executor.execute(command(), plan=plan())

    assert result.execution_result is FollowExecutionStatus.DISABLED
    assert service.moves == []
    assert service.stops == []


def test_simulation_command_never_reaches_robot_service() -> None:
    service = FakeRobotService()

    result = executor(service).execute(
        command(simulation_mode=True),
        plan=plan(),
    )

    assert result.execution_result is FollowExecutionStatus.SIMULATION_ONLY
    assert service.moves == []


def test_safe_command_uses_robot_service_and_hard_limits() -> None:
    service = FakeRobotService()

    result = executor(service).execute(command(), plan=plan())

    assert result.execution_result is FollowExecutionStatus.SENT
    assert result.to_dict()["robot_result"] == {"code": 0}
    assert service.moves == [
        {
            "vx": 0.10,
            "vy": 0.0,
            "wz": 0.15,
            "duration": 0.10,
            "source": "follow_executor",
        }
    ]


def test_temporary_joystick_handoff_wraps_single_move() -> None:
    service = FakeRobotService()
    follow_executor = FollowExecutor(
        service,  # type: ignore[arg-type]
        config=FollowExecutorConfig(
            execution_enabled=True,
            temporary_joystick_handoff=True,
        ),
        control_owner_provider=lambda: ControlOwner.FOLLOW,
        emergency_stop_provider=lambda: False,
        monotonic_clock=MutableClock(),
        wall_clock=lambda: TIMESTAMP,
        sleep=lambda _seconds: None,
    )

    result = follow_executor.execute(command(), plan=plan())

    assert result.execution_result is FollowExecutionStatus.SENT
    assert service.joystick_switches == [False, True]
    assert len(service.moves) == 1


def test_joystick_is_restored_when_move_raises() -> None:
    service = FakeRobotService()
    service.fail_move = True
    follow_executor = FollowExecutor(
        service,  # type: ignore[arg-type]
        config=FollowExecutorConfig(
            execution_enabled=True,
            temporary_joystick_handoff=True,
        ),
        control_owner_provider=lambda: ControlOwner.FOLLOW,
        emergency_stop_provider=lambda: False,
        monotonic_clock=MutableClock(),
        wall_clock=lambda: TIMESTAMP,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(RuntimeError, match="move failed"):
        follow_executor.execute(command(), plan=plan())

    assert service.joystick_switches == [False, True]
    assert service.stops == ["follow_executor:error"]


def test_executor_reaches_adapter_only_through_real_robot_service(client) -> None:
    service = client.app.state.robot_service
    adapter = client.app.state.adapter
    follow_executor = FollowExecutor(
        service,
        config=FollowExecutorConfig(execution_enabled=True),
        control_owner_provider=lambda: ControlOwner.FOLLOW,
        emergency_stop_provider=lambda: False,
        monotonic_clock=MutableClock(),
        wall_clock=lambda: TIMESTAMP,
    )
    before_moves = adapter.move_count

    result = follow_executor.execute(command(), plan=plan())

    assert result.execution_result is FollowExecutionStatus.SENT
    assert adapter.move_count == before_moves + 1
    assert adapter.moves[-1] == (0.10, 0.0, 0.15)


def test_non_safe_command_stops_instead_of_moving() -> None:
    service = FakeRobotService()

    result = executor(service).execute(
        command(safety_state=SafetyState.STOP_UWB_TIMEOUT),
        plan=plan(),
    )

    assert result.execution_result is FollowExecutionStatus.SAFETY_STOPPED
    assert service.moves == []
    assert service.stops == ["follow_executor:safety"]


def test_emergency_stop_has_highest_priority() -> None:
    service = FakeRobotService()

    result = executor(service, emergency=True).execute(command(), plan=plan())

    assert result.execution_result is FollowExecutionStatus.EMERGENCY_STOPPED
    assert service.moves == []
    assert service.stops == ["follow_executor:emergency"]


def test_emergency_control_owner_also_stops() -> None:
    service = FakeRobotService()

    result = executor(
        service,
        simulation_owner=ControlOwner.EMERGENCY_STOP,
    ).execute(command(), plan=plan())

    assert result.execution_result is FollowExecutionStatus.EMERGENCY_STOPPED
    assert service.moves == []
    assert service.stops == ["follow_executor:emergency_owner"]


@pytest.mark.parametrize(
    "owner",
    [ControlOwner.MANUAL, ControlOwner.NAVIGATION, ControlOwner.NONE],
)
def test_higher_priority_or_unowned_control_yields_without_stop(
    owner: ControlOwner,
) -> None:
    service = FakeRobotService()

    result = executor(service, simulation_owner=owner).execute(
        command(),
        plan=plan(),
    )

    assert result.execution_result is FollowExecutionStatus.CONTROL_YIELDED
    assert service.moves == []
    assert service.stops == []


def test_executor_never_exceeds_five_hz() -> None:
    service = FakeRobotService()
    clock = MutableClock(10.0)
    follow_executor = executor(
        service,
        single_command_only=False,
        clock=clock,
    )

    first = follow_executor.execute(command(), plan=plan())
    clock.value = 10.1
    second = follow_executor.execute(
        command(vx=0.05),
        plan=plan(timestamp=TIMESTAMP + timedelta(milliseconds=100)),
    )

    assert first.execution_result is FollowExecutionStatus.SENT
    assert second.execution_result is FollowExecutionStatus.RATE_LIMITED
    assert len(service.moves) == 1


def test_same_uwb_sample_is_not_executed_twice() -> None:
    service = FakeRobotService()
    clock = MutableClock(10.0)
    follow_executor = executor(
        service,
        single_command_only=False,
        clock=clock,
    )
    source_plan = plan()

    first = follow_executor.execute(command(), plan=source_plan)
    clock.value = 11.0
    second = follow_executor.execute(command(), plan=source_plan)

    assert first.execution_result is FollowExecutionStatus.SENT
    assert second.execution_result is FollowExecutionStatus.NO_NEW_UWB_DATA
    assert len(service.moves) == 1


def test_single_command_guard_requires_explicit_rearm() -> None:
    service = FakeRobotService()
    clock = MutableClock(10.0)
    follow_executor = executor(service, clock=clock)

    first = follow_executor.execute(command(vx=0.05), plan=plan())
    clock.value = 11.0
    second_plan = plan(timestamp=TIMESTAMP + timedelta(seconds=1))
    blocked = follow_executor.execute(command(wz=0.1), plan=second_plan)
    follow_executor.rearm_single_command()
    sent_after_rearm = follow_executor.execute(command(wz=0.1), plan=second_plan)

    assert first.execution_result is FollowExecutionStatus.SENT
    assert blocked.execution_result is FollowExecutionStatus.SINGLE_COMMAND_LIMIT
    assert sent_after_rearm.execution_result is FollowExecutionStatus.SENT
    assert len(service.moves) == 2


def test_zero_velocity_requests_stop() -> None:
    service = FakeRobotService()

    result = executor(service).execute(
        command(vx=0.0, vy=0.0, wz=0.0),
        plan=plan(),
    )

    assert result.execution_result is FollowExecutionStatus.ZERO_COMMAND_STOPPED
    assert service.moves == []
    assert service.stops == ["follow_executor:zero_command"]


def test_non_finite_velocity_requests_stop() -> None:
    service = FakeRobotService()

    result = executor(service).execute(
        command(vx=math.nan),
        plan=plan(),
    )

    assert result.execution_result is FollowExecutionStatus.INVALID_COMMAND_STOPPED
    assert service.moves == []
    assert service.stops == ["follow_executor:invalid_command"]


def test_execution_log_contains_real_motion_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = FakeRobotService()
    source_plan = plan()

    with caplog.at_level(logging.INFO, logger="app.follow.executor"):
        result = executor(service).execute(command(), plan=source_plan)

    record = json.loads(caplog.messages[-1])
    assert result.execution_result is FollowExecutionStatus.SENT
    assert record == {
        "timestamp": "2026-07-31T12:00:00+00:00",
        "uwb_distance": 3.0,
        "target_x": source_plan.target_x,
        "target_y": source_plan.target_y,
        "vx": 0.10,
        "wz": 0.15,
        "safety_state": "SAFE",
        "execution_result": "sent",
    }


def test_real_motion_limits_reject_invalid_values() -> None:
    with pytest.raises(ValueError):
        RealMotionSafetyLimit(max_vx=math.nan)


def test_executor_frequency_cannot_exceed_five_hz() -> None:
    with pytest.raises(ValueError):
        FollowExecutorConfig(max_frequency_hz=5.1)
