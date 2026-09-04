from __future__ import annotations

import math
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from app.companion.competition_lifecycle import LifecycleReadiness
from app.companion.models import CompanionState
from app.motion.scripted_motion import MotionActionResult
from app.webrtc.follow_target_forwarder import FollowTargetState
from tools.go2_wireless_runtime import (
    CONFIRM_APP_CLOSED,
    CONFIRM_AREA,
    CONFIRM_COMPETITION,
    CONFIRM_POSE_AUDIO,
    CONFIRM_WRITER,
    RuntimeConsole,
    WALK_FOLLOW_PRESET,
    WALK_FOLLOW_TEXT,
    _confirm_startup,
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
    monkeypatch.setattr(console, "_wav_duration_seconds", lambda _path: 1.0)
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


def test_auto_demo_requires_competition_confirmation(tmp_path, monkeypatch) -> None:
    lifecycle = tmp_path / "companion.json"
    lifecycle.write_text('{"state":"IDLE"}', encoding="utf-8")
    settings = SimpleNamespace(companion_state_path=str(lifecycle))
    answers = iter(
        [
            CONFIRM_WRITER,
            CONFIRM_APP_CLOSED,
            CONFIRM_AREA,
            CONFIRM_COMPETITION,
            CONFIRM_POSE_AUDIO,
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    _confirm_startup(settings, auto_demo="phone_demo")


def test_launcher_can_skip_repetitive_operator_prompts(tmp_path, monkeypatch) -> None:
    lifecycle = tmp_path / "companion.json"
    lifecycle.write_text('{"state":"IDLE"}', encoding="utf-8")
    settings = SimpleNamespace(companion_state_path=str(lifecycle))

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


def test_console_start_plays_announcement_before_starting_follow(
    monkeypatch,
) -> None:
    console = _start_command_console(manual_confirm_start=False)
    events: list[str] = []
    console._play_start_announcement = lambda: events.append("announcement")
    def start_companion(*, before_start=None):
        if before_start is not None:
            before_start()
        events.append("start")
        return {"state": "FOLLOWING", "runtime_active": True}

    console.start_companion = start_companion
    commands = iter(("START", "EXIT"))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    assert console.run() == 0
    assert events == ["announcement", "start"]


def test_console_start_debug_switch_restores_single_confirmation(
    monkeypatch, capsys
) -> None:
    console = _start_command_console(manual_confirm_start=True)
    commands = iter(("START", "WIRELESS_COMPANION_START_APPROVED", "EXIT"))
    prompts: list[str] = []

    def command_input(prompt: str) -> str:
        prompts.append(prompt)
        return next(commands)

    monkeypatch.setattr("builtins.input", command_input)

    assert console.run() == 0

    assert prompts == [
        "wireless> ",
        "Type WIRELESS_COMPANION_START_APPROVED: ",
        "wireless> ",
    ]
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
        "START_REJECTED:UWB_NOT_READY:uwb_not_fresh"
        in capsys.readouterr().out
    )


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
            last_dropout_duration_seconds=None,
            maximum_dropout_duration_seconds=0.0,
            to_dict=lambda: {"reason": "operator_stop"},
        )


def test_http_companion_control_starts_and_stops_same_console_session(monkeypatch) -> None:
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

    started = console.start_companion()
    stopped = console.stop_companion()

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
    assert stopped["state"] == "IDLE"
    assert stopped["runtime_active"] is False
    assert controller.stop_count >= 1
    assert runtime.companion_activation_count == 1
    assert runtime.companion_deactivation_count >= 1
    assert forwarder.start_count == 1
    assert forwarder.close_count >= 1


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
