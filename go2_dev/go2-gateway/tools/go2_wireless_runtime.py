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
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOGGER = logging.getLogger(__name__)

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
    ManualKeyboardController,
    WindowsAsyncKeyState,
)
from app.motion.contracts import ExternalRiskEvent, ExternalRiskEventType
from app.services.robot_service import RobotService
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
from app.webrtc.video_bridge import (
    WirelessCompanionControlError,
    create_video_bridge,
)
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
        self.manual_confirm_start = bool(manual_confirm_start)
        self.voice_intent_adapter = VoiceIntentAdapter()
        self.lifecycle = CompetitionLifecycle()
        self.manual_controller = ManualKeyboardController(
            service,
            event_callback=self._manual_event,
        )
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
        self._voice_layer_lock = threading.Lock()
        self._voice_preload_attempted = False

    def run(self, *, auto_demo: str | None = None) -> int:
        status = self.runtime.status()
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
        print(
            "Commands     : START | STOP | RESUME | VOICE_CONTROL | VOICE_OFF | "
            "VOICE_INTENT_GATE | MANUAL | WALK_FOLLOW | NO_RESPONSE | RESET_DEMO | "
            "UWB_GATE | MIC_GATE | FOLLOW_3MIN | GATE | POSE_GATE | "
            "AUDIO_GATE | START_DEMO | STATUS | EXIT"
        )
        print("=" * 57)
        if auto_demo == "phone_demo":
            print("AUTO_DEMO: starting phone_demo")
            self._start_motion("phone_demo", self._phone_demo)
        while True:
            try:
                command = input("wireless> ").strip().upper()
            except (EOFError, KeyboardInterrupt):
                self._request_runtime_shutdown()
                self.stop_motion()
                return 130
            if command == "STATUS":
                status = self.runtime.status()
                wireless_config = load_wireless_uwb_follow_config(
                    WIRELESS_FOLLOW_CONFIG
                )
                status["wirelessCompanion"] = {
                    **dict(self._follow_status),
                    "effective_control_frequency_hz": (
                        wireless_config.control_rate_hz
                    ),
                    "config_source": str(
                        WIRELESS_FOLLOW_CONFIG.relative_to(ROOT)
                    ).replace("\\", "/"),
                }
                print(json.dumps(status, ensure_ascii=False, indent=2))
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
            elif command == "STOP":
                self.stop_companion()
            elif command == "RESUME":
                try:
                    print(json.dumps(self.resume_companion(), ensure_ascii=False, indent=2))
                except WirelessCompanionControlError as exc:
                    print(f"RESUME_REJECTED: {exc.code}: {exc.message}")
            elif command == "NO_RESPONSE":
                try:
                    print(json.dumps(self.record_no_response(), ensure_ascii=False, indent=2))
                except WirelessCompanionControlError as exc:
                    print(f"NO_RESPONSE_REJECTED: {exc.code}: {exc.message}")
            elif command == "RESET_DEMO":
                print(json.dumps(self.reset_demo(), ensure_ascii=False, indent=2))
            elif command == "MANUAL":
                self._manual_console()
            elif command == "WALK_FOLLOW":
                self._walk_follow()
            elif command == "START":
                if self._motion_thread and self._motion_thread.is_alive():
                    print("START_REJECTED:CONTROL_BUSY:MOTION_BUSY")
                    continue
                if self.manual_confirm_start:
                    observed = input(f"Type {CONFIRM_COMPANION_START}: ").strip()
                    if observed != CONFIRM_COMPANION_START:
                        print(
                            "START_REJECTED:MANUAL_CONFIRMATION_FAILED:"
                            f"expected {CONFIRM_COMPANION_START}"
                        )
                        continue
                try:
                    started = self.start_companion(
                        before_start=self._play_start_announcement
                    )
                    print("START accepted -> FOLLOWING")
                    print(json.dumps(started, ensure_ascii=False, indent=2))
                except WirelessCompanionControlError as exc:
                    print(f"START_REJECTED:{exc.code}:{exc.message}")
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
        runtime_active = bool(thread_alive and motion_name == "companion")
        state = lifecycle.state.value
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
            "reason": lifecycle.reason,
            "incident_id": lifecycle.active_incident_id,
            "resume_required": lifecycle.resume_required,
            "help_required": lifecycle.help_required,
            "response_attempts": lifecycle.response_attempts,
            "emergency_escalated": lifecycle.emergency_escalated,
            "monitoring_active": lifecycle.monitoring_active,
            "runtime_active": runtime_active,
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
                "manual_takeover": lifecycle.state is CompanionState.MANUAL_CONTROL,
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
                    "EMERGENCY"
                    if lifecycle.monitoring_active
                    else "MANUAL"
                    if lifecycle.state is CompanionState.MANUAL_CONTROL
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
        with self._state_lock:
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
        if not self._start_motion("companion", self._companion_session):
            self.lifecycle.stop(reason="start_worker_busy")
            self._deactivate_companion_layer()
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
        self._deactivate_companion_layer()
        if still_running:
            raise WirelessCompanionControlError(
                "COMPANION_STOP_NOT_CONFIRMED",
                "StopMove was sent but the companion worker has not stopped yet.",
                503,
            )
        return self.companion_status()

    def resume_companion(self) -> dict[str, object]:
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
        if not self._start_motion("companion", self._companion_session):
            self.lifecycle.stop(reason="resume_worker_busy")
            self._deactivate_companion_layer()
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
            result = self.lifecycle.ingest_fall(
                incident_id=str(event.incident_id),
                confirmed=event.event_type is ExternalRiskEventType.FALL_CONFIRMED,
            )
            if not result.accepted:
                raise WirelessCompanionControlError(
                    "RISK_EVENT_REJECTED", result.reason, 409
                )
            self.stop_motion()
            self._wait_for_motion_stop()
            self._record_lifecycle_actions(result.to_dict())
            self._start_emergency_voice_check()
            return {"eventAccepted": True, **self.companion_status()}
        if event.event_type is ExternalRiskEventType.RECOVERY_CONFIRMED:
            self._emergency_voice_cancel.set()
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
        self.stop_motion()
        self._record_lifecycle_actions(result.to_dict())
        self._play_lifecycle_preset_best_effort(
            "VOICE_RECHECK.wav"
            if result.reason == "first_no_response_recheck"
            else "NO_RESPONSE_ESCALATED.wav"
        )
        return self.companion_status()

    def reset_demo(self) -> dict[str, object]:
        self._emergency_voice_cancel.set()
        self.stop_motion()
        self._wait_for_motion_stop()
        if self.manual_controller.active:
            self.manual_controller.release(reason="demo_reset")
        result = self.lifecycle.reset_demo()
        with self._state_lock:
            self._follow_status = {
                "state": "IDLE",
                "motion": "STOPPED",
                "reason": "demo_reset_ready",
                "autoRecovery": "IDLE",
            }
            self._lifecycle_notifications.clear()
        return {"reset": result.to_dict(), "companion": self.companion_status()}

    def _start_emergency_voice_check(self) -> None:
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
            self._emergency_voice_thread = threading.Thread(
                target=self._emergency_voice_worker,
                name="wireless-emergency-voice-check",
                daemon=True,
            )
            self._emergency_voice_thread.start()

    def _emergency_voice_worker(self) -> None:
        prompt_presets = (
            "VOICE_CHECK.wav",
            "VOICE_RECHECK.wav",
        )
        try:
            for attempt, prompt_preset in enumerate(prompt_presets, start=1):
                if self._emergency_voice_cancel.is_set():
                    return
                self._play_lifecycle_preset_best_effort(prompt_preset)
                if self._emergency_voice_cancel.wait(2.5):
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
                if self._emergency_voice_cancel.is_set():
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
                self._deactivate_companion_layer()
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

    def release_manual(self) -> dict[str, object]:
        self.manual_controller.release(reason="manual_release")
        if self.lifecycle.state is CompanionState.MANUAL_CONTROL:
            self.lifecycle.release_manual()
            print("Lifecycle=IDLE; explicit START required for Companion", flush=True)
        return self.companion_status()

    def _manual_console(self) -> None:
        if os.name != "nt":
            print("MANUAL_REJECTED: Windows keyboard console is required")
            return
        try:
            self.enter_manual()
        except Exception as exc:
            if self.manual_controller.active:
                self.release_manual()
            print(f"MANUAL_REJECTED: {type(exc).__name__}: {exc}")
            return
        print("authority=MANUAL")
        print("W/S/A/D/Q/E | SPACE stop | ESC exit")
        config = self.manual_controller.config
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
            "MANUAL_DEADMAN condition=keyboard_or_control_loop_stale "
            f"timeout={config.deadman_seconds:.2f}s"
        )
        key_state = WindowsAsyncKeyState()
        previous_space = False
        try:
            while True:
                pressed = key_state.snapshot()
                if "ESC" in pressed:
                    self.release_manual()
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
                    self.release_manual()
                    print(f"MANUAL_FAILED: {type(exc).__name__}: {exc}")
                    return
                time.sleep(config.control_poll_seconds)
        finally:
            if self.manual_controller.active:
                self.release_manual()

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

    def _start_motion(self, name: str, target) -> bool:
        with self._state_lock:
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
                    self._deactivate_companion_layer()
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

            playback_reply = VOICE_CONTROL_FEEDBACK_TEXT.get(
                turn.intent,
                turn.reply,
            )
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
) -> None:
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
    if skip_operator_prompts:
        print(
            "STARTUP_CONFIRMATIONS: skipped by launcher; "
            "Companion IDLE check passed, runtime safety interlocks remain enabled"
        )
    else:
        for expected in (CONFIRM_WRITER, CONFIRM_APP_CLOSED, CONFIRM_AREA):
            if input(f"Type {expected}: ").strip() != expected:
                raise RuntimeError(f"confirmation failed; expected exact text {expected}")
    if auto_demo == "phone_demo":
        if input(f"Type {CONFIRM_COMPETITION}: ").strip() != CONFIRM_COMPETITION:
            raise RuntimeError(
                f"confirmation failed; expected exact text {CONFIRM_COMPETITION}"
            )
        if input(f"Type {CONFIRM_POSE_AUDIO}: ").strip() != CONFIRM_POSE_AUDIO:
            raise RuntimeError(
                f"confirmation failed; expected exact text {CONFIRM_POSE_AUDIO}"
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
        "--device-mac", default=os.environ.get("HEALTH_NEW_DEVICE_MAC", "")
    )
    parser.add_argument(
        "--voice-session-id",
        default=os.environ.get("HEALTH_NEW_VOICE_SESSION_ID", "go2-wireless"),
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--manual-confirm-start",
        action="store_true",
        default=str(
            os.environ.get("GO2_MANUAL_CONFIRM_START", "0")
        ).strip().lower()
        in {"1", "true", "yes", "on"},
        help=(
            "debug-only: require WIRELESS_COMPANION_START_APPROVED before a "
            "console START; production defaults to Lifecycle safety gates"
        ),
    )
    parser.add_argument(
        "--skip-startup-confirmations",
        action="store_true",
        help=(
            "skip the three repetitive operator text prompts; the Companion IDLE "
            "check and runtime motion safety interlocks remain active"
        ),
    )
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be in [1, 65535]")

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
    )
    # Child logger records bypass ancestor logger filters during propagation.
    # Put the same filter on root handlers so WebRTCAudioHub INFO records are
    # quiet by default while Warning/Error still pass through unchanged.
    for handler in root_logger.handlers:
        handler.addFilter(protocol_log_filter)
    if settings.mode == "real" and not args.execute:
        print("WIRELESS_RUNTIME_REJECTED: pass --execute", file=sys.stderr)
        return 2
    try:
        _confirm_startup(
            settings,
            auto_demo=args.auto_demo,
            skip_operator_prompts=args.skip_startup_confirmations,
        )
    except RuntimeError as exc:
        print(f"WIRELESS_RUNTIME_REJECTED: {exc}", file=sys.stderr)
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

    def create_voice_services() -> tuple[Any, Any, Any]:
        asr_service = HealthNewASRService(args.health_new_url)
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
        manual_confirm_start=args.manual_confirm_start,
    )
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
        if not args.no_open_browser:
            webbrowser.open(f"http://127.0.0.1:{args.port}/")
        console.preload_required_demo_presets()
        LOGGER.info(
            "RUNTIME_BASE_READY video=on companion=standby voice=standby "
            "audiohub_preload=required_presets_ready_or_reported"
        )
        return console.run(auto_demo=args.auto_demo)
    except Exception as exc:
        controller.emergency_stop()
        print(f"WIRELESS_RUNTIME_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
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
