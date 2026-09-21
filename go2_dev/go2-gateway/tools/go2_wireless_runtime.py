from __future__ import annotations

import argparse
import json
import logging
import math
import os
import socket
import sys
import threading
import time
import webbrowser
import wave
from array import array
from enum import Enum
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_project_env_defaults(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_project_env_defaults(ROOT / ".env")

LOGGER = logging.getLogger(__name__)
DEMO_CONSOLE_PREFIX = "\x1fGO2_DEMO\x1f"


class RuntimeOutputSession:
    """Tee process output to a debug log and optionally simplify the console."""

    def __init__(self, path: Path, *, demo_console: bool) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.demo_console = bool(demo_console)
        self._file = path.open("a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        self.stdout = _RuntimeOutputStream(self, self._original_stdout)
        self.stderr = _RuntimeOutputStream(self, self._original_stderr)

    def install(self) -> None:
        sys.stdout = self.stdout
        sys.stderr = self.stderr

    def restore(self) -> None:
        root_logger = logging.getLogger()
        for handler in root_logger.handlers:
            if getattr(handler, "stream", None) is self.stderr:
                handler.setStream(self._original_stderr)
        if sys.stdout is self.stdout:
            sys.stdout = self._original_stdout
        if sys.stderr is self.stderr:
            sys.stderr = self._original_stderr
        with self._lock:
            self._file.flush()
            self._file.close()

    def write_debug(self, text: str) -> None:
        with self._lock:
            self._file.write(text.replace(DEMO_CONSOLE_PREFIX, ""))


class _RuntimeOutputStream:
    def __init__(self, session: RuntimeOutputSession, console_stream: Any) -> None:
        self.session = session
        self.console_stream = console_stream
        self._buffer = ""
        self._lock = threading.Lock()

    def write(self, text: str) -> int:
        value = str(text)
        self.session.write_debug(value)
        if not self.session.demo_console:
            return self.console_stream.write(value.replace(DEMO_CONSOLE_PREFIX, ""))
        with self._lock:
            self._buffer += value
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                normalized = line.rstrip("\r")
                if normalized.startswith(DEMO_CONSOLE_PREFIX):
                    self.console_stream.write(
                        normalized[len(DEMO_CONSOLE_PREFIX) :] + "\n"
                    )
        return len(value)

    def flush(self) -> None:
        self.session.write_debug("")
        self.console_stream.flush()

    def isatty(self) -> bool:
        return bool(self.console_stream.isatty())

    @property
    def encoding(self) -> str | None:
        return getattr(self.console_stream, "encoding", None)

    def fileno(self) -> int:
        return self.console_stream.fileno()


def _emit_demo_console(message: str, *, timestamp: bool = True) -> None:
    prefix = time.strftime("%H:%M:%S  ") if timestamp else ""
    marker = DEMO_CONSOLE_PREFIX if isinstance(sys.stdout, _RuntimeOutputStream) else ""
    print(f"{marker}{prefix}{message}", flush=True)

from app.adapters.webrtc_motion_backend import WebRTCMotionBackend
from app.companion.config_loader import load_companion_demo_config
from app.companion.competition_lifecycle import (
    CompetitionLifecycle,
    LifecycleReadiness,
)
from app.companion.models import CompanionState
from app.config import load_settings
from app.core.state_store import StateStore
from app.gateway.go2_gateway import Go2Gateway
from app.motion.action_sequence import MotionActionDispatcher, load_motion_sequence
from app.motion.scripted_motion import ScriptedMotionController, load_scripted_motion_config
from app.motion.manual_control import (
    ManualControlConfig,
    ManualKeyboardController,
    WindowsAsyncKeyState,
)
from app.motion.contracts import ExternalRiskEvent, ExternalRiskEventType
from app.services.robot_service import RobotService
from app.iot import (
    CommandDispatcher,
    CommandMessage,
    Go2ControlAdapter,
    MockTransport,
)
from app.voice.local_voice import (
    FunASRLocalASRService,
    Go2ASRAudioBridge,
    LocalVoicePipeline,
    LocalVoiceSessionManager,
    WindowsWaveInMicrophoneSource,
)
from app.voice.clip_composer import (
    clip_id_to_filename,
    dynamic_clip_phrases,
    temperature_value_clip,
)
from app.voice.interaction_flow import InteractionFlowController
from app.voice.xiaokang_agent import (
    CachedWeatherProvider,
    ClipAssembler,
    LocalFirstXiaokangAgent,
    OpenMeteoWeatherProvider,
    WeatherCondition,
    WeatherContext,
    build_default_health_provider,
    build_default_medication_provider,
)
from app.webrtc.go2_wireless_runtime import (
    ExpectedAioiceBindNoiseFilter,
    Go2WirelessRuntime,
    HighFrequencyUnitreeDataLogFilter,
)
from app.webrtc.follow_target_forwarder import (
    FollowTargetForwardConfig,
    Go2UwbFollowTargetSource,
    UdpFollowTargetForwarder,
)
from app.webrtc.uwb_follow import (
    WirelessUwbFollowSession,
    load_wireless_uwb_follow_config,
)
try:
    from app.webrtc.video_bridge import (
        WirelessCompanionControlError,
        create_video_bridge,
    )
except ModuleNotFoundError as exc:
    class WirelessCompanionControlError(RuntimeError):
        pass

    def create_video_bridge(*_args: Any, **_kwargs: Any) -> Any:
        raise ModuleNotFoundError(
            "video bridge dependencies are unavailable; install fastapi to use the "
            "robot/WebRTC runtime"
        ) from exc
from app.webrtc.voice_intent import (
    CompanionAgentClient,
    AgentTurn,
    CompanionLifecycleSnapshot,
    CompanionLifecycleState,
    CompanionSpeechCache,
    CompanionSpeechRenderer,
    HealthNewASRService,
    HealthNewTTSService,
    HealthNewWeatherCache,
    VoiceFastIntentRouter,
    VoiceIntentAdapter,
    VoiceIntent,
    WakeWordMatcher,
)


PHONE_DEMO = ROOT / "configs" / "phone_demo.yaml"
MOTION_CONFIG = ROOT / "configs" / "scripted_motion.yaml"
COMPANION_CONFIG = ROOT / "configs" / "companion_follow_real.yaml"
WIRELESS_FOLLOW_CONFIG = ROOT / "configs" / "webrtc_uwb_follow_3min.yaml"
VOICE_PRESET_DIR = Path(
    os.environ.get(
        "GO2_VOICE_PRESET_DIR",
        str(ROOT / "data" / "voice" / "presets" / "current"),
    )
).resolve()
EMERGENCY_VOICE_CLIPS = {"fall.alert.sound", "fall.help.broadcast"}
EMERGENCY_VOICE_ALARM_PAUSE_SECONDS = 0.25
VOICE_PLAYBACK_TIMEOUT_MARGIN_SECONDS = 4.0
FALL_MANUAL_CONTROL_CONFIG = ManualControlConfig(
    forward_mps=0.22,
    backward_mps=0.18,
    lateral_mps=0.10,
    yaw_radps=0.30,
    curve_forward_mps=0.18,
    curve_backward_mps=0.15,
    curve_yaw_radps=0.25,
    send_rate_hz=5.0,
    control_poll_seconds=0.02,
    deadman_seconds=0.40,
)
VOICE_PLAYBACK_TIMEOUT_MIN_SECONDS = 8.0
VOICE_PLAYBACK_WATCHDOG_MARGIN_SECONDS = max(
    0.0,
    min(2.0, float(os.environ.get("GO2_VOICE_PLAYBACK_WATCHDOG_MARGIN_SECONDS", "0.3"))),
)
VOICE_PLAYBACK_ECHO_GUARD_SECONDS = max(
    0.0,
    min(5.0, float(os.environ.get("GO2_VOICE_PLAYBACK_ECHO_GUARD_SECONDS", "1.5"))),
)
VOICE_PLAYBACK_INTER_CLIP_GAP_SECONDS = max(
    0.0,
    min(0.5, float(os.environ.get("GO2_VOICE_PLAYBACK_INTER_CLIP_GAP_SECONDS", "0.08"))),
)
EMERGENCY_VOICE_TIMEOUT_MARGIN_SECONDS = 6.0
EMERGENCY_VOICE_TIMEOUT_MIN_SECONDS = 15.0
XIAOKANG_RUNTIME_PRELOAD_BASE_CLIPS = (
    "sess.wake_ack",
    "outing.allow.health_good",
    "health.hr.prefix",
    "num.76",
    "num.77",
    "num.78",
    "unit.bpm",
    "health.spo2.98",
    "health.temperature.36_5",
    "health.temperature.prefix",
    "weather.condition.sunny",
    "weather.condition.cloudy",
    "weather.condition.overcast",
    "weather.condition.rain",
    "weather.condition.snow",
    "weather.temperature.prefix",
    "temperature.value.17",
    "temperature.value.22",
    "temperature.value.23",
    "temperature.value.24",
    "temperature.value.36_6",
    "medication.reminder.before_outing",
    "outing.allow.suffix",
    "outing.medication_check",
    "outing.start",
    "follow.resume.safe",
    "follow.stop",
    "fall.confirm",
    "fall.confirm.second",
    "fall.alert.sound",
    "fall.help.broadcast",
    "fall.recovered",
    "fall.normal_activity",
    "reading.ask_book",
)


def _xiaokang_competition_required_clips() -> tuple[str, ...]:
    weather_temperatures = [temperature_value_clip(value) for value in range(0, 41)]
    body_temperatures = [
        temperature_value_clip(tenths / 10.0)
        for tenths in range(360, 376)
    ]
    return tuple(
        dict.fromkeys(
            (
                *XIAOKANG_RUNTIME_PRELOAD_BASE_CLIPS,
                *weather_temperatures,
                *body_temperatures,
            )
        )
    )


XIAOKANG_RUNTIME_REQUIRED_CLIPS = _xiaokang_competition_required_clips()


def _xiaokang_runtime_preload_clips() -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for clip_id in (*XIAOKANG_RUNTIME_PRELOAD_BASE_CLIPS, *dynamic_clip_phrases().keys()):
        normalized = str(clip_id or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    return tuple(ordered)


XIAOKANG_RUNTIME_PRELOAD_CLIPS = _xiaokang_runtime_preload_clips()
VOICE_INTENT_CAPTURE_SECONDS = max(
    1.5,
    min(10.0, float(os.environ.get("GO2_VOICE_CAPTURE_SECONDS", "8.0"))),
)
VOICE_VAD_TRAILING_SILENCE_SECONDS = max(
    0.2,
    min(
        1.0,
        float(os.environ.get("GO2_VOICE_VAD_TRAILING_SILENCE_SECONDS", "0.3")),
    ),
)
VOICE_CONTROL_PRESETS = {
    VoiceIntent.START_COMPANION: "START_COMPANION.wav",
    VoiceIntent.STOP_COMPANION: "STOP_COMPANION.wav",
    VoiceIntent.RESUME_COMPANION: "RESUME_COMPANION.wav",
    VoiceIntent.REQUEST_HELP: "REQUEST_HELP.wav",
    VoiceIntent.CALL_FAMILY: "CALL_FAMILY.wav",
    VoiceIntent.I_AM_OK: "I_AM_OK.wav",
}
VOICE_CONTROL_FEEDBACK_TEXT = {
    VoiceIntent.START_COMPANION: "伴随模式已启动。",
    VoiceIntent.STOP_COMPANION: "伴随已停止。",
    VoiceIntent.RESUME_COMPANION: "正在恢复伴随。",
    VoiceIntent.REQUEST_HELP: "已收到您的求助。",
    VoiceIntent.CALL_FAMILY: "已为您联系家人。",
    VoiceIntent.I_AM_OK: "好的，我会继续在这里陪着您。",
}
WALK_FOLLOW_TEXT = (
    "您当前心率为76次每分钟，血氧为98%，状态正常。"
    "伴随模式已启动，请注意出行安全。"
)
WALK_FOLLOW_PRESET = "WALK_FOLLOW.wav"
CONFIRM_GATE = "JOINT_VIDEO_MOTION_GATE_APPROVED"
CONFIRM_DEMO = "PHONE_DEMO_APPROVED"
CONFIRM_WRITER = "EXCLUSIVE_MOTION_WRITER"
CONFIRM_APP_CLOSED = "UNITREE_APP_CLOSED"
CONFIRM_AREA = "OPEN_AREA_REMOTE_READY"
CONFIRM_COMPETITION = "COMPETITION_PHONE_DEMO_APPROVED"
CONFIRM_POSE = "POSE_GATE_APPROVED"
CONFIRM_AUDIO = "AUDIO_GATE_APPROVED"
CONFIRM_POSE_AUDIO = "POSE_AUDIO_REAL_APPROVED"
CONFIRM_UWB_READONLY = "WEBRTC_UWB_READONLY_GATE"
CONFIRM_FOLLOW_3MIN = "WIRELESS_UWB_FOLLOW_3MIN_APPROVED"
CONFIRM_COMPANION_START = "WIRELESS_COMPANION_START_APPROVED"
CONFIRM_FOLLOW_NO_LIDAR = "UWB_ONLY_NO_LIDAR_OPEN_AREA"
CONFIRM_REMOTE_STOP = "REMOTE_STOP_READY"
CONFIRM_MIC_READONLY = "WEBRTC_MIC_READONLY_GATE"


class CompetitionAction(str, Enum):
    SKILL2_REPORT = "SKILL2_REPORT"
    MEDICATION_RECHECK = "MEDICATION_RECHECK"
    OUTING_START = "OUTING_START"
    FOLLOW_RESUME = "FOLLOW_RESUME"
    FOLLOW_STOP = "FOLLOW_STOP"
    FALL_PROMPT_1 = "FALL_PROMPT_1"
    FALL_PROMPT_1_AUDIO = "FALL_PROMPT_1_AUDIO"
    FALL_PROMPT_2 = "FALL_PROMPT_2"
    FALL_HELP = "FALL_HELP"
    FALL_RECOVER = "FALL_RECOVER"
    XIAOKANG_WAKE_ACK = "XIAOKANG_WAKE_ACK"
    READING_NORMAL = "READING_NORMAL"
    MANUAL_TAKEOVER = "MANUAL_TAKEOVER"
    DEMO_RESET = "DEMO_RESET"
    VOICE_LISTENER_TOGGLE = "VOICE_LISTENER_TOGGLE"
    QUICK_FOLLOW_RECOVERY = "QUICK_FOLLOW_RECOVERY"
    VOICE_RECOVERY = "VOICE_RECOVERY"
    KEYBOARD_CLOSE = "KEYBOARD_CLOSE"
    DIRECT_FOLLOW_START = "DIRECT_FOLLOW_START"
    DIRECT_FOLLOW_STOP = "DIRECT_FOLLOW_STOP"


# This is the only table that binds operator keys to competition behavior.
# Reordering the show must not require changes to any action implementation.
HOTKEY_ACTIONS = {
    "CTRL+F1": CompetitionAction.SKILL2_REPORT,
    "CTRL+F2": CompetitionAction.MEDICATION_RECHECK,
    "CTRL+F3": CompetitionAction.OUTING_START,
    "CTRL+F4": CompetitionAction.FOLLOW_STOP,
    "CTRL+F5": CompetitionAction.FOLLOW_RESUME,
    "CTRL+F6": CompetitionAction.FALL_PROMPT_1,
    "CTRL+F7": CompetitionAction.FALL_HELP,
    "CTRL+F8": CompetitionAction.XIAOKANG_WAKE_ACK,
    "CTRL+F9": CompetitionAction.QUICK_FOLLOW_RECOVERY,
    "CTRL+F10": CompetitionAction.DIRECT_FOLLOW_STOP,
    "CTRL+F11": CompetitionAction.MANUAL_TAKEOVER,
    "CTRL+F12": CompetitionAction.KEYBOARD_CLOSE,
    "CTRL+SHIFT+F9": CompetitionAction.DEMO_RESET,
    "CTRL+SHIFT+F10": CompetitionAction.VOICE_RECOVERY,
}
if HOTKEY_ACTIONS.get("CTRL+F12") is not CompetitionAction.KEYBOARD_CLOSE:
    raise RuntimeError("required operator shutdown binding is missing")

FUNCTION_KEY_VIRTUAL_KEYS = {
    f"F{number}": 0x6F + number for number in range(1, 13)
}
PRIORITY_ACTIONS = {
    CompetitionAction.FOLLOW_STOP,
    CompetitionAction.FALL_PROMPT_1,
    CompetitionAction.DIRECT_FOLLOW_STOP,
    CompetitionAction.MANUAL_TAKEOVER,
    CompetitionAction.QUICK_FOLLOW_RECOVERY,
    CompetitionAction.KEYBOARD_CLOSE,
}
VOICE_TASK_ACTIONS = {
    CompetitionAction.SKILL2_REPORT,
    CompetitionAction.MEDICATION_RECHECK,
    CompetitionAction.OUTING_START,
}

CTRL_HOTKEY_SCAN_CODES = {
    "^": "CTRL+F1",
    "_": "CTRL+F2",
    "`": "CTRL+F3",
    "a": "CTRL+F4",
    "b": "CTRL+F5",
    "c": "CTRL+F6",
    "d": "CTRL+F7",
    "e": "CTRL+F8",
    "f": "CTRL+F9",
    "g": "CTRL+F10",
    "\x89": "CTRL+F11",
    "\x8a": "CTRL+F12",
}
CTRL_SHIFT_HOTKEY_SCAN_CODES = {
    "f": "CTRL+SHIFT+F9",
    "g": "CTRL+SHIFT+F10",
}

LEGACY_ACTION_ALIASES = {
    "START": CompetitionAction.DIRECT_FOLLOW_START,
    "STOP": CompetitionAction.DIRECT_FOLLOW_STOP,
    "START_OR_RESUME": CompetitionAction.FOLLOW_RESUME,
    "RESUME": CompetitionAction.FOLLOW_RESUME,
    "SCENE2_ASSESSMENT": CompetitionAction.SKILL2_REPORT,
    "SCENE3_MEDICATION_CHECK": CompetitionAction.MEDICATION_RECHECK,
    "SCENE3_DEPART": CompetitionAction.OUTING_START,
    "FALL_SUSPECTED": CompetitionAction.FALL_PROMPT_1,
    "FALL_RECOVERED": CompetitionAction.FALL_RECOVER,
    "READING": CompetitionAction.READING_NORMAL,
    "RESET_DEMO": CompetitionAction.DEMO_RESET,
    "RESET": CompetitionAction.DEMO_RESET,
    "TOGGLE_VOICE_LISTENER": CompetitionAction.VOICE_LISTENER_TOGGLE,
    "RECOVER_FOLLOW": CompetitionAction.QUICK_FOLLOW_RECOVERY,
    "PLAY_FALL_CONFIRM": CompetitionAction.FALL_PROMPT_1_AUDIO,
    "PLAY_FALL_CONFIRM_SECOND": CompetitionAction.FALL_PROMPT_2,
    "PLAY_FALL_HELP": CompetitionAction.FALL_HELP,
    "VOICE_RECOVERY": CompetitionAction.VOICE_RECOVERY,
}

CONSOLE_COMMAND_ALIASES = {
    **{key: action.value for key, action in LEGACY_ACTION_ALIASES.items()},
}


def _display_hotkey_label(label: str) -> str:
    return label.replace("CTRL+", "Ctrl+").replace("SHIFT+", "Shift+")


def _hotkey_label_for_keypress(
    function_key: str,
    *,
    ctrl_down: bool,
    shift_down: bool,
) -> str | None:
    if not ctrl_down:
        return None
    prefix = "CTRL+SHIFT+" if shift_down else "CTRL+"
    label = f"{prefix}{function_key.upper()}"
    return label if label in HOTKEY_ACTIONS else None


def _console_hotkey_command(
    scan_code: str,
    *,
    shift_down: bool = False,
) -> tuple[str, str] | None:
    """Return (physical key label, competition action) for a scan code."""

    scan_codes = (
        CTRL_SHIFT_HOTKEY_SCAN_CODES if shift_down else CTRL_HOTKEY_SCAN_CODES
    )
    label = scan_codes.get(scan_code)
    if label is None:
        return None
    action = HOTKEY_ACTIONS.get(label)
    if action is None:
        return None
    return _display_hotkey_label(label), action.value


def _normalize_console_command(command: str) -> str:
    normalized = str(command or "").strip().upper()
    hotkey_action = HOTKEY_ACTIONS.get(normalized)
    if hotkey_action is not None:
        return hotkey_action.value
    return CONSOLE_COMMAND_ALIASES.get(normalized, normalized)


def _coerce_competition_action(
    action: CompetitionAction | str,
) -> CompetitionAction:
    if isinstance(action, CompetitionAction):
        return action
    return CompetitionAction(_normalize_console_command(action))


def _is_quiet_fall_rejection(
    action: CompetitionAction,
    exc: WirelessCompanionControlError,
) -> bool:
    if action not in {
        CompetitionAction.FALL_PROMPT_2,
        CompetitionAction.FALL_HELP,
        CompetitionAction.FALL_RECOVER,
    }:
        return False
    return str(getattr(exc, "code", "") or "") in {
        "FALL_RECOVERY_REJECTED",
        "FALL_STAGE_CONFLICT",
    }


def _hotkey_label_for_action(action: CompetitionAction) -> str:
    for label, mapped_action in HOTKEY_ACTIONS.items():
        if mapped_action is action:
            return _display_hotkey_label(label)
    return action.value


def _windows_hotkeys_available() -> bool:
    if os.name != "nt":
        return False
    # msvcrt reads the console input handle. stdout may be redirected to a
    # log file while stdin is still an interactive console.
    if not sys.stdin.isatty():
        return False
    try:
        import msvcrt  # noqa: F401
    except Exception:
        return False
    return True


def _default_local_asr_backend() -> str:
    value = str(os.environ.get("GO2_ASR_BACKEND", "funasr-local")).strip().lower()
    if value in {"funasr", "funasr-local", "local"}:
        return "funasr-local"
    return "remote"


def _weather_condition_zh(condition: WeatherCondition | str) -> str:
    value = str(getattr(condition, "value", condition)).strip().lower()
    return {
        "sunny": "晴",
        "cloudy": "多云",
        "overcast": "阴",
        "rain": "雨",
        "snow": "雪",
        "fog": "雾",
    }.get(value, "天气状态未知")


def discover_lan_ipv4(robot_ip: str) -> str | None:
    """Return the local IPv4 selected for the route to Go2 without sending data."""

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((robot_ip, 9991))
        address = str(sock.getsockname()[0])
        return address if address and address != "0.0.0.0" else None
    except OSError:
        return None
    finally:
        sock.close()


def _emergency_volume_gain() -> float:
    try:
        value = float(os.environ.get("GO2_EMERGENCY_VOLUME_GAIN", "1.6"))
    except ValueError:
        return 1.6
    return max(1.0, min(3.0, value))


def _prepare_emergency_voice_file(source: Path, *, gain: float) -> Path:
    if gain <= 1.001:
        return source
    try:
        stat = source.stat()
    except OSError:
        return source
    cache_dir = VOICE_PRESET_DIR / ".emergency_cache"
    cache_name = (
        f"{source.stem}_emergency_g{str(round(gain, 2)).replace('.', '_')}_"
        f"{stat.st_mtime_ns}_{stat.st_size}.wav"
    )
    target = cache_dir / cache_name
    if target.is_file():
        return target
    try:
        return _write_limited_gain_pcm16_wav(source, target, gain=gain)
    except Exception as exc:
        LOGGER.warning(
            "EMERGENCY_VOICE_GAIN_SKIPPED path=%s reason=%s: %s",
            source,
            type(exc).__name__,
            exc,
        )
        return source


def _write_limited_gain_pcm16_wav(source: Path, target: Path, *, gain: float) -> Path:
    with wave.open(str(source), "rb") as stream:
        params = stream.getparams()
        frames = stream.readframes(stream.getnframes())
    if params.sampwidth != 2 or params.comptype != "NONE":
        return source
    samples = array("h")
    samples.frombytes(frames)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return source
    peak = max(abs(sample) for sample in samples)
    limit = 32700
    effective_gain = min(float(gain), limit / peak) if peak else float(gain)
    if effective_gain <= 1.001:
        return source
    for index, sample in enumerate(samples):
        boosted = int(round(sample * effective_gain))
        samples[index] = max(-32768, min(32767, boosted))
    if sys.byteorder != "little":
        samples.byteswap()
    target.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(target), "wb") as stream:
        stream.setparams(params)
        stream.writeframes(samples.tobytes())
    return target


class RuntimeConsole:
    def __init__(
        self,
        runtime: Go2WirelessRuntime,
        service: RobotService,
        controller: ScriptedMotionController,
        *,
        video_host: str,
        video_port: int,
        lan_ip: str | None,
        asr_service: HealthNewASRService | None = None,
        tts_service: HealthNewTTSService | None = None,
        weather_cache: HealthNewWeatherCache | None = None,
        speech_cache: CompanionSpeechCache | None = None,
        elder_name: str = "李四",
        agent_client: CompanionAgentClient | None = None,
        follow_target_source: Go2UwbFollowTargetSource | None = None,
        follow_target_forwarder: UdpFollowTargetForwarder | None = None,
        voice_services_factory: Callable[[], tuple[Any, Any, Any]] | None = None,
        manual_confirm_start: bool = False,
        voice_business_interactions_enabled: bool = True,
        demo_console: bool = False,
    ) -> None:
        self.runtime = runtime
        self.service = service
        self.controller = controller
        self.video_host = video_host
        self.video_port = video_port
        self.lan_ip = lan_ip
        self.asr_service = asr_service
        self.tts_service = tts_service
        self.weather_cache = weather_cache
        self.speech_cache = speech_cache
        self.elder_name = str(elder_name or "李四").strip() or "李四"
        self.agent_client = agent_client
        self.follow_target_source = follow_target_source
        self.follow_target_forwarder = follow_target_forwarder
        self.voice_services_factory = voice_services_factory
        # Retained in the constructor for old callers only. Competition motion
        # starts are never allowed to block stdin for an operator phrase.
        self.manual_confirm_start = False
        self.voice_business_interactions_enabled = bool(
            voice_business_interactions_enabled
        )
        self.demo_console = bool(demo_console)
        self.voice_intent_adapter = VoiceIntentAdapter()
        self.lifecycle = CompetitionLifecycle()
        self.manual_controller = ManualKeyboardController(
            service,
            event_callback=self._manual_event,
        )
        self.fall_manual_controller = ManualKeyboardController(
            service,
            config=FALL_MANUAL_CONTROL_CONFIG,
            event_callback=self._manual_event,
        )
        self._fall_manual_stop = threading.Event()
        self._fall_manual_thread: threading.Thread | None = None
        self._manual_console_stop = threading.Event()
        self._manual_console_active = threading.Event()
        self._motion_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._motion_thread: threading.Thread | None = None
        self._motion_name: str | None = None
        self._motion_cancel = threading.Event()
        self._follow_status: dict[str, object] = {
            "state": "IDLE",
            "motion": "STOPPED",
            "autoRecovery": "IDLE",
        }
        self._last_follow_progress_log_at = 0.0
        self._lifecycle_notifications: list[dict[str, object]] = []
        self._emergency_voice_thread: threading.Thread | None = None
        self._emergency_voice_cancel = threading.Event()
        self._emergency_voice_generation = 0
        self._voice_layer_lock = threading.Lock()
        self._voice_preload_attempted = False
        self.control_adapter: Go2ControlAdapter | None = None
        self._voice_session_manager: LocalVoiceSessionManager | None = None
        self._interaction_flow_controller: InteractionFlowController | None = None
        self._local_voice_agent: LocalFirstXiaokangAgent | None = None
        self._go2_asr_bridge: Go2ASRAudioBridge | None = None
        self._motion_generation = 0
        self._was_following_before_fall = False
        self._voice_playback_lock = threading.Lock()
        self._voice_playback_busy = False
        self._voice_playback_active = False
        self._voice_playback_signature: tuple[str, ...] | None = None
        self._voice_playback_seq = 0
        self._voice_playback_generation = 0
        self._voice_startup_ready = False
        self._asr_startup_ready = False
        self._voice_listener_paused = False
        self._last_script_action: CompetitionAction | None = None
        self._hotkey_action_lock = threading.Lock()
        self._follow_stop_lock = threading.Lock()
        self._manual_takeover_lock = threading.Lock()

    def _demo_event(self, message: str) -> None:
        if bool(getattr(self, "demo_console", False)):
            _emit_demo_console(message)

    def _demo_rejection(self) -> None:
        self._demo_event("操作未执行：当前系统状态不允许")

    def set_control_adapter(self, adapter: Go2ControlAdapter) -> None:
        self.control_adapter = adapter

    def set_voice_session_manager(self, manager: LocalVoiceSessionManager) -> None:
        self._voice_session_manager = manager

    def set_local_voice_agent(self, agent: LocalFirstXiaokangAgent) -> None:
        self._local_voice_agent = agent

    def set_go2_asr_bridge(self, bridge: Go2ASRAudioBridge) -> None:
        self._go2_asr_bridge = bridge

    def _ensure_control_runtime_state(self) -> None:
        if not hasattr(self, "_state_lock"):
            self._state_lock = threading.RLock()
        if not hasattr(self, "_motion_generation"):
            self._motion_generation = 0
        if not hasattr(self, "_was_following_before_fall"):
            self._was_following_before_fall = False
        if not hasattr(self, "_emergency_voice_cancel"):
            self._emergency_voice_cancel = threading.Event()
        if not hasattr(self, "_emergency_voice_generation"):
            self._emergency_voice_generation = 0
        if not hasattr(self, "_priority_hotkey_lock"):
            self._priority_hotkey_lock = threading.Lock()
        if not hasattr(self, "_priority_hotkey_suppressed_until"):
            self._priority_hotkey_suppressed_until: dict[str, float] = {}
        if not hasattr(self, "_follow_stop_lock"):
            self._follow_stop_lock = threading.Lock()
        if not hasattr(self, "_manual_console_stop"):
            self._manual_console_stop = threading.Event()
        if not hasattr(self, "_manual_console_active"):
            self._manual_console_active = threading.Event()

    def set_interaction_flow_controller(
        self,
        controller: InteractionFlowController,
    ) -> None:
        self._interaction_flow_controller = controller

    def _cancel_pending_motion_actions(self, *, reason: str) -> int:
        self._ensure_control_runtime_state()
        with self._state_lock:
            self._motion_generation = int(getattr(self, "_motion_generation", 0)) + 1
            generation = self._motion_generation
        agent = getattr(self, "_local_voice_agent", None)
        cancel = getattr(agent, "cancel_pending_actions", None)
        cancelled = int(cancel(reason=reason)) if callable(cancel) else 0
        print(
            "[MOTION] pending starts invalidated "
            f"reason={reason} count={cancelled} generation={generation}"
        )
        return cancelled

    def _cancel_emergency_voice(self, *, reason: str) -> None:
        self._ensure_control_runtime_state()
        with self._state_lock:
            self._emergency_voice_generation += 1
            generation = self._emergency_voice_generation
            self._emergency_voice_cancel.set()
        print(f"[FALL] pending audio cancelled reason={reason} generation={generation}")

    def execute_local_interaction_event(
        self,
        event: str,
        *,
        payload: dict[str, Any] | None = None,
        status_callback: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        """Run a keyboard business event in-process without a transport round trip."""

        flow = getattr(self, "_interaction_flow_controller", None)
        handle_event = getattr(flow, "handle_event", None)
        if not callable(handle_event):
            print(
                "LOCAL_INTERACTION_SKIPPED: local interaction flow is not active"
            )
            return {
                "event": str(event or "").strip().upper(),
                "decisions": 0,
                "playback": [],
                "actions": [],
                "skipped": True,
            }
        event_name = str(event or "").strip().upper()
        event_payload = {
            "source": "operator_control",
            "session_id": f"local-{event_name.lower()}-{time.time_ns()}",
            **dict(payload or {}),
        }
        decisions = list(handle_event(event_name, event_payload))
        playback_results: list[dict[str, object]] = []
        executed_actions: list[str] = []
        for decision in decisions:
            intent = str(getattr(decision, "intent", "unknown") or "unknown")
            clips = [str(item) for item in tuple(getattr(decision, "clips", ()) or ())]
            action = str(getattr(decision, "action", "") or "").strip()
            print(f"[LOCAL ACTION] event={event_name} intent={intent}")
            if getattr(decision, "heart_rate", None) is not None or getattr(
                decision, "health_status", None
            ) is not None:
                print(
                    "[HEALTH] "
                    f"hr={getattr(decision, 'heart_rate', None)} "
                    f"status={getattr(decision, 'health_status', None)}"
                )
            if getattr(decision, "weather", None) is not None or getattr(
                decision, "temperature", None
            ) is not None:
                print(
                    f"[WEATHER] {getattr(decision, 'weather', None)} "
                    f"{getattr(decision, 'temperature', None)}C"
                )

            if action == "stop_follow":
                self._cancel_pending_motion_actions(reason="local_interaction_stop")
                self._run_control_command(
                    "stop_follow",
                    request_id=f"local-stop-{time.time_ns()}",
                    payload={},
                )
                executed_actions.append(action)

            if clips:
                print(f"[VOICE] local clips={clips}")
                try:
                    playback = self.play_voice_clips(
                        clips,
                        request_id=(
                            f"local-voice-{event_name.lower()}-{time.time_ns()}"
                        ),
                        session_id=str(event_payload["session_id"]),
                        source="operator_control",
                        status_callback=status_callback,
                    )
                finally:
                    time.sleep(0.4)
                    bridge = getattr(self, "_go2_asr_bridge", None)
                    if bridge is not None:
                        bridge.clear_pending_audio()
                        bridge.arm_post_playback_quiet_gate()
                playback_results.append(playback)
                if playback.get("status") != "done":
                    raise WirelessCompanionControlError(
                        "VOICE_PLAYBACK_FAILED",
                        str(playback.get("status") or "unknown"),
                        503,
                    )

            if action == "start_follow":
                if self.lifecycle.risk_active:
                    raise WirelessCompanionControlError(
                        "COMPANION_STATE_CONFLICT",
                        "risk_active",
                        409,
                    )
                self._run_control_command(
                    "start_follow",
                    request_id=f"local-start-{time.time_ns()}",
                    payload={
                        "duration_minutes": 3,
                        "skip_start_announcement": True,
                    },
                )
                executed_actions.append(action)
            elif action and action != "stop_follow":
                raise WirelessCompanionControlError(
                    "LOCAL_ACTION_UNSUPPORTED",
                    action,
                    422,
                )

        print(
            f"LOCAL_INTERACTION_DONE: event={event_name} "
            f"decisions={len(decisions)} actions={executed_actions}"
        )
        return {
            "event": event_name,
            "decisions": len(decisions),
            "playback": playback_results,
            "actions": executed_actions,
        }

    def _demo_phase(self) -> str:
        flow = getattr(self, "_interaction_flow_controller", None)
        context = getattr(flow, "context", None)
        return str(getattr(context, "demo_phase", "unavailable"))

    def _set_demo_phase(self, phase: str) -> None:
        flow = getattr(self, "_interaction_flow_controller", None)
        context = getattr(flow, "context", None)
        if context is not None:
            context.demo_phase = str(phase)

    def _print_demo_guidance(self) -> None:
        phase = self._demo_phase()
        next_action = {
            "skill2_ready": CompetitionAction.SKILL2_REPORT,
            "skill3_ready": CompetitionAction.MEDICATION_RECHECK,
            "skill3_wait_depart": CompetitionAction.OUTING_START,
            "skill3_first_following": CompetitionAction.FOLLOW_STOP,
            "skill3_first_follow_stopped": CompetitionAction.FOLLOW_RESUME,
            "fall_demo_following": CompetitionAction.FALL_PROMPT_1,
            "following": CompetitionAction.FALL_PROMPT_1,
            "fall_check_1": CompetitionAction.FALL_HELP,
            "fall_check_2": CompetitionAction.FALL_HELP,
            "wait_reading": CompetitionAction.MANUAL_TAKEOVER,
            "wait_resume": CompetitionAction.FOLLOW_RESUME,
            "final_following": CompetitionAction.FOLLOW_STOP,
        }.get(phase)
        if phase in {"skill3_first_start_pending", "following_start_pending"}:
            next_action_name = "WAIT_FOR_AUDIO"
        elif phase == "complete":
            next_action_name = "NONE"
        elif next_action is None:
            next_action_name = "STATUS"
        else:
            next_action_name = next_action.value
        print(f"[DEMO] phase={phase} NEXT_ACTION={next_action_name}")

    def trigger_script_action(
        self,
        action: CompetitionAction | str,
        *,
        status_callback: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        normalized_action = _coerce_competition_action(action)
        flow = getattr(self, "_interaction_flow_controller", None)
        if not callable(getattr(flow, "handle_event", None)):
            raise WirelessCompanionControlError(
                "DEMO_STEP_UNAVAILABLE",
                "voice interaction flow is not active",
                503,
            )
        event = {
            CompetitionAction.SKILL2_REPORT: "OPERATOR_OUTING_ASSESSMENT",
            CompetitionAction.MEDICATION_RECHECK: "OPERATOR_MEDICATION_CHECK",
            CompetitionAction.OUTING_START: "OPERATOR_DEPART",
        }[normalized_action]
        replay = getattr(self, "_last_script_action", None) is normalized_action
        if normalized_action in {
            CompetitionAction.SKILL2_REPORT,
            CompetitionAction.MEDICATION_RECHECK,
        }:
            self._wait_for_competition_weather()
        start_message = {
            CompetitionAction.SKILL2_REPORT: "外出健康评估开始",
            CompetitionAction.MEDICATION_RECHECK: "出行前健康复查开始",
        }.get(normalized_action)
        if start_message:
            self._demo_event(start_message)
        interaction_kwargs: dict[str, Any] = {
            "payload": (
                {
                    "replay": True,
                    "session_id": f"terminal-{event.lower()}-replay-{time.time_ns()}",
                }
                if replay
                else None
            )
        }
        if status_callback is not None:
            interaction_kwargs["status_callback"] = status_callback
        self.execute_local_interaction_event(event, **interaction_kwargs)
        self._last_script_action = normalized_action
        if replay:
            self._demo_event("当前业务语音已重新播放")
        if normalized_action is CompetitionAction.SKILL2_REPORT:
            self._demo_event("健康状态数据已获取")
            self._demo_event("外出条件满足")
        elif normalized_action is CompetitionAction.MEDICATION_RECHECK:
            self._demo_event("等待服药确认")
        if normalized_action is CompetitionAction.OUTING_START:
            self._set_demo_phase(
                "skill3_first_following"
                if self.lifecycle.state is CompanionState.FOLLOWING
                else "skill3_first_follow_stopped"
            )
        self._print_demo_guidance()
        return {
            "accepted": True,
            "action": normalized_action.value,
            "phase": self._demo_phase(),
            **({"replay": True} if replay else {}),
        }

    def trigger_script_step_from_hotkey(self, command: str) -> dict[str, object]:
        """Compatibility wrapper for older tests and operator integrations."""

        return self.trigger_script_action(command)

    def _read_command(self, prompt: str) -> str:
        if not _windows_hotkeys_available():
            visible_prompt = "" if bool(getattr(self, "demo_console", False)) else prompt
            return _normalize_console_command(input(visible_prompt))
        import ctypes
        import msvcrt

        buffer: list[str] = []
        show_input = not bool(getattr(self, "demo_console", False))
        if show_input:
            print(prompt, end="", flush=True)
        while True:
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                scan_code = msvcrt.getwch()
                shift_down = bool(
                    int(ctypes.windll.user32.GetAsyncKeyState(0x10)) & 0x8000
                )
                mapped = _console_hotkey_command(
                    scan_code,
                    shift_down=shift_down,
                )
                if mapped is None:
                    continue
                label, command = mapped
                watcher = getattr(self, "_priority_hotkey_thread", None)
                if watcher is not None and watcher.is_alive():
                    # The global key-state watcher is the sole owner of
                    # physical F-keys. Consume the console scan code so the
                    # same press cannot execute twice.
                    continue
                if self._consume_priority_hotkey_suppression(label):
                    continue
                return command
            if ch == "\x03":
                raise KeyboardInterrupt
            if ch in ("\r", "\n"):
                if show_input:
                    print()
                return _normalize_console_command("".join(buffer))
            if ch == "\b":
                if buffer:
                    buffer.pop()
                    if show_input:
                        print("\b \b", end="", flush=True)
                continue
            if ch and ch >= " ":
                buffer.append(ch)
                if show_input:
                    print(ch, end="", flush=True)

    def _suppress_buffered_priority_hotkey(self, label: str) -> None:
        self._ensure_control_runtime_state()
        with self._priority_hotkey_lock:
            self._priority_hotkey_suppressed_until[label.upper()] = (
                time.monotonic() + 30.0
            )

    def _consume_priority_hotkey_suppression(self, label: str) -> bool:
        self._ensure_control_runtime_state()
        now = time.monotonic()
        with self._priority_hotkey_lock:
            deadline = self._priority_hotkey_suppressed_until.pop(
                label.upper(), None
            )
            stale = [
                key
                for key, value in self._priority_hotkey_suppressed_until.items()
                if value < now
            ]
            for key in stale:
                self._priority_hotkey_suppressed_until.pop(key, None)
        return deadline is not None and deadline >= now

    def _dispatch_priority_hotkey(self, label: str) -> None:
        action = HOTKEY_ACTIONS[label.upper()]
        try:
            self.execute_competition_action(action)
        except WirelessCompanionControlError as exc:
            print(
                f"操作未执行:{action.value}:{exc.code}:{exc.message}",
                flush=True,
            )
            if not _is_quiet_fall_rejection(action, exc):
                self._demo_rejection()
        except Exception as exc:
            print(
                f"系统操作失败:{action.value}:{type(exc).__name__}:{exc}",
                flush=True,
            )
            self._demo_rejection()

    def _start_priority_hotkey_watcher(self) -> None:
        """Keep every physical competition key active while stdin is busy."""

        if not _windows_hotkeys_available():
            return
        existing = getattr(self, "_priority_hotkey_thread", None)
        if existing is not None and existing.is_alive():
            return
        import ctypes

        stop_event = threading.Event()
        self._priority_hotkey_stop = stop_event
        get_async_key_state = ctypes.windll.user32.GetAsyncKeyState
        get_async_key_state.argtypes = [ctypes.c_int]
        get_async_key_state.restype = ctypes.c_short

        def watch_priority_hotkeys() -> None:
            virtual_keys = dict(FUNCTION_KEY_VIRTUAL_KEYS)
            was_down = {label: False for label in virtual_keys}
            while not stop_event.wait(0.02):
                ctrl_down = bool(int(get_async_key_state(0x11)) & 0x8000)
                shift_down = bool(int(get_async_key_state(0x10)) & 0x8000)
                for label, virtual_key in virtual_keys.items():
                    key_state = int(get_async_key_state(virtual_key))
                    is_down = bool(key_state & 0x8000)
                    pressed_since_scan = bool(key_state & 0x0001)
                    if (is_down and not was_down[label]) or (
                        pressed_since_scan and not was_down[label]
                    ):
                        effective_label = _hotkey_label_for_keypress(
                            label,
                            ctrl_down=ctrl_down,
                            shift_down=shift_down,
                        )
                        if effective_label is None:
                            continue
                        action = HOTKEY_ACTIONS[effective_label.upper()]
                        threading.Thread(
                            target=self._dispatch_priority_hotkey,
                            args=(effective_label,),
                            name=f"go2-{action.value.lower()}-operator-control",
                            daemon=True,
                        ).start()
                    was_down[label] = is_down

        self._priority_hotkey_thread = threading.Thread(
            target=watch_priority_hotkeys,
            name="go2-priority-safety-controls",
            daemon=True,
        )
        self._priority_hotkey_thread.start()

    def _play_fall_voice_fallback(self, clips: list[str]) -> dict[str, object]:
        result = self.play_voice_clips(clips)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result

    def play_xiaokang_wake_ack(self) -> dict[str, object]:
        result = self.play_voice_clips(
            ["sess.wake_ack"],
            session_id="terminal-wake-ack",
            source="operator_action",
        )
        if result.get("status") != "done":
            raise WirelessCompanionControlError(
                "WAKE_ACK_PLAYBACK_FAILED",
                str(result.get("status") or "unknown"),
                503,
            )
        self._demo_event("小康已回应")
        return result

    def trigger_fall(self) -> dict[str, object]:
        self._ensure_control_runtime_state()
        snapshot = self.lifecycle.snapshot()
        with self._state_lock:
            thread = getattr(self, "_motion_thread", None)
            self._was_following_before_fall = bool(
                snapshot.state is CompanionState.FOLLOWING
                or (
                    thread is not None
                    and thread.is_alive()
                    and getattr(self, "_motion_name", None) == "companion"
                )
            )
        self._cancel_pending_motion_actions(reason="fall_suspected")
        incident_id = snapshot.active_incident_id or f"operator-{time.time_ns()}"
        result = self.lifecycle.ingest_fall(
            incident_id=incident_id,
            confirmed=False,
        )
        if not result.accepted:
            raise WirelessCompanionControlError(
                "FALL_SUSPECTED_REJECTED",
                result.reason,
                409,
            )
        self._demo_event("检测到疑似跌倒")
        if self._was_following_before_fall:
            self.stop_motion()
            self._wait_for_motion_stop()
            self._demo_event("自主伴随已停止")
        self._interrupt_voice_playback(reason="fall_suspected")
        self._record_lifecycle_actions(result.to_dict())
        manual_ready = self._start_fall_manual_mode()
        self.execute_local_interaction_event(
            "FALL_SUSPECTED",
            payload={
                "incident_id": incident_id,
                "motion_already_stopped": True,
            },
        )
        self._print_demo_guidance()
        return {
            "fallTriggered": True,
            "incident_id": incident_id,
            "fallManualReady": manual_ready,
            "lifecycle": result.to_dict(),
            "companion": self.companion_status(),
        }

    def recover_fall(self) -> dict[str, object]:
        if not self.lifecycle.risk_active:
            raise WirelessCompanionControlError(
                "FALL_RECOVERY_REJECTED",
                "no_active_fall",
                409,
            )
        self._cancel_emergency_voice(reason="fall_recovered")
        self._cancel_pending_motion_actions(reason="fall_recovered")
        self.stop_motion()
        self._wait_for_motion_stop()
        self._interrupt_voice_playback(reason="fall_recovered")
        lifecycle_result: dict[str, object] | None = None
        if self.lifecycle.risk_active:
            snapshot = self.lifecycle.snapshot()
            incident_id = snapshot.active_incident_id
            if not incident_id:
                raise WirelessCompanionControlError(
                    "FALL_RECOVERY_REJECTED",
                    "active fall has no incident_id",
                    409,
                )
            cleared = self.lifecycle.clear_risk(incident_id=incident_id)
            if not cleared.accepted:
                raise WirelessCompanionControlError(
                    "FALL_RECOVERY_REJECTED",
                    cleared.reason,
                    409,
                )
            lifecycle_result = cleared.to_dict()
            self.lifecycle.stop(reason="fall_recovered_idle")
        self.execute_local_interaction_event(
            "FALL_RECOVERED",
            payload={"force_recovered": True},
        )
        self._demo_event("老人状态恢复")
        self._print_demo_guidance()
        return {
            "recovered": True,
            "lifecycle": lifecycle_result,
            "companion": self.companion_status(),
        }

    def trigger_reading(self) -> dict[str, object]:
        flow = getattr(self, "_interaction_flow_controller", None)
        flow_safety_state = str(
            getattr(getattr(flow, "context", None), "safety_state", "normal")
        )
        if self.lifecycle.state in {
            CompanionState.HELP_REQUESTED,
            CompanionState.ESCALATED_EMERGENCY,
        } or flow_safety_state == "helping":
            raise WirelessCompanionControlError(
                "READING_REJECTED",
                "emergency_helping_must_be_recovered_before_manual_takeover",
                409,
            )
        self._cancel_emergency_voice(reason="normal_activity_reading")
        self._cancel_pending_motion_actions(reason="normal_activity_reading")
        self._interrupt_voice_playback(reason="normal_activity_reading")
        self.stop_motion()
        self._wait_for_motion_stop()
        lifecycle_result: dict[str, object] | None = None
        if self.lifecycle.risk_active:
            snapshot = self.lifecycle.snapshot()
            incident_id = snapshot.active_incident_id
            if not incident_id:
                raise WirelessCompanionControlError(
                    "READING_REJECTED",
                    "active fall has no incident_id",
                    409,
                )
            cleared = self.lifecycle.clear_risk(incident_id=incident_id)
            if not cleared.accepted:
                raise WirelessCompanionControlError(
                    "READING_REJECTED",
                    cleared.reason,
                    409,
                )
            lifecycle_result = cleared.to_dict()
            if not bool(getattr(self, "_was_following_before_fall", False)):
                self.lifecycle.stop(reason="reading_false_alarm_from_idle")
        elif self.lifecycle.state is CompanionState.FOLLOWING:
            self.lifecycle.stop(reason="normal_activity_reading")
        self.execute_local_interaction_event("NORMAL_ACTIVITY_READING")
        self._demo_event("正常阅读行为确认")
        self._print_demo_guidance()
        return {
            "readingTriggered": True,
            "lifecycle": lifecycle_result,
            "companion": self.companion_status(),
        }

    def advance_fall_timeout(self, *, stage: int) -> dict[str, object]:
        expected_state = CompanionState.VOICE_CHECK if stage == 1 else CompanionState.RECHECK
        if stage not in {1, 2}:
            raise ValueError("fall timeout stage must be 1 or 2")
        replay_state = (
            CompanionState.RECHECK
            if stage == 1
            else CompanionState.ESCALATED_EMERGENCY
        )
        if self.lifecycle.risk_active and self.lifecycle.state is replay_state:
            clips = (
                ["fall.confirm.second"]
                if stage == 1
                else ["fall.alert.sound", "fall.help.broadcast"]
            )
            playback = self.play_voice_clips(
                clips,
                session_id=f"fall-stage-{stage}-replay",
                source="operator_replay",
            )
            return {
                "fallStage": stage,
                "replay": True,
                "playback": playback,
                "companion": self.companion_status(),
            }
        skip_second_prompt = False
        if (
            stage == 2
            and self.lifecycle.risk_active
            and self.lifecycle.state is CompanionState.VOICE_CHECK
        ):
            skipped = self.lifecycle.no_response()
            if not skipped.accepted:
                raise WirelessCompanionControlError(
                    "FALL_STAGE_REJECTED", skipped.reason, 409
                )
            skip_second_prompt = True
        if not self.lifecycle.risk_active or self.lifecycle.state is not expected_state:
            raise WirelessCompanionControlError(
                "FALL_STAGE_CONFLICT",
                f"stage={stage} requires {expected_state.value}; observed={self.lifecycle.state.value}",
                409,
            )
        result = self.lifecycle.no_response()
        if not result.accepted:
            raise WirelessCompanionControlError(
                "FALL_STAGE_REJECTED",
                result.reason,
                409,
            )
        self._hold_fall_manual_position(reason=f"fall_stage_{stage}")
        self._record_lifecycle_actions(result.to_dict())
        self._demo_event(
            "进入二次安全确认" if stage == 1 else "紧急求助已启动"
        )
        self.execute_local_interaction_event(
            "FALL_RESPONSE_TIMEOUT",
            payload={
                "stage": stage,
                **({"skip_second_prompt": True} if skip_second_prompt else {}),
            },
        )
        self._print_demo_guidance()
        return {
            "fallStage": stage,
            "lifecycle": result.to_dict(),
            "companion": self.companion_status(),
        }

    def recover_voice_pipeline(self) -> dict[str, object]:
        """Recover playback/ASR/session state without clearing demo context."""

        self._cancel_pending_motion_actions(reason="voice_recovery")
        self._interrupt_voice_playback(reason="voice_recovery")
        bridge = getattr(self, "_go2_asr_bridge", None)
        drained = 0
        if bridge is not None:
            drained = int(bridge.clear_pending_audio())
            bridge.arm_post_playback_quiet_gate()
        manager = getattr(self, "_voice_session_manager", None)
        if manager is not None:
            manager.recover_to_wake_guard(reason="voice_recovery")
        print(
            "[VOICE] RECOVERY COMPLETE - business/safety/motion context preserved; "
            f"pcm_frames_cleared={drained}"
        )
        self._demo_event("语音链路已恢复")
        return {
            "voiceRecovered": True,
            "pcmFramesCleared": drained,
        }

    def toggle_voice_listener(self) -> bool:
        manager = self._voice_session_manager
        if manager is None:
            print("[VOICE] LISTENER UNAVAILABLE")
            return False
        will_enable = not manager.listener_enabled
        if not will_enable:
            flow = self._interaction_flow_controller
            if flow is not None:
                flow.clear_pending_reply()
        enabled, _messages = manager.toggle_listener()
        self._voice_listener_paused = not enabled
        self._demo_event("小康监听已恢复" if enabled else "小康监听已暂停")
        return enabled

    def start_or_resume_follow(self, *, announce: bool = True) -> dict[str, object]:
        """Start follow after optional speech and the normal readiness checks."""

        self._ensure_control_runtime_state()
        # Motion stops before the stop announcement ends. Wait for the whole
        # stop operation so this announcement cannot reject the restart audio.
        with self._follow_stop_lock:
            pass
        if self.lifecycle.risk_active:
            raise WirelessCompanionControlError(
                "COMPANION_STATE_CONFLICT",
                "risk_active",
                409,
            )
        manual_interrupted = self._stop_manual_console_for_follow_resume()
        with self._state_lock:
            motion_thread = getattr(self, "_motion_thread", None)
            motion_name = getattr(self, "_motion_name", None)
            motion_generation = self._motion_generation
        if motion_thread is not None and motion_thread.is_alive():
            if motion_name == "companion":
                print("START accepted -> already FOLLOWING")
                return self.companion_status()
            raise WirelessCompanionControlError(
                "CONTROL_BUSY",
                "MOTION_BUSY",
                409,
            )
        observed_phase = self._demo_phase()
        if announce:
            playback = self.play_voice_clips(
                ["follow.resume.safe"],
                session_id="terminal-resume",
                source="operator_action",
            )
            if playback.get("status") != "done":
                raise WirelessCompanionControlError(
                    "START_ANNOUNCEMENT_FAILED",
                    str(playback.get("status") or "unknown"),
                    503,
                )

        with self._state_lock:
            if motion_generation != self._motion_generation:
                raise WirelessCompanionControlError(
                    "START_CANCELLED",
                    "motion generation changed during announcement",
                    409,
                )
            motion_thread = getattr(self, "_motion_thread", None)
        if motion_thread is not None and motion_thread.is_alive():
            raise WirelessCompanionControlError(
                "CONTROL_BUSY",
                "MOTION_BUSY",
                409,
            )
        if self.lifecycle.risk_active:
            raise WirelessCompanionControlError(
                "COMPANION_STATE_CONFLICT",
                "risk_active",
                409,
            )
        use_resume = (
            self.lifecycle.state is CompanionState.WAIT_RESUME
            and bool(getattr(self, "_was_following_before_fall", False))
        )
        if manual_interrupted:
            # The adapter may still remember FOLLOWING from before manual
            # takeover and answer a start request as a no-op.  Start through
            # this console so the actual Runtime worker is recreated.
            started = self.start_companion()
        else:
            started = self._run_control_command(
                "resume_follow" if use_resume else "start_follow",
                request_id="terminal-resume" if use_resume else "terminal-start",
                payload={},
            )
        next_phase = {
            "skill3_first_follow_stopped": "fall_demo_following",
            "wait_resume": "final_following",
        }.get(observed_phase, "following")
        self._set_demo_phase(next_phase)
        self._demo_event("自主伴随恢复" if use_resume else "自主伴随启动")
        print(
            "RESUME accepted -> FOLLOWING"
            if use_resume
            else "START accepted -> FOLLOWING"
        )
        self._print_demo_guidance()
        return started

    def _stop_manual_console_for_follow_resume(self) -> bool:
        """Release regular keyboard control before F5 restarts Companion."""

        manual_controller = getattr(self, "manual_controller", None)
        manual_console_active = bool(
            getattr(self, "_manual_console_active", threading.Event()).is_set()
        )
        lifecycle_is_manual = (
            getattr(getattr(self, "lifecycle", None), "state", None)
            is CompanionState.MANUAL_CONTROL
        )
        manual_active = bool(
            manual_console_active
            or getattr(manual_controller, "active", False)
            or lifecycle_is_manual
        )
        if not manual_active:
            return False

        stop_event = getattr(self, "_manual_console_stop", None)
        if stop_event is None:
            stop_event = threading.Event()
            self._manual_console_stop = stop_event
        stop_event.set()

        if manual_console_active:
            deadline = time.monotonic() + 1.0
            while (
                bool(
                    getattr(self, "_manual_console_active", threading.Event()).is_set()
                )
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
        if bool(
            getattr(self, "_manual_console_active", threading.Event()).is_set()
        ):
            raise WirelessCompanionControlError(
                "MANUAL_STOP_NOT_CONFIRMED",
                "Manual control did not stop before the next motion action.",
                503,
            )

        # MANUAL can also be entered through the HTTP/manual-key API, where no
        # console loop exists to observe the event. Once a console thread has
        # exited, these checks only clean up any incomplete release it left.
        if bool(getattr(manual_controller, "active", False)):
            self.release_manual(quiet=True)
        elif lifecycle_is_manual and self.lifecycle.state is CompanionState.MANUAL_CONTROL:
            self.lifecycle.release_manual()
        return True

    def stop_follow(self, *, announce: bool = True) -> dict[str, object]:
        """Stop motion first, then optionally play the formal announcement."""

        self._ensure_control_runtime_state()
        with self._follow_stop_lock:
            observed_phase = self._demo_phase()
            # F4 must also release an active F11 manual-control session before
            # issuing the regular follow stop.
            try:
                self._stop_manual_console_for_follow_resume()
            except WirelessCompanionControlError as exc:
                LOGGER.warning(
                    "MANUAL_STOP_BEFORE_FOLLOW_STOP_FAILED: %s: %s",
                    exc.code,
                    exc.message,
                )
            self._cancel_pending_motion_actions(reason="operator_stop")
            stopped = self._run_control_command(
                "stop_follow",
                request_id="terminal-stop",
                payload={},
            )
            self._interrupt_voice_playback(reason="operator_stop")
            next_phase = {
                "skill3_first_start_pending": "skill3_first_follow_stopped",
                "skill3_first_following": "skill3_first_follow_stopped",
                "fall_demo_following": "skill3_first_follow_stopped",
                "final_following": "complete",
            }.get(observed_phase, observed_phase)
            self._set_demo_phase(next_phase)
            self._demo_event(
                "自主伴随结束"
                if observed_phase == "final_following"
                else "自主伴随停止"
            )
            print("STOP accepted -> motion stopped")
            if announce:
                try:
                    playback = self.play_voice_clips(
                        ["follow.stop"],
                        session_id="terminal-stop",
                        source="operator_action",
                    )
                    if playback.get("status") != "done":
                        print(f"STOP_ANNOUNCEMENT_FAILED: {playback}")
                except Exception as exc:
                    print(
                        "STOP_ANNOUNCEMENT_FAILED: "
                        f"{type(exc).__name__}: {exc}"
                    )
            self._print_demo_guidance()
            return stopped

    def _run_control_command(
        self,
        command: str,
        *,
        request_id: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        control_adapter = getattr(self, "control_adapter", None)
        if control_adapter is None:
            if command == "start_follow":
                return self.start_companion()
            if command == "stop_follow":
                return self.stop_companion()
            if command == "resume_follow":
                return self.resume_companion()
            raise ValueError(f"unsupported terminal control command: {command}")
        control_adapter.handle(
            CommandMessage(
                device_id=self.service.settings.robot_id,
                command=command,
                request_id=request_id,
                payload=dict(payload or {}),
            )
        )
        companion_status = getattr(self, "companion_status", None)
        if callable(companion_status):
            return dict(companion_status())
        return {}

    def execute_competition_action(
        self,
        action: CompetitionAction | str,
    ) -> object:
        """Execute one stable business action, independent of its input binding."""

        normalized_action = _coerce_competition_action(action)
        report_voice_status = normalized_action in VOICE_TASK_ACTIONS
        status_finished = False

        def report_status(message: str) -> None:
            nonlocal status_finished
            if status_finished:
                return
            self._print_operator_line(message)
            if message in {"播报完成", "语音播报失败，请重试"}:
                status_finished = True

        if report_voice_status:
            report_status("语音任务已接收")
        handlers: dict[CompetitionAction, Callable[[], object]] = {
            CompetitionAction.SKILL2_REPORT: lambda: self.trigger_script_action(
                CompetitionAction.SKILL2_REPORT,
                status_callback=report_status,
            ),
            CompetitionAction.MEDICATION_RECHECK: lambda: self.trigger_script_action(
                CompetitionAction.MEDICATION_RECHECK,
                status_callback=report_status,
            ),
            CompetitionAction.OUTING_START: lambda: self.trigger_script_action(
                CompetitionAction.OUTING_START,
                status_callback=report_status,
            ),
            CompetitionAction.FOLLOW_RESUME: self.start_or_resume_follow,
            CompetitionAction.FOLLOW_STOP: self.stop_follow,
            CompetitionAction.FALL_PROMPT_1: self.trigger_fall,
            CompetitionAction.FALL_PROMPT_1_AUDIO: lambda: self._play_fall_voice_fallback(
                ["fall.confirm"]
            ),
            CompetitionAction.FALL_PROMPT_2: lambda: self.advance_fall_timeout(stage=1),
            CompetitionAction.FALL_HELP: lambda: self.advance_fall_timeout(stage=2),
            CompetitionAction.FALL_RECOVER: self.recover_fall,
            CompetitionAction.XIAOKANG_WAKE_ACK: self.play_xiaokang_wake_ack,
            CompetitionAction.READING_NORMAL: self.trigger_reading,
            CompetitionAction.MANUAL_TAKEOVER: self.manual_takeover,
            CompetitionAction.DEMO_RESET: self.reset_demo,
            CompetitionAction.VOICE_LISTENER_TOGGLE: self.toggle_voice_listener,
            CompetitionAction.QUICK_FOLLOW_RECOVERY: self.quick_follow_recovery,
            CompetitionAction.VOICE_RECOVERY: self.recover_voice_pipeline,
            CompetitionAction.KEYBOARD_CLOSE: self.close_keyboard_control,
            CompetitionAction.DIRECT_FOLLOW_START: lambda: self.start_or_resume_follow(
                announce=False
            ),
            CompetitionAction.DIRECT_FOLLOW_STOP: lambda: self.stop_follow(
                announce=False
            ),
        }
        handler = handlers[normalized_action]
        if normalized_action in PRIORITY_ACTIONS:
            return handler()
        self._ensure_control_runtime_state()
        if not hasattr(self, "_hotkey_action_lock"):
            self._hotkey_action_lock = threading.Lock()
        try:
            with self._hotkey_action_lock:
                result = handler()
        except Exception:
            if report_voice_status:
                report_status("语音播报失败，请重试")
            raise
        if report_voice_status and not status_finished:
            report_status("语音播报失败，请重试")
        return result

    # Compatibility names for integrations that predate the action layer.
    trigger_fall_from_hotkey = trigger_fall
    recover_fall_from_hotkey = recover_fall
    trigger_reading_from_hotkey = trigger_reading
    advance_fall_timeout_from_hotkey = advance_fall_timeout
    start_or_resume_from_hotkey = start_or_resume_follow
    stop_from_hotkey = stop_follow

    def _competition_weather_snapshot(self) -> Any | None:
        flow = getattr(self, "_interaction_flow_controller", None)
        provider = getattr(flow, "weather_provider", None)
        get_weather = getattr(provider, "get_weather", None)
        if not callable(get_weather):
            return None
        try:
            return get_weather()
        except Exception:
            return None

    def _print_operator_line(self, message: str) -> None:
        if bool(getattr(self, "demo_console", False)):
            _emit_demo_console(message, timestamp=False)
        else:
            print(message)

    def _print_competition_status(self) -> None:
        runtime_status = dict(self.runtime.status() or {})
        uwb = dict(runtime_status.get("uwb") or {})
        fields = dict(uwb.get("fields") or {})
        uwb_ready = bool(
            uwb.get("fresh")
            and fields.get("enabled_from_app") == 1
            and fields.get("distance_est") is not None
            and fields.get("orientation_est") is not None
            and fields.get("error_state") in {None, 0}
        )
        weather = self._competition_weather_snapshot()
        weather_ready = bool(
            weather is not None
            and getattr(weather, "temperature", None) is not None
            and str(getattr(weather, "condition", "unknown")).lower()
            not in {"unknown", "weathercondition.unknown"}
        )
        lifecycle = self.lifecycle.snapshot()
        rows = (
            f"WebRTC    {'READY' if runtime_status.get('connected') else 'NOT_READY'}",
            f"Video     {'READY' if runtime_status.get('videoReady') else 'NOT_READY'}",
            f"ASR       {'READY' if getattr(self, '_asr_startup_ready', False) else 'NOT_READY'}",
            f"Audio     {'READY' if getattr(self, '_voice_startup_ready', False) else 'NOT_READY'}",
            f"UWB       {'READY' if uwb_ready else 'NOT_READY'}",
            f"Weather   {'READY' if weather_ready else 'FALLBACK'}",
            f"Robot     {self.companion_status().get('state', lifecycle.state.value)}",
            f"FallFlow  {'ACTIVE' if self.lifecycle.risk_active else 'IDLE'}",
        )
        for row in rows:
            self._print_operator_line(row)

    def _print_uwb_once(self) -> None:
        uwb = dict(self.companion_status().get("uwb") or {})
        self._print_operator_line(
            "UWB "
            f"target_valid={str(bool(uwb.get('valid'))).lower()} "
            f"distance={uwb.get('distance_m')}m "
            f"angle={uwb.get('bearing_deg')}deg "
            f"age_ms={uwb.get('age_ms')}"
        )

    def _print_weather_once(self) -> None:
        weather = self._competition_weather_snapshot()
        if weather is None:
            self._print_operator_line("WEATHER cache=unavailable")
            return
        condition = getattr(weather, "condition", None)
        condition_value = getattr(condition, "value", condition)
        self._print_operator_line(
            "WEATHER "
            f"city={getattr(weather, 'city', '北京')} "
            f"condition={condition_value} "
            f"temperature={getattr(weather, 'temperature', None)}C"
        )

    def _refresh_weather_background(self) -> None:
        flow = getattr(self, "_interaction_flow_controller", None)
        provider = getattr(flow, "weather_provider", None)
        start_prefetch = getattr(provider, "start_prefetch", None)
        if not callable(start_prefetch):
            self._print_operator_line("WEATHER_REFRESH unavailable")
            return
        started = bool(start_prefetch())
        self._print_operator_line(
            "WEATHER_REFRESH started"
            if started
            else "WEATHER_REFRESH already_running"
        )

    def _wait_for_competition_weather(self) -> Any | None:
        """Make F1/F2 assemble speech from the completed live snapshot."""

        flow = getattr(self, "_interaction_flow_controller", None)
        provider = getattr(flow, "weather_provider", None)
        wait_for_live_weather = getattr(provider, "wait_for_live_weather", None)
        if not callable(wait_for_live_weather):
            return None
        try:
            timeout_seconds = float(
                os.environ.get("XIAOKANG_WEATHER_READY_TIMEOUT", "1.8")
            )
        except (TypeError, ValueError):
            timeout_seconds = 1.8
        weather = wait_for_live_weather(max(0.0, timeout_seconds))
        if weather is not None:
            condition = getattr(weather, "condition", None)
            condition_value = getattr(condition, "value", condition)
            source = "fallback" if getattr(weather, "error", None) else "live"
            self._print_operator_line(
                "WEATHER_DEMO_READY "
                f"source={source} "
                f"condition={condition_value} "
                f"temperature={getattr(weather, 'temperature', None)}C"
            )
        return weather

    def run(self, *, auto_demo: str | None = None) -> int:
        self._start_priority_hotkey_watcher()
        status = self.runtime.status()
        if bool(getattr(self, "demo_console", False)):
            voice_ready = bool(getattr(self, "_voice_startup_ready", False))
            asr_ready = bool(getattr(self, "_asr_startup_ready", False))
            voice_paused = bool(getattr(self, "_voice_listener_paused", False))
            _emit_demo_console("=" * 48, timestamp=False)
            _emit_demo_console(
                "          Go2 Intelligent Care Runtime",
                timestamp=False,
            )
            _emit_demo_console("=" * 48, timestamp=False)
            _emit_demo_console(
                f"WebRTC      {'READY' if status['connected'] else 'DEGRADED'}",
                timestamp=False,
            )
            _emit_demo_console(
                f"Video       {'READY' if status['videoReady'] else 'DEGRADED'}",
                timestamp=False,
            )
            _emit_demo_console(
                f"Voice       {'PAUSED' if voice_paused else ('READY' if voice_ready else 'STANDBY')}",
                timestamp=False,
            )
            _emit_demo_console(
                f"ASR         {'READY' if asr_ready else 'STANDBY'}",
                timestamp=False,
            )
            _emit_demo_console("UWB         READY", timestamp=False)
            _emit_demo_console("Weather     READY", timestamp=False)
            _emit_demo_console("Robot       IDLE", timestamp=False)
            _emit_demo_console("FallFlow    IDLE", timestamp=False)
            _emit_demo_console("-" * 48, timestamp=False)
        else:
            print("=" * 57)
            print("Go2 Competition Wireless Runtime" if auto_demo else "Go2 Wireless Runtime")
            print(f"Robot          : {status['robotIp']}")
            print(f"WebRTC         : {'CONNECTED' if status['connected'] else 'NOT READY'}")
            print(f"PeerConnection : {status['connectionCount']}")
            print(f"DataChannel    : {'READY' if status['dataChannelReady'] else 'NOT READY'}")
            print(
                "SportState     : "
                + ("READY" if status["sportStateReady"] else "STANDBY")
            )
            print(f"Video Track    : {'READY' if status['videoReady'] else 'NOT READY'}")
            print(f"Companion Layer: {(status.get('layers') or {}).get('companion', 'unknown').upper()}")
            print(f"Voice Layer    : {(status.get('layers') or {}).get('voice', 'unknown').upper()}")
            print("Video Relay:")
            print(f"  Bind         : {self.video_host}:{self.video_port}")
            print(f"  Local        : http://127.0.0.1:{self.video_port}/stream.mjpg")
            print(
                "  LAN          : "
                + (
                    f"http://{self.lan_ip}:{self.video_port}/stream.mjpg"
                    if self.lan_ip
                    else "UNAVAILABLE (check Windows network route)"
                )
            )
            print(f"Motion Demo    : {auto_demo or 'manual'}")
            print("Operator Control: READY")
            print(
                "Voice Mode   : "
                + (
                    "wake-only (business actions use operator control)"
                    if not getattr(self, "voice_business_interactions_enabled", True)
                    else "wake + business speech"
                )
            )
            print("Diagnostic Interface: READY")
            print("=" * 57)
        self._print_demo_guidance()
        if auto_demo == "phone_demo":
            print("AUTO_DEMO: starting phone_demo")
            self._start_motion("phone_demo", self._phone_demo)
        while True:
            try:
                command = self._read_command("wireless> ").strip().upper()
            except (EOFError, KeyboardInterrupt):
                self._request_runtime_shutdown()
                self.stop_motion()
                return 130
            try:
                action = CompetitionAction(command)
            except ValueError:
                action = None
            if action is not None:
                try:
                    result = self.execute_competition_action(action)
                    if result is not None:
                        print(json.dumps(result, ensure_ascii=False, indent=2))
                except WirelessCompanionControlError as exc:
                    print(
                        f"ACTION_REJECTED:{action.value}:{exc.code}:{exc.message}",
                        flush=True,
                    )
                    if not _is_quiet_fall_rejection(action, exc):
                        self._demo_rejection()
                except Exception as exc:
                    print(
                        f"ACTION_FAILED:{action.value}:"
                        f"{type(exc).__name__}:{exc}",
                        flush=True,
                    )
                    self._demo_rejection()
                continue
            if command == "STATUS":
                self._print_competition_status()
            elif command == "UWB":
                self._print_uwb_once()
            elif command == "WEATHER":
                self._print_weather_once()
            elif command == "WEATHER_REFRESH":
                self._refresh_weather_background()
            elif command == "UWB_GATE":
                if self._motion_thread and self._motion_thread.is_alive():
                    print("UWB_GATE_REJECTED: MOTION_BUSY")
                    continue
                if (
                    input(f"Type {CONFIRM_UWB_READONLY}: ").strip()
                    != CONFIRM_UWB_READONLY
                ):
                    print("UWB_GATE_REJECTED")
                    continue
                self._uwb_gate()
            elif command == "MIC_GATE":
                if self._motion_thread and self._motion_thread.is_alive():
                    print("MIC_GATE_REJECTED: MOTION_BUSY")
                    continue
                if input(f"Type {CONFIRM_MIC_READONLY}: ").strip() != CONFIRM_MIC_READONLY:
                    print("MIC_GATE_REJECTED")
                    continue
                try:
                    self._mic_gate()
                except Exception as exc:
                    print(f"MIC_GATE_FAILED: {type(exc).__name__}: {exc}")
                    print("SPORT_COMMANDS_SENT=false")
                    print("WIRELESS_RUNTIME=CONTINUES")
            elif command == "VOICE_INTENT_GATE":
                if self._motion_thread and self._motion_thread.is_alive():
                    print("VOICE_INTENT_GATE_REJECTED: MOTION_BUSY")
                    continue
                print("VOICE_INTENT_GATE: read-only; confirmation not required")
                self._voice_intent_gate()
            elif command == "VOICE_CONTROL":
                try:
                    self.ensure_voice_ready()
                    print("VOICE_CONTROL: high-level lifecycle execution enabled")
                    self._voice_intent_gate(execute=True)
                except Exception as exc:
                    print(f"VOICE_CONTROL_FAILED: {type(exc).__name__}: {exc}")
            elif command == "VOICE_OFF":
                self.disable_voice_layer()
                print("VOICE_LAYER=STANDBY")
            elif command == "NO_RESPONSE":
                try:
                    print(json.dumps(self.record_no_response(), ensure_ascii=False, indent=2))
                except WirelessCompanionControlError as exc:
                    print(f"NO_RESPONSE_REJECTED: {exc.code}: {exc.message}")
            elif command == "MANUAL":
                self._manual_console()
            elif command == "WALK_FOLLOW":
                self._walk_follow()
            elif command == "FALL_TIMEOUT":
                self.execute_local_interaction_event("FALL_RESPONSE_TIMEOUT")
            elif command == "FOLLOW_3MIN":
                if self._motion_thread and self._motion_thread.is_alive():
                    print("FOLLOW_3MIN_REJECTED: MOTION_BUSY")
                    continue
                confirmations = (
                    CONFIRM_FOLLOW_3MIN,
                    CONFIRM_FOLLOW_NO_LIDAR,
                    CONFIRM_REMOTE_STOP,
                )
                rejected = False
                for expected in confirmations:
                    if input(f"Type {expected}: ").strip() != expected:
                        print(f"FOLLOW_3MIN_REJECTED: expected {expected}")
                        rejected = True
                        break
                if not rejected:
                    self._start_motion("follow_3min", self._follow_3min)
            elif command == "GATE":
                if input(f"Type {CONFIRM_GATE}: ").strip() != CONFIRM_GATE:
                    print("GATE_REJECTED")
                    continue
                self._start_motion("joint_gate", self._joint_gate)
            elif command == "POSE_GATE":
                if input(f"Type {CONFIRM_POSE}: ").strip() != CONFIRM_POSE:
                    print("POSE_GATE_REJECTED")
                    continue
                self._start_motion("pose_gate", self._pose_gate)
            elif command == "AUDIO_GATE":
                if input(f"Type {CONFIRM_AUDIO}: ").strip() != CONFIRM_AUDIO:
                    print("AUDIO_GATE_REJECTED")
                    continue
                self._start_motion("audio_gate", self._audio_gate)
            elif command == "START_DEMO":
                if input(f"Type {CONFIRM_DEMO}: ").strip() != CONFIRM_DEMO:
                    print("PHONE_DEMO_REJECTED")
                    continue
                if input(f"Type {CONFIRM_POSE_AUDIO}: ").strip() != CONFIRM_POSE_AUDIO:
                    print("POSE_AUDIO_REJECTED")
                    continue
                self._start_motion("phone_demo", self._phone_demo)
            elif command == "EXIT":
                self._request_runtime_shutdown()
                self.stop_motion()
                return 0
            elif command:
                print("INVALID_COMMAND")

    def _request_runtime_shutdown(self) -> None:
        stop_event = getattr(self, "_priority_hotkey_stop", None)
        if stop_event is not None:
            stop_event.set()
        request_shutdown = getattr(self.runtime, "request_shutdown", None)
        if callable(request_shutdown):
            request_shutdown()

    def companion_status(self) -> dict[str, object]:
        compact_status = getattr(self.runtime, "companion_telemetry_status", None)
        raw_runtime_status = (
            compact_status() if callable(compact_status) else self.runtime.status()
        )
        # Telemetry is optional during layer transitions.  Treat a transient
        # None like an empty snapshot instead of failing after motion authority
        # has already changed.
        runtime_status = dict(raw_runtime_status or {})
        lifecycle = self.lifecycle.snapshot()
        runtime_uwb = dict(runtime_status.get("uwb") or {})
        runtime_uwb_fields = dict(runtime_uwb.get("fields") or {})
        target = None
        if self.follow_target_source is not None:
            compact_target = getattr(
                self.follow_target_source,
                "current_state_from_runtime_status",
                None,
            )
            target = (
                compact_target(runtime_status)
                if callable(compact_target) and callable(compact_status)
                else self.follow_target_source.current_state()
            )
        with self._state_lock:
            thread = self._motion_thread
            motion_name = self._motion_name
            follow_status = dict(self._follow_status)
            thread_alive = bool(thread is not None and thread.is_alive())
        fall_manual_active = bool(
            getattr(getattr(self, "fall_manual_controller", None), "active", False)
        )
        regular_manual_active = bool(
            getattr(getattr(self, "manual_controller", None), "active", False)
        )
        keyboard_manual_active = fall_manual_active or regular_manual_active
        runtime_active = bool(thread_alive and motion_name == "companion")
        safety_state = lifecycle.state.value
        state = "MANUAL_CONTROL" if keyboard_manual_active else safety_state
        profile = load_companion_demo_config(COMPANION_CONFIG).follow
        wireless_config = load_wireless_uwb_follow_config(WIRELESS_FOLLOW_CONFIG)
        target_valid = bool(target is not None and target.target_valid)
        # FollowTargetState uses the external forwarding convention
        # (right-positive). The engineering monitor uses the controller's
        # robot-frame convention (left-positive), so invert that representation
        # without duplicating the UWB calibration itself.
        bearing_rad = (
            None
            if target is None or target.bearing_deg is None
            else -math.radians(target.bearing_deg)
        )
        execution_status = (
            "SENT"
            if runtime_active and state == "FOLLOWING"
            else ("STOPPED" if runtime_active else "NOT_STARTED")
        )
        return {
            "state": state,
            "safety_state": safety_state,
            "reason": lifecycle.reason,
            "incident_id": lifecycle.active_incident_id,
            "resume_required": lifecycle.resume_required,
            "help_required": lifecycle.help_required,
            "response_attempts": lifecycle.response_attempts,
            "emergency_escalated": lifecycle.emergency_escalated,
            "monitoring_active": lifecycle.monitoring_active,
            "runtime_active": runtime_active,
            "motion_generation": int(getattr(self, "_motion_generation", 0)),
            "robot_online": bool(runtime_status.get("connected")),
            "uwb": {
                "valid": target_valid,
                "age_ms": runtime_uwb.get("ageMs"),
                "enabled_from_app": runtime_uwb_fields.get("enabled_from_app"),
                "error_state": runtime_uwb_fields.get("error_state"),
                "distance_m": None if target is None else target.distance_m,
                "bearing_deg": None if target is None else target.bearing_deg,
                "bearing_rad": bearing_rad,
                "orientation_est_rad": runtime_uwb_fields.get("orientation_est"),
            },
            "lidar": {
                "valid": False,
                "state": "UNAVAILABLE",
                "reason": "wireless_uwb_follow_is_uwb_only",
            },
            "risk": {
                "state": "ACTIVE" if self.lifecycle.risk_active else "NORMAL",
                "incident_id": lifecycle.active_incident_id,
                "manual_takeover": keyboard_manual_active,
                "emergency_active": lifecycle.monitoring_active,
            },
            "motion": {
                "vx": (
                    float(follow_status.get("vx") or 0.0)
                    if runtime_active and lifecycle.state is CompanionState.FOLLOWING
                    else 0.0
                ),
                "vy": 0.0,
                "wz": (
                    float(follow_status.get("wz") or 0.0)
                    if runtime_active and lifecycle.state is CompanionState.FOLLOWING
                    else 0.0
                ),
                "authority": (
                    "MANUAL"
                    if keyboard_manual_active
                    else "EMERGENCY"
                    if lifecycle.monitoring_active
                    else "COMPANION"
                    if runtime_active
                    else "IDLE"
                ),
            },
            "notifications": list(self._lifecycle_notifications),
            "configuration": {
                "transport": "webrtc",
                "uwb_only": True,
                "target_distance_m": profile.target_distance,
                "target_bearing_rad": profile.target_bearing_radians,
                "control_frequency_hz": wireless_config.control_rate_hz,
                "effective_control_frequency_hz": wireless_config.control_rate_hz,
                "config_source": str(
                    WIRELESS_FOLLOW_CONFIG.relative_to(ROOT)
                ).replace("\\", "/"),
                "motion_limits_aligned": (
                    profile.vx_max <= self.service.settings.max_vx
                    and profile.wz_max <= self.service.settings.max_wz
                ),
                "vx_max_mps": profile.vx_max,
                "gateway_max_vx_mps": self.service.settings.max_vx,
                "walk_min_mps": profile.walk_min,
                "wz_max_radps": profile.wz_max,
                "wz_normal_max_radps": wireless_config.normal_max_wz_radps,
                "wz_alignment_max_radps": (
                    wireless_config.alignment_turn_speed_radps
                ),
                "alignment_enter_deg": wireless_config.alignment_enter_error_deg,
                "alignment_exit_deg": wireless_config.alignment_exit_error_deg,
                "full_speed_distance_m": wireless_config.full_speed_distance_m,
                "distance_speed_curve_exponent": (
                    wireless_config.distance_speed_curve_exponent
                ),
                "turn_slowdown_start_deg": (
                    wireless_config.turn_slowdown_start_error_deg
                ),
                "turn_slowdown_min_scale": (
                    wireless_config.turn_slowdown_min_scale
                ),
                "gateway_max_wz_radps": self.service.settings.max_wz,
                "vy_mps": 0.0,
            },
            "runtime": {
                "worker_alive": thread_alive,
                "failure": runtime_status.get("lastError"),
                "input": {
                    "uwb_topic": runtime_uwb.get("topic") or "rt/uwbstate",
                    "uwb_samples": runtime_uwb.get("sampleCount"),
                    "lidar_topic": None,
                    "lidar_samples": 0,
                    "transport": "webrtc",
                },
                "control": {
                    "execution_status": execution_status,
                    "transport": "webrtc",
                },
            },
        }

    def robot_status(self) -> dict[str, object]:
        runtime_status = self.runtime.status()
        with self._state_lock:
            busy = bool(self._motion_thread and self._motion_thread.is_alive())
            owner = self._motion_name
        online = bool(runtime_status.get("connected"))
        return {
            "robotId": self.service.settings.robot_id,
            "online": online,
            "transport": "webrtc",
            "dds": {
                "ddsInitialized": online,
                "ddsStateAvailable": bool(runtime_status.get("sportStateReady")),
                "transport": "webrtc_compatibility_status",
            },
            "control": {"busy": busy, "owner": owner},
        }

    def _activate_companion_layer(self) -> None:
        config = load_wireless_uwb_follow_config(WIRELESS_FOLLOW_CONFIG)
        activate = getattr(self.runtime, "activate_companion_inputs", None)
        if callable(activate):
            activate(
                timeout_seconds=5.0,
                enable_multiple_state=config.require_uwb_switch,
            )
        if self.follow_target_forwarder is not None:
            self.follow_target_forwarder.start()
        print(
            "COMPANION_INPUTS=READY "
            f"UWB=ON SportState=ON MultiState="
            f"{'ON' if config.require_uwb_switch else 'OFF'}",
            flush=True,
        )

    def _deactivate_companion_layer(self) -> None:
        if self.follow_target_source is not None:
            self.follow_target_source.set_follow_active(False)
        if self.follow_target_forwarder is not None:
            self.follow_target_forwarder.close()
        deactivate = getattr(self.runtime, "deactivate_companion_inputs", None)
        if callable(deactivate):
            try:
                deactivate()
            except Exception as exc:
                LOGGER.warning("COMPANION_INPUT_DEACTIVATION_FAILED: %s", exc)

    def ensure_voice_ready(self) -> None:
        with self._voice_layer_lock:
            activate = getattr(self.runtime, "activate_voice", None)
            if callable(activate):
                activate()
            if self.voice_services_factory is not None and self.asr_service is None:
                asr_service, tts_service, agent_client = self.voice_services_factory()
                self.asr_service = asr_service
                self.tts_service = tts_service
                self.agent_client = agent_client
            if not self._voice_preload_attempted:
                self._voice_preload_attempted = True
                self.preload_voice_control_presets()
        print("VOICE_LAYER=READY", flush=True)

    def disable_voice_layer(self) -> None:
        with self._voice_layer_lock:
            deactivate = getattr(self.runtime, "deactivate_voice", None)
            if callable(deactivate):
                deactivate()
            if self.voice_services_factory is not None:
                self.asr_service = None
                self.tts_service = None
                self.agent_client = None

    def start_companion(
        self,
        *,
        before_start: Callable[[], None] | None = None,
    ) -> dict[str, object]:
        self._ensure_control_runtime_state()
        with self._state_lock:
            start_generation = self._motion_generation
            if self._motion_thread is not None and self._motion_thread.is_alive():
                if self._motion_name == "companion":
                    return self.companion_status()
                raise WirelessCompanionControlError(
                    "CONTROL_BUSY",
                    f"Wireless motion is already running: {self._motion_name}",
                    409,
                )
        try:
            self._activate_companion_layer()
            self._build_follow_session().preflight()
            if before_start is not None:
                before_start()
        except Exception as exc:
            self._deactivate_companion_layer()
            reason = str(exc).rsplit(":", maxsplit=1)[-1].strip()
            code = "UWB_NOT_READY" if reason.startswith("uwb_") else "RUNTIME_NOT_READY"
            raise WirelessCompanionControlError(code, str(exc), 503) from exc
        lifecycle_result = self.lifecycle.start(
            self._lifecycle_readiness(preflight_verified=True)
        )
        if not lifecycle_result.accepted:
            self._deactivate_companion_layer()
            raise WirelessCompanionControlError(
                "COMPANION_STATE_CONFLICT", lifecycle_result.reason, 409
            )
        with self._state_lock:
            self._follow_status = {
                "state": "STARTING",
                "motion": "STOPPED",
                "reason": "http_start_requested",
                "autoRecovery": "ENABLED_FOR_UWB_AND_SPORT_STALE",
            }
        if not self._start_motion(
            "companion",
            self._companion_session,
            expected_generation=start_generation,
        ):
            with self._state_lock:
                start_cancelled = start_generation != self._motion_generation
            if self.lifecycle.state is CompanionState.FOLLOWING:
                self.lifecycle.stop(
                    reason=(
                        "start_cancelled"
                        if start_cancelled
                        else "start_worker_busy"
                    )
                )
            self._deactivate_companion_layer()
            if start_cancelled:
                raise WirelessCompanionControlError(
                    "START_CANCELLED",
                    "motion generation changed before companion worker start",
                    409,
                )
            raise WirelessCompanionControlError(
                "CONTROL_BUSY", "Motion control became busy before START.", 409
            )
        deadline = time.monotonic() + 0.8
        while time.monotonic() < deadline:
            status = self.companion_status()
            if status["state"] == "FOLLOWING" and status["runtime_active"]:
                return status
            with self._state_lock:
                alive = bool(self._motion_thread and self._motion_thread.is_alive())
            if not alive:
                break
            time.sleep(0.02)
        status = self.companion_status()
        if status["state"] != "FOLLOWING" or not status["runtime_active"]:
            self.lifecycle.stop(reason="start_not_confirmed")
            raise WirelessCompanionControlError(
                "COMPANION_START_NOT_CONFIRMED",
                f"Wireless Runtime did not confirm FOLLOWING; state={status['state']}",
                503,
            )
        return status

    def stop_companion(self) -> dict[str, object]:
        self._cancel_pending_motion_actions(reason="stop_follow")
        self.stop_motion()
        with self._state_lock:
            thread = self._motion_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=0.8)
        with self._state_lock:
            still_running = bool(self._motion_thread and self._motion_thread.is_alive())
            if not still_running:
                self._follow_status = {
                    "state": "IDLE",
                    "motion": "STOPPED",
                    "reason": "http_stop_confirmed",
                    "autoRecovery": "DISABLED",
                }
        self.lifecycle.stop(reason="explicit_stop")
        if still_running:
            raise WirelessCompanionControlError(
                "COMPANION_STOP_NOT_CONFIRMED",
                "StopMove was sent but the companion worker has not stopped yet.",
                503,
            )
        return self.companion_status()

    def resume_companion(self) -> dict[str, object]:
        self._ensure_control_runtime_state()
        with self._state_lock:
            start_generation = self._motion_generation
        try:
            self._activate_companion_layer()
            self._build_follow_session().preflight()
        except Exception as exc:
            self._deactivate_companion_layer()
            raise WirelessCompanionControlError("UWB_NOT_READY", str(exc), 503) from exc
        result = self.lifecycle.resume(
            self._lifecycle_readiness(preflight_verified=True)
        )
        if not result.accepted:
            self._deactivate_companion_layer()
            raise WirelessCompanionControlError(
                "COMPANION_RESUME_REJECTED", result.reason, 409
            )
        with self._state_lock:
            self._follow_status = {
                "state": "STARTING",
                "motion": "STOPPED",
                "reason": "explicit_resume_requested",
                "autoRecovery": "ENABLED_FOR_UWB_AND_SPORT_STALE",
            }
        if not self._start_motion(
            "companion",
            self._companion_session,
            expected_generation=start_generation,
        ):
            with self._state_lock:
                start_cancelled = start_generation != self._motion_generation
            if self.lifecycle.state is CompanionState.FOLLOWING:
                self.lifecycle.stop(
                    reason=(
                        "resume_cancelled"
                        if start_cancelled
                        else "resume_worker_busy"
                    )
                )
            self._deactivate_companion_layer()
            if start_cancelled:
                raise WirelessCompanionControlError(
                    "START_CANCELLED",
                    "motion generation changed before companion worker resume",
                    409,
                )
            raise WirelessCompanionControlError(
                "CONTROL_BUSY", "Motion control became busy before RESUME.", 409
            )
        deadline = time.monotonic() + 0.8
        while time.monotonic() < deadline:
            status = self.companion_status()
            if status["runtime_active"]:
                return status
            time.sleep(0.02)
        self.stop_motion()
        self.lifecycle.stop(reason="resume_not_confirmed")
        raise WirelessCompanionControlError(
            "COMPANION_RESUME_NOT_CONFIRMED",
            "Wireless Runtime did not confirm resumed companion motion.",
            503,
        )

    def apply_voice_intent(self, intent_value: str) -> dict[str, object]:
        try:
            intent = VoiceIntent(str(intent_value or "").strip().upper())
        except ValueError as exc:
            raise WirelessCompanionControlError(
                "VOICE_INTENT_INVALID", "intent is not in the frozen whitelist", 422
            ) from exc
        turn = AgentTurn(
            transcript="",
            reply="",
            intent=intent,
            confidence=1.0,
            scope="companion",
            raw={"source": "wireless_runtime_control"},
        )
        decision = self.voice_intent_adapter.authorize(
            turn, self._voice_lifecycle_snapshot()
        )
        if not decision.authorized:
            raise WirelessCompanionControlError(
                "VOICE_INTENT_REJECTED", decision.reason, 409
            )
        if intent is VoiceIntent.START_COMPANION:
            status = self.start_companion()
        elif intent is VoiceIntent.STOP_COMPANION:
            status = self.stop_companion()
        elif intent is VoiceIntent.RESUME_COMPANION:
            status = self.resume_companion()
        elif intent is VoiceIntent.I_AM_OK:
            self._emergency_voice_cancel.set()
            result = self.lifecycle.i_am_ok()
            if not result.accepted:
                raise WirelessCompanionControlError(
                    "VOICE_INTENT_REJECTED", result.reason, 409
                )
            self.stop_motion()
            status = self.companion_status()
        elif intent in {VoiceIntent.REQUEST_HELP, VoiceIntent.CALL_FAMILY}:
            self._emergency_voice_cancel.set()
            result = self.lifecycle.request_help(
                call_family=intent is VoiceIntent.CALL_FAMILY
            )
            if not result.accepted:
                raise WirelessCompanionControlError(
                    "VOICE_INTENT_REJECTED", result.reason, 409
                )
            self.stop_motion()
            self._record_lifecycle_actions(result.to_dict())
            status = self.companion_status()
        else:
            status = self.companion_status()
        return {
            "intent": intent.value,
            "authorized": True,
            "executed": True,
            "reason": decision.reason,
            "companion": status,
        }

    def ingest_risk_event(self, payload: dict[str, object]) -> dict[str, object]:
        try:
            event = ExternalRiskEvent.from_payload(payload)
        except ValueError as exc:
            raise WirelessCompanionControlError(
                "RISK_EVENT_INVALID", str(exc), 422
            ) from exc
        if event.event_type in {
            ExternalRiskEventType.FALL_SUSPECTED,
            ExternalRiskEventType.FALL_CONFIRMED,
        }:
            self._ensure_control_runtime_state()
            with self._state_lock:
                thread = getattr(self, "_motion_thread", None)
                self._was_following_before_fall = bool(
                    self.lifecycle.state is CompanionState.FOLLOWING
                    or (
                        thread is not None
                        and thread.is_alive()
                        and getattr(self, "_motion_name", None) == "companion"
                    )
                )
            self._cancel_pending_motion_actions(reason="external_fall")
            self._interrupt_voice_playback(reason="external_fall")
            result = self.lifecycle.ingest_fall(
                incident_id=str(event.incident_id),
                confirmed=event.event_type is ExternalRiskEventType.FALL_CONFIRMED,
            )
            if not result.accepted:
                raise WirelessCompanionControlError(
                    "RISK_EVENT_REJECTED", result.reason, 409
                )
            if self._was_following_before_fall:
                self.stop_motion()
                self._wait_for_motion_stop()
            self._record_lifecycle_actions(result.to_dict())
            self._start_emergency_voice_check()
            return {"eventAccepted": True, **self.companion_status()}
        if event.event_type is ExternalRiskEventType.RECOVERY_CONFIRMED:
            self._cancel_emergency_voice(reason="external_recovery")
            self._cancel_pending_motion_actions(reason="external_recovery")
            self._interrupt_voice_playback(reason="external_recovery")
            result = self.lifecycle.clear_risk(incident_id=str(event.incident_id))
            if not result.accepted:
                raise WirelessCompanionControlError(
                    "RISK_EVENT_REJECTED", result.reason, 409
                )
            return {"eventAccepted": True, **self.companion_status()}
        return {"eventAccepted": True, **self.companion_status()}

    def record_no_response(self) -> dict[str, object]:
        result = self.lifecycle.no_response()
        if not result.accepted:
            raise WirelessCompanionControlError(
                "NO_RESPONSE_REJECTED", result.reason, 409
            )
        self._hold_fall_manual_position(reason="fall_no_response")
        self._record_lifecycle_actions(result.to_dict())
        self._play_lifecycle_preset_best_effort(
            "VOICE_RECHECK.wav"
            if result.reason == "first_no_response_recheck"
            else "NO_RESPONSE_ESCALATED.wav"
        )
        return self.companion_status()

    def reset_demo(self) -> dict[str, object]:
        self._cancel_emergency_voice(reason="full_demo_reset")
        self._cancel_pending_motion_actions(reason="full_demo_reset")
        self._interrupt_voice_playback(reason="full_demo_reset")
        self.stop_motion()
        self._wait_for_motion_stop()
        if self.manual_controller.active:
            self.manual_controller.release(reason="demo_reset")
        flow = self._interaction_flow_controller
        if flow is not None:
            flow.reset_demo()
        manager = self._voice_session_manager
        if manager is not None:
            manager.recover_to_wake_guard(reason="full_demo_reset")
        result = self.lifecycle.reset_demo()
        with self._state_lock:
            self._was_following_before_fall = False
            self._last_script_action = None
            self._follow_status = {
                "state": "IDLE",
                "motion": "STOPPED",
                "reason": "demo_reset_ready",
                "autoRecovery": "IDLE",
            }
            self._lifecycle_notifications.clear()
        self._demo_event("演示上下文已重置")
        return {
            "reset": result.to_dict(),
            "companion": self.companion_status(),
        }

    def quick_follow_recovery(self) -> dict[str, object]:
        """Release operator state and restart following without speech."""

        self.close_keyboard_control()
        self.reset_demo()
        return self.start_or_resume_follow(announce=False)

    def _start_emergency_voice_check(self) -> None:
        self._ensure_control_runtime_state()
        try:
            self.ensure_voice_ready()
        except Exception as exc:
            LOGGER.warning("EMERGENCY_VOICE_ACTIVATION_FAILED: %s", exc)
        if self.asr_service is None:
            with self._state_lock:
                self._lifecycle_notifications.append(
                    {
                        "timestamp": time.time(),
                        "state": self.lifecycle.state.value,
                        "actions": ["ASK_FOR_HELP"],
                        "delivery": "VOICE_CHECK_WAITING_FOR_ASR",
                    }
                )
            return
        with self._state_lock:
            if (
                self._emergency_voice_thread is not None
                and self._emergency_voice_thread.is_alive()
            ):
                return
            self._emergency_voice_cancel.clear()
            self._emergency_voice_generation += 1
            generation = self._emergency_voice_generation
            self._emergency_voice_thread = threading.Thread(
                target=self._emergency_voice_worker,
                args=(generation,),
                name="wireless-emergency-voice-check",
                daemon=True,
            )
            self._emergency_voice_thread.start()

    def _emergency_voice_worker(self, generation: int | None = None) -> None:
        self._ensure_control_runtime_state()
        if generation is None:
            generation = self._emergency_voice_generation
        prompt_presets = (
            "VOICE_CHECK.wav",
            "VOICE_RECHECK.wav",
        )
        try:
            for attempt, prompt_preset in enumerate(prompt_presets, start=1):
                if self._emergency_voice_cancelled(generation):
                    return
                self._play_lifecycle_preset_best_effort(prompt_preset)
                if self._emergency_voice_cancel.wait(2.5) or self._emergency_voice_cancelled(
                    generation
                ):
                    return
                transcript = ""
                try:
                    capture = self._mic_gate(
                        seconds=6.0,
                        vad_enabled=True,
                        vad_trailing_silence_seconds=(
                            VOICE_VAD_TRAILING_SILENCE_SECONDS
                        ),
                        output_name=f"emergency_response_{attempt}.wav",
                        diagnostic_prefix=f"EMERGENCY_{attempt}",
                    )
                    if getattr(capture, "speech_detected", False):
                        transcript = self.asr_service.transcribe(capture.path)
                except Exception as exc:
                    print(
                        f"EMERGENCY_RESPONSE_FAILED attempt={attempt}: "
                        f"{type(exc).__name__}: {exc}",
                        flush=True,
                    )
                if self._emergency_voice_cancelled(generation):
                    return
                turn = VoiceFastIntentRouter.route(transcript)
                if turn is not None and turn.intent in {
                    VoiceIntent.I_AM_OK,
                    VoiceIntent.REQUEST_HELP,
                    VoiceIntent.CALL_FAMILY,
                }:
                    try:
                        self.apply_voice_intent(turn.intent.value)
                        self._play_lifecycle_preset_best_effort(
                            VOICE_CONTROL_PRESETS[turn.intent]
                        )
                    except WirelessCompanionControlError as exc:
                        print(
                            f"EMERGENCY_INTENT_REJECTED: {exc.code}: {exc.message}",
                            flush=True,
                        )
                    return
                result = self.lifecycle.no_response()
                if not result.accepted:
                    return
                self._record_lifecycle_actions(result.to_dict())
                if result.snapshot.state is CompanionState.ESCALATED_EMERGENCY:
                    self._play_lifecycle_preset_best_effort(
                        "NO_RESPONSE_ESCALATED.wav"
                    )
                    return
        finally:
            with self._state_lock:
                if self._emergency_voice_thread is threading.current_thread():
                    self._emergency_voice_thread = None

    def _emergency_voice_cancelled(self, generation: int) -> bool:
        with self._state_lock:
            stale = generation != self._emergency_voice_generation
        return stale or self._emergency_voice_cancel.is_set()

    def _start_fall_manual_mode(self) -> bool:
        """Acquire a stationary, low-speed manual writer for fall camera framing."""

        manual_controller = getattr(self, "manual_controller", None)
        if manual_controller is not None and manual_controller.active:
            print("FALL_MANUAL_RETAINED: existing operator control remains active")
            return True
        controller = getattr(self, "fall_manual_controller", None)
        if controller is None or os.name != "nt":
            print("FALL_MANUAL_UNAVAILABLE: Windows operator control is required")
            return False
        self._ensure_control_runtime_state()
        if controller.active:
            return True
        try:
            controller.acquire()
            controller.stop(reason="fall_manual_enter_zero")
            self._fall_manual_stop.clear()
            thread = threading.Thread(
                target=self._fall_manual_key_loop,
                name="go2-fall-manual-operator-control",
                daemon=True,
            )
            self._fall_manual_thread = thread
            thread.start()
        except Exception as exc:
            if controller.active:
                controller.release(reason="fall_manual_start_failed")
            print(f"FALL_MANUAL_UNAVAILABLE: {type(exc).__name__}: {exc}")
            return False
        config = controller.config
        print(
            "FALL_MANUAL_READY: W/S slow move | A/D slow turn | "
            "SPACE stop | ESC leave manual; "
            f"vx<= {config.forward_mps:.2f}m/s wz<= {config.yaw_radps:.2f}rad/s"
        )
        return True

    def _fall_manual_key_loop(self) -> None:
        controller = self.fall_manual_controller
        key_state = WindowsAsyncKeyState()
        previous_space = False
        try:
            while not self._fall_manual_stop.wait(controller.config.control_poll_seconds):
                pressed = key_state.snapshot()
                if {"CTRL", "F12"}.issubset(pressed) and "SHIFT" not in pressed:
                    self.close_keyboard_control()
                    return
                if "ESC" in pressed:
                    return
                space = "SPACE" in pressed
                if space and not previous_space:
                    controller.stop(reason="fall_manual_space")
                previous_space = space
                controller.update_pressed(
                    set()
                    if space
                    else set(pressed).intersection({"W", "S", "A", "D"})
                )
                failure = controller.snapshot().get("failure")
                if failure:
                    raise RuntimeError(str(failure))
        except Exception as exc:
            print(f"FALL_MANUAL_FAILED: {type(exc).__name__}: {exc}")
        finally:
            if controller.active:
                controller.release(reason="fall_manual_exit")
            self._fall_manual_thread = None

    def _stop_fall_manual_mode(self, *, reason: str) -> None:
        stop_event = getattr(self, "_fall_manual_stop", None)
        if stop_event is not None:
            stop_event.set()
        controller = getattr(self, "fall_manual_controller", None)
        if controller is not None and controller.active:
            controller.release(reason=reason)
        thread = getattr(self, "_fall_manual_thread", None)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._fall_manual_thread = None

    def _hold_fall_manual_position(self, *, reason: str) -> None:
        controller = getattr(self, "fall_manual_controller", None)
        if controller is not None and controller.active:
            controller.stop(reason=reason)
            return
        manual_controller = getattr(self, "manual_controller", None)
        if manual_controller is not None and manual_controller.active:
            manual_controller.stop(reason=reason)
            return
        self.stop_motion()

    def close_keyboard_control(self) -> dict[str, object]:
        """Stop and release keyboard motion control without latching a lock."""

        self._ensure_control_runtime_state()
        closed = False
        fall_controller = getattr(self, "fall_manual_controller", None)
        if fall_controller is not None and fall_controller.active:
            self._stop_fall_manual_mode(reason="keyboard_close")
            closed = True
        manual_controller = getattr(self, "manual_controller", None)
        manual_active = bool(
            getattr(manual_controller, "active", False)
            or self._manual_console_active.is_set()
            or getattr(getattr(self, "lifecycle", None), "state", None)
            is CompanionState.MANUAL_CONTROL
        )
        if manual_active:
            self._stop_manual_console_for_follow_resume()
            closed = True
        if closed:
            self._demo_event("键盘控制已关闭")
        return self.companion_status()

    def manual_key(self, key: str) -> dict[str, object]:
        normalized = str(key or "").strip().upper()
        if normalized in {"M", "ESC"}:
            return self.release_manual()
        if normalized in {"SPACE", " "}:
            self.manual_controller.stop(reason="manual_space")
            return self.companion_status()
        if normalized not in {"W", "S", "A", "D", "Q", "E"}:
            raise WirelessCompanionControlError(
                "MANUAL_KEY_INVALID", "key must be W/S/A/D/Q/E/SPACE/M/ESC", 422
            )
        self.enter_manual()
        command = self.manual_controller.command(normalized)
        return {
            "command": command,
            # Preserve the old response key for the video bridge while clients
            # migrate away from pulse terminology.
            "pulse": command,
            "companion": self.companion_status(),
        }

    def enter_manual(self) -> dict[str, object]:
        """Preempt companion motion and acquire the existing shared writer."""

        if self.lifecycle.state is CompanionState.MANUAL_CONTROL:
            return self.companion_status()
        acquired_here = False
        if self.lifecycle.state is not CompanionState.MANUAL_CONTROL:
            if self.lifecycle.state is CompanionState.FOLLOWING:
                self.stop_motion()
                self._wait_for_motion_stop()
                self.lifecycle.stop(reason="manual_preempted_companion")
            result = self.lifecycle.acquire_manual()
            if not result.accepted:
                raise WirelessCompanionControlError(
                    "MANUAL_REJECTED", result.reason, 409
                )
            try:
                self.manual_controller.acquire()
                acquired_here = True
            except Exception:
                self.lifecycle.release_manual()
                raise
        try:
            return self.companion_status()
        except Exception:
            # Authority acquisition is transactional: a status serialization
            # failure must not leave the real robot writer owned by MANUAL.
            if acquired_here:
                self.manual_controller.release(reason="manual_enter_failed")
                if self.lifecycle.state is CompanionState.MANUAL_CONTROL:
                    self.lifecycle.release_manual()
            raise

    def release_manual(self, *, quiet: bool = False) -> dict[str, object]:
        self.manual_controller.release(reason="manual_release")
        if self.lifecycle.state is CompanionState.MANUAL_CONTROL:
            self.lifecycle.release_manual()
            if not quiet:
                print("Lifecycle=IDLE; explicit START required for Companion", flush=True)
        return self.companion_status()

    def manual_takeover(self) -> None:
        self._ensure_control_runtime_state()
        if not hasattr(self, "_manual_takeover_lock"):
            self._manual_takeover_lock = threading.Lock()
        if not self._manual_takeover_lock.acquire(blocking=False):
            return
        try:
            self._cancel_pending_motion_actions(reason="manual_takeover")
            self._cancel_emergency_voice(reason="manual_takeover")
            self._interrupt_voice_playback(reason="manual_takeover")
            if bool(getattr(getattr(self, "lifecycle", None), "risk_active", False)):
                if self._start_fall_manual_mode():
                    self._demo_event("检测到异常情况")
                return
            self._manual_console_stop.clear()
            self._manual_console_active.set()
            try:
                self._manual_console(
                    demo_takeover=True,
                    reset_stop_event=False,
                )
            finally:
                self._manual_console_active.clear()
        finally:
            self._manual_takeover_lock.release()

    def _manual_console(
        self,
        *,
        demo_takeover: bool = False,
        reset_stop_event: bool = True,
    ) -> None:
        if os.name != "nt":
            if not demo_takeover:
                print("MANUAL_REJECTED: Windows operator console is required")
            else:
                LOGGER.warning("MANUAL_REJECTED: Windows operator control is required")
            return
        self._ensure_control_runtime_state()
        if reset_stop_event:
            self._manual_console_stop.clear()
        try:
            self.enter_manual()
        except Exception as exc:
            if self.manual_controller.active:
                self.release_manual(quiet=demo_takeover)
            if not demo_takeover:
                print(f"MANUAL_REJECTED: {type(exc).__name__}: {exc}")
            else:
                LOGGER.warning("MANUAL_REJECTED: %s: %s", type(exc).__name__, exc)
            return
        if demo_takeover:
            self._set_demo_phase("wait_resume")
            if bool(getattr(self, "demo_console", False)):
                self._demo_event("检测到异常情况")
            else:
                print("检测到异常情况", flush=True)
        else:
            print("authority=MANUAL")
            print("W/S/A/D/Q/E | SPACE stop | ESC exit")
        config = self.manual_controller.config
        if not demo_takeover:
            print(
                "MANUAL_CONFIG "
                f"W=+{config.forward_mps:.2f} S=-{config.backward_mps:.2f} "
                f"Q/E=+/-{config.lateral_mps:.2f} "
                f"A/D=+/-{config.yaw_radps:.2f} "
                f"send_hz={config.send_rate_hz:.1f} single_flight=true"
            )
            print(
                "MANUAL_CURVE "
                f"W+A/D vx=+{config.curve_forward_mps:.2f} "
                f"S+A/D vx=-{config.curve_backward_mps:.2f} "
                f"wz=+/-{config.curve_yaw_radps:.2f}"
            )
            print(
                "MANUAL_DEADMAN condition=operator_input_or_control_loop_stale "
                f"timeout={config.deadman_seconds:.2f}s"
            )
        key_state = WindowsAsyncKeyState()
        previous_space = False
        try:
            while True:
                if self._manual_console_stop.is_set():
                    self.release_manual(quiet=demo_takeover)
                    return
                pressed = key_state.snapshot()
                if {"CTRL", "F12"}.issubset(pressed) and "SHIFT" not in pressed:
                    self._manual_console_stop.set()
                    self.release_manual(quiet=True)
                    return
                if "ESC" in pressed:
                    self.release_manual(quiet=demo_takeover)
                    return
                space = "SPACE" in pressed
                if space and not previous_space:
                    self.manual_key("SPACE")
                previous_space = space
                try:
                    self.manual_controller.update_pressed(
                        set()
                        if space
                        else set(pressed).intersection(
                            self.manual_controller.MOTION_KEYS
                        )
                    )
                    failure = self.manual_controller.snapshot().get("failure")
                    if failure:
                        raise RuntimeError(str(failure))
                except Exception as exc:
                    self.release_manual(quiet=demo_takeover)
                    if not demo_takeover:
                        print(f"MANUAL_FAILED: {type(exc).__name__}: {exc}")
                    else:
                        LOGGER.warning("MANUAL_FAILED: %s: %s", type(exc).__name__, exc)
                    return
                time.sleep(config.control_poll_seconds)
        finally:
            if self.manual_controller.active:
                self.release_manual(quiet=demo_takeover)

    def _walk_follow(self) -> None:
        """Play the fixed outing prompt, then reuse the existing MANUAL flow."""

        preset = VOICE_PRESET_DIR / WALK_FOLLOW_PRESET
        try:
            if not preset.is_file():
                raise FileNotFoundError(preset)
            duration_seconds = self._wav_duration_seconds(preset)
            self.runtime.play_audio_file(preset, timeout_seconds=5.0)
            # AudioHub returns when playback is accepted, not when speaker
            # output ends. Wait the WAV's measured duration (never a guessed
            # fixed delay) before handing control to the keyboard mode.
            if duration_seconds > 0.0:
                time.sleep(duration_seconds)
        except Exception as exc:
            LOGGER.warning("WALK_FOLLOW voice playback failed: %s", exc)
        self._manual_console()

    def _play_start_announcement(self) -> None:
        """Play the fixed start notice fully before terminal START can move."""

        preset = VOICE_PRESET_DIR / VOICE_CONTROL_PRESETS[
            VoiceIntent.START_COMPANION
        ]
        try:
            if not preset.is_file():
                raise FileNotFoundError(preset)
            duration_seconds = self._wav_duration_seconds(preset)
            self.runtime.play_audio_file(preset, timeout_seconds=3.0)
            if duration_seconds > 0.0:
                time.sleep(duration_seconds)
        except Exception as exc:
            # A speaker failure must not disable an otherwise safe START. The
            # existing Lifecycle/UWB/runtime gates still decide whether motion
            # is allowed immediately after this best-effort announcement.
            LOGGER.warning("START announcement playback failed: %s", exc)

    @staticmethod
    def _manual_event(event: str, payload: dict[str, object]) -> None:
        if event == "entered":
            print("MANUAL_MODE_ENTERED", flush=True)
            return
        if event == "command":
            print(
                "MANUAL "
                f"vx={float(payload.get('vx', 0.0)):.2f} "
                f"vy={float(payload.get('vy', 0.0)):.2f} "
                f"wz={float(payload.get('wz', 0.0)):.2f}",
                flush=True,
            )
            return
        if event == "stopped":
            print(f"MANUAL_STOP reason={payload.get('reason', 'unknown')}", flush=True)
            return
        if event == "exited":
            print("MANUAL_MODE_EXITED", flush=True)
            return
        if event == "error":
            print(f"MANUAL_WARNING {payload.get('reason', 'unknown')}", flush=True)

    def _lifecycle_readiness(
        self, *, preflight_verified: bool = False
    ) -> LifecycleReadiness:
        runtime_status = self.runtime.status()
        uwb = dict(runtime_status.get("uwb") or {})
        fields = dict(uwb.get("fields") or {})
        uwb_valid = bool(
            fields.get("enabled_from_app") == 1
            and fields.get("distance_est") is not None
            and fields.get("orientation_est") is not None
            and fields.get("error_state") in {None, 0}
        )
        with self._state_lock:
            writer_busy = bool(
                self._motion_thread and self._motion_thread.is_alive()
            ) or self.manual_controller.active
        return LifecycleReadiness(
            webrtc_connected=bool(runtime_status.get("connected")),
            uwb_fresh=preflight_verified or bool(uwb.get("fresh")),
            uwb_valid=preflight_verified or uwb_valid,
            motion_writer_available=not writer_busy,
            manual_takeover=self.manual_controller.active,
        )

    def _wait_for_motion_stop(self, timeout_seconds: float = 1.0) -> None:
        with self._state_lock:
            thread = self._motion_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout_seconds)

    def _record_lifecycle_actions(self, payload: dict[str, object]) -> None:
        actions = list(payload.get("actions") or [])
        notification_actions = {
            "NOTIFY_FAMILY",
            "NOTIFY_COMMUNITY",
            "PLAY_ESCALATION",
        }
        if not notification_actions.intersection(actions):
            return
        with self._state_lock:
            self._lifecycle_notifications.append(
                {
                    "timestamp": time.time(),
                    "state": payload.get("state"),
                    "actions": actions,
                    "delivery": "PENDING_EXTERNAL_ADAPTER",
                }
            )
            self._lifecycle_notifications[:] = self._lifecycle_notifications[-20:]

    def _play_lifecycle_preset_best_effort(self, filename: str) -> None:
        path = VOICE_PRESET_DIR / filename
        try:
            if not path.is_file():
                raise FileNotFoundError(path)
            self.runtime.play_audio_file(path, timeout_seconds=3.0)
            print(f"LIFECYCLE_VOICE_PLAYBACK: complete ({filename})")
        except Exception as exc:
            # StopMove and the lifecycle transition have already completed.
            # Feedback audio must never roll back or block the safety state.
            print(
                "LIFECYCLE_VOICE_PLAYBACK: failed "
                f"({filename}, {type(exc).__name__}: {exc})"
            )

    def _voice_clip_paths(self, clips: list[str] | tuple[str, ...]) -> tuple[list[Path], list[str]]:
        paths: list[Path] = []
        missing: list[str] = []
        aliases = {
            "sess.wake_ack": "WAKE_READY.wav",
        }
        for clip in clips:
            clip_id = str(clip or "").strip()
            if not clip_id:
                continue
            if clip_id in aliases:
                path = VOICE_PRESET_DIR / aliases[clip_id]
            else:
                try:
                    path = VOICE_PRESET_DIR / clip_id_to_filename(clip_id)
                except Exception:
                    missing.append(clip_id)
                    continue
            if path.is_file():
                paths.append(path)
            else:
                missing.append(clip_id)
        return paths, missing

    def preload_voice_clips(self, clips: list[str] | tuple[str, ...]) -> dict[str, object]:
        paths, missing = self._voice_clip_paths(clips)
        if missing:
            return {
                "clips": [str(item).strip() for item in clips if str(item).strip()],
                "played": 0,
                "status": "missing",
                "missing_clips": missing,
            }
        if not paths:
            return {"clips": [], "played": 0, "status": "missing", "missing_clips": []}
        results = self.runtime.preload_audio_files(tuple(paths), retry_attempts=2)
        failed = [
            clip
            for clip, path in zip([str(item).strip() for item in clips if str(item).strip()], paths)
            if not (results.get(str(path.resolve())) and results[str(path.resolve())].ready)
        ]
        return {
            "clips": [str(item).strip() for item in clips if str(item).strip()],
            "played": 0,
            "status": "done" if not failed else "error",
            "missing_clips": failed,
        }

    def _ensure_voice_playback_guard(self) -> None:
        if not hasattr(self, "_voice_playback_lock"):
            self._voice_playback_lock = threading.Lock()
        if not hasattr(self, "_voice_playback_busy"):
            self._voice_playback_busy = False
        if not hasattr(self, "_voice_playback_active"):
            self._voice_playback_active = False
        if not hasattr(self, "_voice_playback_signature"):
            self._voice_playback_signature = None
        if not hasattr(self, "_voice_playback_seq"):
            self._voice_playback_seq = 0
        if not hasattr(self, "_voice_playback_generation"):
            self._voice_playback_generation = 0

    def is_voice_playback_active(self) -> bool:
        self._ensure_voice_playback_guard()
        with self._voice_playback_lock:
            return bool(self._voice_playback_active)

    def is_voice_playback_busy(self) -> bool:
        self._ensure_voice_playback_guard()
        with self._voice_playback_lock:
            return bool(self._voice_playback_busy or self._voice_playback_active)

    def _next_voice_playback_seq(self) -> int:
        self._ensure_voice_playback_guard()
        with self._voice_playback_lock:
            self._voice_playback_seq += 1
            return self._voice_playback_seq

    def _stop_voice_audio_playback(self, *, reason: str) -> None:
        stop_audio_playback = getattr(
            getattr(self, "runtime", None),
            "stop_audio_playback",
            None,
        )
        if not callable(stop_audio_playback):
            return
        try:
            stop_audio_playback(reason=reason, timeout_seconds=3.0)
            print(f"[AUDIO] STOP_REQ reason={reason}")
        except Exception as exc:
            print(
                "[AUDIO] STOP_FAILED "
                f"reason={reason} error={type(exc).__name__}: {exc}"
            )

    def _interrupt_voice_playback(self, *, reason: str) -> None:
        self._ensure_voice_playback_guard()
        with self._voice_playback_lock:
            self._voice_playback_generation += 1
            generation = self._voice_playback_generation
            self._voice_playback_busy = False
            self._voice_playback_active = False
            self._voice_playback_signature = None
        self._stop_voice_audio_playback(reason=f"interrupt:{reason}")
        print(f"[AUDIO] playback invalidated reason={reason} generation={generation}")

    def play_voice_clips(
        self,
        clips: list[str] | tuple[str, ...],
        *,
        request_id: str | None = None,
        session_id: str | None = None,
        source: str = "runtime",
        status_callback: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        self._ensure_voice_playback_guard()
        if status_callback is not None:
            status_callback("正在准备播报...")
        normalized = [str(item).strip() for item in clips if str(item).strip()]
        paths, missing = self._voice_clip_paths(tuple(normalized))
        if missing:
            print(f"VOICE_CLIPS_MISSING: {','.join(missing)}")
            return {
                "clips": normalized,
                "played": 0,
                "status": "missing",
                "missing_clips": missing,
            }
        signature = tuple(normalized)
        with self._voice_playback_lock:
            if self._voice_playback_busy or self._voice_playback_active:
                print(
                    "[AUDIO] duplicate playback dropped "
                    f"clips={list(signature)} active_clips={list(self._voice_playback_signature or ())}"
                )
                return {
                    "clips": normalized,
                    "played": 0,
                    "status": "error",
                    "missing_clips": [],
                    "reason": "playback_active",
                }
            self._voice_playback_busy = True
            self._voice_playback_signature = signature
            operation_generation = self._voice_playback_generation
        played = 0
        current_clip_id = ""
        try:
            playback_paths: list[Path] = []
            durations: list[float] = []
            gaps: list[float] = []
            for index, (clip_id, path) in enumerate(zip(normalized, paths)):
                emergency = clip_id in EMERGENCY_VOICE_CLIPS
                playback_path = (
                    _prepare_emergency_voice_file(path, gain=_emergency_volume_gain())
                    if emergency
                    else path
                )
                playback_paths.append(playback_path)
                durations.append(self._voice_clip_duration_seconds(playback_path))
                if index + 1 < len(normalized):
                    gaps.append(
                        EMERGENCY_VOICE_ALARM_PAUSE_SECONDS
                        if (
                            clip_id == "fall.alert.sound"
                            and normalized[index + 1] == "fall.help.broadcast"
                        )
                        else VOICE_PLAYBACK_INTER_CLIP_GAP_SECONDS
                    )

            preload_audio_files = getattr(self.runtime, "preload_audio_files", None)
            if callable(preload_audio_files):
                results = preload_audio_files(tuple(playback_paths), retry_attempts=2)
                not_ready = [
                    clip_id
                    for clip_id, path in zip(normalized, playback_paths)
                    if not (
                        results.get(str(path.resolve()))
                        and results[str(path.resolve())].ready
                    )
                ]
                if not_ready:
                    print(f"VOICE_CLIPS_NOT_READY: {','.join(not_ready)}")
                    return {
                        "clips": normalized,
                        "played": 0,
                        "status": "error",
                        "missing_clips": not_ready,
                    }

            with self._voice_playback_lock:
                if operation_generation != self._voice_playback_generation:
                    return {
                        "clips": normalized,
                        "played": 0,
                        "status": "interrupted",
                        "missing_clips": [],
                    }

            play_audio_files = getattr(self.runtime, "play_audio_files", None)
            if callable(play_audio_files):
                batch_timeout = self._voice_clip_batch_timeout_seconds(
                    durations,
                    gaps,
                    emergency=any(clip_id in EMERGENCY_VOICE_CLIPS for clip_id in normalized),
                )
                print(
                    "[AUDIO] PLAY_BATCH_REQ "
                    f"clips={normalized} "
                    f"session_id={str(session_id or '')} "
                    f"request_id={str(request_id or '')} "
                    f"source={str(source or '')} "
                    f"duration={sum(durations) + sum(gaps):.2f}s "
                    f"timeout={batch_timeout:.1f}s"
                )
                with self._voice_playback_lock:
                    if operation_generation != self._voice_playback_generation:
                        return {
                            "clips": normalized,
                            "played": 0,
                            "status": "interrupted",
                            "missing_clips": [],
                        }
                    self._voice_playback_active = True
                playback_kwargs: dict[str, Any] = {
                    "timeout_seconds": batch_timeout,
                    "inter_clip_gap_seconds": gaps,
                }
                if status_callback is not None:
                    playback_kwargs["on_playback_started"] = lambda: status_callback(
                        "语音播报中"
                    )
                play_audio_files(tuple(playback_paths), **playback_kwargs)
                played = len(normalized)
            else:
                for index, (clip_id, playback_path, duration_seconds) in enumerate(
                    zip(normalized, playback_paths, durations)
                ):
                    current_clip_id = clip_id
                    emergency = clip_id in EMERGENCY_VOICE_CLIPS
                    timeout_seconds = self._voice_clip_timeout_seconds(
                        playback_path,
                        emergency=emergency,
                    )
                    seq = self._next_voice_playback_seq()
                    print(
                        "[AUDIO] PLAY_REQ "
                        f"seq={seq:03d} clip={clip_id} "
                        f"session_id={str(session_id or '')} "
                        f"request_id={str(request_id or '')} "
                        f"source={str(source or '')} "
                        f"duration={duration_seconds:.2f}s timeout={timeout_seconds:.1f}s "
                        f"path={playback_path.name}"
                    )
                    with self._voice_playback_lock:
                        if operation_generation != self._voice_playback_generation:
                            return {
                                "clips": normalized,
                                "played": played,
                                "status": "interrupted",
                                "missing_clips": [],
                            }
                        self._voice_playback_active = True
                    self.runtime.play_audio_file(
                        playback_path,
                        timeout_seconds=timeout_seconds,
                    )
                    if played == 0 and status_callback is not None:
                        status_callback("语音播报中")
                    played += 1
                    if duration_seconds > 0.0:
                        watchdog_seconds = (
                            duration_seconds + VOICE_PLAYBACK_WATCHDOG_MARGIN_SECONDS
                        )
                        print(
                            "[AUDIO] WATCHDOG_ARMED "
                            f"seq={seq:03d} clip={clip_id} "
                            f"wait={watchdog_seconds:.2f}s"
                        )
                        time.sleep(watchdog_seconds)
                    self._stop_voice_audio_playback(
                        reason=f"voice_clip_complete:{clip_id}"
                    )
                    if (
                        duration_seconds > 0.0
                        and VOICE_PLAYBACK_ECHO_GUARD_SECONDS > 0.0
                    ):
                        time.sleep(VOICE_PLAYBACK_ECHO_GUARD_SECONDS)
                    if index < len(gaps):
                        time.sleep(gaps[index])
            print(f"VOICE_CLIPS_PLAYED: {played}/{len(normalized)}")
            if status_callback is not None:
                status_callback("播报完成")
            return {
                "clips": normalized,
                "played": played,
                "status": "done",
                "missing_clips": [],
            }
        except Exception as exc:
            with self._voice_playback_lock:
                interrupted = operation_generation != self._voice_playback_generation
            print(
                "VOICE_CLIPS_PLAYBACK_FAILED: "
                f"clip={current_clip_id} "
                f"played={played}/{len(normalized)} "
                f"error={type(exc).__name__}: {exc}"
            )
            return {
                "clips": normalized,
                "played": played,
                "status": "interrupted" if interrupted else "error",
                "missing_clips": [],
                "error": f"{type(exc).__name__}: {exc}",
            }
        finally:
            with self._voice_playback_lock:
                current = operation_generation == self._voice_playback_generation
            if current:
                self._stop_voice_audio_playback(reason="voice_playback_cleanup")
            with self._voice_playback_lock:
                if (
                    operation_generation == self._voice_playback_generation
                    and self._voice_playback_signature == signature
                ):
                    self._voice_playback_busy = False
                    self._voice_playback_active = False
                    self._voice_playback_signature = None

    @staticmethod
    def _voice_clip_batch_timeout_seconds(
        durations: list[float] | tuple[float, ...],
        gaps: list[float] | tuple[float, ...],
        *,
        emergency: bool = False,
    ) -> float:
        total_duration = sum(max(0.0, float(item)) for item in durations)
        total_gap = sum(max(0.0, float(item)) for item in gaps)
        if emergency:
            return max(
                EMERGENCY_VOICE_TIMEOUT_MIN_SECONDS,
                total_duration + total_gap + EMERGENCY_VOICE_TIMEOUT_MARGIN_SECONDS,
            )
        return max(
            VOICE_PLAYBACK_TIMEOUT_MIN_SECONDS,
            total_duration + total_gap + VOICE_PLAYBACK_TIMEOUT_MARGIN_SECONDS,
        )

    @staticmethod
    def _voice_clip_timeout_seconds(path: Path, *, emergency: bool = False) -> float:
        duration = RuntimeConsole._voice_clip_duration_seconds(path)
        if emergency:
            return max(
                EMERGENCY_VOICE_TIMEOUT_MIN_SECONDS,
                duration + EMERGENCY_VOICE_TIMEOUT_MARGIN_SECONDS,
            )
        return max(
            VOICE_PLAYBACK_TIMEOUT_MIN_SECONDS,
            duration + VOICE_PLAYBACK_TIMEOUT_MARGIN_SECONDS,
        )

    @staticmethod
    def _voice_clip_duration_seconds(path: Path) -> float:
        try:
            return RuntimeConsole._wav_duration_seconds(path)
        except Exception:
            return 0.0

    def preload_voice_control_presets(self) -> None:
        filenames = {
            *VOICE_CONTROL_PRESETS.values(),
            WALK_FOLLOW_PRESET,
            "START_REJECTED.wav",
            "RESUME_REJECTED.wav",
            "CONTROL_REJECTED.wav",
            "VOICE_CHECK.wav",
            "VOICE_RECHECK.wav",
            "NO_RESPONSE_ESCALATED.wav",
        }
        available: list[Path] = []
        for filename in sorted(filenames):
            path = VOICE_PRESET_DIR / filename
            if path.is_file():
                available.append(path)
            else:
                print(
                    "VOICE_CONTROL_PRELOAD_FAILED: "
                    f"{filename} (FileNotFoundError: {path})"
                )
        if not available:
            return
        try:
            results = self.runtime.preload_audio_files(
                tuple(available), retry_attempts=2
            )
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            for path in available:
                print(
                    "VOICE_CONTROL_PRELOAD_FAILED: "
                    f"{path.name} ({type(exc).__name__}: {detail})"
                )
            return
        for path in available:
            result = results.get(str(path.resolve()))
            if result is not None and result.ready:
                print(f"VOICE_CONTROL_PRELOAD_READY: {path.name}")
                continue
            reason = (
                result.error
                if result is not None and result.error
                else "RuntimeError: preload returned no result"
            )
            attempts = result.attempts if result is not None else 0
            print(
                "VOICE_CONTROL_PRELOAD_FAILED: "
                f"{path.name} (attempts={attempts}, reason={reason})"
            )

    def preload_xiaokang_runtime_clips(
        self,
        clips: list[str] | tuple[str, ...] | None = None,
        *,
        label: str = "XIAOKANG_AUDIO_PRELOAD",
        strict: bool = False,
    ) -> dict[str, object]:
        clip_list = tuple(clips or XIAOKANG_RUNTIME_PRELOAD_CLIPS)
        paths, missing = self._voice_clip_paths(clip_list)
        missing_set = set(missing)
        available_clips = [clip_id for clip_id in clip_list if clip_id not in missing_set]
        playback_paths = [
            (
                _prepare_emergency_voice_file(path, gain=_emergency_volume_gain())
                if clip_id in EMERGENCY_VOICE_CLIPS
                else path
            )
            for clip_id, path in zip(available_clips, paths)
        ]
        print(
            f"{label}_START "
            f"clips={len(clip_list)} "
            f"local_wavs={len(paths)} missing_local={len(missing)}"
        )
        for clip_id in missing:
            print(f"{label}_FAILED: {clip_id} (missing local wav)")
        if not paths:
            summary = {
                "required": len(clip_list),
                "ready": 0,
                "failed": 0,
                "missingLocal": len(missing),
            }
            if strict:
                raise RuntimeError(f"{label}: no required local WAV files are available")
            return summary
        try:
            results = self.runtime.preload_audio_files(
                tuple(playback_paths),
                retry_attempts=2,
            )
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            for path in paths:
                print(
                    f"{label}_FAILED: "
                    f"{path.name} ({type(exc).__name__}: {detail})"
                )
            if strict:
                raise RuntimeError(
                    f"{label}: AudioHub preload failed: {type(exc).__name__}: {detail}"
                ) from exc
            return {
                "required": len(clip_list),
                "ready": 0,
                "failed": len(paths),
                "missingLocal": len(missing),
            }
        ready_count = 0
        failed_count = 0
        for path, playback_path in zip(paths, playback_paths):
            result = results.get(str(playback_path.resolve()))
            if result is not None and result.ready:
                ready_count += 1
                print(f"{label}_READY: {path.name}")
                continue
            failed_count += 1
            reason = (
                result.error
                if result is not None and result.error
                else "RuntimeError: preload returned no result"
            )
            attempts = result.attempts if result is not None else 0
            print(
                f"{label}_FAILED: "
                f"{path.name} (attempts={attempts}, reason={reason})"
            )
        print(
            f"{label}_DONE "
            f"ready={ready_count} failed={failed_count} missing_local={len(missing)}"
        )
        summary = {
            "required": len(clip_list),
            "ready": ready_count,
            "failed": failed_count,
            "missingLocal": len(missing),
        }
        if strict and (failed_count or missing):
            raise RuntimeError(
                f"{label}: required clips are not ready "
                f"(ready={ready_count} failed={failed_count} missing_local={len(missing)})"
            )
        return summary

    def preload_xiaokang_required_clips(self) -> None:
        self.preload_xiaokang_runtime_clips(
            XIAOKANG_RUNTIME_REQUIRED_CLIPS,
            label="XIAOKANG_AUDIO_REQUIRED_PRELOAD",
            strict=True,
        )

    def preload_required_demo_presets(self) -> None:
        """Preload the two fixed clips used before voice services are enabled."""

        filenames = (
            VOICE_CONTROL_PRESETS[VoiceIntent.START_COMPANION],
            WALK_FOLLOW_PRESET,
        )
        paths = tuple(VOICE_PRESET_DIR / filename for filename in filenames)
        available = tuple(path for path in paths if path.is_file())
        for path in paths:
            if not path.is_file():
                print(
                    "DEMO_AUDIO_PRELOAD_FAILED: "
                    f"{path.name} (FileNotFoundError: {path})"
                )
        if not available:
            return
        try:
            results = self.runtime.preload_audio_files(
                available,
                retry_attempts=2,
            )
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            for path in available:
                print(
                    "DEMO_AUDIO_PRELOAD_FAILED: "
                    f"{path.name} ({type(exc).__name__}: {detail})"
                )
            return
        for path in available:
            result = results.get(str(path.resolve()))
            if result is not None and result.ready:
                print(f"DEMO_AUDIO_PRELOAD_READY: {path.name}")
                continue
            reason = (
                result.error
                if result is not None and result.error
                else "RuntimeError: preload returned no result"
            )
            attempts = result.attempts if result is not None else 0
            print(
                "DEMO_AUDIO_PRELOAD_FAILED: "
                f"{path.name} (attempts={attempts}, reason={reason})"
            )

    def stop_motion(self) -> None:
        self._motion_cancel.set()
        self._stop_fall_manual_mode(reason="motion_stop")
        if self.manual_controller.active:
            self.manual_controller.release(reason="motion_stop")
        if self.follow_target_source is not None:
            self.follow_target_source.set_follow_active(False)
        code = self.controller.emergency_stop()
        if self._follow_status.get("state") not in {"IDLE", "STOPPED"}:
            self._follow_status = {
                **self._follow_status,
                "state": "STOP_REQUESTED",
                "motion": "STOPPED",
                "reason": "manual_stop",
                "autoRecovery": "DISABLED",
            }
        status = self.runtime.status()
        print(f"EMERGENCY_STOP code={code}")
        print("MOTION=STOPPED")
        print(f"VIDEO={'ACTIVE' if status['videoReady'] else 'NOT_READY'}")
        print(f"WEBRTC={'CONNECTED' if status['connected'] else 'DISCONNECTED'}")

    def _start_motion(
        self,
        name: str,
        target,
        *,
        expected_generation: int | None = None,
    ) -> bool:
        with self._state_lock:
            if (
                expected_generation is not None
                and expected_generation != self._motion_generation
            ):
                return False
            if self._motion_thread and self._motion_thread.is_alive():
                print("MOTION_BUSY")
                return False
            self._motion_cancel.clear()
            self.controller.clear_emergency_stop()
            self._motion_name = name
            self._motion_thread = threading.Thread(
                target=self._motion_worker,
                args=(name, target),
                name=f"wireless-{name}",
                daemon=True,
            )
            self._motion_thread.start()
        return True

    def _motion_worker(self, name: str, target) -> None:
        with self._motion_lock:
            try:
                target()
            except Exception as exc:
                self.controller.emergency_stop()
                print(f"{name.upper()}_FAILED: {type(exc).__name__}: {exc}")
            finally:
                if name == "companion":
                    with self._state_lock:
                        reason = str(
                            self._follow_status.get("reason") or "worker_exit"
                        )
                    before = self.lifecycle.state
                    if before is CompanionState.FOLLOWING:
                        abnormal = not self._motion_cancel.is_set()
                        if abnormal:
                            print(
                                f"COMPANION_SESSION_ABORTED reason={reason}",
                                flush=True,
                            )
                        self.lifecycle.stop(reason=f"companion_worker_exit:{reason}")
                        after = self.lifecycle.state
                        print(
                            f"LIFECYCLE_SYNC {before.value}->{after.value}",
                            flush=True,
                        )
                with self._state_lock:
                    if self._motion_thread is threading.current_thread():
                        self._motion_thread = None
                        self._motion_name = None

    def _joint_gate(self) -> None:
        before = self.runtime.status()
        if not before["videoReady"] or not before["sportStateReady"]:
            raise RuntimeError("video and SportModeState must both be READY")
        print("GATE: observing shared video for 10 seconds before motion")
        time.sleep(10.0)
        before_motion = self.runtime.status()
        result = self.controller.forward(0.20)
        if not result.completed:
            raise RuntimeError(f"forward failed: {result.reason}")
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        print("GATE: StopMove complete; observing shared video for 10 seconds")
        time.sleep(10.0)
        after = self.runtime.status()
        passed = bool(
            after["videoReady"]
            and after["sportStateReady"]
            and before["connectionCount"] == 1
            and after["connectionCount"] == 1
            and after["frameCount"] > before_motion["frameCount"]
        )
        print(
            json.dumps(
                {
                    "gate": "video_motion_stop_video",
                    "completed": passed,
                    "connectionCountBefore": before["connectionCount"],
                    "connectionCountAfter": after["connectionCount"],
                    "framesBeforeMotion": before_motion["frameCount"],
                    "framesAfterStopObservation": after["frameCount"],
                    "videoReadyAfterStop": after["videoReady"],
                    "sportStateReadyAfterStop": after["sportStateReady"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    def _phone_demo(self) -> None:
        sequence = load_motion_sequence(PHONE_DEMO)
        print(f"Starting phone_demo ({len(sequence.steps)} YAML steps)")
        try:
            result = MotionActionDispatcher(
                self.controller,
                step_callback=lambda step: print(
                    f"STEP {step.index:02d}: {step.action}: {step.status}: {step.reason}",
                    flush=True,
                ),
            ).execute(sequence)
        finally:
            self.controller.stop()
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        status = self.runtime.status()
        if result.completed:
            print("PHONE_DEMO_COMPLETE")
        else:
            print(f"PHONE_DEMO_FAILED reason={result.reason}")
        print("MOTION=STOPPED")
        print(f"VIDEO={'ACTIVE' if status['videoReady'] else 'NOT_READY'}")
        print(f"WEBRTC={'CONNECTED' if status['connected'] else 'DISCONNECTED'}")

    def _pose_gate(self) -> None:
        result = self.controller.pose(
            roll_deg=-6.0,
            pitch_deg=14.0,
            yaw_deg=0.0,
            body_height_m=-0.08,
            duration_s=1.5,
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        if not result.completed:
            raise RuntimeError(f"pose gate failed: {result.reason}")
        status = self.runtime.status()
        print("POSE_GATE_COMPLETE")
        print("POSE=NEUTRAL")
        print("MOTION=STOPPED")
        print(f"VIDEO={'ACTIVE' if status['videoReady'] else 'NOT_READY'}")

    def _audio_gate(self) -> None:
        self.ensure_voice_ready()
        self.controller.speak("演示完成")
        status = self.runtime.status()
        print("AUDIO_GATE_COMMAND_COMPLETE")
        print("MOTION=STOPPED")
        print(f"VIDEO={'ACTIVE' if status['videoReady'] else 'NOT_READY'}")

    def _follow_3min(self) -> None:
        self._wireless_follow(run_until_stopped=False)

    def _companion_session(self) -> None:
        self._wireless_follow(run_until_stopped=True)

    def _build_follow_session(self) -> WirelessUwbFollowSession:
        profile = load_companion_demo_config(COMPANION_CONFIG).follow
        config = load_wireless_uwb_follow_config(WIRELESS_FOLLOW_CONFIG)
        return WirelessUwbFollowSession(
            self.runtime,
            self.service,
            profile,
            config,
            bearing_sign=self.service.settings.uwb_bearing_sign,
            bearing_zero_offset_rad=self.service.settings.uwb_bearing_zero_offset_rad,
            cancel_event=self._motion_cancel,
            progress_callback=self._record_follow_progress,
        )

    def _wireless_follow(self, *, run_until_stopped: bool) -> None:
        profile = load_companion_demo_config(COMPANION_CONFIG).follow
        config = load_wireless_uwb_follow_config(WIRELESS_FOLLOW_CONFIG)
        label = "COMPANION_SESSION" if run_until_stopped else "FOLLOW_3MIN"
        duration = "until STOP" if run_until_stopped else f"{config.duration_seconds:.0f}s"
        print(
            f"{label}_START: UWB-only, no LiDAR obstacle input; "
            f"duration={duration} rate={config.control_rate_hz:.1f}Hz "
            f"stale_stop={config.uwb_stale_timeout_seconds:.2f}s "
            f"auto_recover={config.auto_recover_uwb_stale}",
            flush=True,
        )
        self._follow_status = {
            "state": "FOLLOWING",
            "motion": "ACTIVE",
            "autoRecovery": "ENABLED_FOR_UWB_AND_SPORT_STALE",
        }
        if self.follow_target_source is not None:
            self.follow_target_source.set_follow_active(True)
        try:
            result = self._build_follow_session().run(
                run_until_stopped=run_until_stopped
            )
        except Exception as exc:
            self._follow_status = {
                "state": "STOPPED",
                "motion": "STOPPED",
                "reason": f"{type(exc).__name__}: {exc}",
                "autoRecovery": "DISABLED",
            }
            raise
        finally:
            if self.follow_target_source is not None:
                self.follow_target_source.set_follow_active(False)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), flush=True)
        self._follow_status = {
            "state": "STOPPED",
            "motion": "STOPPED",
            "reason": result.reason,
            "uwbDropoutCount": result.uwb_dropout_count,
            "autoRecoveryCount": result.auto_recovery_count,
            "sportStateDropoutCount": result.sport_state_dropout_count,
            "sportStateAutoRecoveryCount": (
                result.sport_state_auto_recovery_count
            ),
            "uwbStaleEscalationCount": result.uwb_stale_escalation_count,
            "lastDropoutDurationSeconds": result.last_dropout_duration_seconds,
            "maximumDropoutDurationSeconds": result.maximum_dropout_duration_seconds,
        }
        if run_until_stopped:
            print("COMPANION_SESSION_STOPPED")
        else:
            print("FOLLOW_3MIN_COMPLETE" if result.completed else "FOLLOW_3MIN_STOPPED")
        print("MOTION=STOPPED")
        print("AUTO_RECOVERY=UWB_AND_SPORT_STALE")

    def _record_follow_progress(self, row: dict[str, object]) -> None:
        now = time.monotonic()
        with self._state_lock:
            self._follow_status = {
                **self._follow_status,
                **row,
                "updated_monotonic": now,
            }
            should_log = bool(
                row.get("event")
                or self._last_follow_progress_log_at <= 0.0
                or now - self._last_follow_progress_log_at >= 1.0
            )
            if should_log:
                self._last_follow_progress_log_at = now
        if should_log:
            print(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")),
                flush=True,
            )
            distance = row.get("distance_m")
            bearing = row.get("bearing_deg")
            valid_target = (
                isinstance(distance, (int, float))
                and not isinstance(distance, bool)
                and math.isfinite(float(distance))
                and isinstance(bearing, (int, float))
                and not isinstance(bearing, bool)
                and math.isfinite(float(bearing))
            )
            if valid_target:
                self._demo_event(
                    "UWB READY | Target VALID | "
                    f"Distance {float(distance):.2f} m | "
                    f"Direction {float(bearing):.1f}°"
                )
            else:
                self._demo_event("UWB READY | Target WAITING")

    def _uwb_gate(self, seconds: float = 15.0) -> None:
        before = self.runtime.status()
        before_count = int(before["uwb"]["sampleCount"])
        before_commands = dict(before.get("commandCounts") or {})
        deadline = time.monotonic() + seconds
        last_printed_count = before_count
        print(
            "UWB_GATE: subscriber-only observation for "
            f"{seconds:.0f}s; no Move/Stop/Sport request will be sent"
        )

        while time.monotonic() < deadline:
            status = self.runtime.status()
            uwb = status["uwb"]
            count = int(uwb["sampleCount"])
            if count > last_printed_count:
                print(
                    json.dumps(
                        {
                            "sampleCount": count,
                            "ageMs": uwb["ageMs"],
                            **(uwb.get("fields") or {}),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    flush=True,
                )
                last_printed_count = count
            time.sleep(0.25)

        after = self.runtime.status()
        uwb = after["uwb"]
        fields = uwb.get("fields") or {}
        after_commands = dict(after.get("commandCounts") or {})
        command_delta = {
            name: int(after_commands.get(name, 0)) - int(before_commands.get(name, 0))
            for name in set(before_commands) | set(after_commands)
            if int(after_commands.get(name, 0)) - int(before_commands.get(name, 0))
        }
        transport_required = (
            "distance_est",
            "orientation_est",
            "enabled_from_app",
        )
        new_sample_count = int(uwb["sampleCount"]) - before_count
        received_during_gate = new_sample_count > 0
        schema_complete = all(
            fields.get(name) is not None for name in transport_required
        )
        error_state_available = fields.get("error_state") is not None
        try:
            schema_valid = bool(
                schema_complete
                and math.isfinite(float(fields["distance_est"]))
                and float(fields["distance_est"]) >= 0.0
                and math.isfinite(float(fields["orientation_est"]))
                and int(fields["enabled_from_app"]) in {0, 1}
            )
        except (TypeError, ValueError, OverflowError):
            schema_valid = False
        transport_passed = bool(
            new_sample_count >= 2
            and schema_valid
            and uwb["fresh"]
            and not command_delta
            and after["connectionCount"] == 1
        )
        follow_input_ready = bool(
            transport_passed
            and int(fields["enabled_from_app"]) == 1
            and error_state_available
            and int(fields["error_state"]) == 0
        )
        result = {
            "gate": "webrtc_uwb_readonly",
            "status": (
                "WEBRTC_UWB_READONLY_PASS"
                if follow_input_ready
                else "WEBRTC_UWB_READONLY_PASS_INPUT_NOT_READY"
                if transport_passed
                else "WEBRTC_UWB_SCHEMA_INVALID"
                if received_during_gate and not schema_valid
                else "WEBRTC_UWB_NO_SAMPLES"
                if not received_during_gate
                else "WEBRTC_UWB_INSUFFICIENT_SAMPLES"
                if new_sample_count < 2
                else "WEBRTC_UWB_READONLY_INVARIANT_FAILED"
            ),
            "subscriberOnly": True,
            "sportClientCreated": False,
            "publisherCreated": False,
            "observationSeconds": seconds,
            "topic": uwb["topic"],
            "sampleCountBefore": before_count,
            "sampleCountAfter": uwb["sampleCount"],
            "newSampleCount": new_sample_count,
            "receivedDuringGate": received_during_gate,
            "schemaComplete": schema_complete,
            "schemaValid": schema_valid,
            "errorStateAvailable": error_state_available,
            "transportPassed": transport_passed,
            "followInputReady": follow_input_ready,
            "latestFresh": uwb["fresh"],
            "fields": fields,
            "sourceKeys": uwb.get("sourceKeys") or [],
            "multipleState": after["multipleState"],
            "lowState": after["lowState"],
            "sportStateReady": after["sportStateReady"],
            "videoReady": after["videoReady"],
            "connectionCount": after["connectionCount"],
            "sportCommandsSentDuringGate": command_delta,
            "moveCommandsSentDuringGate": command_delta.get("Move", 0),
            "completed": transport_passed,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)

    def _mic_gate(
        self,
        seconds: float = 5.0,
        *,
        vad_enabled: bool = False,
        vad_trailing_silence_seconds: float = 1.0,
        output_name: str = "mic_gate_latest.wav",
        diagnostic_prefix: str | None = None,
    ):
        before = dict(self.runtime.status().get("commandCounts") or {})
        output = ROOT / "data" / "voice" / output_name
        result = self.runtime.record_microphone_wav(
            output,
            duration_seconds=seconds,
            vad_enabled=vad_enabled,
            vad_trailing_silence_seconds=vad_trailing_silence_seconds,
            diagnostic_prefix=diagnostic_prefix,
        )
        after = dict(self.runtime.status().get("commandCounts") or {})
        command_delta = {
            name: int(after.get(name, 0)) - int(before.get(name, 0))
            for name in set(before) | set(after)
            if int(after.get(name, 0)) - int(before.get(name, 0))
        }
        payload = {**result.to_dict(), "sportCommandsSentDuringGate": command_delta}
        print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
        print(
            "WEBRTC_MIC_READONLY_PASS"
            if result.byte_count > 0 and not command_delta
            else "WEBRTC_MIC_READONLY_FAIL"
        )
        return result

    def _voice_intent_gate(self, *, execute: bool = False) -> None:
        """Capture one complete utterance and route control intents locally."""

        if self.asr_service is None:
            print("VOICE_INTENT_GATE_REJECTED: ASR_NOT_CONFIGURED")
            return
        try:
            gate_started = time.monotonic()
            print(f"VOICE_GATE_BEGIN: t={gate_started:.3f}", flush=True)
            print("ASR_STATUS_CHECK: skipped_live_path", flush=True)
            print("VOICE_STAGE: SINGLE_UTTERANCE_LISTENING")
            print(
                'VOICE_PROMPT: wait for "Audio channel: on", then say '
                "小康 + 完整指令 in one sentence"
            )
            print(
                "VOICE_MIC_OPEN_BEGIN: "
                f"t={time.monotonic():.3f} "
                f"gate_start_ms={(time.monotonic() - gate_started) * 1000.0:.1f}",
                flush=True,
            )
            capture = self._mic_gate(
                seconds=VOICE_INTENT_CAPTURE_SECONDS,
                vad_enabled=True,
                vad_trailing_silence_seconds=(
                    VOICE_VAD_TRAILING_SILENCE_SECONDS
                ),
                output_name="mic_command_latest.wav",
                diagnostic_prefix="VOICE",
            )
            capture_end = time.monotonic()
            print(f"VOICE_RECORD_DONE: t={capture_end:.3f}", flush=True)
            if getattr(capture, "speech_detected", True) is False:
                print("VOICE_COMMAND_REJECTED: no_speech_detected")
                print("INTENT: NONE")
                print("AUTHORIZED: false")
                print("EXECUTED: false")
                print("MOTION=UNCHANGED")
                return

            trailing_silence = float(
                getattr(capture, "trailing_silence_seconds", 0.0) or 0.0
            )
            t0 = capture_end - trailing_silence
            print(f"VOICE_T0_VAD_END: t={t0:.3f}")
            print(
                "VOICE_VAD_TRAILING_SILENCE_MS: "
                f"{trailing_silence * 1000.0:.0f}"
            )

            asr_started = time.monotonic()
            print(f"VOICE_ASR_BEGIN: t={asr_started:.3f}")
            try:
                transcript = self.asr_service.transcribe(capture.path)
            finally:
                t1 = time.monotonic()
                print(f"VOICE_T1_ASR_FINAL: t={t1:.3f}")
                print(f"VOICE_ASR_MS: {(t1 - asr_started) * 1000.0:.0f}")
                print(f"ASR_LATENCY_MS: {(t1 - t0) * 1000.0:.0f}")
            print(f"TRANSCRIPT: {transcript}")

            lifecycle = self._voice_lifecycle_snapshot()
            intent_route_started = time.monotonic()
            print(f"INTENT_ROUTE_BEGIN: t={intent_route_started:.3f}")
            turn = VoiceFastIntentRouter.route(transcript)
            intent_route = (
                "local_explicit_command"
                if turn is not None
                else "health_new_agent"
            )
            if turn is not None:
                print("FAST_PATH=true")
                print("AGENT_BYPASSED=true")
                print("HEALTH_NEW_SKIPPED: local_explicit_command")
            elif self.agent_client is None:
                print("FAST_PATH=false")
                print("AGENT_BYPASSED=true")
                print("HEALTH_NEW_SKIPPED: agent_not_configured")
                turn = AgentTurn(
                    transcript=transcript,
                    reply="当前普通对话服务未配置。",
                    intent=VoiceIntent.NONE,
                    confidence=0.0,
                    scope="dialogue",
                    raw={"source": "agent_not_configured"},
                )
            else:
                print("FAST_PATH=false")
                print("AGENT_BYPASSED=false")
                health_new_started = time.monotonic()
                print(f"HEALTH_NEW_BEGIN: t={health_new_started:.3f}")
                try:
                    turn = self.agent_client.text_turn(transcript, lifecycle)
                finally:
                    health_new_elapsed = (
                        time.monotonic() - health_new_started
                    ) * 1000.0
                    print(f"HEALTH_NEW_END: t={time.monotonic():.3f}")
                    print(f"HEALTH_NEW_MS: {health_new_elapsed:.0f}")
            t2 = time.monotonic()
            print(f"VOICE_T2_INTENT_READY: t={t2:.3f}")
            print(f"INTENT_ROUTE_END: t={t2:.3f}")
            print(
                "INTENT_ROUTE_MS: "
                f"{(t2 - intent_route_started) * 1000.0:.1f}"
            )
            print(f"INTENT_LATENCY_MS: {(t2 - t1) * 1000.0:.1f}")

            decision = self.voice_intent_adapter.authorize(turn, lifecycle)
            executed = False
            execution_reason = decision.reason
            if execute and decision.authorized:
                t3 = time.monotonic()
                print(f"VOICE_T3_LIFECYCLE_ACCEPTED: t={t3:.3f}")
                try:
                    execution = self.apply_voice_intent(turn.intent.value)
                    executed = bool(execution.get("executed"))
                    t4 = time.monotonic()
                    print(f"VOICE_T4_CONTROL_EXECUTED: t={t4:.3f}")
                    print(f"CONTROL_LATENCY_MS: {(t4 - t0) * 1000.0:.0f}")
                except WirelessCompanionControlError as exc:
                    execution_reason = f"{exc.code}: {exc.message}"
                    print(f"VOICE_LIFECYCLE_EXECUTION_FAILED: {execution_reason}")

            playback_reply = VOICE_CONTROL_FEEDBACK_TEXT.get(turn.intent, turn.reply)
            print(f"INTENT_ROUTE: {intent_route}")
            if turn.reply and playback_reply != turn.reply:
                print(f"AGENT_RAW_REPLY: {turn.reply}")
            print(f"AGENT_REPLY: {playback_reply}")
            try:
                if turn.intent in VOICE_CONTROL_PRESETS:
                    if not execute:
                        print("VOICE_FEEDBACK_SKIPPED: read_only_gate")
                    else:
                        feedback = self._control_feedback_preset(
                            turn.intent,
                            authorized=decision.authorized,
                            executed=executed,
                        )
                        self.runtime.play_audio_file(
                            feedback,
                            timeout_seconds=3.0,
                        )
                        t5 = time.monotonic()
                        print(f"VOICE_T5_AUDIO_ACCEPTED: t={t5:.3f}")
                        print(
                            "SPEECH_FEEDBACK_LATENCY_MS: "
                            f"{(t5 - t0) * 1000.0:.0f}"
                        )
                        print(
                            "AGENT_REPLY_SOURCE: local_fast_path "
                            f"({feedback.name})"
                        )
                elif self.tts_service is not None:
                    wav_path, cache_hit = self.tts_service.synthesize_to_wav(
                        playback_reply
                    )
                    self.runtime.play_audio_file(wav_path)
                    source = (
                        "qwen_tts_local_cache"
                        if cache_hit
                        else "qwen_tts_generated"
                    )
                    print(
                        f"AGENT_REPLY_SOURCE: {source} "
                        f"(voice={self.tts_service.voice})"
                    )
                else:
                    self.runtime.speak(playback_reply)
                    print("AGENT_REPLY_SOURCE: windows_system_speech_fallback")
                print("AGENT_REPLY_PLAYBACK: complete")
            except Exception as playback_exc:
                print(
                    "AGENT_REPLY_PLAYBACK: failed "
                    f"({type(playback_exc).__name__}: {playback_exc})"
                )
            print(f"INTENT: {turn.intent.value}")
            print(f"INTENT_CONFIDENCE: {turn.confidence:.3f}")
            print(f"AUTHORIZED: {str(decision.authorized).lower()}")
            print(f"EXECUTED: {str(executed).lower()}")
            print(f"REASON: {execution_reason}")
        except Exception as exc:
            print(f"VOICE_INTENT_GATE_FAILED: {type(exc).__name__}: {exc}")
            print("INTENT: NONE")
            print("AUTHORIZED: false")
            print("EXECUTED: false")
            print("MOTION=UNCHANGED")
            print("WIRELESS_RUNTIME=CONTINUES")

    @staticmethod
    def _control_feedback_preset(
        intent: VoiceIntent,
        *,
        authorized: bool,
        executed: bool,
    ) -> Path:
        if authorized and executed:
            filename = VOICE_CONTROL_PRESETS[intent]
        elif intent is VoiceIntent.START_COMPANION:
            filename = "START_REJECTED.wav"
        elif intent is VoiceIntent.RESUME_COMPANION:
            filename = "RESUME_REJECTED.wav"
        else:
            filename = "CONTROL_REJECTED.wav"
        path = VOICE_PRESET_DIR / filename
        if not path.is_file():
            raise RuntimeError(
                f"required control feedback preset is missing: {filename}"
            )
        return path

    def _voice_intent_gate_legacy(self, *, execute: bool = False) -> None:
        if self.asr_service is None or self.agent_client is None:
            print("VOICE_INTENT_GATE_REJECTED: HEALTH_NEW_NOT_CONFIGURED")
            return
        try:
            gate_started = time.monotonic()
            print(f"VOICE_GATE_BEGIN: t={gate_started:.3f}", flush=True)
            # Do not synchronously probe /api/v1/voice/status for every turn.
            # That endpoint can be delayed by provider/background work and used
            # to block microphone startup for the full HTTP timeout. The actual
            # ASR call below remains authoritative and fails safely when the
            # service is unavailable or unconfigured.
            print("ASR_STATUS_CHECK: skipped_live_path", flush=True)
            print("VOICE_STAGE: WAKE_LISTENING")
            print('VOICE_PROMPT: wait for "Audio channel: on", then say 小康')
            print(
                "WAKE_MIC_OPEN_BEGIN: "
                f"t={time.monotonic():.3f} "
                f"gate_start_ms={(time.monotonic() - gate_started) * 1000.0:.1f}",
                flush=True,
            )
            wake_capture = self._mic_gate(
                seconds=6.0,
                vad_enabled=True,
                vad_trailing_silence_seconds=0.6,
                output_name="mic_wake_latest.wav",
            )
            wake_capture_end = time.monotonic()
            if getattr(wake_capture, "speech_detected", True) is False:
                print("VOICE_WAKE_REJECTED: no_speech_detected")
                print("INTENT: NONE")
                print("AUTHORIZED: false")
                print("EXECUTED: false")
                print("MOTION=UNCHANGED")
                return
            wake_asr_started = time.monotonic()
            print(f"WAKE_ASR_BEGIN: t={wake_asr_started:.3f}")
            try:
                wake_transcript = self.asr_service.transcribe(wake_capture.path)
            finally:
                wake_asr_elapsed = (time.monotonic() - wake_asr_started) * 1000.0
                print(f"WAKE_ASR_END: t={time.monotonic():.3f}")
                print(f"WAKE_ASR_MS: {wake_asr_elapsed:.0f}")
            print(f"WAKE_TRANSCRIPT: {wake_transcript}")
            pre_routed = VoiceFastIntentRouter.route(wake_transcript)
            if pre_routed is not None:
                print("VOICE_STAGE: FULL_COMMAND_IN_WAKE_CAPTURE")
                capture = wake_capture
                capture_end = wake_capture_end
                transcript = wake_transcript
            else:
                if not WakeWordMatcher.matches(wake_transcript):
                    print("VOICE_WAKE_REJECTED: wake_word_not_detected")
                    print("INTENT: NONE")
                    print("AUTHORIZED: false")
                    print("EXECUTED: false")
                    print("MOTION=UNCHANGED")
                    return
                wake_ready = VOICE_PRESET_DIR / "WAKE_READY.wav"
                if wake_ready.is_file():
                    wake_duration = self._wav_duration_seconds(wake_ready)
                    print(
                        "WAKE_ACK_PLAY_BEGIN: "
                        f"t={time.monotonic():.3f} duration_s={wake_duration:.3f}",
                        flush=True,
                    )
                    try:
                        self.runtime.play_audio_file(
                            wake_ready,
                            timeout_seconds=3.0,
                        )
                        print(
                            "WAKE_ACK_FIRST_AUDIO: command_accepted "
                            f"t={time.monotonic():.3f}",
                            flush=True,
                        )
                    except Exception as playback_exc:
                        print(
                            "WAKE_ACK_PLAYBACK_RETURN_TIMEOUT: "
                            f"{type(playback_exc).__name__}: {playback_exc}",
                            flush=True,
                        )
                    print("VOICE_WAKE_ACK: local_preset (WAKE_READY.wav)")
                    time.sleep(wake_duration)
                    print(
                        f"WAKE_ACK_PLAY_END: t={time.monotonic():.3f}",
                        flush=True,
                    )
                else:
                    self.runtime.speak("我在，请说。")
                    print("VOICE_WAKE_ACK: windows_system_speech_fallback")
                    time.sleep(1.0)
                print(
                    f"POST_PLAYBACK_DELAY_BEGIN: t={time.monotonic():.3f}",
                    flush=True,
                )
                time.sleep(0.2)
                print(
                    f"POST_PLAYBACK_DELAY_END: t={time.monotonic():.3f}",
                    flush=True,
                )
                print(
                    f"COMMAND_STAGE_ENTER: t={time.monotonic():.3f}",
                    flush=True,
                )
                print("VOICE_STAGE: COMMAND_LISTENING")
                print(
                    f"COMMAND_MIC_OPEN_BEGIN: t={time.monotonic():.3f}",
                    flush=True,
                )
                capture = self._mic_gate(
                    seconds=VOICE_INTENT_CAPTURE_SECONDS,
                    vad_enabled=True,
                    vad_trailing_silence_seconds=0.6,
                    output_name="mic_command_latest.wav",
                    diagnostic_prefix="COMMAND",
                )
                print(
                    f"COMMAND_RECORD_DONE: t={time.monotonic():.3f}",
                    flush=True,
                )
                capture_end = time.monotonic()
                command_asr_started = time.monotonic()
                print(f"COMMAND_ASR_BEGIN: t={command_asr_started:.3f}")
                try:
                    transcript = self.asr_service.transcribe(capture.path)
                finally:
                    command_asr_elapsed = (
                        time.monotonic() - command_asr_started
                    ) * 1000.0
                    print(f"COMMAND_ASR_END: t={time.monotonic():.3f}")
                    print(f"COMMAND_ASR_MS: {command_asr_elapsed:.0f}")
                print(f"COMMAND_TRANSCRIPT: {transcript}")
            end_of_speech = capture_end - float(
                getattr(capture, "trailing_silence_seconds", 0.0) or 0.0
            )
            lifecycle = self._voice_lifecycle_snapshot()
            intent_route_started = time.monotonic()
            print(f"INTENT_ROUTE_BEGIN: t={intent_route_started:.3f}")
            turn = VoiceFastIntentRouter.route(transcript)
            intent_route = "local_explicit_command" if turn is not None else "health_new_agent"
            if turn is None:
                print("FAST_PATH=false")
                print("AGENT_BYPASSED=false")
                health_new_started = time.monotonic()
                print(f"HEALTH_NEW_BEGIN: t={health_new_started:.3f}")
                try:
                    turn = self.agent_client.text_turn(transcript, lifecycle)
                finally:
                    health_new_elapsed = (
                        time.monotonic() - health_new_started
                    ) * 1000.0
                    print(f"HEALTH_NEW_END: t={time.monotonic():.3f}")
                    print(f"HEALTH_NEW_MS: {health_new_elapsed:.0f}")
            else:
                print("FAST_PATH=true")
                print("AGENT_BYPASSED=true")
                print("HEALTH_NEW_SKIPPED: local_explicit_command")
            intent_route_elapsed = (
                time.monotonic() - intent_route_started
            ) * 1000.0
            print(f"INTENT_ROUTE_END: t={time.monotonic():.3f}")
            print(f"INTENT_ROUTE_MS: {intent_route_elapsed:.1f}")
            decision = self.voice_intent_adapter.authorize(turn, lifecycle)
            executed = False
            execution_reason = decision.reason
            if execute and decision.authorized:
                try:
                    execution = self.apply_voice_intent(turn.intent.value)
                    executed = bool(execution.get("executed"))
                except WirelessCompanionControlError as exc:
                    execution_reason = f"{exc.code}: {exc.message}"
                    print(f"VOICE_LIFECYCLE_EXECUTION_FAILED: {execution_reason}")
            playback_reply = turn.reply
            if turn.intent.value == "START_COMPANION":
                playback_reply = CompanionSpeechRenderer.render_start(
                    elder_name=self.elder_name,
                    weather=(
                        None
                        if self.weather_cache is None
                        else self.weather_cache.snapshot()
                    ),
                )
            elif turn.intent is VoiceIntent.I_AM_OK:
                playback_reply = (
                    "好的，我不会升级求助，也不会自动恢复移动。"
                    "需要继续伴随时，请明确说继续走吧。"
                )
            print(f"TRANSCRIPT: {turn.transcript}")
            print(f"INTENT_ROUTE: {intent_route}")
            if turn.reply and playback_reply != turn.reply:
                print(f"AGENT_RAW_REPLY: {turn.reply}")
            print(f"AGENT_REPLY: {playback_reply}")
            try:
                # Capture and playback are deliberately sequential (half duplex).
                # Playback always uses the shared WebRTC runtime. The read-only
                # VOICE_INTENT_GATE never executes lifecycle actions; only the
                # explicit VOICE_CONTROL path can use the single motion writer.
                preset = VOICE_PRESET_DIR / f"{turn.intent.value}.wav"
                use_dynamic_start = turn.intent.value == "START_COMPANION"
                start_ack = VOICE_PRESET_DIR / "START_ACK.wav"
                if use_dynamic_start:
                    source = self._play_cached_start(
                        start_ack=start_ack,
                        end_of_speech=end_of_speech,
                    )
                    print(f"AGENT_REPLY_SOURCE: {source}")
                elif turn.intent.value in {
                    "STOP_COMPANION",
                    "RESUME_COMPANION",
                    "REQUEST_HELP",
                    "CALL_FAMILY",
                }:
                    if not preset.is_file():
                        raise RuntimeError(
                            f"required fast-path preset is missing: {preset.name}"
                        )
                    self.runtime.play_audio_file(preset)
                    print(f"AGENT_REPLY_SOURCE: local_fast_path ({preset.name})")
                elif self.tts_service is not None:
                    wav_path, cache_hit = self.tts_service.synthesize_to_wav(
                        playback_reply
                    )
                    self.runtime.play_audio_file(wav_path)
                    source = "qwen_tts_local_cache" if cache_hit else "qwen_tts_generated"
                    print(
                        f"AGENT_REPLY_SOURCE: {source} "
                        f"(voice={self.tts_service.voice})"
                    )
                else:
                    self.runtime.speak(playback_reply)
                    print("AGENT_REPLY_SOURCE: windows_system_speech_fallback")
                print("AGENT_REPLY_PLAYBACK: complete")
            except Exception as playback_exc:
                print(
                    "AGENT_REPLY_PLAYBACK: failed "
                    f"({type(playback_exc).__name__}: {playback_exc})"
                )
            print(f"INTENT: {turn.intent.value}")
            print(f"INTENT_CONFIDENCE: {turn.confidence:.3f}")
            print(f"AUTHORIZED: {str(decision.authorized).lower()}")
            print(f"EXECUTED: {str(executed).lower()}")
            print(f"REASON: {execution_reason}")
        except Exception as exc:
            print(f"VOICE_INTENT_GATE_FAILED: {type(exc).__name__}: {exc}")
            print("INTENT: NONE")
            print("AUTHORIZED: false")
            print("EXECUTED: false")
            print("MOTION=UNCHANGED")
            print("WIRELESS_RUNTIME=CONTINUES")

    @staticmethod
    def _wav_duration_seconds(path: Path) -> float:
        with wave.open(str(path), "rb") as stream:
            rate = stream.getframerate()
            frame_bytes = stream.getnchannels() * stream.getsampwidth()
            declared_frames = stream.getnframes()
            data_chunk = getattr(stream, "_data_chunk", None)
            data_offset = getattr(data_chunk, "offset", None)
            if data_offset is not None and frame_bytes > 0:
                # Streaming TTS WAVs can retain a 0x7fffffff RIFF/data size
                # placeholder. Never use that declared size for a sleep: cap
                # it to the PCM bytes physically present in the local file.
                physical_bytes = max(0, path.stat().st_size - (int(data_offset) + 8))
                physical_frames = physical_bytes // frame_bytes
                frames = min(declared_frames, physical_frames)
            else:
                frames = declared_frames
            return 0.0 if rate <= 0 else frames / rate

    def _play_cached_start(self, *, start_ack: Path, end_of_speech: float) -> str:
        lookup_started = time.monotonic()
        print(f"SPEECH_CACHE_LOOKUP_BEGIN: t={lookup_started:.3f}")
        cached = (
            {"ready": False, "path": None, "age_seconds": None, "last_error": None}
            if self.speech_cache is None
            else self.speech_cache.lookup_start()
        )
        lookup_ms = (time.monotonic() - lookup_started) * 1000.0
        print(f"SPEECH_CACHE_LOOKUP_END: t={time.monotonic():.3f}")
        print(f"SPEECH_CACHE_LOOKUP_MS: {lookup_ms:.1f}")
        ready = bool(cached.get("ready") and cached.get("path"))
        age = cached.get("age_seconds")
        print(f"CACHE_READY: {str(ready).lower()}")
        print("CACHE_AGE: unknown" if age is None else f"CACHE_AGE: {float(age):.1f}s")
        print(f"AUDIO_LOOKUP_MS: {lookup_ms:.1f}")
        print(f"SPEECH_CACHE_HIT: {str(ready).lower()}")
        if cached.get("last_error"):
            print(f"CACHE_LAST_ERROR: {cached['last_error']}")

        if ready:
            cached_path = Path(cached["path"])
            audio_started = time.monotonic()
            print(f"GO2_AUDIO_COMMAND_BEGIN: t={audio_started:.3f}")
            try:
                self.runtime.play_audio_file(cached_path, timeout_seconds=3.0)
                audio_elapsed = (time.monotonic() - audio_started) * 1000.0
                print(f"GO2_AUDIO_COMMAND_ACCEPTED: t={time.monotonic():.3f}")
                print(f"GO2_AUDIO_COMMAND_MS: {audio_elapsed:.0f}")
                first_audio_ms = (time.monotonic() - end_of_speech) * 1000.0
                print(
                    "AGENT_FIRST_AUDIO: speech_cache "
                    f"({cached_path.name}, text={cached.get('text') or ''})"
                )
                print(f"EOS_TO_FIRST_AUDIO_MS: {first_audio_ms:.0f}")
                return "companion_speech_cache"
            except Exception as exc:
                print(
                    "GO2_AUDIO_COMMAND_FAILED: speech_cache "
                    f"({type(exc).__name__}: {exc})"
                )

        # A stable, much smaller preset is preferred when the full dynamic
        # cache is absent or cannot be uploaded within the live-path budget.
        stable_fallback = VOICE_PRESET_DIR / "START_COMPANION.wav"
        if stable_fallback.is_file():
            fallback_started = time.monotonic()
            print(
                "GO2_AUDIO_COMMAND_BEGIN: "
                f"t={fallback_started:.3f} source=stable_start_fallback"
            )
            self.runtime.play_audio_file(stable_fallback, timeout_seconds=4.0)
            print(f"GO2_AUDIO_COMMAND_ACCEPTED: t={time.monotonic():.3f}")
            print(
                "GO2_AUDIO_COMMAND_MS: "
                f"{(time.monotonic() - fallback_started) * 1000.0:.0f}"
            )
            print(
                "EOS_TO_FIRST_AUDIO_MS: "
                f"{(time.monotonic() - end_of_speech) * 1000.0:.0f}"
            )
            print("AGENT_REPLY_FALLBACK: stable_local_preset")
            return "local_start_fallback_audiohub_cache_miss"

        # Cache creation is deliberately never awaited by the live command.
        # Both fallback clips are preloaded at startup and contain no live TTS.
        if start_ack.is_file():
            ack_started = time.monotonic()
            print(f"GO2_AUDIO_COMMAND_BEGIN: t={ack_started:.3f} source=fallback_ack")
            self.runtime.play_audio_file(start_ack, timeout_seconds=3.0)
            print(f"GO2_AUDIO_COMMAND_ACCEPTED: t={time.monotonic():.3f}")
            print(
                "GO2_AUDIO_COMMAND_MS: "
                f"{(time.monotonic() - ack_started) * 1000.0:.0f}"
            )
            first_audio_ms = (time.monotonic() - end_of_speech) * 1000.0
            print(f"EOS_TO_FIRST_AUDIO_MS: {first_audio_ms:.0f}")
            remaining_ack = self._wav_duration_seconds(start_ack) - (
                time.monotonic() - ack_started
            )
            if remaining_ack > 0:
                time.sleep(remaining_ack + 0.05)
        fallback = VOICE_PRESET_DIR / "START_DYNAMIC_FALLBACK.wav"
        if fallback.is_file():
            self.runtime.play_audio_file(fallback, timeout_seconds=3.0)
            print("AGENT_REPLY_FALLBACK: local_preset")
            return "local_start_fallback_cache_not_ready"
        raise RuntimeError("START speech cache and local fallback are unavailable")

    def _voice_lifecycle_snapshot(self) -> CompanionLifecycleSnapshot:
        status = self.runtime.status()
        lifecycle_state = self.lifecycle.state
        if lifecycle_state is CompanionState.WAIT_RESUME:
            state = CompanionLifecycleState.WAIT_RESUME
        elif lifecycle_state in {
            CompanionState.FALL_SUSPECTED,
            CompanionState.EMERGENCY_STOP,
            CompanionState.VOICE_CHECK,
            CompanionState.RECHECK,
            CompanionState.HELP_REQUESTED,
            CompanionState.ESCALATED_EMERGENCY,
            CompanionState.MONITORING,
            CompanionState.RECOVERING,
        }:
            state = CompanionLifecycleState.PAUSED_BY_FALL
        elif lifecycle_state is CompanionState.FOLLOWING:
            state = CompanionLifecycleState.FOLLOWING
        else:
            state = CompanionLifecycleState.IDLE
        uwb = dict(status.get("uwb") or {})
        fields = dict(uwb.get("fields") or {})
        uwb_valid = bool(
            fields.get("enabled_from_app") == 1
            and fields.get("distance_est") is not None
            and fields.get("orientation_est") is not None
        )
        return CompanionLifecycleSnapshot(
            state=state,
            webrtc_connected=bool(status.get("connected")),
            uwb_fresh=bool(uwb.get("fresh")),
            uwb_valid=uwb_valid,
            fall_active=self.lifecycle.risk_active,
            manual_takeover=lifecycle_state is CompanionState.MANUAL_CONTROL,
            motion_writer_available=not (
                self._motion_thread and self._motion_thread.is_alive()
            ),
        )


def _wait_for_video(runtime: Go2WirelessRuntime, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if runtime.status()["videoReady"]:
            return True
        time.sleep(min(0.2, max(0.01, deadline - time.monotonic())))
    return False


def _wait_for_http_server(server, timeout_seconds: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if server.started:
            return
        time.sleep(0.05)
    raise RuntimeError("local video relay did not start")


def _confirm_startup(
    settings,
    *,
    auto_demo: str | None = None,
    skip_operator_prompts: bool = False,
    voice_enabled: bool = True,
    video_enabled: bool = True,
) -> None:
    del auto_demo, skip_operator_prompts
    lifecycle = Path(settings.companion_state_path)
    if not lifecycle.is_absolute():
        lifecycle = ROOT / lifecycle
    try:
        payload = json.loads(lifecycle.read_text(encoding="utf-8"))
        companion_state = str(payload.get("state") or "UNKNOWN").upper()
    except Exception as exc:
        raise RuntimeError(f"cannot verify Companion IDLE: {exc}") from exc
    if companion_state != "IDLE":
        raise RuntimeError(f"COMPANION_NOT_CONFIRMED_IDLE: observed={companion_state}")
    motion_enabled = bool(
        getattr(settings, "control_enabled", True)
        and not getattr(settings, "read_only_mode", False)
    )
    print("[GO2] Core Runtime starting")
    print(f"[GO2] Motion control {'enabled' if motion_enabled else 'disabled'}")
    print(f"[VOICE] Xiaokang listener {'initializing' if voice_enabled else 'disabled'}")
    print(f"[VIDEO] WebRTC video {'enabled' if video_enabled else 'disabled'}")
    print("[GO2] Companion IDLE check passed; runtime safety interlocks remain enabled")


def _print_audio_devices(devices: list[object]) -> None:
    if not devices:
        print("[AUDIO] no capture devices found")
        return
    print("[AUDIO] available capture devices")
    for device in devices:
        print(
            f"[{device.index}] {device.name} "
            f"(channels={device.channels}, formats=0x{device.formats:08x})"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Unified Go2 WebRTC motion + video runtime")
    parser.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8093)
    parser.add_argument("--auto-demo", choices=("phone_demo",))
    parser.add_argument("--no-open-browser", action="store_true")
    parser.add_argument("--video-timeout", type=float, default=30.0)
    parser.add_argument(
        "--health-new-url",
        default=os.environ.get("HEALTH_NEW_BASE_URL", "http://127.0.0.1:8000"),
    )
    parser.add_argument(
        "--elder-id", default=os.environ.get("HEALTH_NEW_ELDER_ID", "")
    )
    parser.add_argument(
        "--elder-name", default=os.environ.get("HEALTH_NEW_ELDER_NAME", "李四")
    )
    parser.add_argument(
        "--weather-city", default=os.environ.get("GO2_WEATHER_CITY", "北京")
    )
    parser.add_argument(
        "--xiaokang-auto-follow",
        action="store_true",
        default=str(os.environ.get("XIAOKANG_AUTO_FOLLOW", "0")).strip().lower()
        in {"1", "true", "yes", "on"},
        help="start Go2 follow after outing reply clip_done; default is off",
    )
    parser.add_argument(
        "--device-mac", default=os.environ.get("HEALTH_NEW_DEVICE_MAC", "")
    )
    parser.add_argument(
        "--voice-session-id",
        default=os.environ.get("HEALTH_NEW_VOICE_SESSION_ID", "go2-wireless"),
    )
    parser.add_argument(
        "--audio-source",
        choices=("webrtc", "go2", "local"),
        default=os.environ.get("GO2_AUDIO_SOURCE", "go2"),
        help="select Go2 microphone ASR, WebRTC video-only mode, or local Windows microphone test mode",
    )
    parser.add_argument(
        "--no-voice",
        action="store_true",
        default=str(os.environ.get("GO2_NO_VOICE", "0")).strip().lower()
        in {"1", "true", "yes", "on"},
        help="disable the background Go2 voice listener",
    )
    parser.add_argument(
        "--voice-listener-paused",
        action="store_true",
        default=str(os.environ.get("GO2_VOICE_LISTENER_PAUSED", "0")).strip().lower()
        in {"1", "true", "yes", "on"},
        help="start with wake-word handling paused while keeping Go2 audio and ASR warm",
    )
    parser.add_argument(
        "--disable-voice-wake",
        action="store_true",
        default=str(os.environ.get("GO2_DISABLE_VOICE_WAKE", "0")).strip().lower()
        in {"1", "true", "yes", "on"},
        help=(
            "permanently disable spoken Xiaokang wake handling for this runtime; "
            "keep Go2 audio/ASR warm for the operator wake reply"
        ),
    )
    parser.add_argument(
        "--voice-business-interactions",
        action="store_true",
        default=str(
            os.environ.get("GO2_VOICE_BUSINESS_INTERACTIONS", "0")
        ).strip().lower()
        in {"1", "true", "yes", "on"},
        help=(
            "allow speech after Xiaokang wake to trigger business actions; "
            "default is wake-only and operator-controlled"
        ),
    )
    parser.add_argument(
        "--asr-backend",
        choices=("remote", "funasr-local"),
        default=_default_local_asr_backend(),
        help="ASR backend for local microphone mode",
    )
    parser.add_argument(
        "--funasr-model",
        default=os.environ.get("GO2_FUNASR_MODEL", "paraformer-zh-streaming"),
        help="FunASR model name for local ASR",
    )
    parser.add_argument(
        "--funasr-hub",
        default=os.environ.get("GO2_FUNASR_HUB", "ms"),
        help="FunASR hub name for local ASR",
    )
    parser.add_argument(
        "--funasr-device",
        default=os.environ.get("GO2_FUNASR_DEVICE", "cpu"),
        help="FunASR device for local ASR",
    )
    parser.add_argument(
        "--funasr-ncpu",
        type=int,
        default=int(os.environ.get("GO2_FUNASR_NCPU", "4")),
        help="CPU worker count for local FunASR",
    )
    parser.add_argument(
        "--list-audio-devices",
        action="store_true",
        help="print local microphone devices and exit",
    )
    parser.add_argument(
        "--mic-device",
        type=int,
        default=None,
        help="local microphone device index; omit to use the system default",
    )
    parser.add_argument(
        "--device-id",
        default=os.environ.get("GO2_DEVICE_ID", "DOG-LJG-001"),
        help="contract device_id used in local microphone test mode",
    )
    parser.add_argument(
        "--session-timeout",
        type=float,
        default=float(os.environ.get("GO2_VOICE_SESSION_TIMEOUT_SECONDS", "10")),
        help="local voice session idle timeout in seconds",
    )
    parser.add_argument(
        "--voice-max-turns",
        type=int,
        default=int(os.environ.get("GO2_VOICE_MAX_TURNS", "2")),
        help="maximum speech turns after wake before returning to wake guard",
    )
    parser.add_argument(
        "--voice-debug",
        action="store_true",
        default=str(os.environ.get("GO2_VOICE_DEBUG", "0")).strip().lower()
        in {"1", "true", "yes", "on"},
        help="print ASR partial/VAD/ignored-transcript diagnostics",
    )
    parser.add_argument(
        "--capture-seconds",
        type=float,
        default=15.0,
        help="local microphone capture window in seconds",
    )
    parser.add_argument(
        "--vad-trailing-silence-seconds",
        type=float,
        default=0.3,
        help="local microphone trailing silence threshold in seconds",
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--manual-confirm-start",
        action="store_true",
        default=False,
        help=(
            "deprecated no-op; operator starts never prompt for confirmation"
        ),
    )
    parser.add_argument(
        "--skip-startup-confirmations",
        action="store_true",
        help=(
            "deprecated no-op retained for old launchers; startup operator text "
            "prompts were removed, while the Companion IDLE check and runtime "
            "motion safety interlocks remain active"
        ),
    )
    console_group = parser.add_mutually_exclusive_group()
    console_group.add_argument(
        "--demo-console",
        dest="demo_console",
        action="store_true",
        help="show only competition-ready system state and business events (default)",
    )
    console_group.add_argument(
        "--debug-console",
        dest="demo_console",
        action="store_false",
        help="show full runtime diagnostics in the console",
    )
    parser.set_defaults(demo_console=True)
    args = parser.parse_args(argv)
    debug_log = Path(
        os.environ.get("GO2_RUNTIME_DEBUG_LOG", ROOT / "logs" / "runtime_debug.log")
    ).expanduser()
    if not debug_log.is_absolute():
        debug_log = ROOT / debug_log
    output_session = RuntimeOutputSession(
        debug_log,
        demo_console=bool(args.demo_console and not args.list_audio_devices),
    )
    output_session.install()
    if args.demo_console and not args.list_audio_devices:
        _emit_demo_console("Go2 Intelligent Care Runtime starting...", timestamp=False)
    try:
        return _run_main(args, parser)
    except Exception as exc:
        LOGGER.exception("WIRELESS_RUNTIME_UNHANDLED_FAILURE")
        if args.demo_console:
            _emit_demo_console(
                f"Runtime startup failed ({type(exc).__name__}); see logs/runtime_debug.log"
            )
        return 1
    finally:
        output_session.restore()


def _run_main(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.no_voice and args.audio_source != "local":
        args.audio_source = "webrtc"
    if not 1 <= args.port <= 65535:
        parser.error("--port must be in [1, 65535]")

    if args.audio_source == "local":
        microphone = WindowsWaveInMicrophoneSource()
        if args.list_audio_devices:
            _print_audio_devices(microphone.list_devices())
            return 0
        if args.asr_backend == "funasr-local":
            asr_service = FunASRLocalASRService(
                model=args.funasr_model,
                hub=args.funasr_hub,
                device=args.funasr_device,
                ncpu=args.funasr_ncpu,
            )
        else:
            asr_service = HealthNewASRService(args.health_new_url)
        pipeline = LocalVoicePipeline(
            microphone=microphone,
            asr_service=asr_service,
            device_id=args.device_id,
            topic_prefix=load_settings().mqtt_topic_prefix,
            microphone_device_index=args.mic_device,
            session_timeout_seconds=args.session_timeout,
            max_turns=args.voice_max_turns,
            capture_seconds=args.capture_seconds,
            vad_trailing_silence_seconds=args.vad_trailing_silence_seconds,
            wake_only=not args.voice_business_interactions,
            voice_debug=args.voice_debug,
        )
        try:
            pipeline.run_forever()
            return 0
        except KeyboardInterrupt:
            return 130

    import uvicorn

    logging.getLogger("aiortc.codecs.h264").setLevel(logging.ERROR)
    # Keep useful ICE INFO diagnostics while dropping only the known Windows
    # bind noise for disconnected / tentative candidate addresses.
    logging.getLogger("aioice.ice").addFilter(ExpectedAioiceBindNoiseFilter())
    uwb_verbose = str(os.environ.get("GO2_UWB_VERBOSE", "0")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    protocol_verbose = str(
        os.environ.get("GO2_VERBOSE_PROTOCOL_LOG", "0")
    ).strip().lower() in {"1", "true", "yes", "on"}
    protocol_log_filter = HighFrequencyUnitreeDataLogFilter(
        uwb_verbose=uwb_verbose,
        protocol_verbose=protocol_verbose,
    )
    root_logger = logging.getLogger()
    root_logger.addFilter(protocol_log_filter)

    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    # Child logger records bypass ancestor logger filters during propagation.
    # Put the same filter on root handlers so WebRTCAudioHub INFO records are
    # quiet by default while Warning/Error still pass through unchanged.
    for handler in root_logger.handlers:
        handler.addFilter(protocol_log_filter)
    if settings.mode == "real" and not args.execute:
        print("WIRELESS_RUNTIME_REJECTED: pass --execute", file=sys.stderr)
        if args.demo_console:
            _emit_demo_console("Runtime start rejected; see logs/runtime_debug.log")
        return 2
    try:
        _confirm_startup(
            settings,
            auto_demo=args.auto_demo,
            skip_operator_prompts=args.skip_startup_confirmations,
            voice_enabled=not args.no_voice,
            video_enabled=True,
        )
    except RuntimeError as exc:
        print(f"WIRELESS_RUNTIME_REJECTED: {exc}", file=sys.stderr)
        if args.demo_console:
            _emit_demo_console("Runtime start rejected; see logs/runtime_debug.log")
        return 2
    runtime = Go2WirelessRuntime(
        settings.robot_ip,
        aes_key=os.environ.get("GO2_AES_KEY", "").strip() or None,
        command_timeout_seconds=settings.sdk_timeout_seconds,
        state_stale_seconds=settings.state_stale_seconds,
        reconnect_delay_seconds=settings.webrtc_reconnect_initial_seconds,
        reconnect_backoff_step_seconds=settings.webrtc_reconnect_step_seconds,
        reconnect_max_delay_seconds=settings.webrtc_reconnect_max_seconds,
        reconnect_stable_reset_seconds=(
            settings.webrtc_reconnect_stable_reset_seconds
        ),
        disconnect_grace_seconds=settings.webrtc_disconnect_grace_seconds,
        reconnect_on_multi_signal_stale=settings.webrtc_reconnect_on_stale,
        multi_signal_stale_grace_seconds=settings.webrtc_stale_grace_seconds,
        enable_video_active_recovery=(
            settings.webrtc_enable_video_active_recovery
        ),
        enable_video=True,
        enable_sport_state=settings.webrtc_enable_sport_state,
        enable_uwb=settings.webrtc_enable_uwb,
        enable_multiple_state=settings.webrtc_enable_multiple_state,
        enable_low_state=settings.webrtc_enable_low_state,
        enable_audio=settings.webrtc_enable_audio,
        tts_voice=os.environ.get("GO2_TTS_VOICE", "Microsoft Huihui Desktop"),
    )
    forwarding_config = FollowTargetForwardConfig(
        enabled=settings.follow_target_forward_enabled,
        host=settings.follow_target_forward_host,
        port=settings.follow_target_forward_port,
        hz=settings.follow_target_forward_hz,
        stale_seconds=settings.follow_target_forward_stale_seconds,
        stats_interval_seconds=(
            settings.follow_target_forward_stats_interval_seconds
        ),
        verbose=uwb_verbose or protocol_verbose,
    )
    wireless_follow_config = load_wireless_uwb_follow_config(WIRELESS_FOLLOW_CONFIG)
    follow_target_source = Go2UwbFollowTargetSource(
        runtime,
        bearing_sign=settings.uwb_bearing_sign,
        bearing_zero_offset_rad=settings.uwb_bearing_zero_offset_rad,
        stale_seconds=forwarding_config.stale_seconds,
        allow_missing_error_state=wireless_follow_config.allow_missing_error_state,
        monitoring_active=settings.follow_target_monitoring_enabled,
    )
    follow_target_forwarder = UdpFollowTargetForwarder(
        forwarding_config,
        follow_target_source,
    )
    adapter = WebRTCMotionBackend(runtime, settings.robot_id, close_runtime=False)
    service = RobotService(
        Go2Gateway(adapter),
        settings,
        StateStore(settings.robot_id, settings.state_stale_seconds),
    )
    controller = ScriptedMotionController(
        service,
        load_scripted_motion_config(MOTION_CONFIG),
    )

    def create_asr_service() -> Any:
        if args.asr_backend == "funasr-local":
            return FunASRLocalASRService(
                model=args.funasr_model,
                hub=args.funasr_hub,
                device=args.funasr_device,
                ncpu=args.funasr_ncpu,
            )
        return HealthNewASRService(args.health_new_url)

    def create_voice_services() -> tuple[Any, Any, Any]:
        asr_service = create_asr_service()
        tts_service = HealthNewTTSService(
            args.health_new_url,
            cache_dir=ROOT / "data" / "voice" / "dynamic_cache",
            voice=os.environ.get("GO2_QWEN_TTS_VOICE", "Cherry"),
        )
        agent_client = (
            CompanionAgentClient(
                args.health_new_url,
                elder_id=args.elder_id,
                session_id=args.voice_session_id,
                device_mac=args.device_mac or None,
            )
            if args.elder_id.strip()
            else None
        )
        return asr_service, tts_service, agent_client

    console = RuntimeConsole(
        runtime,
        service,
        controller,
        video_host=args.host,
        video_port=args.port,
        lan_ip=discover_lan_ipv4(settings.robot_ip),
        elder_name=args.elder_name,
        follow_target_source=follow_target_source,
        follow_target_forwarder=follow_target_forwarder,
        voice_services_factory=create_voice_services,
        manual_confirm_start=False,
        voice_business_interactions_enabled=args.voice_business_interactions,
        demo_console=args.demo_console,
    )
    def voice_clip_available(clip_id: str) -> bool:
        alias = {
            "sess.wake_ack": "WAKE_READY.wav",
        }
        filename = alias.get(str(clip_id or "").strip())
        if filename is None:
            try:
                filename = clip_id_to_filename(clip_id)
            except Exception:
                return False
        return (VOICE_PRESET_DIR / filename).is_file()

    def play_xiaokang_clips(message: CommandMessage) -> dict[str, Any]:
        try:
            return console.play_voice_clips(
                [str(item) for item in list(message.payload.get("clips") or [])],
                request_id=message.request_id,
                session_id=str(message.payload.get("session_id") or ""),
                source=message.source,
            )
        except Exception as exc:
            clips = [str(item) for item in list(message.payload.get("clips") or [])]
            print(
                "VOICE_CLIPS_PLAYBACK_FAILED: "
                f"clips={clips} error={type(exc).__name__}: {exc}"
            )
            return {
                "clips": clips,
                "played": 0,
                "status": "error",
                "missing_clips": [],
                "error": f"{type(exc).__name__}: {exc}",
            }
        finally:
            time.sleep(0.4)
            if go2_bridge is not None:
                go2_bridge.clear_pending_audio()
                go2_bridge.arm_post_playback_quiet_gate()

    def start_follow_from_adapter(message: CommandMessage) -> dict[str, Any]:
        if bool(message.payload.get("skip_start_announcement", False)):
            return console.start_companion()
        if bool(message.payload.get("announce_before_start", False)):
            return console.start_companion(before_start=console._play_start_announcement)
        return console.start_companion()

    control_adapter = Go2ControlAdapter(
        start_follow=start_follow_from_adapter,
        stop_follow=lambda _message: console.stop_companion(),
        resume_follow=lambda _message: console.resume_companion(),
        play_clips=play_xiaokang_clips,
        ping=lambda message: {
            "nonce": message.payload.get("nonce") or message.request_id,
            "battery": None,
            "follow_mode": console.companion_status().get("state") == "FOLLOWING",
        },
    )
    console.set_control_adapter(control_adapter)
    go2_bridge: Go2ASRAudioBridge | None = None
    if args.audio_source == "go2":
        voice_transport = MockTransport()
        CommandDispatcher(
            voice_transport,
            control_adapter,
            topic_prefix=settings.mqtt_topic_prefix,
        ).bind(args.device_id)
        weather_source = OpenMeteoWeatherProvider(
            city=args.weather_city,
            latitude=float(os.environ.get("XIAOKANG_WEATHER_LAT", "39.9042")),
            longitude=float(os.environ.get("XIAOKANG_WEATHER_LON", "116.4074")),
            api_url=os.environ.get(
                "XIAOKANG_WEATHER_API_URL",
                "https://api.open-meteo.com/v1/forecast",
            ),
            timeout_seconds=float(os.environ.get("XIAOKANG_WEATHER_TIMEOUT", "1.5")),
        )
        fallback_condition_name = str(
            os.environ.get("XIAOKANG_WEATHER_FALLBACK_CONDITION", "sunny")
        ).strip().lower()
        try:
            fallback_condition = WeatherCondition(fallback_condition_name)
        except ValueError:
            fallback_condition = WeatherCondition.SUNNY
        try:
            fallback_temperature = int(
                round(float(os.environ.get("XIAOKANG_WEATHER_FALLBACK_TEMPERATURE", "22")))
            )
        except ValueError:
            fallback_temperature = 22
        weather_provider = CachedWeatherProvider(
            weather_source,
            fallback=WeatherContext(
                city=args.weather_city,
                condition=fallback_condition,
                temperature=max(0, min(40, fallback_temperature)),
                error="competition_static_fallback",
            ),
            refresh_interval_seconds=float(
                os.environ.get("XIAOKANG_WEATHER_REFRESH_SECONDS", "300")
            ),
            on_update=(
                lambda weather: _emit_demo_console(
                    "实时天气数据已更新："
                    f"{weather.city} "
                    f"{_weather_condition_zh(weather.condition)} "
                    f"{weather.temperature}℃"
                )
                if args.demo_console
                else None
            ),
        )
        weather_provider.start_prefetch()
        print(
            "[WEATHER] startup prefetch started; business reports use cached data or static fallback"
        )
        interaction_flow = InteractionFlowController(
            health_provider=build_default_health_provider(ROOT),
            weather_provider=weather_provider,
            medication_provider=build_default_medication_provider(),
            clip_assembler=ClipAssembler(
                is_clip_available=voice_clip_available,
                printer=print,
            ),
            auto_follow=args.xiaokang_auto_follow,
        )
        voice_session_manager = LocalVoiceSessionManager(
            voice_transport,
            device_id=args.device_id,
            topic_prefix=settings.mqtt_topic_prefix,
            session_timeout_seconds=args.session_timeout,
            max_turns=args.voice_max_turns,
            # The Go2 microphone path currently accepts only the normal
            # wake-word/session flow. Emergency bypass is a later phase.
            emergency_bypass_enabled=False,
            wake_only=not args.voice_business_interactions,
            wake_listener_forced_off=args.disable_voice_wake,
            voice_debug=args.voice_debug,
        )
        if args.voice_listener_paused or args.disable_voice_wake:
            voice_session_manager.set_listener_enabled(False)
            console._voice_listener_paused = True
        local_voice_agent = LocalFirstXiaokangAgent(
            voice_transport,
            args.device_id,
            interaction_flow,
            topic_prefix=settings.mqtt_topic_prefix,
            printer=print,
            speech_enabled=args.voice_business_interactions,
        )
        local_voice_agent.bind()
        console.set_local_voice_agent(local_voice_agent)
        console.set_interaction_flow_controller(interaction_flow)
        console.set_voice_session_manager(voice_session_manager)
        go2_bridge = Go2ASRAudioBridge(
            asr_service=create_asr_service(),
            session_manager=voice_session_manager,
            printer=print,
            is_playback_active=console.is_voice_playback_active,
            voice_debug=args.voice_debug,
        )
        console.set_go2_asr_bridge(go2_bridge)
    server = uvicorn.Server(
        uvicorn.Config(
            create_video_bridge(
                runtime,
                follow_target_forwarder=follow_target_forwarder,
                companion_control=console,
            ),
            host=args.host,
            port=args.port,
            log_level="warning",
        )
    )
    server_thread = threading.Thread(target=server.run, name="go2-video-bridge", daemon=True)
    try:
        service.initialize()
        server_thread.start()
        _wait_for_http_server(server)
        if not _wait_for_video(runtime, args.video_timeout):
            video_status = runtime.status()
            LOGGER.warning(
                "STARTUP_VIDEO_DEGRADED video_health=%s connection_state=%s "
                "peer_state=%s ice_state=%s reconnect_count=%s action=continue",
                video_status.get("videoHealthState"),
                video_status.get("connectionState"),
                video_status.get("peerConnectionState"),
                video_status.get("iceConnectionState"),
                video_status.get("reconnectCount"),
            )
        time.sleep(1.0)
        console.preload_required_demo_presets()
        console.preload_xiaokang_required_clips()
        if go2_bridge is not None:
            go2_bridge.warmup()
            console._asr_startup_ready = True
            runtime.register_microphone_pcm_consumer(go2_bridge.push_pcm)
            go2_bridge.start()
            try:
                runtime.activate_voice()
                console._voice_startup_ready = True
                print(
                    "[VOICE] READY - listener paused; operator wake reply remains available"
                    if args.voice_listener_paused or args.disable_voice_wake
                    else "[VOICE] READY - say Xiaokang"
                )
            except Exception as exc:
                LOGGER.warning("GO2_AUDIO_BRIDGE_ACTIVATION_FAILED: %s", exc)
                print("[VOICE] NOT_READY - Go2 audio activation failed")
        if not args.no_open_browser:
            webbrowser.open(f"http://127.0.0.1:{args.port}/")
        LOGGER.info(
            "RUNTIME_BASE_READY video=on companion=standby voice=standby "
            "audiohub_preload=required_presets_ready_or_reported"
        )
        return console.run(auto_demo=args.auto_demo)
    except Exception as exc:
        controller.emergency_stop()
        print(f"WIRELESS_RUNTIME_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        if args.demo_console:
            _emit_demo_console(
                f"Runtime stopped after an error ({type(exc).__name__}); "
                "see logs/runtime_debug.log"
            )
        return 1
    finally:
        if go2_bridge is not None:
            try:
                runtime.unregister_microphone_pcm_consumer(go2_bridge.push_pcm)
            except Exception:
                pass
            go2_bridge.stop()
        follow_target_source.set_follow_active(False)
        follow_target_forwarder.close()
        controller.stop()
        service.close()
        server.should_exit = True
        if server_thread.is_alive():
            server_thread.join(timeout=3.0)
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
