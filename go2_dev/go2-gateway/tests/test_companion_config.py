from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
import yaml

from app.companion import (
    CompanionConfigError,
    CompanionState,
    CompanionSupervisor,
    FollowProfile,
    load_companion_demo_config,
)
from app.companion.runtime import build_companion_loop
from app.config import Settings
from app.core.control_owner import ControlOwner
from app.follow import SafetyState, UwbObservation, VelocityCommand
from tools.go2_supervised_follow_phase7_2c import (
    DEFAULT_COMPANION_CONFIG,
    build_supervised_loop,
    main,
)


REAL_COMPANION_CONFIG = (
    Path(__file__).resolve().parents[1]
    / "configs"
    / "companion_follow_real.yaml"
)


def profile_payload() -> dict[str, object]:
    payload = yaml.safe_load(DEFAULT_COMPANION_CONFIG.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def write_profile(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "companion.yaml"
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def test_demo_yaml_loads_every_field_tunable_behavior() -> None:
    config = load_companion_demo_config(DEFAULT_COMPANION_CONFIG)

    assert config.follow.target_distance == pytest.approx(1.75)
    assert config.follow.follow_start_distance == pytest.approx(1.90)
    assert config.follow.follow_stop_distance == pytest.approx(1.70)
    assert config.follow.walk_min == pytest.approx(0.20)
    assert config.follow.vx_max == pytest.approx(0.30)
    assert config.follow.wz_max == pytest.approx(0.30)
    assert math.degrees(config.follow.bearing_deadband_radians) == pytest.approx(12.0)
    assert config.follow.person_stop_hold_seconds == pytest.approx(1.5)
    assert config.follow.uwb_timeout_seconds == pytest.approx(2.0)
    assert config.lidar.slow_distance == pytest.approx(1.40)
    assert config.lidar.stop_distance == pytest.approx(0.80)
    assert config.lidar.slow_speed_scale == pytest.approx(0.35)
    assert config.lidar.roi_min_z == pytest.approx(-0.25)
    assert config.lidar.roi_half_width == pytest.approx(0.45)
    assert config.companion.view_adjust.target_bearing_radians == 0.0
    assert config.safety.require_manual_resume_after_preempt is True
    assert config.safety.watchdog_seconds == pytest.approx(0.5)


def test_real_yaml_uses_elder_follow_envelope_and_gateway_motion_limits() -> None:
    config = load_companion_demo_config(REAL_COMPANION_CONFIG)

    assert config.follow.target_distance == pytest.approx(1.35)
    assert config.follow.follow_start_distance == pytest.approx(1.20)
    assert config.follow.follow_stop_distance == pytest.approx(1.05)
    assert config.follow.min_distance == pytest.approx(1.00)
    assert config.follow.max_distance == pytest.approx(2.50)
    assert config.follow.walk_min == pytest.approx(0.24)
    assert config.follow.vx_max == pytest.approx(0.42)
    assert config.follow.wz_max == pytest.approx(0.55)
    assert config.follow.kx == pytest.approx(0.40)
    assert math.degrees(config.follow.bearing_deadband_radians) == pytest.approx(5.0)
    assert config.safety.watchdog_seconds == pytest.approx(1.25)

    assert config.lidar.slow_distance == pytest.approx(0.70)
    assert config.lidar.stop_distance == pytest.approx(0.50)
    assert config.lidar.slow_speed_scale == pytest.approx(0.35)
    assert config.lidar.roi_min_x == pytest.approx(0.10)
    assert config.lidar.roi_max_x == pytest.approx(2.00)
    assert config.lidar.roi_half_width == pytest.approx(0.45)
    assert config.lidar.roi_min_z == pytest.approx(-0.25)
    assert config.lidar.roi_max_z == pytest.approx(0.65)

    settings = Settings(
        mode="real",
        follow_simulation=False,
        follow_execution_enabled=True,
        phase7_motion_execution_enabled=True,
        max_vx=0.42,
        max_wz=0.55,
    )
    loop = build_companion_loop(object(), settings, config)  # type: ignore[arg-type]
    assert loop.controller.safety_guard.config.min_distance == pytest.approx(1.00)
    assert loop.controller.safety_guard.config.uwb_timeout_seconds == pytest.approx(2.0)

    bearing = config.follow.target_bearing_radians
    started = loop.controller.calculate_velocity(
        loop.planner.process_measurement(1.20, bearing),
        control_owner=ControlOwner.FOLLOW,
    )
    stopped = loop.controller.calculate_velocity(
        loop.planner.process_measurement(1.05, bearing),
        control_owner=ControlOwner.FOLLOW,
    )
    held = loop.controller.calculate_velocity(
        loop.planner.process_measurement(1.10, bearing),
        control_owner=ControlOwner.FOLLOW,
    )
    resumed = loop.controller.calculate_velocity(
        loop.planner.process_measurement(1.20, bearing),
        control_owner=ControlOwner.FOLLOW,
    )
    assert loop.controller.motion_reason == "auto_resume_distance_clear"
    catch_up = loop.controller.calculate_velocity(
        loop.planner.process_measurement(2.20, bearing),
        control_owner=ControlOwner.FOLLOW,
    )

    assert started.vx == pytest.approx(0.24)
    assert stopped.vx == 0.0
    assert held.vx == 0.0
    assert resumed.vx == pytest.approx(0.24)
    # The generic controller remains proportional; the wireless V2.1 layer
    # adds the externalized distance curve that reaches 0.42 m/s at 2.0 m.
    assert 0.30 < catch_up.vx <= 0.42


def test_loader_rejects_unknown_keys_and_automatic_resume(tmp_path: Path) -> None:
    unknown = profile_payload()
    assert isinstance(unknown["follow"], dict)
    unknown["follow"]["typo_speed"] = 0.2  # type: ignore[index]
    with pytest.raises(CompanionConfigError, match="unknown keys"):
        load_companion_demo_config(write_profile(tmp_path, unknown))

    unsafe = profile_payload()
    assert isinstance(unsafe["safety"], dict)
    unsafe["safety"]["require_manual_resume_after_preempt"] = False  # type: ignore[index]
    with pytest.raises(CompanionConfigError, match="must remain true"):
        load_companion_demo_config(write_profile(tmp_path, unsafe))


def test_live_builder_uses_yaml_for_follow_lidar_and_timeouts() -> None:
    config = load_companion_demo_config(DEFAULT_COMPANION_CONFIG)
    settings = Settings(
        mode="real",
        control_enabled=True,
        read_only_mode=False,
        follow_simulation=False,
        follow_execution_enabled=True,
        phase7_motion_execution_enabled=True,
        phase7_require_external_risk_feed=True,
    )
    loop = build_supervised_loop(  # type: ignore[arg-type]
        object(),
        settings,
        max_execute_vx=0.20,
        max_execute_wz=0.10,
        walking_speed_floor_enabled=True,
        companion_config=config,
    )

    assert loop.planner.lost_timeout_seconds == pytest.approx(2.0)
    assert loop.arbiter.config.uwb_timeout_seconds == pytest.approx(2.0)
    assert loop.controller.config.max_vx == pytest.approx(0.30)
    assert loop.executor.limits.max_vx == pytest.approx(0.30)
    assert loop.executor.limits.max_wz == pytest.approx(0.30)
    assert loop.lidar_guard.config is config.lidar
    assert loop.companion_supervisor is not None
    assert loop.companion_supervisor.config is config.companion


def test_view_adjust_is_rotation_only_and_uses_its_own_target_bearing() -> None:
    profile = FollowProfile(person_stop_hold_seconds=1.0)
    supervisor = CompanionSupervisor(profile=profile, monotonic_clock=lambda: 0.0)
    supervisor.start(now_monotonic=0.0)

    def observe(at: float) -> None:
        bearing = math.radians(20.0)
        supervisor.ingest_uwb(
            UwbObservation(
                distance_metres=1.75,
                bearing_radians=bearing,
                sample_monotonic=at,
                enabled_from_app=1,
                error_state=0,
            ),
            # The supervisor only stores this plan; behavior comes from the
            # normalized observation and profile.
            _plan(distance=1.75, bearing=bearing),
        )

    observe(0.0)
    observe(1.0)
    assert supervisor.state is CompanionState.PERSON_STOPPED
    observe(1.1)
    assert supervisor.state is CompanionState.VIEW_ADJUST
    directive = supervisor.govern(
        VelocityCommand(
            vx=0.30,
            vy=0.0,
            wz=0.0,
            safety_state=SafetyState.SAFE,
            simulation_mode=False,
        )
    )

    assert directive.command is not None
    assert directive.command.vx == 0.0
    assert directive.command.vy == 0.0
    assert directive.command.wz == pytest.approx(0.20)


def test_configuration_check_fails_closed_for_missing_profile(
    tmp_path: Path, capsys
) -> None:
    missing = tmp_path / "missing.yaml"
    assert main(["--companion-config", str(missing)]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert "does not exist" in output["error"]


def _plan(*, distance: float, bearing: float):
    from app.follow import FollowTargetPlanner

    profile = FollowProfile()
    return FollowTargetPlanner(profile.follow_offset()).process_measurement(
        distance, bearing
    )
