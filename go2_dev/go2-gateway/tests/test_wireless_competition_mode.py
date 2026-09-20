from __future__ import annotations

import math
from pathlib import Path
import threading
import time
import wave
from types import SimpleNamespace

import pytest

from app.companion.competition_lifecycle import CompetitionLifecycle, LifecycleReadiness
from app.companion.models import CompanionState
from app.motion.scripted_motion import MotionActionResult
from app.iot import Go2ControlAdapter, MockTransport
from app.webrtc.follow_target_forwarder import FollowTargetState
from tools.go2_wireless_runtime import (
    CompetitionAction,
    CONFIRM_APP_CLOSED,
    CONFIRM_AREA,
    CONFIRM_WRITER,
    HOTKEY_ACTIONS,
    RuntimeOutputSession,
    RuntimeConsole,
    WALK_FOLLOW_PRESET,
    WALK_FOLLOW_TEXT,
    WirelessCompanionControlError,
    _console_hotkey_command,
    _confirm_startup,
    _emit_demo_console,
    _hotkey_label_for_keypress,
    _normalize_console_command,
    _wait_for_video,
    discover_lan_ipv4,
)


class FakeSocket:
    def __init__(self) -> None:
        self.connected_to = None
        self.closed = False

    def connect(self, address) -> None:
        self.connected_to = address

    def getsockname(self):
        return ("192.168.8.254", 54321)

    def close(self) -> None:
        self.closed = True


def _write_pcm16_wav(path: Path, samples: list[int]) -> None:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        stream.writeframes(
            b"".join(int(sample).to_bytes(2, "little", signed=True) for sample in samples)
        )


def _pcm16_wav_peak(path: Path) -> int:
    with wave.open(str(path), "rb") as stream:
        data = stream.readframes(stream.getnframes())
    if not data:
        return 0
    samples = [
        int.from_bytes(data[index : index + 2], "little", signed=True)
        for index in range(0, len(data), 2)
    ]
    return max(abs(sample) for sample in samples)


def test_lan_video_address_uses_route_to_robot(monkeypatch) -> None:
    fake = FakeSocket()
    monkeypatch.setattr("tools.go2_wireless_runtime.socket.socket", lambda *_args: fake)

    assert discover_lan_ipv4("192.168.8.252") == "192.168.8.254"
    assert fake.connected_to == ("192.168.8.252", 9991)
    assert fake.closed is True


def test_wait_for_video_reports_ready_without_failing_startup() -> None:
    runtime = SimpleNamespace(status=lambda: {"videoReady": True})

    assert _wait_for_video(runtime, 0.05) is True


def test_wait_for_video_timeout_returns_degraded_instead_of_raising() -> None:
    runtime = SimpleNamespace(status=lambda: {"videoReady": False})

    assert _wait_for_video(runtime, 0.01) is False


def test_voice_control_preload_uses_one_batch_and_keeps_per_preset_status(
    tmp_path, monkeypatch, capsys
) -> None:
    import tools.go2_wireless_runtime as runtime_tool

    filenames = {
        *runtime_tool.VOICE_CONTROL_PRESETS.values(),
        runtime_tool.WALK_FOLLOW_PRESET,
        "START_REJECTED.wav",
        "RESUME_REJECTED.wav",
        "CONTROL_REJECTED.wav",
        "VOICE_CHECK.wav",
        "VOICE_RECHECK.wav",
        "NO_RESPONSE_ESCALATED.wav",
    }
    for filename in filenames:
        (tmp_path / filename).write_bytes(b"RIFF" + b"\0" * 40)

    class BatchRuntime:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[Path, ...], int]] = []

        def preload_audio_files(self, paths, *, retry_attempts: int):
            observed = tuple(paths)
            self.calls.append((observed, retry_attempts))
            results = {}
            for path in observed:
                failed = path.name == "NO_RESPONSE_ESCALATED.wav"
                results[str(path.resolve())] = SimpleNamespace(
                    ready=not failed,
                    attempts=2 if failed else 0,
                    error=(
                        "TimeoutError: AudioHub upload exceeded 53.0s"
                        if failed
                        else None
                    ),
                )
            return results

    monkeypatch.setattr(runtime_tool, "VOICE_PRESET_DIR", tmp_path)
    runtime = BatchRuntime()
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.runtime = runtime

    console.preload_voice_control_presets()

    output = capsys.readouterr().out
    assert len(runtime.calls) == 1
    assert runtime.calls[0][1] == 2
    assert len(runtime.calls[0][0]) == len(filenames)
    assert "VOICE_CONTROL_PRELOAD_READY: START_COMPANION.wav" in output
    assert (
        "VOICE_CONTROL_PRELOAD_FAILED: NO_RESPONSE_ESCALATED.wav "
        "(attempts=2, reason=TimeoutError: AudioHub upload exceeded 53.0s)"
        in output
    )


def test_required_demo_preload_batches_start_and_walk_follow(
    tmp_path, monkeypatch, capsys
) -> None:
    import tools.go2_wireless_runtime as runtime_tool

    filenames = ("START_COMPANION.wav", runtime_tool.WALK_FOLLOW_PRESET)
    for filename in filenames:
        (tmp_path / filename).write_bytes(b"RIFF" + b"\0" * 40)

    class Runtime:
        def __init__(self) -> None:
            self.calls = []

        def preload_audio_files(self, paths, *, retry_attempts):
            observed = tuple(Path(path) for path in paths)
            self.calls.append((observed, retry_attempts))
            return {
                str(path.resolve()): SimpleNamespace(
                    ready=True,
                    attempts=0,
                    error=None,
                )
                for path in observed
            }

    monkeypatch.setattr(runtime_tool, "VOICE_PRESET_DIR", tmp_path)
    runtime = Runtime()
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.runtime = runtime

    console.preload_required_demo_presets()

    assert len(runtime.calls) == 1
    assert runtime.calls[0][1] == 2
    assert {path.name for path in runtime.calls[0][0]} == set(filenames)
    output = capsys.readouterr().out
    assert "DEMO_AUDIO_PRELOAD_READY: START_COMPANION.wav" in output
    assert "DEMO_AUDIO_PRELOAD_READY: WALK_FOLLOW.wav" in output


def test_xiaokang_runtime_preload_batches_business_clips(
    tmp_path, monkeypatch, capsys
) -> None:
    import tools.go2_wireless_runtime as runtime_tool

    filenames = {
        "WAKE_READY.wav",
        "outing_allow_health_good.wav",
        "health_hr_prefix.wav",
        "num_76.wav",
        "unit_bpm.wav",
        "health_spo2_98.wav",
        "health_temperature_36_5.wav",
        "weather_condition_sunny.wav",
        "weather_temperature_prefix.wav",
        "temperature_value_22.wav",
        "temperature_value_24.wav",
        "temperature_value_36_6.wav",
        "medication_reminder_before_outing.wav",
        "outing_allow_suffix.wav",
        "outing_medication_check.wav",
        "outing_start.wav",
        "fall_confirm.wav",
        "fall_alert_sound.wav",
        "fall_help_broadcast.wav",
    }
    for filename in filenames:
        (tmp_path / filename).write_bytes(b"RIFF" + b"\0" * 40)
    _write_pcm16_wav(tmp_path / "fall_alert_sound.wav", [1000, -1000] * 120)
    _write_pcm16_wav(tmp_path / "fall_help_broadcast.wav", [1200, -1200] * 120)

    class Runtime:
        def __init__(self) -> None:
            self.calls = []

        def preload_audio_files(self, paths, *, retry_attempts):
            observed = tuple(Path(path) for path in paths)
            self.calls.append((observed, retry_attempts))
            return {
                str(path.resolve()): SimpleNamespace(
                    ready=True,
                    attempts=0,
                    error=None,
                )
                for path in observed
            }

    monkeypatch.setattr(runtime_tool, "VOICE_PRESET_DIR", tmp_path)
    runtime = Runtime()
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.runtime = runtime

    console.preload_xiaokang_runtime_clips()

    assert len(runtime.calls) == 1
    assert runtime.calls[0][1] == 2
    preloaded = {path.name for path in runtime.calls[0][0]}
    assert "WAKE_READY.wav" in preloaded
    assert "outing_allow_health_good.wav" in preloaded
    assert "outing_start.wav" in preloaded
    emergency_paths = [
        path for path in runtime.calls[0][0] if path.parent.name == ".emergency_cache"
    ]
    assert len(emergency_paths) == 2
    assert any("fall_help_broadcast_emergency" in path.name for path in emergency_paths)
    assert "temperature_value_22.wav" in preloaded
    assert "temperature_value_36_6.wav" in preloaded
    output = capsys.readouterr().out
    assert "XIAOKANG_AUDIO_PRELOAD_READY: WAKE_READY.wav" in output


def test_xiaokang_required_preload_is_small_and_covers_live_demo_values(
    tmp_path, monkeypatch, capsys
) -> None:
    import tools.go2_wireless_runtime as runtime_tool

    for clip_id in runtime_tool.XIAOKANG_RUNTIME_REQUIRED_CLIPS:
        filename = (
            "WAKE_READY.wav"
            if clip_id == "sess.wake_ack"
            else runtime_tool.clip_id_to_filename(clip_id)
        )
        (tmp_path / filename).write_bytes(b"RIFF" + b"\0" * 40)

    class Runtime:
        def __init__(self) -> None:
            self.calls = []

        def preload_audio_files(self, paths, *, retry_attempts):
            observed = tuple(Path(path) for path in paths)
            self.calls.append((observed, retry_attempts))
            return {
                str(path.resolve()): SimpleNamespace(
                    ready=True,
                    attempts=0,
                    error=None,
                )
                for path in observed
            }

    monkeypatch.setattr(runtime_tool, "VOICE_PRESET_DIR", tmp_path)
    runtime = Runtime()
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.runtime = runtime

    console.preload_xiaokang_required_clips()

    assert len(runtime.calls) == 1
    assert len(runtime.calls[0][0]) == len(runtime_tool.XIAOKANG_RUNTIME_REQUIRED_CLIPS)
    assert len(runtime.calls[0][0]) < len(runtime_tool.XIAOKANG_RUNTIME_PRELOAD_CLIPS)
    preloaded = {path.name for path in runtime.calls[0][0]}
    assert "temperature_value_0.wav" in preloaded
    assert "temperature_value_22.wav" in preloaded
    assert "temperature_value_40.wav" in preloaded
    assert "temperature_value_36_6.wav" in preloaded
    assert "temperature_value_37_5.wav" in preloaded
    output = capsys.readouterr().out
    assert "XIAOKANG_AUDIO_REQUIRED_PRELOAD_START" in output
    assert "XIAOKANG_AUDIO_REQUIRED_PRELOAD_DONE" in output


def test_xiaokang_required_preload_fails_when_any_required_clip_is_missing(
    tmp_path, monkeypatch
) -> None:
    import tools.go2_wireless_runtime as runtime_tool

    missing_clip = "temperature.value.22"
    for clip_id in runtime_tool.XIAOKANG_RUNTIME_REQUIRED_CLIPS:
        if clip_id == missing_clip:
            continue
        filename = (
            "WAKE_READY.wav"
            if clip_id == "sess.wake_ack"
            else runtime_tool.clip_id_to_filename(clip_id)
        )
        (tmp_path / filename).write_bytes(b"RIFF" + b"\0" * 40)

    class Runtime:
        def preload_audio_files(self, paths, *, retry_attempts):
            return {
                str(Path(path).resolve()): SimpleNamespace(
                    ready=True,
                    attempts=1,
                    error=None,
                )
                for path in paths
            }

    monkeypatch.setattr(runtime_tool, "VOICE_PRESET_DIR", tmp_path)
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.runtime = Runtime()

    with pytest.raises(RuntimeError, match="required clips are not ready"):
        console.preload_xiaokang_required_clips()


def test_voice_clip_playback_uses_batch_audiohub_session(
    tmp_path, monkeypatch, capsys
) -> None:
    import tools.go2_wireless_runtime as runtime_tool

    for filename in (
        "outing_allow_health_good.wav",
        "health_hr_prefix.wav",
        "num_78.wav",
        "unit_bpm.wav",
        "health_spo2_98.wav",
        "health_temperature_prefix.wav",
        "temperature_value_36_6.wav",
        "weather_condition_sunny.wav",
        "weather_temperature_prefix.wav",
        "temperature_value_22.wav",
        "medication_reminder_before_outing.wav",
        "outing_allow_suffix.wav",
    ):
        _write_pcm16_wav(tmp_path / filename, [1000, -1000] * 120)

    class Runtime:
        def __init__(self) -> None:
            self.preloaded: list[tuple[str, ...]] = []
            self.batches: list[tuple[tuple[str, ...], float, tuple[float, ...]]] = []
            self.stops: list[str] = []
            self.playback_active_during_preload: list[bool] = []
            self.playback_active_during_play: list[bool] = []
            self.console = None

        def preload_audio_files(self, paths, *, retry_attempts):
            self.playback_active_during_preload.append(
                self.console.is_voice_playback_active()
            )
            observed = tuple(str(Path(path).resolve()) for path in paths)
            self.preloaded.append(observed)
            return {
                path: SimpleNamespace(ready=True, attempts=0, error=None)
                for path in observed
            }

        def play_audio_files(self, paths, *, timeout_seconds, inter_clip_gap_seconds):
            self.playback_active_during_play.append(
                self.console.is_voice_playback_active()
            )
            self.batches.append(
                (
                    tuple(Path(path).name for path in paths),
                    timeout_seconds,
                    tuple(inter_clip_gap_seconds),
                )
            )

        def stop_audio_playback(self, *, reason, timeout_seconds):
            self.stops.append(reason)

    monkeypatch.setattr(runtime_tool, "VOICE_PRESET_DIR", tmp_path)
    runtime = Runtime()
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.runtime = runtime
    runtime.console = console

    clips = [
        "outing.allow.health_good",
        "health.hr.prefix",
        "num.78",
        "unit.bpm",
        "health.spo2.98",
        "health.temperature.prefix",
        "temperature.value.36_6",
        "weather.condition.sunny",
        "weather.temperature.prefix",
        "temperature.value.22",
        "medication.reminder.before_outing",
        "outing.allow.suffix",
    ]
    result = console.play_voice_clips(clips)

    assert result["status"] == "done"
    assert result["played"] == len(clips)
    assert len(runtime.preloaded) == 1
    assert len(runtime.batches) == 1
    assert runtime.playback_active_during_preload == [False]
    assert runtime.playback_active_during_play == [True]
    played_names, timeout_seconds, gaps = runtime.batches[0]
    assert played_names == (
        "outing_allow_health_good.wav",
        "health_hr_prefix.wav",
        "num_78.wav",
        "unit_bpm.wav",
        "health_spo2_98.wav",
        "health_temperature_prefix.wav",
        "temperature_value_36_6.wav",
        "weather_condition_sunny.wav",
        "weather_temperature_prefix.wav",
        "temperature_value_22.wav",
        "medication_reminder_before_outing.wav",
        "outing_allow_suffix.wav",
    )
    assert len(gaps) == len(clips) - 1
    assert all(gap == runtime_tool.VOICE_PLAYBACK_INTER_CLIP_GAP_SECONDS for gap in gaps)
    assert timeout_seconds == runtime_tool.VOICE_PLAYBACK_TIMEOUT_MIN_SECONDS
    assert runtime.stops == ["voice_playback_cleanup"]
    output = capsys.readouterr().out
    assert "[AUDIO] PLAY_BATCH_REQ" in output
    assert "VOICE_CLIPS_PLAYED: 12/12" in output


@pytest.mark.parametrize(
    ("playback_fails", "expected_statuses"),
    (
        (
            False,
            ["语音任务已接收", "正在准备播报...", "语音播报中", "播报完成"],
        ),
        (
            True,
            ["语音任务已接收", "正在准备播报...", "语音播报失败，请重试"],
        ),
    ),
)
def test_operator_voice_status_reports_acceptance_before_playback_result(
    tmp_path, monkeypatch, playback_fails, expected_statuses
) -> None:
    import tools.go2_wireless_runtime as runtime_tool

    _write_pcm16_wav(
        tmp_path / "outing_allow_health_good.wav",
        [1000, -1000] * 120,
    )

    class Runtime:
        def preload_audio_files(self, paths, *, retry_attempts):
            del retry_attempts
            return {
                str(Path(path).resolve()): SimpleNamespace(ready=True)
                for path in paths
            }

        def play_audio_files(
            self,
            _paths,
            *,
            timeout_seconds,
            inter_clip_gap_seconds,
            on_playback_started,
        ):
            del timeout_seconds, inter_clip_gap_seconds
            if playback_fails:
                raise TimeoutError("AudioHub unavailable")
            on_playback_started()

        def stop_audio_playback(self, *, reason, timeout_seconds):
            del reason, timeout_seconds

    statuses: list[str] = []

    class ObservedLock:
        def __enter__(self):
            assert statuses == ["语音任务已接收"]
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            return False

    decision = SimpleNamespace(
        intent="outing_assessment",
        clips=("outing.allow.health_good",),
        action="",
    )
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.runtime = Runtime()
    console.demo_console = False
    console.lifecycle = SimpleNamespace(
        risk_active=False,
        state=CompanionState.IDLE,
    )
    console._interaction_flow_controller = SimpleNamespace(
        context=SimpleNamespace(demo_phase="skill2_ready"),
        handle_event=lambda _event, _payload: [decision],
    )
    console._go2_asr_bridge = None
    console._last_script_action = None
    console._hotkey_action_lock = ObservedLock()
    console._demo_event = lambda _message: None
    console._print_demo_guidance = lambda: None
    console._print_operator_line = statuses.append
    monkeypatch.setattr(runtime_tool, "VOICE_PRESET_DIR", tmp_path)
    monkeypatch.setattr("tools.go2_wireless_runtime.time.sleep", lambda _seconds: None)

    if playback_fails:
        with pytest.raises(WirelessCompanionControlError):
            console.execute_competition_action(CompetitionAction.SKILL2_REPORT)
    else:
        result = console.execute_competition_action(
            CompetitionAction.SKILL2_REPORT
        )
        assert result["accepted"] is True

    assert statuses == expected_statuses


def test_voice_clip_playback_resolves_clip_ids_in_order(
    tmp_path, monkeypatch, capsys
) -> None:
    import tools.go2_wireless_runtime as runtime_tool

    for filename in (
        "outing_allow_health_good.wav",
        "health_hr_prefix.wav",
        "num_76.wav",
        "unit_bpm.wav",
    ):
        (tmp_path / filename).write_bytes(b"RIFF" + b"\0" * 40)

    class Runtime:
        def __init__(self) -> None:
            self.played: list[tuple[str, float]] = []

        def play_audio_file(self, path, *, timeout_seconds):
            self.played.append((Path(path).name, timeout_seconds))

    monkeypatch.setattr(runtime_tool, "VOICE_PRESET_DIR", tmp_path)
    runtime = Runtime()
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.runtime = runtime

    result = console.play_voice_clips(
        [
            "outing.allow.health_good",
            "health.hr.prefix",
            "num.76",
            "unit.bpm",
        ]
    )

    assert result == {
        "clips": [
            "outing.allow.health_good",
            "health.hr.prefix",
            "num.76",
            "unit.bpm",
        ],
        "played": 4,
        "status": "done",
        "missing_clips": [],
    }
    assert runtime.played == [
        ("outing_allow_health_good.wav", runtime_tool.VOICE_PLAYBACK_TIMEOUT_MIN_SECONDS),
        ("health_hr_prefix.wav", runtime_tool.VOICE_PLAYBACK_TIMEOUT_MIN_SECONDS),
        ("num_76.wav", runtime_tool.VOICE_PLAYBACK_TIMEOUT_MIN_SECONDS),
        ("unit_bpm.wav", runtime_tool.VOICE_PLAYBACK_TIMEOUT_MIN_SECONDS),
    ]
    assert "VOICE_CLIPS_PLAYED: 4/4" in capsys.readouterr().out


def test_voice_clip_playback_timeout_scales_with_wav_duration(
    tmp_path, monkeypatch
) -> None:
    import tools.go2_wireless_runtime as runtime_tool

    _write_pcm16_wav(tmp_path / "WAKE_READY.wav", [1000, -1000] * 120000)
    timeouts: list[float] = []
    stops: list[str] = []
    sleeps: list[float] = []

    class Runtime:
        def play_audio_file(self, _path, *, timeout_seconds):
            timeouts.append(timeout_seconds)

        def stop_audio_playback(self, *, reason, timeout_seconds):
            stops.append(reason)

    monkeypatch.setattr(runtime_tool, "VOICE_PRESET_DIR", tmp_path)
    monkeypatch.setattr(runtime_tool.time, "sleep", lambda seconds: sleeps.append(seconds))
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.runtime = Runtime()

    result = console.play_voice_clips(["sess.wake_ack"])

    assert result["status"] == "done"
    assert timeouts == [
        pytest.approx(10.0 + runtime_tool.VOICE_PLAYBACK_TIMEOUT_MARGIN_SECONDS)
    ]
    assert stops == ["voice_clip_complete:sess.wake_ack", "voice_playback_cleanup"]
    assert sleeps == [
        pytest.approx(10.0 + runtime_tool.VOICE_PLAYBACK_WATCHDOG_MARGIN_SECONDS),
        pytest.approx(runtime_tool.VOICE_PLAYBACK_ECHO_GUARD_SECONDS),
    ]


def test_emergency_voice_playback_uses_limited_gain_and_alarm_pause(
    tmp_path, monkeypatch
) -> None:
    import tools.go2_wireless_runtime as runtime_tool

    _write_pcm16_wav(tmp_path / "fall_alert_sound.wav", [1000, -1000] * 120)
    _write_pcm16_wav(tmp_path / "fall_help_broadcast.wav", [1200, -1200] * 120)
    events: list[tuple[str, str | float, float | None]] = []

    class Runtime:
        def play_audio_file(self, path, *, timeout_seconds):
            events.append(("play", str(Path(path)), timeout_seconds))

        def stop_audio_playback(self, *, reason, timeout_seconds):
            events.append(("stop", reason, timeout_seconds))

    monkeypatch.setattr(runtime_tool, "VOICE_PRESET_DIR", tmp_path)
    monkeypatch.setattr(
        runtime_tool.time,
        "sleep",
        lambda seconds: events.append(("sleep", seconds, None)),
    )
    runtime = Runtime()
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.runtime = runtime

    result = console.play_voice_clips(["fall.alert.sound", "fall.help.broadcast"])

    assert result["status"] == "done"
    play_events = [event for event in events if event[0] == "play"]
    assert len(play_events) == 2
    assert play_events[0][2] == runtime_tool.EMERGENCY_VOICE_TIMEOUT_MIN_SECONDS
    assert play_events[1][2] == runtime_tool.EMERGENCY_VOICE_TIMEOUT_MIN_SECONDS
    assert Path(str(play_events[0][1])).parent.name == ".emergency_cache"
    assert Path(str(play_events[1][1])).parent.name == ".emergency_cache"
    assert _pcm16_wav_peak(Path(str(play_events[0][1]))) > 1000
    assert _pcm16_wav_peak(Path(str(play_events[1][1]))) > 1200
    assert _pcm16_wav_peak(Path(str(play_events[1][1]))) <= 32700
    sleep_events = [event for event in events if event[0] == "sleep"]
    assert sleep_events == [
        (
            "sleep",
            pytest.approx(0.01 + runtime_tool.VOICE_PLAYBACK_WATCHDOG_MARGIN_SECONDS),
            None,
        ),
        ("sleep", pytest.approx(runtime_tool.VOICE_PLAYBACK_ECHO_GUARD_SECONDS), None),
        ("sleep", runtime_tool.EMERGENCY_VOICE_ALARM_PAUSE_SECONDS, None),
        (
            "sleep",
            pytest.approx(0.01 + runtime_tool.VOICE_PLAYBACK_WATCHDOG_MARGIN_SECONDS),
            None,
        ),
        ("sleep", pytest.approx(runtime_tool.VOICE_PLAYBACK_ECHO_GUARD_SECONDS), None),
    ]
    stop_events = [event for event in events if event[0] == "stop"]
    assert stop_events == [
        ("stop", "voice_clip_complete:fall.alert.sound", 3.0),
        ("stop", "voice_clip_complete:fall.help.broadcast", 3.0),
        ("stop", "voice_playback_cleanup", 3.0),
    ]


def test_voice_clip_playback_refuses_partial_sentence_when_clip_missing(
    tmp_path, monkeypatch, capsys
) -> None:
    import tools.go2_wireless_runtime as runtime_tool

    (tmp_path / "health_hr_prefix.wav").write_bytes(b"RIFF" + b"\0" * 40)

    class Runtime:
        def __init__(self) -> None:
            self.played: list[str] = []

        def play_audio_file(self, path, *, timeout_seconds):
            self.played.append(Path(path).name)

    monkeypatch.setattr(runtime_tool, "VOICE_PRESET_DIR", tmp_path)
    runtime = Runtime()
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.runtime = runtime

    result = console.play_voice_clips(["health.hr.prefix", "num.76"])

    assert result["status"] == "missing"
    assert result["played"] == 0
    assert result["missing_clips"] == ["num.76"]
    assert runtime.played == []
    assert "VOICE_CLIPS_MISSING: num.76" in capsys.readouterr().out


def test_voice_clip_playback_failure_returns_error_result(
    tmp_path, monkeypatch, capsys
) -> None:
    import tools.go2_wireless_runtime as runtime_tool

    _write_pcm16_wav(tmp_path / "WAKE_READY.wav", [1000, -1000] * 120)

    class Runtime:
        def play_audio_file(self, _path, *, timeout_seconds):
            raise TimeoutError("audiohub timeout")

    monkeypatch.setattr(runtime_tool, "VOICE_PRESET_DIR", tmp_path)
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.runtime = Runtime()

    result = console.play_voice_clips(["sess.wake_ack"])

    assert result["status"] == "error"
    assert result["played"] == 0
    assert result["clips"] == ["sess.wake_ack"]
    assert "TimeoutError: audiohub timeout" in result["error"]
    assert "VOICE_CLIPS_PLAYBACK_FAILED: clip=sess.wake_ack" in capsys.readouterr().out


def test_voice_clip_playback_reentry_is_dropped(tmp_path, monkeypatch, capsys) -> None:
    import tools.go2_wireless_runtime as runtime_tool

    _write_pcm16_wav(tmp_path / "WAKE_READY.wav", [1000, -1000] * 120)

    class Runtime:
        def play_audio_file(self, _path, *, timeout_seconds):
            raise AssertionError("duplicate playback must not be queued")

    monkeypatch.setattr(runtime_tool, "VOICE_PRESET_DIR", tmp_path)
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.runtime = Runtime()
    console._voice_playback_lock = threading.Lock()
    console._voice_playback_active = True
    console._voice_playback_signature = ("sess.wake_ack",)
    console._voice_playback_seq = 1

    result = console.play_voice_clips(["sess.wake_ack"])

    assert result["status"] == "error"
    assert result["reason"] == "playback_active"
    assert result["played"] == 0
    assert "[AUDIO] duplicate playback dropped" in capsys.readouterr().out


def test_walk_follow_plays_fixed_preset_then_enters_existing_manual(
    tmp_path, monkeypatch
) -> None:
    import tools.go2_wireless_runtime as runtime_tool

    preset = tmp_path / WALK_FOLLOW_PRESET
    preset.write_bytes(b"RIFF" + b"\0" * 40)
    events: list[object] = []

    class Runtime:
        def play_audio_file(self, path, *, timeout_seconds):
            events.append(("play", Path(path).name, timeout_seconds))

    console = RuntimeConsole.__new__(RuntimeConsole)
    console.runtime = Runtime()
    monkeypatch.setattr(runtime_tool, "VOICE_PRESET_DIR", tmp_path)
    monkeypatch.setattr(console, "_wav_duration_seconds", lambda _path: 1.25)
    monkeypatch.setattr(runtime_tool.time, "sleep", lambda value: events.append(("wait", value)))
    monkeypatch.setattr(console, "_manual_console", lambda: events.append("manual"))

    console._walk_follow()

    assert WALK_FOLLOW_TEXT == (
        "您当前心率为76次每分钟，血氧为98%，状态正常。"
        "伴随模式已启动，请注意出行安全。"
    )
    assert events == [
        ("play", WALK_FOLLOW_PRESET, 5.0),
        ("wait", 1.25),
        "manual",
    ]


def test_walk_follow_voice_failure_still_enters_manual(
    tmp_path, monkeypatch, caplog
) -> None:
    import tools.go2_wireless_runtime as runtime_tool

    preset = tmp_path / WALK_FOLLOW_PRESET
    preset.write_bytes(b"RIFF" + b"\0" * 40)
    entered: list[bool] = []

    class Runtime:
        def play_audio_file(self, _path, *, timeout_seconds):
            raise RuntimeError("speaker unavailable")

    console = RuntimeConsole.__new__(RuntimeConsole)
    console.runtime = Runtime()
    monkeypatch.setattr(runtime_tool, "VOICE_PRESET_DIR", tmp_path)
    monkeypatch.setattr(console, "_manual_console", lambda: entered.append(True))

    with caplog.at_level("WARNING"):
        console._walk_follow()

    assert entered == [True]
    assert "WALK_FOLLOW voice playback failed" in caplog.text


def test_start_announcement_uses_existing_preset_and_waits_for_playback(
    tmp_path, monkeypatch
) -> None:
    import tools.go2_wireless_runtime as runtime_tool

    preset = tmp_path / "START_COMPANION.wav"
    preset.write_bytes(b"RIFF" + b"\0" * 40)
    events: list[object] = []

    class Runtime:
        def play_audio_file(self, path, *, timeout_seconds):
            events.append(("play", Path(path).name, timeout_seconds))

    console = RuntimeConsole.__new__(RuntimeConsole)
    console.runtime = Runtime()
    monkeypatch.setattr(runtime_tool, "VOICE_PRESET_DIR", tmp_path)
    monkeypatch.setattr(console, "_wav_duration_seconds", lambda _path: 1.75)
    monkeypatch.setattr(
        runtime_tool.time,
        "sleep",
        lambda value: events.append(("wait", value)),
    )

    console._play_start_announcement()

    assert events == [
        ("play", "START_COMPANION.wav", 3.0),
        ("wait", 1.75),
    ]


def test_auto_demo_startup_does_not_block_for_operator_confirmation(
    tmp_path, monkeypatch, capsys
) -> None:
    lifecycle = tmp_path / "companion.json"
    lifecycle.write_text('{"state":"IDLE"}', encoding="utf-8")
    settings = SimpleNamespace(
        companion_state_path=str(lifecycle),
        control_enabled=True,
        read_only_mode=False,
    )

    def unexpected_input(_prompt: str) -> str:
        raise AssertionError("startup prompt must not be shown")

    monkeypatch.setattr("builtins.input", unexpected_input)
    _confirm_startup(settings, auto_demo="phone_demo")

    output = capsys.readouterr().out
    assert "[GO2] Core Runtime starting" in output
    assert "[GO2] Motion control enabled" in output
    assert "[VOICE] Xiaokang listener initializing" in output
    assert "[VIDEO] WebRTC video enabled" in output


def test_startup_confirmation_skip_flag_is_non_blocking_noop(
    tmp_path, monkeypatch
) -> None:
    lifecycle = tmp_path / "companion.json"
    lifecycle.write_text('{"state":"IDLE"}', encoding="utf-8")
    settings = SimpleNamespace(
        companion_state_path=str(lifecycle),
        control_enabled=True,
        read_only_mode=False,
    )

    def unexpected_input(_prompt: str) -> str:
        raise AssertionError("startup prompt must not be shown")

    monkeypatch.setattr("builtins.input", unexpected_input)

    _confirm_startup(
        settings,
        skip_operator_prompts=True,
    )


def _start_command_console(*, manual_confirm_start: bool):
    console = RuntimeConsole.__new__(RuntimeConsole)
    shutdown_calls: list[bool] = []
    console.runtime = SimpleNamespace(
        status=lambda: {
            "robotIp": "192.168.8.252",
            "connected": True,
            "connectionCount": 1,
            "dataChannelReady": True,
            "sportStateReady": True,
            "videoReady": True,
        },
        request_shutdown=lambda: shutdown_calls.append(True),
    )
    console.video_host = "0.0.0.0"
    console.video_port = 8093
    console.lan_ip = "192.168.8.254"
    console._motion_thread = None
    console.lifecycle = CompetitionLifecycle()
    console.manual_confirm_start = manual_confirm_start
    def start_companion(*, before_start=None):
        if before_start is not None:
            before_start()
        return {
            "state": "FOLLOWING",
            "runtime_active": True,
        }

    console.start_companion = start_companion
    console._play_start_announcement = lambda: None
    console.play_voice_clips = lambda *_args, **_kwargs: {
        "clips": ["follow.stop"],
        "played": 1,
        "status": "done",
        "missing_clips": [],
    }
    console.stop_motion = lambda: None
    console.shutdown_calls = shutdown_calls
    return console


def test_console_start_defaults_to_lifecycle_without_confirmation(
    monkeypatch, capsys
) -> None:
    console = _start_command_console(manual_confirm_start=False)
    commands = iter(("START", "EXIT"))
    prompts: list[str] = []

    def command_input(prompt: str) -> str:
        prompts.append(prompt)
        return next(commands)

    monkeypatch.setattr("builtins.input", command_input)

    assert console.run() == 0

    output = capsys.readouterr().out
    assert prompts == ["wireless> ", "wireless> "]
    assert "WIRELESS_COMPANION_START_APPROVED" not in output
    assert "START accepted -> FOLLOWING" in output
    assert console.shutdown_calls == [True]


def test_console_start_bypasses_tts_and_starts_motion(
    monkeypatch,
) -> None:
    console = _start_command_console(manual_confirm_start=False)
    events: list[object] = []
    console.play_voice_clips = (
        lambda clips, **_kwargs: events.append(("voice", tuple(clips)))
        or {"status": "done", "played": len(clips)}
    )

    def start_companion(*, before_start=None):
        if before_start is not None:
            before_start()
        events.append("start_motion")
        return {"state": "FOLLOWING", "runtime_active": True}

    console.start_companion = start_companion
    commands = iter(("START", "EXIT"))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    assert console.run() == 0
    assert events == ["start_motion"]


def test_console_start_and_stop_use_bound_control_adapter(monkeypatch) -> None:
    console = _start_command_console(manual_confirm_start=False)
    calls: list[tuple[str, dict]] = []

    adapter = Go2ControlAdapter(
        start_follow=lambda message: calls.append(("start", dict(message.payload))) or {"ok": True},
        stop_follow=lambda message: calls.append(("stop", dict(message.payload))) or {"ok": True},
        resume_follow=lambda message: {"ok": True},
        play_clips=lambda message: {"clips": [], "played": 0, "status": "done"},
        ping=lambda message: {"nonce": message.request_id},
    )
    console.service = SimpleNamespace(settings=SimpleNamespace(robot_id="DOG-LJG-001"))
    console.companion_status = lambda: {"state": "FOLLOWING", "runtime_active": True}
    console.set_control_adapter(adapter)
    commands = iter(("START", "STOP", "EXIT"))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    assert console.run() == 0
    assert calls == [
        ("start", {"duration_minutes": 3, "runtime_command": "FOLLOW_3MIN", "follow_profile": "FOLLOW_3MIN"}),
        ("stop", {}),
    ]


def test_f5_hotkey_uses_resume_when_lifecycle_waits_for_resume(monkeypatch) -> None:
    console = _start_command_console(manual_confirm_start=False)
    console.lifecycle = SimpleNamespace(
        state=CompanionState.WAIT_RESUME,
        risk_active=False,
    )
    console._was_following_before_fall = True
    calls: list[str] = []
    console._run_control_command = (
        lambda command, *, request_id, payload: calls.append(command)
        or {"state": "FOLLOWING", "runtime_active": True}
    )
    commands = iter(("Ctrl+F5", "EXIT"))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    assert console.run() == 0
    assert calls == ["resume_follow"]


def test_f5_releases_manual_console_and_restarts_runtime_directly() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.lifecycle = CompetitionLifecycle()
    console.lifecycle.acquire_manual()
    console.manual_controller = SimpleNamespace(active=True)
    console._manual_console_stop = threading.Event()
    console._state_lock = threading.RLock()
    console._motion_thread = None
    console._motion_name = None
    console._motion_generation = 0
    console._demo_phase = lambda: "wait_resume"
    console._set_demo_phase = lambda _phase: None
    console._print_demo_guidance = lambda: None
    console.play_voice_clips = lambda *_args, **_kwargs: {"status": "done"}
    calls: list[str] = []

    def release_manual(*, quiet=False):
        assert quiet is True
        console.manual_controller.active = False
        if console.lifecycle.state is CompanionState.MANUAL_CONTROL:
            console.lifecycle.release_manual()
        return {"state": "IDLE"}

    console.release_manual = release_manual
    console.start_companion = lambda: calls.append("direct_start") or {
        "state": "FOLLOWING",
        "runtime_active": True,
    }
    console._run_control_command = lambda *_args, **_kwargs: pytest.fail(
        "manual recovery must recreate the Runtime worker directly"
    )

    result = console.start_or_resume_follow(announce=False)

    assert result["state"] == "FOLLOWING"
    assert calls == ["direct_start"]
    assert console._manual_console_stop.is_set()


def test_f5_is_idempotent_when_companion_is_already_following(
    monkeypatch, capsys
) -> None:
    console = _start_command_console(manual_confirm_start=False)
    console._motion_thread = SimpleNamespace(is_alive=lambda: True)
    console._motion_name = "companion"
    console.companion_status = lambda: {
        "state": "FOLLOWING",
        "runtime_active": True,
    }
    commands = iter(("Ctrl+F5", "EXIT"))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    assert console.run() == 0
    assert "START accepted -> already FOLLOWING" in capsys.readouterr().out


def test_f5_rejects_latched_fall_before_start_announcement(
    monkeypatch, capsys
) -> None:
    console = _start_command_console(manual_confirm_start=False)
    console.lifecycle.ingest_fall(incident_id="latched-fall", confirmed=False)
    announcements: list[bool] = []
    console.play_voice_clips = (
        lambda *_args, **_kwargs: announcements.append(True) or {"status": "done"}
    )
    commands = iter(("Ctrl+F5", "EXIT"))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    assert console.run() == 0
    assert announcements == []
    assert console.lifecycle.risk_active is True
    assert (
        "ACTION_REJECTED:FOLLOW_RESUME:COMPANION_STATE_CONFLICT:risk_active"
        in capsys.readouterr().out
    )


def test_f5_does_not_start_when_motion_generation_changes_during_voice(
    monkeypatch, capsys
) -> None:
    console = _start_command_console(manual_confirm_start=False)
    starts: list[bool] = []
    console.start_companion = lambda **_kwargs: starts.append(True) or {"state": "FOLLOWING"}

    def interrupted_voice(_clips, **_kwargs):
        console._cancel_pending_motion_actions(reason="test_fall_during_voice")
        return {"status": "done", "played": 1}

    console.play_voice_clips = interrupted_voice
    commands = iter(("Ctrl+F5", "EXIT"))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    assert console.run() == 0
    assert starts == []
    assert "ACTION_REJECTED:FOLLOW_RESUME:START_CANCELLED" in capsys.readouterr().out


def test_f9_recovery_clears_latched_lifecycle_and_stops_emergency_worker() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.lifecycle = CompetitionLifecycle()
    console.lifecycle.ingest_fall(incident_id="hotkey-fall-1", confirmed=False)
    console._was_following_before_fall = True
    console._emergency_voice_cancel = threading.Event()
    console._emergency_voice_cancel.clear()
    console.stop_motion = lambda: None
    console._wait_for_motion_stop = lambda: None
    events: list[tuple[str, dict]] = []
    console.execute_local_interaction_event = (
        lambda event, *, payload=None: events.append((event, dict(payload or {})))
    )
    console.companion_status = lambda: {"state": "IDLE"}

    result = console.recover_fall_from_hotkey()

    assert result["recovered"] is True
    assert console.lifecycle.risk_active is False
    assert console.lifecycle.state is CompanionState.IDLE
    assert console._emergency_voice_cancel.is_set()
    assert events == [("FALL_RECOVERED", {"force_recovered": True})]


def test_f9_recovery_from_idle_returns_to_idle_for_next_f5_start() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.lifecycle = CompetitionLifecycle()
    console.lifecycle.ingest_fall(incident_id="hotkey-fall-from-idle", confirmed=False)
    console._was_following_before_fall = False
    console._emergency_voice_cancel = threading.Event()
    console.stop_motion = lambda: None
    console._wait_for_motion_stop = lambda: None
    console.execute_local_interaction_event = lambda _event, *, payload=None: None
    console.companion_status = lambda: {"state": console.lifecycle.state.value}

    result = console.recover_fall_from_hotkey()

    assert result["recovered"] is True
    assert console.lifecycle.risk_active is False
    assert console.lifecycle.state is CompanionState.IDLE


def test_f6_from_idle_enters_fall_manual_without_global_motion_stop() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.lifecycle = CompetitionLifecycle()
    sequence: list[str] = []
    console.stop_motion = lambda: sequence.append("stop")
    console._wait_for_motion_stop = lambda: None
    console._record_lifecycle_actions = lambda _payload: None
    events: list[tuple[str, dict]] = []
    console._start_fall_manual_mode = lambda: sequence.append("manual") or True

    def execute_local(event: str, *, payload=None) -> None:
        sequence.append("prompt")
        events.append((event, dict(payload or {})))

    console.execute_local_interaction_event = execute_local
    console.companion_status = lambda: {"state": console.lifecycle.state.value}

    result = console.trigger_fall_from_hotkey()

    assert result["fallTriggered"] is True
    assert console.lifecycle.risk_active is True
    assert console.lifecycle.state is CompanionState.VOICE_CHECK
    assert result["fallManualReady"] is True
    assert sequence == ["manual", "prompt"]
    assert events[0][0] == "FALL_SUSPECTED"
    assert events[0][1]["incident_id"] == result["incident_id"]
    assert events[0][1]["motion_already_stopped"] is True


def test_f6_stops_active_companion_before_fall_manual_and_prompt() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.lifecycle = CompetitionLifecycle()
    console.lifecycle.start(LifecycleReadiness())
    console._state_lock = threading.RLock()
    console._motion_thread = SimpleNamespace(is_alive=lambda: True)
    console._motion_name = "companion"
    sequence: list[str] = []
    console.stop_motion = lambda: sequence.append("stop_companion")
    console._wait_for_motion_stop = lambda: sequence.append("wait")
    console._record_lifecycle_actions = lambda _payload: None
    console._start_fall_manual_mode = lambda: sequence.append("manual") or True
    console._interrupt_voice_playback = lambda *, reason: sequence.append(
        ("interrupt", reason)
    )
    console._cancel_pending_motion_actions = lambda *, reason: 0
    console._demo_event = lambda _message: None
    console.execute_local_interaction_event = (
        lambda _event, *, payload=None: sequence.append("prompt")
    )
    console._print_demo_guidance = lambda: None
    console.companion_status = lambda: {"state": console.lifecycle.state.value}

    console.trigger_fall()

    assert sequence == [
        "stop_companion",
        "wait",
        ("interrupt", "fall_suspected"),
        "manual",
        "prompt",
    ]


def test_fall_manual_mode_keeps_existing_keyboard_control() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.manual_controller = SimpleNamespace(active=True)
    console.fall_manual_controller = SimpleNamespace(active=False)

    assert console._start_fall_manual_mode() is True
    assert console.manual_controller.active is True
    assert console.fall_manual_controller.active is False


def test_f3_submits_departure_voice_without_waiting_for_f2_phase() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.lifecycle = CompetitionLifecycle()
    events: list[str] = []
    console._interaction_flow_controller = SimpleNamespace(
        context=SimpleNamespace(demo_phase="skill3_ready"),
        handle_event=lambda _event, _payload: [],
    )
    console.execute_local_interaction_event = (
        lambda event, *, payload=None: events.append(event)
    )
    console._print_demo_guidance = lambda: None

    result = console.trigger_script_step_from_hotkey("SCENE3_DEPART")

    assert result["accepted"] is True
    assert events == ["OPERATOR_DEPART"]


def test_f2_still_submits_after_f1_voice_playback_failure() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    context = SimpleNamespace(demo_phase="unrelated")
    console.lifecycle = SimpleNamespace(state=CompanionState.IDLE, risk_active=False)
    console._interaction_flow_controller = SimpleNamespace(
        context=context,
        handle_event=lambda _event, _payload: [],
    )
    console._last_script_action = None
    console._demo_event = lambda _message: None
    console._print_demo_guidance = lambda: None
    events: list[str] = []

    def execute_local(event: str, *, payload=None) -> None:
        del payload
        events.append(event)
        if event == "OPERATOR_OUTING_ASSESSMENT":
            raise WirelessCompanionControlError(
                "VOICE_PLAYBACK_FAILED",
                "error",
                503,
            )
        context.demo_phase = "skill3_wait_depart"

    console.execute_local_interaction_event = execute_local

    with pytest.raises(WirelessCompanionControlError):
        console.trigger_script_action(CompetitionAction.SKILL2_REPORT)

    result = console.trigger_script_action(CompetitionAction.MEDICATION_RECHECK)

    assert result["accepted"] is True
    assert events == [
        "OPERATOR_OUTING_ASSESSMENT",
        "OPERATOR_MEDICATION_CHECK",
    ]


def test_reading_event_is_rejected_while_emergency_helping_is_active() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.lifecycle = SimpleNamespace(
        state=CompanionState.ESCALATED_EMERGENCY,
        risk_active=True,
    )
    console._interaction_flow_controller = SimpleNamespace(
        context=SimpleNamespace(safety_state="helping")
    )

    with pytest.raises(WirelessCompanionControlError) as exc_info:
        console.trigger_reading_from_hotkey()

    assert exc_info.value.code == "READING_REJECTED"


def test_f4_stops_motion_before_interrupting_and_playing_formal_voice() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    events: list[object] = []
    console._cancel_pending_motion_actions = (
        lambda *, reason: events.append(("cancel_motion", reason))
    )
    console._interrupt_voice_playback = (
        lambda *, reason: events.append(("interrupt_voice", reason))
    )
    console._run_control_command = (
        lambda command, *, request_id, payload: events.append(("control", command))
        or {"state": "IDLE"}
    )
    context = SimpleNamespace(demo_phase="skill3_first_following")
    console._interaction_flow_controller = SimpleNamespace(context=context)
    console.play_voice_clips = (
        lambda clips, **_kwargs: events.append(("voice", tuple(clips)))
        or {"status": "done"}
    )
    console._print_demo_guidance = lambda: events.append("guidance")

    assert console.stop_from_hotkey() == {"state": "IDLE"}
    assert events.index(("control", "stop_follow")) < events.index(
        ("interrupt_voice", "operator_stop")
    )
    assert events.index(("control", "stop_follow")) < events.index(
        ("voice", ("follow.stop",))
    )
    assert context.demo_phase == "skill3_first_follow_stopped"


def test_f4_only_marks_demo_complete_after_final_follow() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    context = SimpleNamespace(demo_phase="final_following")
    console._interaction_flow_controller = SimpleNamespace(context=context)
    console._cancel_pending_motion_actions = lambda *, reason: 0
    console._interrupt_voice_playback = lambda *, reason: None
    console._run_control_command = (
        lambda command, *, request_id, payload: {"state": "IDLE"}
    )
    console.play_voice_clips = lambda *_args, **_kwargs: {"status": "done"}
    console._print_demo_guidance = lambda: None

    console.stop_from_hotkey()

    assert context.demo_phase == "complete"


def test_priority_hotkey_suppression_is_consumed_once() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)

    console._suppress_buffered_priority_hotkey("Ctrl+F5")

    assert console._consume_priority_hotkey_suppression("Ctrl+F5") is True
    assert console._consume_priority_hotkey_suppression("Ctrl+F5") is False


def test_priority_hotkey_dispatch_uses_direct_handlers() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    calls: list[CompetitionAction] = []
    console.execute_competition_action = lambda action: calls.append(action)

    for label in ("Ctrl+F4", "Ctrl+F6", "Ctrl+F9", "Ctrl+F10", "Ctrl+F12"):
        console._dispatch_priority_hotkey(label)

    assert calls == [
        CompetitionAction.FOLLOW_STOP,
        CompetitionAction.FALL_PROMPT_1,
        CompetitionAction.QUICK_FOLLOW_RECOVERY,
        CompetitionAction.DIRECT_FOLLOW_STOP,
        CompetitionAction.KEYBOARD_CLOSE,
    ]


def test_control_dispatch_error_output_uses_business_status_only(capsys) -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.execute_competition_action = lambda _action: (_ for _ in ()).throw(
        RuntimeError("offline")
    )
    console._demo_rejection = lambda: None

    console._dispatch_priority_hotkey("Ctrl+F1")

    output = capsys.readouterr().out
    assert "系统操作失败:RuntimeError:offline" in output
    for forbidden in ("F1", "F2", "F3", "HOTKEY", "快捷键", "按键", "触发", "键盘操作"):
        assert forbidden not in output


def test_f9_wake_ack_plays_only_the_prepared_wake_reply() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    console.play_voice_clips = (
        lambda clips, **kwargs: calls.append((tuple(clips), kwargs))
        or {"status": "done", "played": 1}
    )
    console._demo_event = lambda _message: None

    result = console.play_xiaokang_wake_ack()

    assert result["status"] == "done"
    assert calls == [
        (
            ("sess.wake_ack",),
            {
                "session_id": "terminal-wake-ack",
                "source": "operator_action",
            },
        )
    ]


def test_priority_hotkeys_consider_voice_preparation_busy() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    console._voice_playback_lock = threading.Lock()
    console._voice_playback_busy = True
    console._voice_playback_active = False
    console._voice_playback_signature = ("outing.start",)
    console._voice_playback_seq = 1
    console._voice_playback_generation = 0

    assert console.is_voice_playback_active() is False
    assert console.is_voice_playback_busy() is True


def test_reading_clears_false_alarm_risk_and_runs_local_reading_event() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.lifecycle = CompetitionLifecycle()
    console.lifecycle.ingest_fall(incident_id="hotkey-fall-2", confirmed=False)
    console._was_following_before_fall = True
    console._emergency_voice_cancel = threading.Event()
    stop_calls: list[bool] = []
    console.stop_motion = lambda: stop_calls.append(True)
    console._wait_for_motion_stop = lambda: None
    events: list[tuple[str, dict]] = []
    console.execute_local_interaction_event = (
        lambda event, *, payload=None: events.append((event, dict(payload or {})))
    )
    console.companion_status = lambda: {"state": console.lifecycle.state.value}

    result = console.trigger_reading_from_hotkey()

    assert result["readingTriggered"] is True
    assert console.lifecycle.risk_active is False
    assert console.lifecycle.state is CompanionState.WAIT_RESUME
    assert stop_calls == [True]
    assert console._emergency_voice_cancel.is_set()
    assert events == [("NORMAL_ACTIVITY_READING", {})]


def test_reading_creates_new_silent_event_after_f9_cleared_risk() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.lifecycle = CompetitionLifecycle()
    console._was_following_before_fall = False
    console._emergency_voice_cancel = threading.Event()
    stop_calls: list[bool] = []
    console.stop_motion = lambda: stop_calls.append(True)
    console._wait_for_motion_stop = lambda: None
    events: list[str] = []
    console.execute_local_interaction_event = (
        lambda event, *, payload=None: events.append(event)
    )
    console.companion_status = lambda: {"state": console.lifecycle.state.value}

    result = console.trigger_reading_from_hotkey()

    assert result["readingTriggered"] is True
    assert console.lifecycle.risk_active is False
    assert stop_calls == [True]
    assert events == ["NORMAL_ACTIVITY_READING"]


def test_console_start_ignores_removed_manual_confirmation_switch(
    monkeypatch, capsys
) -> None:
    console = _start_command_console(manual_confirm_start=True)
    commands = iter(("START", "EXIT"))
    prompts: list[str] = []

    def command_input(prompt: str) -> str:
        prompts.append(prompt)
        return next(commands)

    monkeypatch.setattr("builtins.input", command_input)

    assert console.run() == 0

    assert prompts == ["wireless> ", "wireless> "]
    assert "START accepted -> FOLLOWING" in capsys.readouterr().out


def test_console_start_reports_lifecycle_rejection_reason_without_prompt(
    monkeypatch, capsys
) -> None:
    console = _start_command_console(manual_confirm_start=False)

    def reject_start(*, before_start=None):
        from app.webrtc.video_bridge import WirelessCompanionControlError

        raise WirelessCompanionControlError(
            "UWB_NOT_READY", "uwb_not_fresh", 503
        )

    console.start_companion = reject_start
    commands = iter(("START", "EXIT"))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    assert console.run() == 0

    assert (
        "ACTION_REJECTED:DIRECT_FOLLOW_START:UWB_NOT_READY:uwb_not_fresh"
        in capsys.readouterr().out
    )


def test_onsite_hotkey_scan_codes_stay_small_and_memorable() -> None:
    assert _console_hotkey_command(";") is None
    assert _console_hotkey_command("^") == ("Ctrl+F1", "SKILL2_REPORT")
    assert _console_hotkey_command("_") == ("Ctrl+F2", "MEDICATION_RECHECK")
    assert _console_hotkey_command("`") == ("Ctrl+F3", "OUTING_START")
    assert _console_hotkey_command("a") == ("Ctrl+F4", "FOLLOW_STOP")
    assert _console_hotkey_command("b") == ("Ctrl+F5", "FOLLOW_RESUME")
    assert _console_hotkey_command("c") == ("Ctrl+F6", "FALL_PROMPT_1")
    assert _console_hotkey_command("d") == ("Ctrl+F7", "FALL_HELP")
    assert _console_hotkey_command("e") == ("Ctrl+F8", "XIAOKANG_WAKE_ACK")
    assert _console_hotkey_command("f") == ("Ctrl+F9", "QUICK_FOLLOW_RECOVERY")
    assert _console_hotkey_command("g") == ("Ctrl+F10", "DIRECT_FOLLOW_STOP")
    assert _console_hotkey_command("\x89") == ("Ctrl+F11", "MANUAL_TAKEOVER")
    assert _console_hotkey_command("\x8a") == ("Ctrl+F12", "KEYBOARD_CLOSE")
    assert _console_hotkey_command("f", shift_down=True) == (
        "Ctrl+Shift+F9",
        "DEMO_RESET",
    )
    assert _console_hotkey_command("g", shift_down=True) == (
        "Ctrl+Shift+F10",
        "VOICE_RECOVERY",
    )


def test_physical_hotkeys_require_exact_modifiers() -> None:
    assert _hotkey_label_for_keypress("F1", ctrl_down=False, shift_down=False) is None
    assert _hotkey_label_for_keypress("F1", ctrl_down=True, shift_down=False) == (
        "CTRL+F1"
    )
    assert _hotkey_label_for_keypress("F1", ctrl_down=True, shift_down=True) is None
    assert _hotkey_label_for_keypress("F9", ctrl_down=True, shift_down=False) == (
        "CTRL+F9"
    )
    assert _hotkey_label_for_keypress("F9", ctrl_down=True, shift_down=True) == (
        "CTRL+SHIFT+F9"
    )
    assert _hotkey_label_for_keypress("F10", ctrl_down=True, shift_down=True) == (
        "CTRL+SHIFT+F10"
    )


def test_typed_function_key_names_are_command_aliases() -> None:
    assert _normalize_console_command("F1") == "F1"
    assert _normalize_console_command("Ctrl+F1") == "SKILL2_REPORT"
    assert _normalize_console_command("Ctrl+F3") == "OUTING_START"
    assert _normalize_console_command("Ctrl+F4") == "FOLLOW_STOP"
    assert _normalize_console_command("Ctrl+F7") == "FALL_HELP"
    assert _normalize_console_command("Ctrl+F8") == "XIAOKANG_WAKE_ACK"
    assert _normalize_console_command("Ctrl+F9") == "QUICK_FOLLOW_RECOVERY"
    assert _normalize_console_command("Ctrl+F10") == "DIRECT_FOLLOW_STOP"
    assert _normalize_console_command("Ctrl+F11") == "MANUAL_TAKEOVER"
    assert _normalize_console_command("TOGGLE_VOICE_LISTENER") == "VOICE_LISTENER_TOGGLE"
    assert _normalize_console_command(" Ctrl+F12 ") == "KEYBOARD_CLOSE"
    assert _normalize_console_command("Ctrl+Shift+F9") == "DEMO_RESET"
    assert _normalize_console_command("Ctrl+Shift+F10") == "VOICE_RECOVERY"
    assert _normalize_console_command("START") == "DIRECT_FOLLOW_START"
    assert _normalize_console_command("STOP") == "DIRECT_FOLLOW_STOP"
    assert _normalize_console_command("RESET") == "DEMO_RESET"
    assert _normalize_console_command("SCENE2_ASSESSMENT") == "SKILL2_REPORT"
    assert _normalize_console_command("STATUS") == "STATUS"


def test_hotkey_remap_changes_only_the_binding_table(monkeypatch) -> None:
    monkeypatch.setitem(
        HOTKEY_ACTIONS,
        "CTRL+F4",
        CompetitionAction.FOLLOW_STOP,
    )

    assert _console_hotkey_command("a") == ("Ctrl+F4", "FOLLOW_STOP")
    assert _normalize_console_command("Ctrl+F4") == "FOLLOW_STOP"
    assert CompetitionAction.SKILL2_REPORT.value == "SKILL2_REPORT"
    assert HOTKEY_ACTIONS["CTRL+F12"] is CompetitionAction.KEYBOARD_CLOSE


def test_competition_actions_route_to_business_methods_only() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    calls: list[object] = []
    console.trigger_script_action = lambda action, **_kwargs: calls.append(action)
    console.start_or_resume_follow = lambda **kwargs: calls.append(
        ("follow_resume", kwargs)
    )
    console.stop_follow = lambda **kwargs: calls.append(("follow_stop", kwargs))
    console.trigger_fall = lambda: calls.append("fall_prompt_1")
    console._play_fall_voice_fallback = lambda clips: calls.append(tuple(clips))
    console.advance_fall_timeout = lambda *, stage: calls.append(("fall_stage", stage))
    console.recover_fall = lambda: calls.append("fall_recover")
    console.play_xiaokang_wake_ack = lambda: calls.append("xiaokang_wake_ack")
    console.trigger_reading = lambda: calls.append("reading_normal")
    console.manual_takeover = lambda: calls.append("manual_takeover")
    console.reset_demo = lambda: calls.append("demo_reset")
    console.toggle_voice_listener = lambda: calls.append("voice_toggle")
    console.quick_follow_recovery = lambda: calls.append("quick_follow_recovery")
    console.recover_voice_pipeline = lambda: calls.append("voice_recovery")
    console.close_keyboard_control = lambda: calls.append("keyboard_close")

    for action in CompetitionAction:
        console.execute_competition_action(action)

    assert calls == [
        CompetitionAction.SKILL2_REPORT,
        CompetitionAction.MEDICATION_RECHECK,
        CompetitionAction.OUTING_START,
        ("follow_resume", {}),
        ("follow_stop", {}),
        "fall_prompt_1",
        ("fall.confirm",),
        ("fall_stage", 1),
        ("fall_stage", 2),
        "fall_recover",
        "xiaokang_wake_ack",
        "reading_normal",
        "manual_takeover",
        "demo_reset",
        "voice_toggle",
        "quick_follow_recovery",
        "voice_recovery",
        "keyboard_close",
        ("follow_resume", {"announce": False}),
        ("follow_stop", {"announce": False}),
    ]


def test_f10_manual_takeover_interrupts_voice_and_enters_quiet_keyboard_mode() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    calls: list[object] = []
    console._cancel_pending_motion_actions = (
        lambda *, reason: calls.append(("cancel_motion", reason))
    )
    console._cancel_emergency_voice = (
        lambda *, reason: calls.append(("cancel_emergency", reason))
    )
    console._interrupt_voice_playback = (
        lambda *, reason: calls.append(("interrupt_voice", reason))
    )
    console._manual_console = (
        lambda *, demo_takeover=False: calls.append(
            ("manual_console", demo_takeover)
        )
    )

    console.manual_takeover()

    assert calls == [
        ("cancel_motion", "manual_takeover"),
        ("cancel_emergency", "manual_takeover"),
        ("interrupt_voice", "manual_takeover"),
        ("manual_console", True),
    ]


def test_f10_uses_fall_camera_control_while_risk_state_is_active() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.lifecycle = SimpleNamespace(risk_active=True)
    calls: list[object] = []
    console._cancel_pending_motion_actions = lambda *, reason: calls.append(
        ("cancel_motion", reason)
    )
    console._cancel_emergency_voice = lambda *, reason: calls.append(
        ("cancel_emergency", reason)
    )
    console._interrupt_voice_playback = lambda *, reason: calls.append(
        ("interrupt_voice", reason)
    )
    console._start_fall_manual_mode = lambda: calls.append("fall_manual") or True
    console._demo_event = lambda message: calls.append(("event", message))
    console._manual_console = lambda **_kwargs: pytest.fail(
        "fall risk must use the dedicated camera control"
    )

    console.manual_takeover()

    assert calls == [
        ("cancel_motion", "manual_takeover"),
        ("cancel_emergency", "manual_takeover"),
        ("interrupt_voice", "manual_takeover"),
        "fall_manual",
        ("event", "检测到异常情况"),
    ]


def test_repeated_f10_does_not_start_a_second_manual_control_loop() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    console._manual_takeover_lock = threading.Lock()
    console._manual_takeover_lock.acquire()
    console._cancel_pending_motion_actions = lambda **_kwargs: pytest.fail(
        "duplicate F10 must not execute"
    )

    try:
        console.manual_takeover()
    finally:
        console._manual_takeover_lock.release()


def test_keyboard_interaction_runs_locally_without_transport_publish(
    monkeypatch,
) -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    transport = MockTransport()
    played: list[tuple[str, ...]] = []
    decision = SimpleNamespace(
        intent="outing_request",
        action=None,
        clips=("outing.allow.health_good",),
        heart_rate=78,
        health_status="good",
        weather="sunny",
        temperature=23,
    )
    console._interaction_flow_controller = SimpleNamespace(
        handle_event=lambda event, payload: [decision]
    )
    console._go2_asr_bridge = None
    console.play_voice_clips = (
        lambda clips, **_kwargs: played.append(tuple(clips))
        or {"status": "done", "played": len(clips)}
    )
    monkeypatch.setattr("tools.go2_wireless_runtime.time.sleep", lambda _seconds: None)

    result = console.execute_local_interaction_event(
        "OPERATOR_OUTING_ASSESSMENT"
    )

    assert result["decisions"] == 1
    assert played == [("outing.allow.health_good",)]
    assert transport.published == []


def test_local_outing_start_waits_for_voice_then_calls_local_control(
    monkeypatch,
) -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    order: list[str] = []
    decision = SimpleNamespace(
        intent="outing_start",
        action="start_follow",
        clips=("outing.start",),
        heart_rate=None,
        health_status="good",
        weather=None,
        temperature=None,
    )
    console.lifecycle = SimpleNamespace(risk_active=False)
    console._interaction_flow_controller = SimpleNamespace(
        handle_event=lambda event, payload: [decision]
    )
    console._go2_asr_bridge = None
    console.play_voice_clips = (
        lambda clips, **_kwargs: order.append("voice")
        or {"status": "done", "played": len(clips)}
    )
    console._run_control_command = (
        lambda command, *, request_id, payload: order.append(command) or {}
    )
    monkeypatch.setattr("tools.go2_wireless_runtime.time.sleep", lambda _seconds: None)

    result = console.execute_local_interaction_event("OPERATOR_DEPART")

    assert order == ["voice", "start_follow"]
    assert result["actions"] == ["start_follow"]


def test_demo_output_router_hides_debug_and_keeps_full_log(tmp_path, capsys) -> None:
    debug_log = tmp_path / "runtime_debug.log"
    output = RuntimeOutputSession(debug_log, demo_console=True)
    output.install()
    try:
        print("[HOTKEY] F3 -> SCENE2_ASSESSMENT")
        print("[VAD] short_silence 200ms")
        _emit_demo_console("外出健康评估开始", timestamp=False)
    finally:
        output.restore()

    visible = capsys.readouterr().out
    logged = debug_log.read_text(encoding="utf-8")
    assert visible == "外出健康评估开始\n"
    assert "[HOTKEY] F3" in logged
    assert "[VAD] short_silence" in logged
    assert "外出健康评估开始" in logged
    assert "GO2_DEMO" not in logged


def test_debug_output_router_shows_all_lines_without_internal_marker(
    tmp_path, capsys
) -> None:
    debug_log = tmp_path / "runtime_debug.log"
    output = RuntimeOutputSession(debug_log, demo_console=False)
    output.install()
    try:
        print("[HOTKEY] F3 -> SCENE2_ASSESSMENT")
        _emit_demo_console("外出健康评估开始", timestamp=False)
    finally:
        output.restore()

    visible = capsys.readouterr().out
    assert "[HOTKEY] F3" in visible
    assert "外出健康评估开始" in visible
    assert "GO2_DEMO" not in visible


def test_demo_command_input_has_no_prompt_or_typed_hotkey_echo(monkeypatch, capsys) -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.demo_console = True
    prompts: list[str] = []
    monkeypatch.setattr(
        "tools.go2_wireless_runtime._windows_hotkeys_available",
        lambda: False,
    )
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: prompts.append(prompt) or "Ctrl+F1",
    )

    assert console._read_command("wireless> ") == "SKILL2_REPORT"
    assert prompts == [""]
    assert "Ctrl+F1" not in capsys.readouterr().out


def test_f1_advances_directly_to_f2_without_exposing_hotkey_names(monkeypatch) -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    context = SimpleNamespace(demo_phase="skill2_ready", skill2_done=False)
    console.demo_console = True
    console.lifecycle = SimpleNamespace(risk_active=False, state=CompanionState.IDLE)
    console._interaction_flow_controller = SimpleNamespace(
        context=context,
        handle_event=lambda _event, _payload: [],
    )
    console._print_demo_guidance = lambda: None
    visible_events: list[str] = []
    monkeypatch.setattr(
        "tools.go2_wireless_runtime._emit_demo_console",
        lambda message, **_kwargs: visible_events.append(message),
    )

    def execute_local(event: str, *, payload=None) -> None:
        del payload
        if event == "OPERATOR_OUTING_ASSESSMENT":
            context.skill2_done = True
            context.demo_phase = "skill3_ready"
        elif event == "OPERATOR_MEDICATION_CHECK":
            context.demo_phase = "skill3_wait_depart"

    console.execute_local_interaction_event = execute_local

    assessment = console.trigger_script_step_from_hotkey("SCENE2_ASSESSMENT")
    medication_check = console.trigger_script_step_from_hotkey(
        "SCENE3_MEDICATION_CHECK"
    )

    assert assessment["phase"] == "skill3_ready"
    assert context.skill2_done is True
    assert medication_check["phase"] == "skill3_wait_depart"
    assert visible_events == [
        "外出健康评估开始",
        "健康状态数据已获取",
        "外出条件满足",
        "出行前健康复查开始",
        "等待服药确认",
    ]
    assert not any("F1" in message or "F2" in message for message in visible_events)


def test_f1_waits_for_live_weather_before_assembling_assessment() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    context = SimpleNamespace(demo_phase="skill2_ready", skill2_done=False)
    weather = SimpleNamespace(
        condition=SimpleNamespace(value="cloudy"),
        temperature=28,
        error=None,
    )
    order: list[str] = []
    wait_calls: list[float] = []
    console.lifecycle = SimpleNamespace(risk_active=False)
    console._interaction_flow_controller = SimpleNamespace(
        context=context,
        weather_provider=SimpleNamespace(
            wait_for_live_weather=lambda timeout: (
                wait_calls.append(timeout),
                order.append("weather"),
                weather,
            )[-1]
        ),
        handle_event=lambda _event, _payload: [],
    )
    console._demo_event = lambda _message: None
    console._print_demo_guidance = lambda: None
    console._print_operator_line = lambda _message: None

    def execute_local(event: str, *, payload=None) -> None:
        del payload
        assert event == "OPERATOR_OUTING_ASSESSMENT"
        order.append("assessment")
        context.skill2_done = True
        context.demo_phase = "skill3_ready"

    console.execute_local_interaction_event = execute_local

    result = console.trigger_script_action(CompetitionAction.SKILL2_REPORT)

    assert result["phase"] == "skill3_ready"
    assert wait_calls == [1.8]
    assert order == ["weather", "assessment"]


def test_current_business_key_replay_gets_unique_session_without_state_change() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    context = SimpleNamespace(demo_phase="skill3_ready")
    console.lifecycle = SimpleNamespace(risk_active=False)
    console._interaction_flow_controller = SimpleNamespace(
        context=context,
        handle_event=lambda _event, _payload: [],
    )
    console._last_script_action = CompetitionAction.SKILL2_REPORT
    console._demo_event = lambda _message: None
    dispatched: list[tuple[str, dict]] = []
    console.execute_local_interaction_event = (
        lambda event, *, payload=None: dispatched.append(
            (event, dict(payload or {}))
        )
    )

    result = console.trigger_script_action(CompetitionAction.SKILL2_REPORT)

    assert result["replay"] is True
    assert context.demo_phase == "skill3_ready"
    assert dispatched[0][0] == "OPERATOR_OUTING_ASSESSMENT"
    assert dispatched[0][1]["replay"] is True
    assert dispatched[0][1]["session_id"].startswith(
        "terminal-operator_outing_assessment-replay-"
    )


def test_demo_uwb_status_is_compact_and_throttled(monkeypatch) -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    console._state_lock = threading.RLock()
    console._follow_status = {}
    console._last_follow_progress_log_at = 0.0
    visible_events: list[str] = []
    console._demo_event = visible_events.append
    times = iter((10.0, 10.2, 11.2))
    monkeypatch.setattr(
        "tools.go2_wireless_runtime.time.monotonic",
        lambda: next(times),
    )
    row = {"distance_m": 1.42, "bearing_deg": -8.3, "state": "TRACKING"}

    console._record_follow_progress(row)
    console._record_follow_progress(row)
    console._record_follow_progress(row)

    assert visible_events == [
        "UWB READY | Target VALID | Distance 1.42 m | Direction -8.3°",
        "UWB READY | Target VALID | Distance 1.42 m | Direction -8.3°",
    ]


def test_f12_outside_keyboard_control_does_not_stop_motion_or_runtime() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    shutdown_calls: list[bool] = []
    stop_calls: list[bool] = []
    console.runtime = SimpleNamespace(
        request_shutdown=lambda: shutdown_calls.append(True),
        status=lambda: {"uwb": {}},
    )
    console.stop_motion = lambda: stop_calls.append(True)
    console.fall_manual_controller = SimpleNamespace(active=False)
    console.manual_controller = SimpleNamespace(active=False)
    console.companion_status = lambda: {
        "state": "FOLLOWING",
        "runtime_active": True,
    }

    status = console.close_keyboard_control()

    assert stop_calls == []
    assert shutdown_calls == []
    assert status == {"state": "FOLLOWING", "runtime_active": True}


def test_ctrl_fall_voice_hotkeys_only_play_fallback_clips(capsys) -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    played: list[list[str]] = []
    console.play_voice_clips = (
        lambda clips: played.append(list(clips))
        or {"clips": list(clips), "played": len(clips), "status": "done"}
    )

    console._play_fall_voice_fallback(["fall.confirm"])
    console._play_fall_voice_fallback(["fall.confirm.second"])
    console._play_fall_voice_fallback(["fall.alert.sound", "fall.help.broadcast"])

    assert played == [
        ["fall.confirm"],
        ["fall.confirm.second"],
        ["fall.alert.sound", "fall.help.broadcast"],
    ]
    assert '"status": "done"' in capsys.readouterr().out


def test_voice_business_listener_toggle_does_not_shutdown_runtime() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    shutdown_calls: list[bool] = []
    console.runtime = SimpleNamespace(request_shutdown=lambda: shutdown_calls.append(True))

    class Manager:
        listener_enabled = True

        def __init__(self) -> None:
            self.toggled = 0

        def toggle_listener(self):
            self.listener_enabled = not self.listener_enabled
            self.toggled += 1
            return self.listener_enabled, []

    class Flow:
        def __init__(self) -> None:
            self.cleared = 0

        def clear_pending_reply(self) -> None:
            self.cleared += 1

    manager = Manager()
    flow = Flow()
    console._voice_session_manager = manager
    console._interaction_flow_controller = flow

    assert console.toggle_voice_listener() is False
    assert manager.toggled == 1
    assert flow.cleared == 1
    assert shutdown_calls == []

    assert console.toggle_voice_listener() is True
    assert manager.toggled == 2
    assert flow.cleared == 1
    assert shutdown_calls == []


def test_f11_quick_follow_recovery_releases_resets_and_starts_without_speech() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    calls: list[object] = []
    console.close_keyboard_control = lambda: calls.append("keyboard_close")
    console.reset_demo = lambda: calls.append("demo_reset")
    console.start_or_resume_follow = lambda **kwargs: (
        calls.append(("follow_resume", kwargs)) or {"state": "FOLLOWING"}
    )

    result = console.quick_follow_recovery()

    assert result == {"state": "FOLLOWING"}
    assert calls == [
        "keyboard_close",
        "demo_reset",
        ("follow_resume", {"announce": False}),
    ]


def test_f7_and_f8_advance_real_fall_state() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.lifecycle = CompetitionLifecycle()
    console.lifecycle.ingest_fall(incident_id="manual-fall", confirmed=False)
    console.stop_motion = lambda: None
    console._record_lifecycle_actions = lambda _payload: None
    console.companion_status = lambda: {"state": console.lifecycle.state.value}
    events: list[tuple[str, dict]] = []
    console.execute_local_interaction_event = (
        lambda event, *, payload=None: events.append((event, dict(payload or {})))
    )

    first = console.advance_fall_timeout_from_hotkey(stage=1)
    second = console.advance_fall_timeout_from_hotkey(stage=2)

    assert first["lifecycle"]["state"] == "RECHECK"
    assert second["lifecycle"]["state"] == "ESCALATED_EMERGENCY"
    assert events == [
        ("FALL_RESPONSE_TIMEOUT", {"stage": 1}),
        ("FALL_RESPONSE_TIMEOUT", {"stage": 2}),
    ]


def test_f7_skips_second_prompt_and_enters_emergency_help_directly() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.lifecycle = CompetitionLifecycle()
    console.lifecycle.ingest_fall(incident_id="direct-help", confirmed=False)
    console.stop_motion = lambda: None
    console._record_lifecycle_actions = lambda _payload: None
    console.companion_status = lambda: {"state": console.lifecycle.state.value}
    events: list[tuple[str, dict]] = []
    console.execute_local_interaction_event = (
        lambda event, *, payload=None: events.append((event, dict(payload or {})))
    )

    result = console.advance_fall_timeout(stage=2)

    assert result["lifecycle"]["state"] == "ESCALATED_EMERGENCY"
    assert console.lifecycle.snapshot().response_attempts == 2
    assert events == [
        (
            "FALL_RESPONSE_TIMEOUT",
            {"stage": 2, "skip_second_prompt": True},
        )
    ]


def test_f7_and_f8_replay_audio_without_advancing_fall_state_twice() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.lifecycle = CompetitionLifecycle()
    console.lifecycle.ingest_fall(incident_id="replay-fall", confirmed=False)
    console.lifecycle.no_response()
    played: list[tuple[str, ...]] = []
    console.play_voice_clips = (
        lambda clips, **_kwargs: played.append(tuple(clips))
        or {"status": "done", "played": len(clips)}
    )
    console.companion_status = lambda: {"state": console.lifecycle.state.value}

    second_prompt = console.advance_fall_timeout(stage=1)

    assert second_prompt["replay"] is True
    assert console.lifecycle.state is CompanionState.RECHECK
    assert console.lifecycle.snapshot().response_attempts == 1

    console.lifecycle.no_response()
    help_broadcast = console.advance_fall_timeout(stage=2)

    assert help_broadcast["replay"] is True
    assert console.lifecycle.state is CompanionState.ESCALATED_EMERGENCY
    assert console.lifecycle.snapshot().response_attempts == 2
    assert played == [
        ("fall.confirm.second",),
        ("fall.alert.sound", "fall.help.broadcast"),
    ]


def test_f12_closes_keyboard_control_without_creating_a_motion_lock() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    console.fall_manual_controller = SimpleNamespace(active=False)
    console.manual_controller = SimpleNamespace(active=True)
    calls: list[object] = []

    def release_manual(*, quiet=False):
        calls.append(("release", quiet))
        console.manual_controller.active = False
        return {"state": "IDLE"}

    console.release_manual = release_manual
    console._stop_fall_manual_mode = lambda *, reason: calls.append(
        ("fall_release", reason)
    )
    console._demo_event = lambda message: calls.append(("event", message))
    console.companion_status = lambda: {"state": "IDLE"}

    result = console.close_keyboard_control()

    assert result == {"state": "IDLE"}
    assert calls == [("release", True), ("event", "键盘控制已关闭")]


def test_voice_recovery_preserves_demo_context_and_resets_live_voice() -> None:
    console = RuntimeConsole.__new__(RuntimeConsole)
    console._state_lock = threading.RLock()
    console._local_voice_agent = SimpleNamespace(
        cancel_pending_actions=lambda **_kwargs: 1
    )
    stopped: list[str] = []
    console.runtime = SimpleNamespace(
        stop_audio_playback=lambda *, reason, timeout_seconds: stopped.append(reason)
    )

    class Bridge:
        def __init__(self) -> None:
            self.quiet_gate_armed = False

        def clear_pending_audio(self) -> int:
            return 7

        def arm_post_playback_quiet_gate(self) -> None:
            self.quiet_gate_armed = True

    class Manager:
        def __init__(self) -> None:
            self.reasons: list[str] = []

        def recover_to_wake_guard(self, *, reason: str) -> None:
            self.reasons.append(reason)

    bridge = Bridge()
    manager = Manager()
    console._go2_asr_bridge = bridge
    console._voice_session_manager = manager
    flow = SimpleNamespace(
        context=SimpleNamespace(
            medication_reminded=True,
            medication_taken=False,
            outing_state="wait_medication",
        )
    )
    console._interaction_flow_controller = flow

    result = console.recover_voice_pipeline()

    assert result["pcmFramesCleared"] == 7
    assert bridge.quiet_gate_armed is True
    assert manager.reasons == ["voice_recovery"]
    assert flow.context.medication_reminded is True
    assert flow.context.medication_taken is False
    assert flow.context.outing_state == "wait_medication"
    assert stopped == ["interrupt:voice_recovery"]


def test_console_walk_follow_command_dispatches_without_changing_manual(
    monkeypatch,
) -> None:
    console = _start_command_console(manual_confirm_start=False)
    calls: list[str] = []
    console._walk_follow = lambda: calls.append("walk_follow")
    commands = iter(("WALK_FOLLOW", "EXIT"))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    assert console.run() == 0
    assert calls == ["walk_follow"]
    assert console.shutdown_calls == [True]


def test_console_hotkey_safety_event_does_not_use_interaction_transport(
    monkeypatch,
) -> None:
    console = _start_command_console(manual_confirm_start=False)
    transport = MockTransport()
    console.service = SimpleNamespace(settings=SimpleNamespace(robot_id="DOG-LJG-001"))
    console._wait_for_motion_stop = lambda: None
    console._record_lifecycle_actions = lambda _payload: None
    console.companion_status = lambda: {"state": console.lifecycle.state.value}
    local_events: list[str] = []
    console.execute_local_interaction_event = (
        lambda event, *, payload=None: local_events.append(event)
    )
    commands = iter(("FALL_SUSPECTED", "EXIT"))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    assert console.run() == 0

    assert local_events == ["FALL_SUSPECTED"]
    assert transport.published == []


class FakeRuntime:
    def status(self) -> dict:
        return {
            "robotIp": "192.168.8.252",
            "connected": True,
            "connectionCount": 1,
            "dataChannelReady": True,
            "sportStateReady": True,
            "videoReady": True,
        }


class FakeUwbRuntime:
    def __init__(self, *, include_error_state: bool = True) -> None:
        self.calls = 0
        self.include_error_state = include_error_state

    def status(self) -> dict:
        self.calls += 1
        count = 0 if self.calls == 1 else 2
        return {
            "uwb": {
                "topic": "rt/uwbstate" if count else None,
                "sampleCount": count,
                "ageMs": 10.0 if count else None,
                "fresh": bool(count),
                "fields": (
                    dict({
                        "distance_est": 1.8,
                        "orientation_est": -0.3,
                        "yaw_est": 0.1,
                        "enabled_from_app": 1,
                    }, **({"error_state": 0} if self.include_error_state else {}))
                    if count
                    else None
                ),
            },
            "multipleState": {
                "received": True,
                "sampleCount": 1,
                "uwbSwitch": True,
            },
            "lowState": {"received": True, "sampleCount": 1},
            "sportStateReady": True,
            "videoReady": True,
            "connectionCount": 1,
            "commandCounts": {},
        }


def action_result(action: str, value: float) -> MotionActionResult:
    return MotionActionResult(
        action=action,
        requested_value=value,
        actual_value=value,
        error=0.0,
        unit="deg" if "turn" in action else "s" if action == "wait" else "m",
        duration_s=0.01,
        completed=True,
        reason="target_reached",
        start_pose=None,
        end_pose=None,
    )


class FakeController:
    def __init__(self) -> None:
        self.calls: list[tuple[str, float | None]] = []
        self.stops = 0

    def _action(self, name: str, value: float) -> MotionActionResult:
        self.calls.append((name, value))
        return action_result(name, value)

    def forward(self, value): return self._action("forward", value)
    def backward(self, value): return self._action("backward", value)
    def move_left(self, value): return self._action("move_left", value)
    def move_right(self, value): return self._action("move_right", value)
    def turn_left(self, value): return self._action("turn_left", value)
    def turn_right(self, value): return self._action("turn_right", value)
    def turn_clockwise(self, value): return self._action("turn_clockwise", value)
    def wait(self, value): return self._action("wait", value)

    def stop(self) -> int:
        self.stops += 1
        return 0

    def pose(self, **parameters):
        return self._action("pose", parameters["duration_s"])

    def play_audio(self, path):
        self.calls.append(("play_audio", path))

    def speak(self, text):
        self.calls.append(("speak", text))


def test_competition_demo_uses_yaml_then_stops_without_closing_video() -> None:
    controller = FakeController()
    runtime = FakeRuntime()
    console = RuntimeConsole(
        runtime,
        SimpleNamespace(),
        controller,
        video_host="0.0.0.0",
        video_port=8093,
        lan_ip="192.168.8.254",
    )

    console._phone_demo()

    assert controller.calls[0] == ("forward", 0.8)
    assert controller.calls[1] == ("turn_clockwise", 90.0)
    assert controller.calls[3] == ("turn_clockwise", 105.0)
    assert controller.stops >= 1
    assert runtime.status()["connected"] is True
    assert runtime.status()["videoReady"] is True


def test_uwb_gate_is_subscriber_only_and_reports_pass(capsys) -> None:
    runtime = FakeUwbRuntime()
    console = RuntimeConsole(
        runtime,
        SimpleNamespace(),
        FakeController(),
        video_host="127.0.0.1",
        video_port=8093,
        lan_ip="192.168.8.254",
    )

    console._uwb_gate(seconds=0.01)

    output = capsys.readouterr().out
    assert "WEBRTC_UWB_READONLY_PASS" in output
    assert '"transportPassed": true' in output
    assert '"followInputReady": true' in output
    assert '"moveCommandsSentDuringGate": 0' in output
    assert '"sportCommandsSentDuringGate": {}' in output


def test_uwb_gate_passes_transport_but_not_follow_when_error_state_is_omitted(
    capsys,
) -> None:
    runtime = FakeUwbRuntime(include_error_state=False)
    console = RuntimeConsole(
        runtime,
        SimpleNamespace(),
        FakeController(),
        video_host="127.0.0.1",
        video_port=8093,
        lan_ip="192.168.8.254",
    )

    console._uwb_gate(seconds=0.01)

    output = capsys.readouterr().out
    assert "WEBRTC_UWB_READONLY_PASS_INPUT_NOT_READY" in output
    assert '"schemaValid": true' in output
    assert '"errorStateAvailable": false' in output
    assert '"transportPassed": true' in output
    assert '"followInputReady": false' in output
    assert '"sportCommandsSentDuringGate": {}' in output


class _HttpControlRuntime:
    def __init__(self) -> None:
        self.companion_activation_count = 0
        self.companion_deactivation_count = 0
        self.voice_activation_count = 0
        self.voice_deactivation_count = 0

    def activate_companion_inputs(
        self, *, timeout_seconds: float, enable_multiple_state: bool
    ) -> dict:
        assert timeout_seconds == pytest.approx(5.0)
        assert enable_multiple_state is False
        self.companion_activation_count += 1
        return self.status()

    def deactivate_companion_inputs(self) -> None:
        self.companion_deactivation_count += 1

    def activate_voice(self) -> dict:
        self.voice_activation_count += 1
        return self.status()

    def deactivate_voice(self) -> None:
        self.voice_deactivation_count += 1

    def status(self) -> dict:
        return {
            "connected": True,
            "connectionCount": 1,
            "sportStateReady": True,
            "videoReady": True,
            "lowState": {"fresh": True},
            "multipleState": {"uwbSwitch": True},
            "uwb": {
                "ageMs": 20.0,
                "fresh": True,
                "fields": {
                    "enabled_from_app": 1,
                    "error_state": 0,
                    "distance_est": 1.4,
                    "orientation_est": 0.1,
                },
            },
        }


class _HttpControlController:
    def __init__(self) -> None:
        self.stop_count = 0

    def clear_emergency_stop(self) -> None:
        return None

    def emergency_stop(self) -> int:
        self.stop_count += 1
        return 0


class _HttpFollowSource:
    def __init__(self) -> None:
        self.active = False

    def set_follow_active(self, active: bool) -> None:
        self.active = active

    def current_state(self) -> FollowTargetState:
        return FollowTargetState(
            target_valid=True,
            follow_active=self.active,
            monitoring_active=True,
            bearing_deg=-5.0,
            distance_m=1.4,
        )


class _HttpFollowForwarder:
    def __init__(self) -> None:
        self.start_count = 0
        self.close_count = 0

    def start(self) -> None:
        self.start_count += 1

    def close(self) -> None:
        self.close_count += 1


class _HttpFollowSession:
    def __init__(self, cancel: threading.Event) -> None:
        self.cancel = cancel

    def preflight(self) -> None:
        return None

    def run(self, *, run_until_stopped: bool):
        assert run_until_stopped is True
        while not self.cancel.wait(0.01):
            pass
        return SimpleNamespace(
            reason="operator_stop",
            uwb_dropout_count=0,
            auto_recovery_count=0,
            sport_state_dropout_count=0,
            sport_state_auto_recovery_count=0,
            uwb_stale_escalation_count=0,
            last_dropout_duration_seconds=None,
            maximum_dropout_duration_seconds=0.0,
            to_dict=lambda: {"reason": "operator_stop"},
        )


def test_companion_control_supports_repeated_start_stop_cycles(monkeypatch) -> None:
    runtime = _HttpControlRuntime()
    controller = _HttpControlController()
    source = _HttpFollowSource()
    forwarder = _HttpFollowForwarder()
    service = SimpleNamespace(
        settings=SimpleNamespace(
            robot_id="go2_edu_01",
            max_vx=0.504,
            max_wz=1.10,
            uwb_bearing_sign=1,
            uwb_bearing_zero_offset_rad=0.0,
        )
    )
    console = RuntimeConsole(
        runtime,
        service,
        controller,
        video_host="0.0.0.0",
        video_port=8093,
        lan_ip="192.168.8.254",
        follow_target_source=source,
        follow_target_forwarder=forwarder,
    )
    monkeypatch.setattr(
        console,
        "_build_follow_session",
        lambda: _HttpFollowSession(console._motion_cancel),
    )

    adapter = Go2ControlAdapter(
        start_follow=lambda _message: console.start_companion(),
        stop_follow=lambda _message: console.stop_companion(),
        resume_follow=lambda _message: console.resume_companion(),
        play_clips=lambda _message: {"status": "done", "played": 0},
        ping=lambda message: {"nonce": message.request_id},
    )
    console.set_control_adapter(adapter)
    monkeypatch.setattr(
        console,
        "play_voice_clips",
        lambda clips, **_kwargs: {
            "clips": list(clips),
            "played": len(clips),
            "status": "done",
        },
    )

    for _cycle in range(2):
        started = console.execute_competition_action(CompetitionAction.FOLLOW_RESUME)

        assert started["state"] == "FOLLOWING"
        assert started["runtime_active"] is True
        assert started["uwb"]["valid"] is True
        assert started["uwb"]["bearing_rad"] == pytest.approx(math.radians(5.0))
        assert started["uwb"]["orientation_est_rad"] == pytest.approx(0.1)
        assert started["configuration"]["target_distance_m"] == pytest.approx(1.35)
        assert started["configuration"]["motion_limits_aligned"] is True
        assert started["configuration"]["control_frequency_hz"] == pytest.approx(4.0)
        assert started["configuration"]["effective_control_frequency_hz"] == pytest.approx(
            4.0
        )
        assert started["configuration"]["config_source"] == (
            "configs/webrtc_uwb_follow_3min.yaml"
        )
        assert started["runtime"]["worker_alive"] is True
        assert started["runtime"]["control"]["execution_status"] == "SENT"
        assert started["lidar"]["state"] == "UNAVAILABLE"
        assert adapter.state.value == "FOLLOWING"

        stopped = console.execute_competition_action(CompetitionAction.FOLLOW_STOP)

        assert stopped["state"] == "IDLE"
        assert stopped["runtime_active"] is False
        assert adapter.state.value == "IDLE"
        assert console._motion_thread is None
        assert console._motion_name is None
        assert console.lifecycle.state is CompanionState.IDLE
        assert source.active is False

    assert controller.stop_count >= 1
    assert runtime.companion_activation_count == 2
    assert runtime.companion_deactivation_count == 0
    assert forwarder.start_count == 2
    assert forwarder.close_count == 0


def test_follow_restart_waits_for_stop_announcement_to_finish() -> None:
    console = _start_command_console(manual_confirm_start=False)
    stop_announcement_started = threading.Event()
    finish_stop_announcement = threading.Event()
    calls: list[object] = []
    failures: list[BaseException] = []

    def run_control(command, *, request_id, payload):
        del request_id, payload
        calls.append(("control", command))
        return {
            "state": "IDLE" if command == "stop_follow" else "FOLLOWING",
            "runtime_active": command != "stop_follow",
        }

    def play_voice(clips, **_kwargs):
        clip_ids = tuple(clips)
        calls.append(("voice", clip_ids))
        if clip_ids == ("follow.stop",):
            stop_announcement_started.set()
            assert finish_stop_announcement.wait(1.0)
        return {"clips": list(clips), "played": len(clips), "status": "done"}

    def run_action(action):
        try:
            console.execute_competition_action(action)
        except BaseException as exc:
            failures.append(exc)

    console._run_control_command = run_control
    console.play_voice_clips = play_voice
    console._interrupt_voice_playback = lambda *, reason: calls.append(
        ("interrupt_voice", reason)
    )
    console._print_demo_guidance = lambda: None

    stop_thread = threading.Thread(
        target=run_action,
        args=(CompetitionAction.FOLLOW_STOP,),
    )
    stop_thread.start()
    assert stop_announcement_started.wait(1.0)

    restart_thread = threading.Thread(
        target=run_action,
        args=(CompetitionAction.FOLLOW_RESUME,),
    )
    restart_thread.start()
    time.sleep(0.05)

    assert restart_thread.is_alive()
    assert ("voice", ("follow.resume.safe",)) not in calls
    assert ("control", "start_follow") not in calls

    finish_stop_announcement.set()
    stop_thread.join(timeout=1.0)
    restart_thread.join(timeout=1.0)

    assert not stop_thread.is_alive()
    assert not restart_thread.is_alive()
    assert failures == []
    assert calls.index(("voice", ("follow.stop",))) < calls.index(
        ("voice", ("follow.resume.safe",))
    )
    assert calls.index(("voice", ("follow.resume.safe",))) < calls.index(
        ("control", "start_follow")
    )


def test_aborted_companion_worker_synchronizes_following_lifecycle_to_idle(
    capsys,
) -> None:
    runtime = _HttpControlRuntime()
    console = RuntimeConsole(
        runtime,
        SimpleNamespace(
            settings=SimpleNamespace(
                robot_id="go2_edu_01",
                max_vx=0.504,
                max_wz=1.10,
                uwb_bearing_sign=1,
                uwb_bearing_zero_offset_rad=0.0,
            )
        ),
        _HttpControlController(),
        video_host="0.0.0.0",
        video_port=8093,
        lan_ip="192.168.8.254",
        follow_target_source=_HttpFollowSource(),
    )
    started = console.lifecycle.start(
        LifecycleReadiness(
            webrtc_connected=True,
            uwb_fresh=True,
            uwb_valid=True,
            motion_writer_available=True,
        )
    )
    assert started.snapshot.state is CompanionState.FOLLOWING

    def abort_session() -> None:
        console._follow_status = {
            "state": "STOPPED",
            "motion": "STOPPED",
            "reason": "webrtc_connection_not_single",
        }

    worker = threading.Thread(
        target=console._motion_worker,
        args=("companion", abort_session),
    )
    with console._state_lock:
        console._motion_name = "companion"
        console._motion_thread = worker
    worker.start()
    worker.join(timeout=1.0)

    assert worker.is_alive() is False
    assert console.lifecycle.state is CompanionState.IDLE
    assert console.companion_status()["state"] == "IDLE"
    output = capsys.readouterr().out
    assert "COMPANION_SESSION_ABORTED reason=webrtc_connection_not_single" in output
    assert "LIFECYCLE_SYNC FOLLOWING->IDLE" in output


def test_voice_layer_initializes_services_and_preloads_only_on_demand(
    monkeypatch,
) -> None:
    runtime = _HttpControlRuntime()
    services = (object(), object(), object())
    factory_calls = []
    preload_calls = []

    def create_services():
        factory_calls.append(True)
        return services

    console = RuntimeConsole(
        runtime,
        SimpleNamespace(),
        _HttpControlController(),
        video_host="0.0.0.0",
        video_port=8093,
        lan_ip="192.168.8.254",
        voice_services_factory=create_services,
    )
    monkeypatch.setattr(
        console,
        "preload_voice_control_presets",
        lambda: preload_calls.append(True),
    )

    assert factory_calls == []
    assert preload_calls == []
    console.ensure_voice_ready()
    console.ensure_voice_ready()

    assert factory_calls == [True]
    assert preload_calls == [True]
    assert console.asr_service is services[0]
    assert console.tts_service is services[1]
    assert console.agent_client is services[2]
    assert runtime.voice_activation_count == 2

    console.disable_voice_layer()
    assert runtime.voice_deactivation_count == 1
    assert console.asr_service is None


def test_risk_i_am_ok_and_explicit_resume_share_one_wireless_lifecycle(
    monkeypatch,
) -> None:
    runtime = _HttpControlRuntime()
    controller = _HttpControlController()
    source = _HttpFollowSource()
    service = SimpleNamespace(
        settings=SimpleNamespace(
            robot_id="go2_edu_01",
            max_vx=0.3,
            max_wz=0.3,
            uwb_bearing_sign=1,
            uwb_bearing_zero_offset_rad=0.0,
        )
    )
    console = RuntimeConsole(
        runtime,
        service,
        controller,
        video_host="0.0.0.0",
        video_port=8093,
        lan_ip="192.168.8.254",
        follow_target_source=source,
    )
    monkeypatch.setattr(
        console,
        "_build_follow_session",
        lambda: _HttpFollowSession(console._motion_cancel),
    )

    console.start_companion()
    risk = console.ingest_risk_event(
        {
            "event_type": "FALL_SUSPECTED",
            "incident_id": "FALL-RUNTIME-001",
            "timestamp": "2026-08-31T10:00:00+08:00",
            "confidence": 0.8,
        }
    )
    ok = console.apply_voice_intent("I_AM_OK")

    assert risk["state"] == "VOICE_CHECK"
    assert risk["runtime_active"] is False
    assert ok["companion"]["state"] == "WAIT_RESUME"
    assert ok["companion"]["help_required"] is False

    console.ingest_risk_event(
        {
            "event_type": "RECOVERY_CONFIRMED",
            "incident_id": "FALL-RUNTIME-001",
            "timestamp": "2026-08-31T10:00:05+08:00",
        }
    )
    resumed = console.apply_voice_intent("RESUME_COMPANION")
    assert resumed["executed"] is True
    assert resumed["companion"]["state"] == "FOLLOWING"
    console.stop_companion()


class _ManualService:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(
            robot_id="go2_edu_01",
            max_vx=0.3,
            max_wz=0.3,
            uwb_bearing_sign=1,
            uwb_bearing_zero_offset_rad=0.0,
        )
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


def test_manual_key_preempts_to_single_writer_and_release_stays_idle() -> None:
    service = _ManualService()
    console = RuntimeConsole(
        _HttpControlRuntime(),
        service,
        _HttpControlController(),
        video_host="0.0.0.0",
        video_port=8093,
        lan_ip="192.168.8.254",
        follow_target_source=_HttpFollowSource(),
    )
    manual = console.manual_key("W")
    deadline = time.monotonic() + 1.0
    while not service.refreshes and time.monotonic() < deadline:
        time.sleep(0.01)
    released = console.release_manual()

    assert manual["companion"]["state"] == "MANUAL_CONTROL"
    assert manual["companion"]["motion"]["authority"] == "MANUAL"
    assert service.refreshes == [(0.35, 0.0, 0.0, "wireless_manual")]
    assert released["state"] == "IDLE"
    assert released["runtime_active"] is False


def test_manual_preempts_following_space_stops_and_exit_never_auto_resumes() -> None:
    service = _ManualService()
    console = RuntimeConsole(
        _HttpControlRuntime(),
        service,
        _HttpControlController(),
        video_host="0.0.0.0",
        video_port=8093,
        lan_ip="192.168.8.254",
        follow_target_source=_HttpFollowSource(),
    )
    started = console.lifecycle.start(
        LifecycleReadiness(
            webrtc_connected=True,
            uwb_fresh=True,
            uwb_valid=True,
            motion_writer_available=True,
        )
    )
    assert started.snapshot.state is CompanionState.FOLLOWING

    manual = console.manual_key("A")
    stopped = console.manual_key("SPACE")
    released = console.release_manual()

    assert manual["companion"]["state"] == "MANUAL_CONTROL"
    assert manual["command"]["wz"] == 0.55
    assert stopped["state"] == "MANUAL_CONTROL"
    assert any("manual_space" in source for source in service.stops)
    assert released["state"] == "IDLE"
    assert released["runtime_active"] is False
    assert service.owner is None


def test_manual_enter_accepts_transient_none_companion_telemetry() -> None:
    class _TransientTelemetryRuntime(_HttpControlRuntime):
        def companion_telemetry_status(self):
            return None

    service = _ManualService()
    console = RuntimeConsole(
        _TransientTelemetryRuntime(),
        service,
        _HttpControlController(),
        video_host="0.0.0.0",
        video_port=8093,
        lan_ip="192.168.8.254",
        follow_target_source=_HttpFollowSource(),
    )

    status = console.enter_manual()

    assert status["state"] == "MANUAL_CONTROL"
    assert status["motion"]["authority"] == "MANUAL"
    assert status["uwb"]["age_ms"] is None
    assert service.owner == "wireless_manual"
    console.release_manual()


def test_manual_enter_status_failure_releases_shared_writer() -> None:
    service = _ManualService()
    service.settings = None
    console = RuntimeConsole(
        _HttpControlRuntime(),
        service,
        _HttpControlController(),
        video_host="0.0.0.0",
        video_port=8093,
        lan_ip="192.168.8.254",
        follow_target_source=_HttpFollowSource(),
    )

    with pytest.raises(AttributeError):
        console.enter_manual()

    assert service.owner is None
    assert console.manual_controller.active is False
    assert console.lifecycle.state is CompanionState.IDLE


class _EmergencyRuntime(_HttpControlRuntime):
    def __init__(self) -> None:
        self.spoken = []
        self.played = []

    def speak(self, text: str) -> None:
        self.spoken.append(text)

    def play_audio_file(self, path, **_kwargs) -> None:
        self.played.append(Path(path).name)


def _emergency_console(asr_service) -> RuntimeConsole:
    return RuntimeConsole(
        _EmergencyRuntime(),
        SimpleNamespace(
            settings=SimpleNamespace(
                robot_id="go2_edu_01",
                max_vx=0.3,
                max_wz=0.3,
                uwb_bearing_sign=1,
                uwb_bearing_zero_offset_rad=0.0,
            )
        ),
        _HttpControlController(),
        video_host="0.0.0.0",
        video_port=8093,
        lan_ip="192.168.8.254",
        follow_target_source=_HttpFollowSource(),
        asr_service=asr_service,
    )


def test_emergency_voice_worker_escalates_after_two_silent_attempts(monkeypatch) -> None:
    console = _emergency_console(SimpleNamespace(transcribe=lambda _path: ""))
    console.lifecycle.ingest_fall(incident_id="FALL-SILENT", confirmed=True)
    played: list[str] = []
    monkeypatch.setattr(
        console,
        "_play_lifecycle_preset_best_effort",
        lambda filename: played.append(filename),
    )
    monkeypatch.setattr(console._emergency_voice_cancel, "wait", lambda _seconds: False)
    monkeypatch.setattr(
        console,
        "_mic_gate",
        lambda **_kwargs: SimpleNamespace(speech_detected=False, path="unused.wav"),
    )

    console._emergency_voice_worker()
    status = console.companion_status()

    assert status["state"] == "ESCALATED_EMERGENCY"
    assert status["response_attempts"] == 2
    assert status["monitoring_active"] is True
    assert played == [
        "VOICE_CHECK.wav",
        "VOICE_RECHECK.wav",
        "NO_RESPONSE_ESCALATED.wav",
    ]
    assert console.runtime.spoken == []
    assert status["notifications"][-1]["delivery"] == "PENDING_EXTERNAL_ADAPTER"


def test_emergency_voice_worker_i_am_ok_waits_for_explicit_resume(monkeypatch) -> None:
    asr = SimpleNamespace(transcribe=lambda _path: "我没事")
    console = _emergency_console(asr)
    console.lifecycle.ingest_fall(incident_id="FALL-OK", confirmed=True)
    played: list[str] = []
    monkeypatch.setattr(
        console,
        "_play_lifecycle_preset_best_effort",
        lambda filename: played.append(filename),
    )
    monkeypatch.setattr(console._emergency_voice_cancel, "wait", lambda _seconds: False)
    monkeypatch.setattr(
        console,
        "_mic_gate",
        lambda **_kwargs: SimpleNamespace(speech_detected=True, path="response.wav"),
    )

    console._emergency_voice_worker()
    status = console.companion_status()

    assert status["state"] == "WAIT_RESUME"
    assert status["help_required"] is False
    assert status["runtime_active"] is False
    assert played == ["VOICE_CHECK.wav", "I_AM_OK.wav"]
