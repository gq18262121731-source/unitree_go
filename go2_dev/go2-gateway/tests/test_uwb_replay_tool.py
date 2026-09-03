from __future__ import annotations

import json
from pathlib import Path

from tools.replay_uwb_phase7_1 import main


def write_capture(
    path: Path,
    *,
    error_state: int = 0,
    yaw_values: tuple[float, ...] = (0.0, 0.2, 0.0, -0.2),
    include_dropout: bool = False,
) -> None:
    events = []
    receive_times = (10.0, 10.2, 10.4, 13.0 if include_dropout else 10.6)
    for sequence, (receive_time, distance, orientation, yaw) in enumerate(
        zip(
            receive_times,
            (3.0, 2.0, 1.6, 1.6),
            (-0.55, 0.17, -0.55, -0.97),
            yaw_values,
        ),
        1,
    ):
        events.append(
            {
                "event": "uwb_sample",
                "timestamp": 1_787_300_000.0 + receive_time,
                "receive_monotonic": receive_time,
                "sequence": sequence,
                "sample": {
                    "distance_est": distance,
                    "orientation_est": orientation,
                    "yaw_est": yaw,
                    "enabled_from_app": 1,
                    "error_state": error_state,
                },
            }
        )
    events.append(
        {
            "event": "probe_result",
            "dds_baseline_ok": True,
            "uwb_writer_discovered": True,
            "uwb_samples_received": True,
        }
    )
    path.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )


def test_real_capture_replay_passes_without_any_motion_call(tmp_path, capsys) -> None:
    capture = tmp_path / "uwb.jsonl"
    report_path = tmp_path / "report.json"
    write_capture(capture)

    exit_code = main(
        [
            str(capture),
            "--bearing-unit",
            "radians",
            "--bearing-sign",
            "1",
            "--bearing-zero-offset-rad",
            "0.55",
            "--confirm-calibration",
            "--output",
            str(report_path),
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report["verdict"] == "PASS_DRY_RUN"
    assert report["checks"]["dropout_produces_zero_motion"] is True
    assert report["checks"]["move_count_zero"] is True
    assert report["motion_calls"] == 0
    assert report["control_direction_gate"]["passed"] is True
    assert report["control_direction_gate"]["checks"] == {
        "front_has_right_rear_correction": True,
        "left_wz_positive": True,
        "right_wz_negative": True,
        "desired_pose_target_error_near_zero": True,
        "desired_pose_command_near_zero": True,
    }
    assert abs(report["desired_right_rear_pose_check"]["vx"]) <= 1e-9
    assert abs(report["desired_right_rear_pose_check"]["wz"]) <= 1e-9
    assert report["phase7_1c_entry_ready"] is True
    assert report_path.exists()


def test_replay_refuses_to_pass_without_physical_calibration_confirmation(
    tmp_path, capsys
) -> None:
    capture = tmp_path / "uwb.jsonl"
    write_capture(capture)

    exit_code = main(
        [str(capture), "--bearing-unit", "radians", "--bearing-sign", "1"]
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 3
    assert report["verdict"] == "HOLD_CALIBRATION_NOT_CONFIRMED"
    assert report["motion_calls"] == 0


def test_replay_fails_closed_on_uwb_error_state(tmp_path, capsys) -> None:
    capture = tmp_path / "uwb.jsonl"
    write_capture(capture, error_state=2)

    exit_code = main(
        [
            str(capture),
            "--bearing-unit",
            "radians",
            "--bearing-sign",
            "1",
            "--confirm-calibration",
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert report["verdict"] == "FAIL_DRY_RUN"
    assert report["motion_calls"] == 0
    assert report["rejected_samples"]


def test_replay_ignores_legacy_yaw_est_for_target_bearing(tmp_path, capsys) -> None:
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    write_capture(first, yaw_values=(-3.0, -2.0, -1.0, 0.0))
    write_capture(second, yaw_values=(0.4, 1.4, 2.4, 3.0))

    arguments = [
        "--bearing-unit",
        "radians",
        "--bearing-sign",
        "1",
        "--bearing-zero-offset-rad",
        "0.55",
        "--confirm-calibration",
    ]
    assert main([str(first), *arguments]) == 0
    first_report = json.loads(capsys.readouterr().out)
    assert main([str(second), *arguments]) == 0
    second_report = json.loads(capsys.readouterr().out)

    assert first_report["control_direction_summary"] == second_report[
        "control_direction_summary"
    ]


def test_replay_detects_captured_gap_and_produces_zero_motion(
    tmp_path, capsys
) -> None:
    capture = tmp_path / "dropout.jsonl"
    write_capture(capture, include_dropout=True)

    exit_code = main(
        [
            str(capture),
            "--bearing-unit",
            "radians",
            "--bearing-sign",
            "1",
            "--bearing-zero-offset-rad",
            "0.55",
            "--confirm-calibration",
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report["captured_dropout_count"] == 1
    assert report["timeout_evidence"][0]["source"] == "captured_receive_gap"
    assert report["timeout_evidence"][0]["zero_motion"] is True
    assert report["motion_calls"] == 0
