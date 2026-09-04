from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.config import Settings
from tools.replay_phase7_2a_control_chain import build_parser, run


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"


@pytest.fixture(scope="module")
def report() -> dict[str, object]:
    args = build_parser().parse_args(
        [
            str(ARTIFACTS / "phase7_1_uwb_capture_elevated_20260822.jsonl"),
            str(ARTIFACTS / "phase7_1_uwb_capture_20260822_1351.jsonl"),
            str(ARTIFACTS / "phase7_1_uwb_yaw_calibration_20260822_141937.jsonl"),
            str(ARTIFACTS / "phase7_1_uwb_powercycle_synced_20260822_141408.jsonl"),
            "--output",
            str(ARTIFACTS / "unused-test-output.json"),
        ]
    )
    result, exit_code = run(args)
    assert exit_code == 0
    return result


def test_real_uwb_far_and_desired_and_too_close_mapping(report: dict[str, object]) -> None:
    checks = report["checks"]
    assert checks["real_historical_far_positive_vx"] is True
    assert checks["desired_pose_near_zero_command"] is True
    assert checks["real_historical_too_close_zero_forward"] is True

    mapping = report["mapping_evidence"]
    assert mapping["far"]["candidate_vx"] > 0.0
    assert mapping["desired_pose"]["candidate_vx"] == pytest.approx(0.0)
    assert mapping["desired_pose"]["candidate_wz"] == pytest.approx(0.0)
    assert mapping["too_close"]["candidate_vx"] == pytest.approx(0.0)


def test_real_uwb_left_and_right_wz_sign(report: dict[str, object]) -> None:
    mapping = report["mapping_evidence"]
    assert mapping["left"]["candidate_wz"] > 0.0
    assert mapping["right"]["candidate_wz"] < 0.0


def test_real_captured_dropout_zeroes_and_clears_resume(report: dict[str, object]) -> None:
    rows = report["mapping_evidence"]["captured_dropouts"]
    assert len(rows) == 1
    assert rows[0]["receive_gap_seconds"] == pytest.approx(22.157)
    assert rows[0]["reason"] == "uwb_stale"
    assert rows[0]["final_vx"] == rows[0]["final_vy"] == rows[0]["final_wz"] == 0.0
    assert rows[0]["resume_authorized"] is False


def test_lidar_clear_slow_stop_arbitration(report: dict[str, object]) -> None:
    lidar = report["arbitration_scenarios"]["lidar"]
    assert lidar["CLEAR"]["passed"] is True
    assert lidar["SLOW"]["passed"] is True
    assert lidar["STOP"]["passed"] is True
    assert lidar["SLOW"]["final_vx"] == pytest.approx(
        lidar["CLEAR"]["final_vx"] * 0.35
    )
    assert lidar["STOP"]["final_vx"] == 0.0
    assert lidar["STOP"]["final_wz"] == 0.0


def test_fall_preempts_and_latches_with_executor_disabled(report: dict[str, object]) -> None:
    fall = report["arbitration_scenarios"]["fall_preemption"]
    assert fall["passed"] is True
    assert fall["fall_authority"] == "EMERGENCY"
    assert fall["fall_risk_state"] == "PAUSED_BY_FALL"
    assert fall["fall_final"] == [0.0, 0.0, 0.0]
    assert fall["continued_authority"] == "EMERGENCY"
    assert fall["continued_final"] == [0.0, 0.0, 0.0]
    assert fall["resume_authorized_after"] is False
    assert fall["executor_status"] == "DISABLED"


def test_required_timeline_fields_and_zero_execution(report: dict[str, object]) -> None:
    required = {
        "timestamp",
        "uwb_distance",
        "uwb_bearing",
        "uwb_valid",
        "follow_target_x",
        "follow_target_y",
        "candidate_vx",
        "candidate_vy",
        "candidate_wz",
        "lidar_state",
        "arbiter_authority",
        "final_vx",
        "final_vy",
        "final_wz",
        "execution_enabled",
        "reason",
    }
    timeline = report["timeline"]
    assert timeline
    assert required <= timeline[0].keys()
    assert all(row["execution_enabled"] is False for row in timeline)
    assert all(row["executor_status"] == "DISABLED" for row in timeline)
    assert all(row["executed_vx"] == row["executed_vy"] == row["executed_wz"] == 0.0 for row in timeline)
    assert report["motion_calls"] == 0
    assert report["safe_stop_calls"] == 0
    assert report["dds_publishers"] == 0


def test_real_motion_setting_remains_false() -> None:
    assert Settings().phase7_motion_execution_enabled is False


def test_future_live_tool_is_reader_only_and_has_no_dispatch_calls() -> None:
    path = ROOT / "tools" / "live_uwb_move_dryrun_phase7_2a.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert "PHASE 7.2-A LIVE DRY-RUN" in source
    assert "REAL MOTION DISABLED" in source
    assert "DDS READERS ONLY" in source
    for forbidden in ("DataWriter", "Publisher", "SportClient", "RobotService", "StopMove", "cmd_vel"):
        assert forbidden not in source
    forbidden_calls = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr.lower() in {"move", "stopmove", "publish", "write"}
    ]
    assert forbidden_calls == []
