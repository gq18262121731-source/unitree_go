from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib.metadata
import json
import logging
import math
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.core.errors import ErrorCode, GatewayError


ConnectionFactory = Callable[..., Any]
AudioHubFactory = Callable[[Any], Any]
LOGGER = logging.getLogger(__name__)


class ExpectedAioiceBindNoiseFilter(logging.Filter):
    """Suppress only unusable Windows candidate bind noise from aioice."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not (
            "Could not bind to" in message and "WinError 10049" in message
        )


class HighFrequencyUnitreeDataLogFilter(logging.Filter):
    """Drop raw high-rate SDK payloads unless explicitly requested."""

    _SPORT_TOPICS = (
        '"topic": "rt/sportmodestate"',
        '"topic": "rt/lf/sportmodestate"',
        '"topic":"rt/sportmodestate"',
        '"topic":"rt/lf/sportmodestate"',
    )

    _UWB_TOPICS = (
        '"topic": "rt/uwbstate"',
        '"topic":"rt/uwbstate"',
    )

    _OTHER_STATE_TOPICS = (
        '"topic": "rt/lf/lowstate"',
        '"topic":"rt/lf/lowstate"',
        '"topic": "rt/lowstate"',
        '"topic":"rt/lowstate"',
        '"topic": "rt/multiplestate"',
        '"topic":"rt/multiplestate"',
    )

    _AUDIO_HUB_PROTOCOL_MARKERS = (
        "rt/api/audiohub/request",
        "rt/api/audiohub/response",
        '"block_content"',
        "'block_content'",
        '"audio_list"',
        "'audio_list'",
    )

    _AUDIO_HUB_PROGRESS_PREFIXES = (
        "Splitting file into ",
        "Sending chunk ",
        "All chunks sent",
    )

    def __init__(
        self,
        *,
        uwb_verbose: bool = False,
        protocol_verbose: bool = False,
    ) -> None:
        super().__init__()
        self.uwb_verbose = bool(uwb_verbose)
        self.protocol_verbose = bool(protocol_verbose)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if self.protocol_verbose or record.levelno >= logging.WARNING:
            return True
        if (
            record.name.endswith("WebRTCAudioHub")
            and message.startswith(self._AUDIO_HUB_PROGRESS_PREFIXES)
        ):
            return False
        if any(marker in message for marker in self._AUDIO_HUB_PROTOCOL_MARKERS):
            return False
        if (
            '"type": "heartbeat"' in message
            or '"type":"heartbeat"' in message
            or '"type": "rtc_inner_req"' in message
            or '"type":"rtc_inner_req"' in message
            or message.startswith("Heartbeat response received")
        ):
            return False
        if not message.startswith("Received message on data channel:"):
            return True
        if any(
            topic in message
            for topic in (*self._SPORT_TOPICS, *self._OTHER_STATE_TOPICS)
        ):
            return False
        if not self.uwb_verbose:
            if any(topic in message for topic in self._UWB_TOPICS):
                return False
        return True


def _default_connection_factory(
    robot_ip: str, aes_key: str | None
) -> tuple[Any, dict[str, str], dict[str, int]]:
    from unitree_webrtc_connect import (
        RTC_TOPIC,
        SPORT_CMD,
        UnitreeWebRTCConnection,
        WebRTCConnectionMethod,
    )

    connection = UnitreeWebRTCConnection(
        WebRTCConnectionMethod.LocalSTA,
        ip=robot_ip,
        aes_128_key=aes_key,
    )
    return connection, RTC_TOPIC, SPORT_CMD


def _default_audio_hub_factory(connection: Any) -> Any:
    from unitree_webrtc_connect.webrtc_audiohub import WebRTCAudioHub

    return WebRTCAudioHub(connection)


def _api_status_code(response: Any) -> int | None:
    if not isinstance(response, dict):
        return None
    try:
        return int(response["data"]["header"]["status"]["code"])
    except (KeyError, TypeError, ValueError):
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _sdp_identity(description: Any) -> dict[str, object]:
    """Return only safe, compact ICE/session identity fields from an SDP."""

    sdp = getattr(description, "sdp", None)
    if not isinstance(sdp, str):
        return {
            "iceUfrag": None,
            "icePwdHash": None,
            "fingerprint": None,
            "setup": None,
            "candidateCount": 0,
        }
    values: dict[str, str | None] = {
        "iceUfrag": None,
        "icePwd": None,
        "fingerprint": None,
        "setup": None,
    }
    candidate_count = 0
    for raw_line in sdp.splitlines():
        line = raw_line.strip()
        if line.startswith("a=candidate:"):
            candidate_count += 1
        elif values["iceUfrag"] is None and line.startswith("a=ice-ufrag:"):
            values["iceUfrag"] = line.partition(":")[2]
        elif values["icePwd"] is None and line.startswith("a=ice-pwd:"):
            values["icePwd"] = line.partition(":")[2]
        elif values["fingerprint"] is None and line.startswith("a=fingerprint:"):
            values["fingerprint"] = line.partition(":")[2]
        elif values["setup"] is None and line.startswith("a=setup:"):
            values["setup"] = line.partition(":")[2]
    ice_pwd = values.pop("icePwd")
    return {
        "iceUfrag": values["iceUfrag"],
        "icePwdHash": (
            hashlib.sha256(ice_pwd.encode("utf-8")).hexdigest()[:8]
            if ice_pwd
            else None
        ),
        "fingerprint": values["fingerprint"],
        "setup": values["setup"],
        "candidateCount": candidate_count,
    }


def _selected_candidate_pairs(pc: Any) -> list[dict[str, object]]:
    """Read aiortc/aioice's nominated pairs without changing ICE behavior."""

    sctp = getattr(pc, "sctp", None)
    dtls_transport = getattr(sctp, "transport", None)
    ice_transport = getattr(dtls_transport, "transport", None)
    ice_connection = getattr(ice_transport, "_connection", None)
    nominated = getattr(ice_connection, "_nominated", None)
    if not isinstance(nominated, dict):
        return []
    pairs: list[dict[str, object]] = []
    for component, pair in sorted(nominated.items(), key=lambda item: str(item[0])):
        local_candidate = getattr(pair, "local_candidate", None)
        remote_candidate = getattr(pair, "remote_candidate", None)
        pairs.append(
            {
                "component": component,
                "local": list(getattr(pair, "local_addr", ())) or None,
                "remote": list(getattr(pair, "remote_addr", ())) or None,
                "localType": getattr(local_candidate, "type", None),
                "remoteType": getattr(remote_candidate, "type", None),
                "protocol": getattr(local_candidate, "transport", None),
            }
        )
    return pairs


@dataclass(frozen=True)
class LatestVideoFrame:
    jpeg: bytes
    sequence: int
    captured_at: str
    width: int
    height: int
    fps: float

    def metadata(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "capturedAt": self.captured_at,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "size": len(self.jpeg),
        }


@dataclass(frozen=True)
class MicrophoneCaptureResult:
    path: str
    sample_rate: int
    channels: int
    duration_seconds: float
    sample_count: int
    frame_count: int
    peak: int
    rms: float
    byte_count: int
    vad_enabled: bool = False
    speech_detected: bool = False
    endpoint_reason: str = "fixed_duration"
    trailing_silence_seconds: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "sampleRate": self.sample_rate,
            "channels": self.channels,
            "durationSeconds": self.duration_seconds,
            "sampleCount": self.sample_count,
            "frameCount": self.frame_count,
            "peak": self.peak,
            "rms": self.rms,
            "byteCount": self.byte_count,
            "vadEnabled": self.vad_enabled,
            "speechDetected": self.speech_detected,
            "endpointReason": self.endpoint_reason,
            "trailingSilenceSeconds": self.trailing_silence_seconds,
        }


@dataclass(frozen=True)
class AudioPreloadResult:
    path: str
    custom_name: str
    ready: bool
    uploaded: bool
    attempts: int
    unique_id: str | None = None
    error: str | None = None


class Go2WirelessRuntime:
    """Sole owner of one Go2 WebRTC PeerConnection.

    DataChannel state/requests stay on the runtime asyncio loop. Video recv only
    places the newest raw frame into a size-one queue; conversion and JPEG
    encoding happen on a dedicated worker so video processing cannot block
    motion refreshes or SportModeState callbacks.
    """

    def __init__(
        self,
        robot_ip: str,
        *,
        aes_key: str | None = None,
        command_timeout_seconds: float = 3.0,
        connect_timeout_seconds: float = 20.0,
        state_timeout_seconds: float = 10.0,
        state_stale_seconds: float = 2.0,
        frame_stale_seconds: float = 3.0,
        stale_timeout_seconds: float | None = None,
        reconnect_delay_seconds: float = 2.0,
        reconnect_backoff_step_seconds: float | None = None,
        reconnect_max_delay_seconds: float = 15.0,
        reconnect_stable_reset_seconds: float = 30.0,
        disconnect_grace_seconds: float = 3.0,
        video_track_end_grace_seconds: float = 2.5,
        reconnect_on_multi_signal_stale: bool = False,
        multi_signal_stale_grace_seconds: float = 10.0,
        enable_video_active_recovery: bool = False,
        # Go2 can need several seconds to deliver the first decodable H.264
        # keyframe after the WebRTC data channel is ready.  Keep the initial
        # grace period longer than the steady-state stale-frame threshold so
        # the watchdog does not interrupt a healthy stream while it starts.
        video_first_frame_wait_seconds: float = 15.0,
        video_soft_recovery_seconds: float = 6.0,
        video_soft_toggle_delay_seconds: float = 0.20,
        video_soft_observe_seconds: float = 3.0,
        video_reconnect_cooldown_seconds: float = 0.0,
        video_recovery_min_frames: int = 10,
        video_recovery_min_duration_seconds: float = 1.0,
        video_recovery_max_gap_seconds: float = 0.50,
        video_false_recovery_window_seconds: float = 3.0,
        capture_fps: float = 15.0,
        jpeg_quality: int = 80,
        enable_video: bool = True,
        enable_sport_state: bool = True,
        enable_uwb: bool = True,
        enable_multiple_state: bool = True,
        enable_low_state: bool = True,
        enable_audio: bool = True,
        diagnostic_mode: bool = False,
        connection_factory: ConnectionFactory | None = None,
        audio_hub_factory: AudioHubFactory | None = None,
        tts_voice: str = "Microsoft Huihui Desktop",
    ) -> None:
        self.robot_ip = robot_ip
        self.aes_key = aes_key
        self.command_timeout_seconds = command_timeout_seconds
        self.connect_timeout_seconds = connect_timeout_seconds
        self.state_timeout_seconds = state_timeout_seconds
        configured_stale_timeout = (
            max(float(state_stale_seconds), float(frame_stale_seconds))
            if stale_timeout_seconds is None
            else float(stale_timeout_seconds)
        )
        self.stale_timeout_seconds = max(0.1, configured_stale_timeout)
        # Preserve the old attributes for callers while using one health timeout.
        self.state_stale_seconds = self.stale_timeout_seconds
        self.frame_stale_seconds = self.stale_timeout_seconds
        self.reconnect_delay_seconds = max(0.01, float(reconnect_delay_seconds))
        configured_step = (
            self.reconnect_delay_seconds
            if reconnect_backoff_step_seconds is None
            else float(reconnect_backoff_step_seconds)
        )
        self.reconnect_backoff_step_seconds = max(0.0, configured_step)
        self.reconnect_max_delay_seconds = max(
            self.reconnect_delay_seconds, float(reconnect_max_delay_seconds)
        )
        self.reconnect_stable_reset_seconds = max(
            0.1, float(reconnect_stable_reset_seconds)
        )
        self.disconnect_grace_seconds = max(
            0.1, float(disconnect_grace_seconds)
        )
        self.video_track_end_grace_seconds = max(
            0.1, float(video_track_end_grace_seconds)
        )
        self.reconnect_on_multi_signal_stale = bool(
            reconnect_on_multi_signal_stale
        )
        self.multi_signal_stale_grace_seconds = max(
            0.1, float(multi_signal_stale_grace_seconds)
        )
        self.enable_video_active_recovery = bool(
            enable_video_active_recovery
        )
        self.video_first_frame_wait_seconds = max(
            self.frame_stale_seconds + 0.05,
            float(video_first_frame_wait_seconds),
        )
        self.video_soft_recovery_seconds = max(
            self.frame_stale_seconds + 0.05,
            float(video_soft_recovery_seconds),
        )
        self.video_soft_toggle_delay_seconds = max(
            0.01, float(video_soft_toggle_delay_seconds)
        )
        self.video_soft_observe_seconds = max(
            0.1, float(video_soft_observe_seconds)
        )
        self.video_reconnect_cooldown_seconds = max(
            0.0, float(video_reconnect_cooldown_seconds)
        )
        self.video_recovery_min_frames = max(2, int(video_recovery_min_frames))
        self.video_recovery_min_duration_seconds = max(
            0.05, float(video_recovery_min_duration_seconds)
        )
        self.video_recovery_max_gap_seconds = max(
            0.05, float(video_recovery_max_gap_seconds)
        )
        self.video_false_recovery_window_seconds = max(
            self.video_recovery_max_gap_seconds,
            float(video_false_recovery_window_seconds),
        )
        self.capture_fps = max(1.0, float(capture_fps))
        self.jpeg_quality = max(40, min(95, int(jpeg_quality)))
        self.enable_video = bool(enable_video)
        self.enable_sport_state = bool(enable_sport_state)
        self.enable_uwb = bool(enable_uwb)
        self.enable_multiple_state = bool(enable_multiple_state)
        self.enable_low_state = bool(enable_low_state)
        self.enable_audio = bool(enable_audio)
        self.diagnostic_mode = bool(diagnostic_mode)
        self._connection_factory = connection_factory or _default_connection_factory
        self._audio_hub_factory = audio_hub_factory or _default_audio_hub_factory
        self.tts_voice = str(tts_voice or "Microsoft Huihui Desktop").strip()

        self._lock = threading.RLock()
        # Sport RPCs are single-flight. In particular, a watchdog StopMove
        # must not race a Move whose ACK is still pending on the same resolver.
        self._sport_request_lock = threading.Lock()
        self._loop_ready = threading.Event()
        self._first_state = threading.Event()
        self._stop = threading.Event()
        self._reconnect_blocked = threading.Event()
        self._encoder_stop = threading.Event()
        self._raw_frames: queue.Queue[Any] = queue.Queue(maxsize=1)
        self._thread: threading.Thread | None = None
        self._encoder_thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connection_lost_event: asyncio.Event | None = None
        self._connection_tasks: dict[int, set[asyncio.Task[Any]]] = {}
        self._supervisor_future: Any = None
        self._initial_connection_done = threading.Event()
        self._initial_connection_error: Exception | None = None
        self._connection: Any = None
        self._connection_generation = 0
        self._lost_generation = 0
        self._connection_state = "disconnected"
        self._peer_connection_state = "new"
        self._ice_connection_state = "new"
        self._data_channel_ready = False
        self._video_degraded_reason: str | None = None
        self._data_degraded_reason: str | None = None
        self._multi_signal_stale_since: float | None = None
        self._connected_since: str | None = None
        self._connected_monotonic: float | None = None
        self._last_disconnect_at: str | None = None
        self._last_disconnect_reason: str | None = None
        self._last_diagnostic_reason: str | None = None
        self._last_disconnect_monotonic: float | None = None
        self._recent_disconnects: deque[dict[str, Any]] = deque(maxlen=20)
        self._last_reconnect_at: str | None = None
        self._reconnect_count = 0
        self._reconnect_failure_streak = 0
        self._last_reconnect_delay_seconds: float | None = None
        self._next_reconnect_delay_seconds: float | None = None
        self._last_peer_closed_at: str | None = None
        self._last_peer_closed_monotonic: float | None = None
        self._last_peer_close_duration_ms: float | None = None
        self._last_connect_trace: dict[str, Any] | None = None
        self._recent_connect_traces: deque[dict[str, Any]] = deque(maxlen=20)
        self._connect_trace_started_monotonic: float | None = None
        self._transport_disconnected_since: float | None = None
        self._transport_disconnect_reason: str | None = None
        self._transport_grace_recovery_in_progress = False
        self._video_track_serial = 0
        self._active_video_track_serial: int | None = None
        self._video_track_end_pending_serial: int | None = None
        self._video_track_end_started_monotonic: float | None = None
        self._video_track_end_reason: str | None = None
        self._video_track_end_count = 0
        self._video_track_end_recovered_count = 0
        self._video_track_end_confirmed_count = 0
        self._subscribed_topics: list[str] = []
        self._rtc_topic: dict[str, str] = {}
        self._sport_cmd: dict[str, int] = {}
        self._audio_hub: Any = None
        self._audio_uuid_cache: dict[str, str] = {}
        # Half-duplex recording/playback must be serialized, but a background
        # AudioHub cache upload must never hold up live microphone capture.
        self._audio_io_lock = threading.Lock()
        self._audio_preload_lock = threading.Lock()
        self._audio_channel_available = False
        self._audio_callback_generation = 0
        self._microphone_recording = False
        self._microphone_frames: list[bytes] = []
        self._microphone_sample_rate = 0
        self._microphone_channels = 0
        self._microphone_sample_count = 0
        self._microphone_frame_count = 0
        self._microphone_target_samples = 0
        self._pending_microphone_duration = 0.0
        self._microphone_complete = threading.Event()
        self._microphone_error: str | None = None
        self._microphone_vad_enabled = False
        self._microphone_speech_detected = False
        self._microphone_last_voice_sample = 0
        self._microphone_vad_min_samples = 0
        self._microphone_vad_silence_samples = 0
        self._microphone_endpoint_reason = "fixed_duration"
        self._microphone_vad_noise_rms: list[float] = []
        self._microphone_vad_noise_peak: list[int] = []
        self._microphone_vad_calibration_samples = 0
        self._microphone_vad_rms_threshold = 650.0
        self._microphone_vad_peak_threshold = 1800
        self._last_microphone_capture: dict[str, object] | None = None
        self._started = False
        self._connected = False
        self._connection_count = 0
        self._disconnect_count = 0

        self._sport_state: dict[str, Any] | None = None
        self._state_topic: str | None = None
        self._state_received_monotonic: float | None = None
        self._state_received_iso: str | None = None
        self._state_sample_counts: dict[str, int] = {}
        self._uwb_state: dict[str, Any] | None = None
        self._uwb_source_keys: list[str] = []
        self._uwb_topic: str | None = None
        self._uwb_received_monotonic: float | None = None
        self._uwb_received_timestamp_ms: int | None = None
        self._uwb_received_iso: str | None = None
        self._uwb_sample_counts: dict[str, int] = {}
        self._multiple_state: dict[str, Any] | None = None
        self._multiple_state_received_monotonic: float | None = None
        self._multiple_state_sample_count = 0
        self._low_state: dict[str, Any] | None = None
        self._low_state_received_monotonic: float | None = None
        self._low_state_sample_counts: dict[str, int] = {}
        self._command_counts: dict[str, int] = {}
        self._motion_request_in_flight: str | None = None
        self._motion_request_started_monotonic: float | None = None
        self._last_motion_ack_at: str | None = None
        self._last_motion_ack_command: str | None = None
        self._last_motion_ack_latency_ms: float | None = None
        self._last_motion_ack_error: str | None = None
        self._motion_request_timeout_count = 0
        self._remote_stop_state = "STOP_CONFIRMED"
        self._stop_required_after_reconnect = False
        self._motion_ready = False

        self._latest_frame: LatestVideoFrame | None = None
        self._last_raw_frame_monotonic: float | None = None
        self._last_raw_frame_iso: str | None = None
        self._last_raw_frame_generation = 0
        self._raw_frame_count = 0
        self._last_frame_monotonic: float | None = None
        self._last_encoded_frame_iso: str | None = None
        self._last_frame_generation = 0
        self._last_encoded_monotonic = 0.0
        self._frame_sequence = 0
        self._encoded_frame_count = 0
        self._video_session_frame_count = 0
        self._video_started_monotonic = 0.0
        self._video_error_count = 0
        self._dropped_frame_count = 0
        self._encode_duration_ms_last: float | None = None
        self._encode_duration_ms_max: float | None = None
        self._encode_duration_ms_ewma: float | None = None
        self._video_client_count = 0
        self._last_video_error: str | None = None
        self._video_watchdog_state = (
            "AWAITING_FIRST_FRAME" if self.enable_video else "DISABLED"
        )
        self._video_stale_started_monotonic: float | None = None
        self._video_recovery_started_monotonic: float | None = None
        self._video_recovery_candidate_started_monotonic: float | None = None
        self._video_recovery_last_frame_monotonic: float | None = None
        self._video_recovery_frame_count = 0
        self._video_recovery_max_gap_observed_seconds = 0.0
        self._video_soft_toggle_off_monotonic: float | None = None
        self._video_soft_toggle_on_monotonic: float | None = None
        self._video_soft_recovery_start_raw_frame_count = 0
        self._video_watchdog_cooldown_until_monotonic: float | None = None
        self._video_channel_enabled_monotonic: float | None = None
        self._video_soft_attempted = False
        self._video_recovery_required_on_next_connection = False
        self._video_last_recovered_monotonic: float | None = None
        self._video_stale_count = 0
        self._video_soft_recovery_count = 0
        self._video_soft_recovery_success_count = 0
        self._video_full_reconnect_count = 0
        self._video_max_raw_frame_age_seconds = 0.0
        self._video_max_recovery_duration_seconds = 0.0
        self._video_false_recovery_count = 0

        try:
            version = importlib.metadata.version("unitree_webrtc_connect")
        except importlib.metadata.PackageNotFoundError:
            version = "source"
        self.sdk_version = f"webrtc-{version}"

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._stop.clear()
            self._reconnect_blocked.clear()
            self._first_state.clear()
            self._initial_connection_done.clear()
            self._initial_connection_error = None
            self._connection_state = "connecting"
            self._started = True
            if self.enable_video:
                self._start_encoder_locked()
            self._loop_ready.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="go2-wireless-runtime",
                daemon=True,
            )
            self._thread.start()
        if not self._loop_ready.wait(timeout=2.0) or self._loop is None:
            with self._lock:
                self._started = False
            self._shutdown_workers()
            raise GatewayError(
                ErrorCode.SDK_NOT_INITIALIZED,
                "Wireless Runtime event loop did not start.",
                503,
            )
        self._supervisor_future = asyncio.run_coroutine_threadsafe(
            self._connection_supervisor(), self._loop
        )
        if not self._initial_connection_done.wait(
            timeout=self.connect_timeout_seconds + self.state_timeout_seconds + 2.0
        ):
            self.close(send_stop=False)
            raise GatewayError(
                ErrorCode.SDK_NOT_INITIALIZED,
                "Wireless Runtime initialization timed out.",
                503,
            )
        if self._initial_connection_error is not None:
            exc = self._initial_connection_error
            self.close(send_stop=False)
            raise GatewayError(
                ErrorCode.SDK_NOT_INITIALIZED,
                f"Wireless Runtime initialization failed: {exc}",
                503,
            ) from exc
        if self._supervisor_future.done():
            try:
                self._supervisor_future.result()
            except Exception as exc:
                self.close(send_stop=False)
                raise GatewayError(
                    ErrorCode.SDK_NOT_INITIALIZED,
                    f"Wireless Runtime initialization failed: {exc}",
                    503,
                ) from exc
        with self._lock:
            connected = self._connected
        if not connected:
            exc = RuntimeError("initial WebRTC connection did not become ready")
            self._initial_connection_error = exc
            self.close(send_stop=False)
            raise GatewayError(
                ErrorCode.SDK_NOT_INITIALIZED,
                f"Wireless Runtime initialization failed: {exc}",
                503,
            ) from exc

    def request_shutdown(self) -> None:
        """Prevent new reconnects while preserving the current link for StopMove."""

        self._reconnect_blocked.set()

    def close(self, *, send_stop: bool = True) -> None:
        with self._lock:
            loop = self._loop
            started = self._started
            supervisor_future = self._supervisor_future
        if started and send_stop:
            try:
                self.stop_motion()
            except Exception:
                pass
        self.request_shutdown()
        self._stop.set()
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._wake_supervisor)
        if supervisor_future is not None and not supervisor_future.done():
            try:
                supervisor_future.result(
                    timeout=max(2.0, self.connect_timeout_seconds + 1.0)
                )
            except Exception:
                supervisor_future.cancel()
        with self._lock:
            self._started = False
            self._connected = False
            self._data_channel_ready = False
            self._motion_ready = False
            self._video_degraded_reason = None
            self._data_degraded_reason = None
            self._multi_signal_stale_since = None
            self._transport_disconnected_since = None
            self._transport_disconnect_reason = None
            self._transport_grace_recovery_in_progress = False
            self._connection_state = "disconnected"
            self._video_watchdog_state = (
                "AWAITING_FIRST_FRAME" if self.enable_video else "DISABLED"
            )
        self._shutdown_workers()
        with self._lock:
            self._supervisor_future = None
            self._connection = None
            self._rtc_topic = {}
            self._sport_cmd = {}
            self._audio_hub = None
            self._audio_uuid_cache = {}
            self._audio_channel_available = False
            self._microphone_recording = False
            self._microphone_frames = []
            self._microphone_complete.clear()
            self._sport_state = None
            self._state_topic = None
            self._state_received_monotonic = None
            self._state_received_iso = None
            self._state_sample_counts = {}
            self._uwb_state = None
            self._uwb_source_keys = []
            self._uwb_topic = None
            self._uwb_received_monotonic = None
            self._uwb_received_timestamp_ms = None
            self._uwb_received_iso = None
            self._uwb_sample_counts = {}
            self._multiple_state = None
            self._multiple_state_received_monotonic = None
            self._multiple_state_sample_count = 0
            self._low_state = None
            self._low_state_received_monotonic = None
            self._low_state_sample_counts = {}
            self._command_counts = {}
            self._first_state.clear()

    def is_started(self) -> bool:
        with self._lock:
            return self._started

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def activate_companion_inputs(
        self,
        *,
        timeout_seconds: float = 5.0,
        enable_multiple_state: bool = True,
    ) -> dict[str, Any]:
        """Subscribe companion topics on the existing BASE PeerConnection."""

        timeout = max(0.5, float(timeout_seconds))
        self._run_runtime_coroutine(
            self._activate_companion_inputs_async(
                enable_multiple_state=enable_multiple_state
            ),
            "WebRTC companion input activation",
            timeout=min(timeout, 2.0),
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.status()
            multiple = status.get("multipleState") or {}
            if (
                status.get("sportStateReady")
                and (status.get("uwb") or {}).get("fresh")
                and (not enable_multiple_state or multiple.get("received"))
            ):
                return status
            if not status.get("connected"):
                break
            time.sleep(0.02)
        status = self.status()
        raise self._command_error(
            "Companion inputs did not become ready "
            f"(sport={status.get('sportStateReady')}, "
            f"uwb={(status.get('uwb') or {}).get('fresh')}, "
            f"multiple={(status.get('multipleState') or {}).get('received')})"
        )

    def deactivate_companion_inputs(self) -> None:
        """Unsubscribe companion topics while keeping BASE video connected."""

        with self._lock:
            connected = self._connected and self._connection is not None
            if not connected:
                self.enable_sport_state = False
                self.enable_uwb = False
                self.enable_multiple_state = False
                self._clear_companion_samples_locked()
                return
        self._run_runtime_coroutine(
            self._deactivate_companion_inputs_async(),
            "WebRTC companion input deactivation",
            timeout=2.0,
        )

    def activate_voice(self) -> dict[str, Any]:
        """Attach microphone processing lazily on the existing connection."""

        self._run_runtime_coroutine(
            self._activate_voice_async(),
            "WebRTC voice activation",
            timeout=2.0,
        )
        return self.status()

    def deactivate_voice(self) -> None:
        with self._lock:
            self.enable_audio = False
            self._audio_channel_available = False
            self._microphone_recording = False
            connected = self._connected and self._connection is not None
            if not connected:
                return
        self._run_runtime_coroutine(
            self._deactivate_voice_async(),
            "WebRTC voice deactivation",
            timeout=2.0,
        )

    def send_move(self, vx: float, vy: float, wz: float) -> int:
        with self._lock:
            api_id = self._sport_cmd.get("Move")
            motion_ready = self._motion_ready
        if not motion_ready:
            raise self._command_error(
                "Motion is locked until the reconnect StopMove is confirmed"
            )
        if api_id is None:
            raise self._command_error("Move API id is unavailable")
        return self._publish(
            {
                "api_id": api_id,
                "parameter": {"x": float(vx), "y": float(vy), "z": float(wz)},
            },
            "Move",
        )

    def stop_motion(self) -> int:
        with self._lock:
            if not self._started:
                return 0
            if self._stop_required_after_reconnect:
                LOGGER.warning(
                    "STOP_ALREADY_PENDING_AFTER_RECONNECT state=%s",
                    self._remote_stop_state,
                )
                return -1
            connection = self._connection
            transport_connected = self._connected and bool(
                getattr(connection, "isConnected", True)
            )
            if not transport_connected:
                self._connected = False
                self._remote_stop_state = "STOP_UNCONFIRMED_TRANSPORT_LOST"
                self._stop_required_after_reconnect = True
                LOGGER.warning("STOP_UNCONFIRMED_TRANSPORT_LOST")
                return -1
            api_id = self._sport_cmd.get("StopMove")
        if api_id is None:
            raise self._command_error("StopMove API id is unavailable")
        return self._publish({"api_id": api_id}, "StopMove")

    def apply_pose(
        self,
        *,
        roll_rad: float,
        pitch_rad: float,
        yaw_rad: float,
        body_height_m: float,
    ) -> int:
        """Enter high-level balance stand and apply relative RPY/body height."""

        self._publish_sport_command("BalanceStand")
        self._publish_sport_command(
            "Euler",
            {"x": float(roll_rad), "y": float(pitch_rad), "z": float(yaw_rad)},
        )
        self._publish_sport_command("BodyHeight", {"data": float(body_height_m)})
        return 0

    def reset_pose(self) -> int:
        """Restore neutral high-level pose; the controller follows with StopMove."""

        self._publish_sport_command("Euler", {"x": 0.0, "y": 0.0, "z": 0.0})
        self._publish_sport_command("BodyHeight", {"data": 0.0})
        return 0

    def play_audio_file(
        self,
        audio_file: str | os.PathLike[str],
        *,
        timeout_seconds: float | None = None,
    ) -> int:
        """Upload/cache and play a WAV file through this runtime's DataChannel."""

        source = os.path.abspath(os.fspath(audio_file))
        if not os.path.isfile(source):
            raise ValueError(f"audio file does not exist: {source}")
        if os.path.splitext(source)[1].lower() != ".wav":
            raise ValueError("WebRTC AudioHub playback currently requires a WAV file")
        digest = self._audiohub_digest(source)
        stem = self._safe_audio_name(os.path.splitext(os.path.basename(source))[0])
        custom_name = f"go2_{stem}_{digest}"
        with self._audio_io_lock:
            with tempfile.TemporaryDirectory(prefix="go2-audio-") as temp_dir:
                upload_path = os.path.join(temp_dir, f"{custom_name}.wav")
                self._prepare_audiohub_wav(source, upload_path)
                playback_timeout = (
                    max(self.command_timeout_seconds + 1.0, 120.0)
                    if timeout_seconds is None
                    else max(0.5, float(timeout_seconds))
                )
                self._run_runtime_coroutine(
                    self._play_audio_file_async(upload_path, custom_name),
                    "AudioHub playback",
                    timeout=playback_timeout,
                )
        return 0

    def preload_audio_file(
        self,
        audio_file: str | os.PathLike[str],
        *,
        replace_existing_stem: bool = False,
    ) -> int:
        """Upload/cache a WAV on Go2 without starting speaker playback."""

        source = os.path.abspath(os.fspath(audio_file))
        if not os.path.isfile(source):
            raise ValueError(f"audio file does not exist: {source}")
        if os.path.splitext(source)[1].lower() != ".wav":
            raise ValueError("WebRTC AudioHub preload currently requires a WAV file")
        digest = self._audiohub_digest(source)
        stem = self._safe_audio_name(os.path.splitext(os.path.basename(source))[0])
        custom_name = f"go2_{stem}_{digest}"
        with self._audio_preload_lock:
            with tempfile.TemporaryDirectory(prefix="go2-audio-preload-") as temp_dir:
                upload_path = os.path.join(temp_dir, f"{custom_name}.wav")
                self._prepare_audiohub_wav(source, upload_path)
                self._run_runtime_coroutine(
                    self._preload_audio_file_async(
                        upload_path,
                        custom_name,
                        replace_prefix=(
                            f"go2_{stem}_" if replace_existing_stem else None
                        ),
                    ),
                    "AudioHub preload",
                    timeout=max(self.command_timeout_seconds + 1.0, 15.0),
                )
        return 0

    def preload_audio_files(
        self,
        audio_files: list[str | os.PathLike[str]] | tuple[str | os.PathLike[str], ...],
        *,
        retry_attempts: int = 2,
    ) -> dict[str, AudioPreloadResult]:
        """Preload fixed presets using one shared AudioHub catalogue snapshot.

        The startup path intentionally never deletes robot audio. It reads the
        catalogue once, uploads only missing content-addressed names, then
        refreshes the catalogue once so every uploaded name has a playable UUID.
        Extra catalogue reads occur only while recovering from an upload failure.
        """

        attempts = max(1, min(3, int(retry_attempts)))
        sources: list[str] = []
        seen: set[str] = set()
        for audio_file in audio_files:
            source = os.path.abspath(os.fspath(audio_file))
            if source in seen:
                continue
            if not os.path.isfile(source):
                raise ValueError(f"audio file does not exist: {source}")
            if os.path.splitext(source)[1].lower() != ".wav":
                raise ValueError(
                    "WebRTC AudioHub preload currently requires WAV files"
                )
            seen.add(source)
            sources.append(source)
        if not sources:
            return {}

        with self._audio_preload_lock:
            with tempfile.TemporaryDirectory(prefix="go2-audio-preload-batch-") as temp_dir:
                prepared: list[tuple[str, str, str, float]] = []
                for source in sources:
                    digest = self._audiohub_digest(source)
                    stem = self._safe_audio_name(
                        os.path.splitext(os.path.basename(source))[0]
                    )
                    custom_name = f"go2_{stem}_{digest}"
                    upload_path = os.path.join(temp_dir, f"{custom_name}.wav")
                    self._prepare_audiohub_wav(source, upload_path)
                    upload_timeout = self._audiohub_upload_timeout(upload_path)
                    prepared.append(
                        (source, upload_path, custom_name, upload_timeout)
                    )

                # Each upload has its own bounded timeout below. This outer bound
                # only prevents the entire background preload from hanging forever.
                batch_timeout = max(
                    30.0,
                    20.0
                    + sum(item[3] * attempts for item in prepared)
                    + (10.0 * attempts),
                )
                return self._run_runtime_coroutine(
                    self._preload_audio_files_async(
                        prepared,
                        retry_attempts=attempts,
                    ),
                    "AudioHub preset batch preload",
                    timeout=batch_timeout,
                )

    def speak(self, text: str) -> int:
        """Render Windows TTS locally, then play it through the same WebRTC peer."""

        normalized = str(text or "").strip()
        if not normalized:
            raise ValueError("speech text must not be empty")
        if len(normalized) > 200:
            raise ValueError("speech text must be at most 200 characters")
        digest = hashlib.sha256(
            f"{self.tts_voice}\0{normalized}".encode("utf-8")
        ).hexdigest()[:12]
        with tempfile.TemporaryDirectory(prefix="go2-tts-") as temp_dir:
            wav_path = os.path.join(temp_dir, f"go2_speech_{digest}.wav")
            self._render_windows_speech(normalized, wav_path)
            return self.play_audio_file(wav_path)

    def record_microphone_wav(
        self,
        output_path: str | os.PathLike[str],
        *,
        duration_seconds: float = 5.0,
        vad_enabled: bool = False,
        vad_trailing_silence_seconds: float = 0.3,
        vad_min_capture_seconds: float = 0.8,
        diagnostic_prefix: str | None = None,
    ) -> MicrophoneCaptureResult:
        """Capture Go2 microphone PCM on the existing PeerConnection."""

        duration = float(duration_seconds)
        if not 0.5 <= duration <= 10.0:
            raise ValueError("microphone duration must be within [0.5, 10.0] seconds")
        target = os.path.abspath(os.fspath(output_path))
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        lock_acquired = self._audio_io_lock.acquire(timeout=2.0)
        if not lock_acquired:
            raise RuntimeError(
                "WebRTC audio I/O is busy; microphone capture refused after 2.0s"
            )
        try:
            payload = self._run_runtime_coroutine(
                self._capture_microphone_async(
                    duration,
                    vad_enabled=vad_enabled,
                    vad_trailing_silence_seconds=vad_trailing_silence_seconds,
                    vad_min_capture_seconds=vad_min_capture_seconds,
                    diagnostic_prefix=diagnostic_prefix,
                ),
                "WebRTC microphone capture",
                timeout=duration + 5.0,
            )
            pcm = bytes(payload["pcm"])
            sample_rate = int(payload["sample_rate"])
            channels = int(payload["channels"])
            with wave.open(target, "wb") as stream:
                stream.setnchannels(channels)
                stream.setsampwidth(2)
                stream.setframerate(sample_rate)
                stream.writeframes(pcm)
        finally:
            self._audio_io_lock.release()
        import numpy as np

        values = np.frombuffer(pcm, dtype="<i2")
        peak = int(np.max(np.abs(values.astype(np.int32)))) if values.size else 0
        rms = float(np.sqrt(np.mean(values.astype(np.float64) ** 2))) if values.size else 0.0
        result = MicrophoneCaptureResult(
            path=target,
            sample_rate=sample_rate,
            channels=channels,
            duration_seconds=(values.size / channels / sample_rate),
            sample_count=int(values.size // channels),
            frame_count=int(payload["frame_count"]),
            peak=peak,
            rms=rms,
            byte_count=len(pcm),
            vad_enabled=bool(payload["vad_enabled"]),
            speech_detected=bool(payload["speech_detected"]),
            endpoint_reason=str(payload["endpoint_reason"]),
            trailing_silence_seconds=float(payload["trailing_silence_seconds"]),
        )
        with self._lock:
            self._last_microphone_capture = result.to_dict()
        return result

    def get_sport_mode_state(self) -> dict[str, Any] | None:
        with self._lock:
            return deepcopy(self._sport_state)

    def get_uwb_state(self) -> dict[str, Any] | None:
        with self._lock:
            return deepcopy(self._uwb_state)

    def get_uwb_snapshot(self) -> dict[str, Any]:
        """Return one coherent read-only UWB transport snapshot."""

        with self._lock:
            return {
                "fields": deepcopy(self._uwb_state),
                "received_monotonic": self._uwb_received_monotonic,
                "received_timestamp_ms": self._uwb_received_timestamp_ms,
                "sample_count": sum(self._uwb_sample_counts.values()),
                "source_keys": list(self._uwb_source_keys),
                "topic": self._uwb_topic,
            }

    def companion_telemetry_status(self) -> dict[str, Any]:
        """Return the small coherent subset needed by the UWB monitor.

        The full runtime status also copies video, audio, reconnect, and
        diagnostic state.  The competition telemetry page polls at 5 Hz, so
        keeping this snapshot small avoids making display latency depend on
        unrelated video work.
        """

        with self._lock:
            now = time.monotonic()
            uwb_age = (
                None
                if self._uwb_received_monotonic is None
                else max(0.0, now - self._uwb_received_monotonic)
            )
            return {
                "connected": self._connected,
                "connectionCount": 1 if self._connected else 0,
                "lastError": None,
                "uwb": {
                    "topic": self._uwb_topic,
                    "sampleCount": sum(self._uwb_sample_counts.values()),
                    "ageMs": None if uwb_age is None else uwb_age * 1000.0,
                    "fields": deepcopy(self._uwb_state),
                    "sourceKeys": list(self._uwb_source_keys),
                    "receivedMonotonic": self._uwb_received_monotonic,
                    "receivedTimestampMs": self._uwb_received_timestamp_ms,
                },
            }

    def get_motion_state(self) -> dict[str, Any] | None:
        with self._lock:
            state = deepcopy(self._sport_state)
            received = self._state_received_monotonic
            topic = self._state_topic
        if state is None or received is None:
            return None
        try:
            position = state["position"]
            rpy = state["imu_state"]["rpy"]
            values = (float(position[0]), float(position[1]), float(rpy[2]))
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        if not all(math.isfinite(value) for value in values):
            return None
        return {
            "x": values[0],
            "y": values[1],
            "yaw": values[2],
            "received_monotonic": received,
            "source": "WebRTC SportModeState.position+imu_state.rpy",
            "topic": topic,
        }

    def latest_frame(self) -> LatestVideoFrame | None:
        with self._lock:
            return self._latest_frame

    def current_frame(self) -> LatestVideoFrame | None:
        """Return a frame only when it belongs to the live connection and is fresh."""

        with self._lock:
            if (
                not self._connected
                or self._latest_frame is None
                or self._last_frame_generation != self._connection_generation
                or self._last_frame_monotonic is None
                or time.monotonic() - self._last_frame_monotonic
                > self.frame_stale_seconds
            ):
                return None
            return self._latest_frame

    def register_video_client(self) -> None:
        with self._lock:
            self._video_client_count += 1

    def unregister_video_client(self) -> None:
        with self._lock:
            self._video_client_count = max(0, self._video_client_count - 1)

    def status(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            state_age = (
                None
                if self._state_received_monotonic is None
                else now - self._state_received_monotonic
            )
            frame_age = (
                None
                if self._last_frame_monotonic is None
                else now - self._last_frame_monotonic
            )
            raw_frame_age = (
                None
                if self._last_raw_frame_monotonic is None
                or self._last_raw_frame_generation != self._connection_generation
                else max(0.0, now - self._last_raw_frame_monotonic)
            )
            if raw_frame_age is not None:
                self._video_max_raw_frame_age_seconds = max(
                    self._video_max_raw_frame_age_seconds,
                    raw_frame_age,
                )
            encoded_frame_age = (
                None
                if self._last_frame_monotonic is None
                or self._last_frame_generation != self._connection_generation
                else max(0.0, now - self._last_frame_monotonic)
            )
            uwb_age = (
                None
                if self._uwb_received_monotonic is None
                else now - self._uwb_received_monotonic
            )
            low_state_age = (
                None
                if self._low_state_received_monotonic is None
                else now - self._low_state_received_monotonic
            )
            multiple_state_age = (
                None
                if self._multiple_state_received_monotonic is None
                else now - self._multiple_state_received_monotonic
            )
            state_fresh = bool(
                self._connected
                and self.enable_sport_state
                and self._data_channel_ready
                and state_age is not None
                and state_age <= self.state_stale_seconds
            )
            frame_fresh = bool(
                self._connected
                and self.enable_video
                and self._video_watchdog_state == "HEALTHY"
                and self._latest_frame is not None
                and self._last_frame_generation == self._connection_generation
                and frame_age is not None
                and frame_age <= self.frame_stale_seconds
            )
            connection_state = self._connection_state
            state_unhealthy = bool(
                self._connected
                and self.enable_sport_state
                and self._data_channel_ready
                and state_age is not None
                and state_age > self.state_stale_seconds
            )
            raw_video_unhealthy = bool(
                self._connected
                and self.enable_video
                and raw_frame_age is not None
                and raw_frame_age > self.frame_stale_seconds
            )
            encoded_video_unhealthy = bool(
                self.enable_video
                and encoded_frame_age is not None
                and encoded_frame_age > self.frame_stale_seconds
            )
            video_unhealthy = raw_video_unhealthy or encoded_video_unhealthy
            video_recovering = self._video_watchdog_state in {
                "DEGRADED",
                "SOFT_RECOVERY",
                "RECOVERING",
                "OFFLINE",
            }
            video_unhealthy = video_unhealthy or video_recovering
            transport_grace_active = (
                self._transport_disconnected_since is not None
            )
            if (
                self._connected
                and not transport_grace_active
                and (state_unhealthy or video_unhealthy)
            ):
                connection_state = "degraded"
            video_degraded_reason = self._video_degraded_reason or (
                "raw_frame_stale"
                if raw_video_unhealthy
                else "encoded_frame_stale"
                if encoded_video_unhealthy
                else None
            )
            data_degraded_reason = "sport_state_stale" if state_unhealthy else None
            multi_signal_stale_seconds = (
                None
                if self._multi_signal_stale_since is None
                else max(0.0, now - self._multi_signal_stale_since)
            )
            first_raw_received = bool(
                self._last_raw_frame_monotonic is not None
                and self._last_raw_frame_generation == self._connection_generation
            )
            first_encoded_produced = bool(
                self._last_frame_monotonic is not None
                and self._last_frame_generation == self._connection_generation
            )
            return {
                "transport": "WebRTC",
                "connectionOwner": "Go2WirelessRuntime",
                "connectionMode": "LocalSTA",
                "robotIp": self.robot_ip,
                "started": self._started,
                "connected": self._connected,
                "connectionState": connection_state,
                "transportHealthState": (
                    "offline"
                    if not self._connected
                    else "degraded"
                    if state_unhealthy
                    or video_unhealthy
                    or transport_grace_active
                    else "healthy"
                ),
                "watchdogPolicy": (
                    "hard_transport_video_l1_l2_or_confirmed_raw_plus_sport_stale"
                    if self.reconnect_on_multi_signal_stale
                    and self.enable_video_active_recovery
                    else "hard_transport_or_confirmed_raw_plus_sport_stale"
                    if self.reconnect_on_multi_signal_stale
                    else "hard_transport_plus_video_l1_l2"
                    if self.enable_video_active_recovery
                    else "hard_transport_video_degraded_only"
                ),
                "diagnosticMode": self.diagnostic_mode,
                "low_state_enabled": self.enable_low_state,
                "layers": {
                    "base": "active" if self._connected else "offline",
                    "companion": (
                        "active"
                        if self.enable_sport_state
                        and self.enable_uwb
                        else "standby"
                    ),
                    "voice": "active" if self.enable_audio else "standby",
                },
                "multiSignalStaleSeconds": multi_signal_stale_seconds,
                "multiSignalStaleGraceSeconds": (
                    self.multi_signal_stale_grace_seconds
                ),
                "peerConnectionState": self._peer_connection_state,
                "iceConnectionState": self._ice_connection_state,
                "connectedSince": self._connected_since,
                "lastDisconnectAt": self._last_disconnect_at,
                "lastDisconnectReason": self._last_disconnect_reason,
                "diagnosticReason": self._last_diagnostic_reason,
                "recentDisconnects": deepcopy(list(self._recent_disconnects)),
                "reconnectCount": self._reconnect_count,
                "lastReconnectAt": self._last_reconnect_at,
                "reconnectFailureStreak": self._reconnect_failure_streak,
                "lastReconnectDelaySeconds": (
                    self._last_reconnect_delay_seconds
                ),
                "nextReconnectDelaySeconds": (
                    self._next_reconnect_delay_seconds
                ),
                "reconnectStableResetSeconds": (
                    self.reconnect_stable_reset_seconds
                ),
                "connectionDiagnostics": {
                    "lastPeerClosedAt": self._last_peer_closed_at,
                    "lastPeerCloseDurationMs": self._last_peer_close_duration_ms,
                    "lastConnectTrace": deepcopy(self._last_connect_trace),
                    "recentConnectTraces": deepcopy(
                        list(self._recent_connect_traces)
                    ),
                },
                "transportDisconnectGrace": {
                    "active": self._transport_disconnected_since is not None,
                    "reason": self._transport_disconnect_reason,
                    "elapsedSeconds": (
                        None
                        if self._transport_disconnected_since is None
                        else max(
                            0.0,
                            now - self._transport_disconnected_since,
                        )
                    ),
                    "timeoutSeconds": self.disconnect_grace_seconds,
                    "stopRecoveryInProgress": (
                        self._transport_grace_recovery_in_progress
                    ),
                },
                "staleTimeoutSeconds": self.stale_timeout_seconds,
                "subscriptionProfile": {
                    "video": self.enable_video,
                    "uwb": self.enable_uwb,
                    "sport": self.enable_sport_state,
                    "multipleState": self.enable_multiple_state,
                    "lowState": self.enable_low_state,
                    "audio": self.enable_audio,
                    "topics": list(self._subscribed_topics),
                },
                "connectionCount": 1 if self._connected else 0,
                "successfulConnectionCount": self._connection_count,
                "disconnectCount": self._disconnect_count,
                "dataChannelReady": bool(self._connected and self._data_channel_ready),
                "motionReady": bool(
                    self._connected
                    and self._data_channel_ready
                    and self._motion_ready
                ),
                "sportStateReady": state_fresh,
                "sportWatchdogArmed": state_age is not None,
                "dataHealthState": (
                    "offline"
                    if not self._connected
                    else "standby"
                    if not self.enable_sport_state
                    else "degraded"
                    if state_unhealthy
                    else "awaiting_first_state"
                    if state_age is None
                    else "healthy"
                ),
                "dataDegradedReason": data_degraded_reason,
                "stateTopic": self._state_topic,
                "stateSampleCounts": dict(self._state_sample_counts),
                "lastStateAt": self._state_received_iso,
                "stateAgeMs": None if state_age is None else state_age * 1000.0,
                "lastSportStateAt": self._state_received_iso,
                "sportStateAgeSeconds": state_age,
                "uwb": {
                    "enabled": self.enable_uwb,
                    "topic": self._uwb_topic,
                    "received": self._uwb_state is not None,
                    "fresh": bool(
                        self._connected
                        and uwb_age is not None
                        and uwb_age <= self.state_stale_seconds
                    ),
                    "sampleCount": sum(self._uwb_sample_counts.values()),
                    "sampleCounts": dict(self._uwb_sample_counts),
                    "lastSampleAt": self._uwb_received_iso,
                    "ageMs": None if uwb_age is None else uwb_age * 1000.0,
                    "fields": deepcopy(self._uwb_state),
                    "sourceKeys": list(self._uwb_source_keys),
                },
                "lowState": {
                    "enabled": self.enable_low_state,
                    "received": self._low_state is not None,
                    "fresh": bool(
                        self._connected
                        and low_state_age is not None
                        and low_state_age <= self.state_stale_seconds
                    ),
                    "sampleCount": sum(self._low_state_sample_counts.values()),
                    "sampleCounts": dict(self._low_state_sample_counts),
                    "ageMs": (
                        None if low_state_age is None else low_state_age * 1000.0
                    ),
                },
                "multipleState": {
                    "enabled": self.enable_multiple_state,
                    "received": self._multiple_state is not None,
                    "sampleCount": self._multiple_state_sample_count,
                    "ageMs": (
                        None
                        if multiple_state_age is None
                        else multiple_state_age * 1000.0
                    ),
                    "uwbSwitch": (
                        None
                        if self._multiple_state is None
                        else self._multiple_state.get("uwbSwitch")
                    ),
                },
                "commandCounts": dict(self._command_counts),
                "motionRpc": {
                    "inFlight": self._motion_request_in_flight,
                    "inFlightAgeMs": (
                        None
                        if self._motion_request_started_monotonic is None
                        else max(
                            0.0,
                            now - self._motion_request_started_monotonic,
                        )
                        * 1000.0
                    ),
                    "lastAckAt": self._last_motion_ack_at,
                    "lastAckCommand": self._last_motion_ack_command,
                    "lastAckLatencyMs": self._last_motion_ack_latency_ms,
                    "lastError": self._last_motion_ack_error,
                    "timeoutCount": self._motion_request_timeout_count,
                    "remoteStopState": self._remote_stop_state,
                    "stopRequiredAfterReconnect": self._stop_required_after_reconnect,
                },
                "microphone": {
                    "available": self._audio_channel_available,
                    "recording": self._microphone_recording,
                    "lastError": self._microphone_error,
                    "lastCapture": deepcopy(self._last_microphone_capture),
                },
                "videoEnabled": self.enable_video,
                "videoReady": frame_fresh,
                "videoHealthState": (
                    "disabled"
                    if not self.enable_video
                    else "offline"
                    if not self._connected
                    else "awaiting_first_raw_frame"
                    if self._video_watchdog_state == "AWAITING_FIRST_FRAME"
                    else "awaiting_first_encoded_frame"
                    if not first_encoded_produced
                    and self._video_watchdog_state == "HEALTHY"
                    else self._video_watchdog_state.lower()
                ),
                "videoDegradedReason": video_degraded_reason,
                "firstRawFrameReceived": first_raw_received,
                "firstEncodedFrameProduced": first_encoded_produced,
                "videoWatchdogArmed": first_raw_received,
                "videoWatchdogPolicy": (
                    "first_frame_15s_stale_3s_soft_8s_"
                    "zero_new_frames_6s_cooldown_15s"
                    if self.enable_video_active_recovery
                    else "degraded_only_keep_transport"
                ),
                "videoActiveRecoveryEnabled": (
                    self.enable_video_active_recovery
                ),
                "videoWatchdog": {
                    "state": self._video_watchdog_state,
                    "track_state": (
                        "ended_grace"
                        if self._video_track_end_pending_serial is not None
                        else "active"
                        if self._active_video_track_serial is not None
                        else "awaiting_track"
                    ),
                    "track_end_count": self._video_track_end_count,
                    "track_end_recovered_count": (
                        self._video_track_end_recovered_count
                    ),
                    "track_end_confirmed_count": (
                        self._video_track_end_confirmed_count
                    ),
                    "track_end_grace_remaining_ms": (
                        0.0
                        if self._video_track_end_started_monotonic is None
                        else max(
                            0.0,
                            self.video_track_end_grace_seconds
                            - (now - self._video_track_end_started_monotonic),
                        )
                        * 1000.0
                    ),
                    "video_stale_count": self._video_stale_count,
                    "soft_recovery_count": self._video_soft_recovery_count,
                    "soft_recovery_success_count": (
                        self._video_soft_recovery_success_count
                    ),
                    "full_reconnect_count": self._reconnect_count,
                    "video_watchdog_full_reconnect_count": (
                        self._video_full_reconnect_count
                    ),
                    "max_raw_frame_age_ms": (
                        self._video_max_raw_frame_age_seconds * 1000.0
                    ),
                    "max_recovery_duration_ms": (
                        self._video_max_recovery_duration_seconds * 1000.0
                    ),
                    "false_recovery_count": self._video_false_recovery_count,
                    "unrecovered_video_stale": int(video_recovering),
                    "recovery_frame_count": self._video_recovery_frame_count,
                    "frames_since_soft_recovery": max(
                        0,
                        self._raw_frame_count
                        - self._video_soft_recovery_start_raw_frame_count,
                    )
                    if self._video_soft_attempted
                    else 0,
                    "reconnect_cooldown_remaining_ms": (
                        0.0
                        if self._video_watchdog_cooldown_until_monotonic is None
                        else max(
                            0.0,
                            self._video_watchdog_cooldown_until_monotonic - now,
                        )
                        * 1000.0
                    ),
                    "recovery_max_gap_ms": (
                        self._video_recovery_max_gap_observed_seconds * 1000.0
                    ),
                    "thresholds": {
                        "degraded_seconds": self.frame_stale_seconds,
                        "track_end_grace_seconds": (
                            self.video_track_end_grace_seconds
                        ),
                        "first_frame_wait_seconds": (
                            self.video_first_frame_wait_seconds
                        ),
                        "soft_recovery_seconds": (
                            self.video_soft_recovery_seconds
                        ),
                        "soft_no_new_frame_seconds": (
                            self.video_soft_observe_seconds
                        ),
                        "soft_observe_seconds": self.video_soft_observe_seconds,
                        "reconnect_cooldown_seconds": (
                            self.video_reconnect_cooldown_seconds
                        ),
                        "stable_frames": self.video_recovery_min_frames,
                        "stable_duration_seconds": (
                            self.video_recovery_min_duration_seconds
                        ),
                        "max_frame_gap_seconds": (
                            self.video_recovery_max_gap_seconds
                        ),
                    },
                },
                "videoState": (
                    "disabled"
                    if not self.enable_video
                    else "offline"
                    if not self._connected
                    else "live"
                    if frame_fresh
                    else "awaiting-first-frame"
                    if (
                        self._latest_frame is None
                        or self._last_frame_generation != self._connection_generation
                    )
                    else "stalled"
                ),
                "frameAgeMs": None if frame_age is None else frame_age * 1000.0,
                "frameAgeSeconds": frame_age,
                "frameCount": self._frame_sequence,
                "rawFrameCount": self._raw_frame_count,
                "encodedFrameCount": self._encoded_frame_count,
                "lastRawFrameAt": (
                    self._last_raw_frame_iso
                    if self._last_raw_frame_generation == self._connection_generation
                    else None
                ),
                "lastEncodedFrameAt": (
                    self._last_encoded_frame_iso
                    if self._last_frame_generation == self._connection_generation
                    else None
                ),
                "rawFrameAgeSeconds": raw_frame_age,
                "encodedFrameAgeSeconds": encoded_frame_age,
                "encodeQueueDepth": self._raw_frames.qsize(),
                "droppedFrameCount": self._dropped_frame_count,
                "encodeDurationMsLast": self._encode_duration_ms_last,
                "encodeDurationMsMax": self._encode_duration_ms_max,
                "encodeDurationMsEwma": self._encode_duration_ms_ewma,
                "videoErrorCount": self._video_error_count,
                "videoClientCount": self._video_client_count,
                "lastVideoError": self._last_video_error,
                "latestFrame": (
                    None if self._latest_frame is None else self._latest_frame.metadata()
                ),
            }

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._lock:
            self._loop = loop
            self._connection_lost_event = asyncio.Event()
        self._loop_ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    async def _connection_supervisor(self) -> None:
        initial_connection = True
        consecutive_failures = 0
        try:
            while not self._stop.is_set() and not self._reconnect_blocked.is_set():
                reconnecting = not initial_connection
                with self._lock:
                    self._connection_state = (
                        "reconnecting" if reconnecting else "connecting"
                    )
                    if reconnecting:
                        self._reconnect_count += 1
                    reconnect_count = self._reconnect_count
                if reconnecting:
                    reconnect_delay = self._reconnect_delay_for_failure_streak(
                        consecutive_failures
                    )
                    with self._lock:
                        self._reconnect_failure_streak = consecutive_failures
                        self._next_reconnect_delay_seconds = reconnect_delay
                    LOGGER.info(
                        "WEBRTC_RECONNECT_SCHEDULED reconnect_count=%d "
                        "failure_streak=%d delay_seconds=%.3f",
                        reconnect_count,
                        consecutive_failures,
                        reconnect_delay,
                    )
                    if await self._sleep_or_stop(reconnect_delay):
                        break
                    with self._lock:
                        self._last_reconnect_delay_seconds = reconnect_delay
                        self._next_reconnect_delay_seconds = None
                try:
                    await self._connect_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._mark_connect_trace_failed(exc)
                    await self._cleanup_connection()
                    if initial_connection:
                        self._initial_connection_error = exc
                        self._initial_connection_done.set()
                        return
                    consecutive_failures += 1
                    with self._lock:
                        self._reconnect_failure_streak = consecutive_failures
                    LOGGER.warning(
                        "WEBRTC_RECONNECT_FAILED reconnect_count=%d error=%s: %s",
                        reconnect_count,
                        type(exc).__name__,
                        exc,
                    )
                    continue

                with self._lock:
                    self._reconnect_failure_streak = consecutive_failures
                    self._next_reconnect_delay_seconds = None
                if initial_connection:
                    initial_connection = False
                    self._initial_connection_done.set()
                else:
                    now = time.monotonic()
                    with self._lock:
                        self._last_reconnect_at = _now_iso()
                        downtime = (
                            0.0
                            if self._last_disconnect_monotonic is None
                            else max(0.0, now - self._last_disconnect_monotonic)
                        )
                        reconnect_count = self._reconnect_count
                    LOGGER.info(
                        "WEBRTC_RECONNECTED reconnect_count=%d downtime_seconds=%.3f",
                        reconnect_count,
                        downtime,
                    )

                stable_window_reached = await self._wait_for_connection_loss()
                if self._stop.is_set() or self._reconnect_blocked.is_set():
                    break
                if stable_window_reached:
                    consecutive_failures = 0
                elif reconnecting:
                    # A reconnect that drops again before the stability
                    # window is a failed recovery, not a healthy reset.
                    consecutive_failures += 1
                    with self._lock:
                        self._reconnect_failure_streak = consecutive_failures
                await self._cleanup_connection()
        finally:
            await self._cleanup_connection()
            if not self._initial_connection_done.is_set():
                self._initial_connection_error = RuntimeError(
                    "Wireless Runtime stopped before initial connection completed"
                )
                self._initial_connection_done.set()

    def _reconnect_delay_for_failure_streak(self, failure_streak: int) -> float:
        exponent = min(max(0, int(failure_streak)), 30)
        return min(
            self.reconnect_max_delay_seconds,
            self.reconnect_delay_seconds
            + ((2**exponent) - 1) * self.reconnect_backoff_step_seconds,
        )

    def _mark_connect_trace_failed(self, exc: Exception) -> None:
        with self._lock:
            trace = self._last_connect_trace
            generation = self._connection_generation
            if trace is None or trace.get("generation") != generation:
                return
            if trace.get("result") == "failed":
                return
            trace["result"] = "failed"
            trace["error"] = type(exc).__name__
            if self._connect_trace_started_monotonic is not None:
                trace["elapsedMs"] = max(
                    0.0,
                    (
                        time.monotonic()
                        - self._connect_trace_started_monotonic
                    )
                    * 1000.0,
                )
            connection = self._connection
            trace["sdk"] = deepcopy(
                getattr(connection, "connection_trace", None)
            )
            failed_trace = deepcopy(trace)
        failed_trace = self._record_connect_trace(failed_trace)
        LOGGER.warning(
            "WEBRTC_CONNECT_TRACE_FAILED %s",
            json.dumps(failed_trace, ensure_ascii=True, separators=(",", ":")),
        )

    def _record_connect_trace(
        self, trace: dict[str, Any]
    ) -> dict[str, Any]:
        """Store a sanitized per-generation trace and flag credential reuse."""

        recorded = deepcopy(trace)
        generation = recorded.get("generation")
        with self._lock:
            previous = next(
                (
                    item
                    for item in reversed(self._recent_connect_traces)
                    if item.get("generation") != generation
                ),
                None,
            )
            if previous is not None:
                for prefix, key in (
                    ("local", "localSdp"),
                    ("remote", "remoteSdp"),
                ):
                    current_identity = recorded.get(key) or {}
                    previous_identity = previous.get(key) or {}
                    current_ufrag = current_identity.get("iceUfrag")
                    current_pwd_hash = current_identity.get("icePwdHash")
                    comparable = bool(
                        current_ufrag
                        and current_pwd_hash
                        and previous_identity.get("iceUfrag")
                        and previous_identity.get("icePwdHash")
                    )
                    reused = bool(
                        comparable
                        and current_ufrag == previous_identity.get("iceUfrag")
                        and current_pwd_hash
                        == previous_identity.get("icePwdHash")
                    )
                    recorded[f"{prefix}IceCredentialsReused"] = (
                        reused if comparable else None
                    )
            else:
                recorded["localIceCredentialsReused"] = None
                recorded["remoteIceCredentialsReused"] = None

            traces = list(self._recent_connect_traces)
            replaced = False
            for index in range(len(traces) - 1, -1, -1):
                if traces[index].get("generation") == generation:
                    traces[index] = deepcopy(recorded)
                    replaced = True
                    break
            if not replaced:
                traces.append(deepcopy(recorded))
            self._recent_connect_traces = deque(traces[-20:], maxlen=20)
            self._last_connect_trace = deepcopy(recorded)
        return deepcopy(recorded)

    async def _activate_companion_inputs_async(
        self, *, enable_multiple_state: bool
    ) -> None:
        with self._lock:
            connection = self._connection
            rtc_topic = dict(self._rtc_topic)
            if connection is None or not self._connected:
                raise RuntimeError("WebRTC connection is unavailable")
            self.enable_sport_state = True
            self.enable_uwb = True
            self.enable_multiple_state = bool(enable_multiple_state)
            self._state_received_monotonic = None
            self._state_received_iso = None
            self._uwb_received_monotonic = None
            self._uwb_received_timestamp_ms = None
            self._uwb_received_iso = None
            self._multiple_state = None
            self._multiple_state_received_monotonic = None
            self._first_state.clear()
        self._subscribe_companion_topics(connection, rtc_topic)

    async def _deactivate_companion_inputs_async(self) -> None:
        with self._lock:
            connection = self._connection
            rtc_topic = dict(self._rtc_topic)
            topics: list[str] = []
            companion_topics = self._companion_topics(rtc_topic)
            if self.enable_sport_state:
                topics.extend(companion_topics[:2])
            if self.enable_uwb:
                topics.append(rtc_topic.get("UWB_STATE", "rt/uwbstate"))
            if self.enable_multiple_state:
                topics.append(
                    rtc_topic.get("MULTIPLE_STATE", "rt/multiplestate")
                )
            topics = list(dict.fromkeys(topics))
            self.enable_sport_state = False
            self.enable_uwb = False
            self.enable_multiple_state = False
        if connection is not None:
            pub_sub = connection.datachannel.pub_sub
            for topic in topics:
                try:
                    pub_sub.unsubscribe(topic)
                except Exception as exc:
                    LOGGER.debug("WebRTC unsubscribe failed topic=%s: %s", topic, exc)
                subscriptions = getattr(pub_sub, "subscriptions", None)
                if isinstance(subscriptions, dict):
                    subscriptions.pop(topic, None)
            with self._lock:
                if self._connection is connection:
                    self._subscribed_topics = [
                        topic for topic in self._subscribed_topics if topic not in topics
                    ]
        with self._lock:
            self._clear_companion_samples_locked()

    def _clear_companion_samples_locked(self) -> None:
        self._sport_state = None
        self._state_topic = None
        self._state_received_monotonic = None
        self._state_received_iso = None
        self._state_sample_counts = {}
        self._uwb_state = None
        self._uwb_source_keys = []
        self._uwb_topic = None
        self._uwb_received_monotonic = None
        self._uwb_received_timestamp_ms = None
        self._uwb_received_iso = None
        self._uwb_sample_counts = {}
        self._multiple_state = None
        self._multiple_state_received_monotonic = None
        self._multiple_state_sample_count = 0
        self._data_degraded_reason = None
        self._multi_signal_stale_since = None
        self._first_state.clear()

    async def _activate_voice_async(self) -> None:
        with self._lock:
            connection = self._connection
            generation = self._connection_generation
            if connection is None or not self._connected:
                raise RuntimeError("WebRTC connection is unavailable")
            self.enable_audio = True
        self._attach_audio_callback(connection, generation)

    async def _deactivate_voice_async(self) -> None:
        with self._lock:
            connection = self._connection
            self.enable_audio = False
            self._audio_channel_available = False
            self._microphone_recording = False
        audio = None if connection is None else getattr(connection, "audio", None)
        if audio is not None and hasattr(audio, "switchAudioChannel"):
            audio.switchAudioChannel(False)

    @staticmethod
    def _companion_topics(rtc_topic: dict[str, str]) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                (
                    rtc_topic.get("LF_SPORT_MOD_STATE", "rt/lf/sportmodestate"),
                    rtc_topic.get("SPORT_MOD_STATE", "rt/sportmodestate"),
                    rtc_topic.get("UWB_STATE", "rt/uwbstate"),
                    rtc_topic.get("MULTIPLE_STATE", "rt/multiplestate"),
                )
            )
        )

    def _subscribe_companion_topics(
        self, connection: Any, rtc_topic: dict[str, str]
    ) -> None:
        subscriptions: list[tuple[str, Callable[[Any], None]]] = []
        if self.enable_sport_state:
            for topic in self._companion_topics(rtc_topic)[:2]:
                subscriptions.append((topic, self._state_callback(topic, connection)))
        if self.enable_uwb:
            topic = rtc_topic.get("UWB_STATE", "rt/uwbstate")
            subscriptions.append((topic, self._uwb_callback(topic, connection)))
        if self.enable_multiple_state:
            topic = rtc_topic.get("MULTIPLE_STATE", "rt/multiplestate")
            subscriptions.append((topic, self._multiple_state_callback_for(connection)))
        for topic, callback in subscriptions:
            connection.datachannel.pub_sub.subscribe(topic, callback)
        with self._lock:
            if self._connection is connection:
                self._subscribed_topics = list(
                    dict.fromkeys((*self._subscribed_topics, *(item[0] for item in subscriptions)))
                )

    def _attach_audio_callback(self, connection: Any, generation: int) -> None:
        audio = getattr(connection, "audio", None)
        if audio is None or not hasattr(audio, "add_track_callback"):
            return
        with self._lock:
            already_attached = self._audio_callback_generation == generation
        if not already_attached:
            async def receive_audio(frame: Any) -> None:
                async def handle_audio() -> None:
                    if self._is_current_connection(connection) and self.enable_audio:
                        await self._receive_audio_frame(frame)

                await self._run_connection_task(generation, handle_audio())

            audio.add_track_callback(receive_audio)
            with self._lock:
                if self._connection is connection:
                    self._audio_callback_generation = generation
        with self._lock:
            if self._connection is connection and self.enable_audio:
                self._audio_channel_available = True

    async def _connect_once(self) -> None:
        connect_trace_started = time.monotonic()
        with self._lock:
            last_peer_closed_monotonic = self._last_peer_closed_monotonic
            self._connect_trace_started_monotonic = connect_trace_started
        time_since_old_peer_closed_ms = (
            None
            if last_peer_closed_monotonic is None
            else max(
                0.0,
                (connect_trace_started - last_peer_closed_monotonic) * 1000.0,
            )
        )
        connection, rtc_topic, sport_cmd = self._connection_factory(
            self.robot_ip, self.aes_key
        )
        with self._lock:
            self._connection_generation += 1
            generation = self._connection_generation
            try:
                setattr(connection, "diagnostic_generation", generation)
            except Exception:
                pass
            self._connection = connection
            self._rtc_topic = dict(rtc_topic)
            self._sport_cmd = dict(sport_cmd)
            self._connected = False
            self._data_channel_ready = False
            self._motion_ready = False
            self._video_degraded_reason = None
            self._data_degraded_reason = None
            self._multi_signal_stale_since = None
            self._peer_connection_state = "new"
            self._ice_connection_state = "new"
            self._connected_since = None
            self._connected_monotonic = None
            self._audio_channel_available = False
            self._audio_callback_generation = 0
            self._audio_hub = None
            # AudioHub records live on the robot. Keep UUIDs across transport
            # generations so reconnect does not trigger catalogue/upload work.
            self._subscribed_topics = []
            self._last_raw_frame_monotonic = None
            self._last_raw_frame_iso = None
            self._last_raw_frame_generation = 0
            self._last_frame_monotonic = None
            self._last_encoded_frame_iso = None
            self._last_frame_generation = 0
            self._last_encoded_monotonic = 0.0
            self._encode_duration_ms_last = None
            self._encode_duration_ms_max = None
            self._encode_duration_ms_ewma = None
            self._video_started_monotonic = time.monotonic()
            self._video_session_frame_count = 0
            self._motion_ready = False
            self._video_recovery_candidate_started_monotonic = None
            self._video_recovery_last_frame_monotonic = None
            self._video_recovery_frame_count = 0
            self._video_recovery_max_gap_observed_seconds = 0.0
            self._video_soft_toggle_off_monotonic = None
            self._video_soft_toggle_on_monotonic = None
            self._video_soft_recovery_start_raw_frame_count = (
                self._raw_frame_count
            )
            self._video_channel_enabled_monotonic = None
            self._transport_disconnected_since = None
            self._transport_disconnect_reason = None
            self._transport_grace_recovery_in_progress = False
            self._active_video_track_serial = None
            self._video_track_end_pending_serial = None
            self._video_track_end_started_monotonic = None
            self._video_track_end_reason = None
            self._reset_connection_samples_locked()
        self._first_state.clear()
        self._clear_raw_frames()
        if self._connection_lost_event is not None:
            self._connection_lost_event.clear()

        LOGGER.info(
            "WEBRTC_CONNECT_TRACE_START generation=%d "
            "time_since_old_peer_closed_ms=%s",
            generation,
            "none"
            if time_since_old_peer_closed_ms is None
            else f"{time_since_old_peer_closed_ms:.3f}",
        )
        try:
            await asyncio.wait_for(
                connection.connect(), timeout=self.connect_timeout_seconds
            )
        except BaseException as exc:
            sdk_trace = deepcopy(getattr(connection, "connection_trace", None))
            pc = getattr(connection, "pc", None)
            failed_trace = {
                "processId": os.getpid(),
                "generation": generation,
                "peerId": None if pc is None else f"0x{id(pc):x}",
                "result": "failed",
                "error": type(exc).__name__,
                "elapsedMs": max(
                    0.0, (time.monotonic() - connect_trace_started) * 1000.0
                ),
                "timeSinceOldPeerClosedMs": time_since_old_peer_closed_ms,
                "sdk": sdk_trace,
                "localSdp": _sdp_identity(
                    getattr(pc, "localDescription", None)
                ),
                "remoteSdp": _sdp_identity(
                    getattr(pc, "remoteDescription", None)
                ),
                "selectedPairs": _selected_candidate_pairs(pc),
            }
            failed_trace = self._record_connect_trace(failed_trace)
            LOGGER.warning(
                "WEBRTC_CONNECT_TRACE_FAILED %s",
                json.dumps(failed_trace, ensure_ascii=True, separators=(",", ":")),
            )
            raise
        transport_connected_monotonic = time.monotonic()
        self._attach_connection_listeners(connection)
        peer_state, ice_state = self._read_transport_states(connection)
        if peer_state in {"closed", "failed", "disconnected"}:
            raise RuntimeError(f"PeerConnection became {peer_state} during connect")
        if ice_state in {"closed", "failed", "disconnected"}:
            raise RuntimeError(f"ICE connection became {ice_state} during connect")
        datachannel = getattr(connection, "datachannel", None)
        data_ready = bool(getattr(datachannel, "data_channel_opened", True))
        pc = getattr(connection, "pc", None)
        completed_trace = {
            "processId": os.getpid(),
            "generation": generation,
            "peerId": None if pc is None else f"0x{id(pc):x}",
            "result": "transport_connected",
            "error": None,
            "elapsedMs": max(
                0.0,
                (transport_connected_monotonic - connect_trace_started) * 1000.0,
            ),
            "transportConnectMs": max(
                0.0,
                (transport_connected_monotonic - connect_trace_started) * 1000.0,
            ),
            "timeSinceOldPeerClosedMs": time_since_old_peer_closed_ms,
            "sdk": deepcopy(getattr(connection, "connection_trace", None)),
            "localSdp": _sdp_identity(getattr(pc, "localDescription", None)),
            "remoteSdp": _sdp_identity(getattr(pc, "remoteDescription", None)),
            "selectedPairs": _selected_candidate_pairs(pc),
        }
        completed_trace = self._record_connect_trace(completed_trace)
        LOGGER.info(
            "WEBRTC_TRANSPORT_TRACE_DONE %s",
            json.dumps(completed_trace, ensure_ascii=True, separators=(",", ":")),
        )
        with self._lock:
            stop_required_after_reconnect = self._stop_required_after_reconnect
            if self._connection is not connection:
                raise RuntimeError("WebRTC connection was superseded during connect")
            self._connected = True
            self._data_channel_ready = data_ready
            self._motion_ready = not stop_required_after_reconnect
            self._connection_state = "connected"
            self._peer_connection_state = peer_state
            self._ice_connection_state = ice_state
            self._connected_since = _now_iso()
            self._connected_monotonic = time.monotonic()
            self._connection_count += 1
            self._video_watchdog_state = (
                "RECOVERING"
                if self.enable_video
                and self._video_recovery_required_on_next_connection
                else "AWAITING_FIRST_FRAME"
                if self.enable_video
                else "DISABLED"
            )
            if self._video_watchdog_state == "RECOVERING":
                self._video_degraded_reason = "recovering_after_reconnect"
                self._video_watchdog_cooldown_until_monotonic = (
                    self._connected_monotonic
                    + self.video_reconnect_cooldown_seconds
                )
            else:
                self._video_watchdog_cooldown_until_monotonic = None

        # Restore the media plane as soon as the validated DataChannel is up.
        # Motion remains locked independently until the reconnect StopMove ACK.
        if self.enable_video:
            async def receive_video(track: Any) -> None:
                track_serial = self._register_video_track(connection)
                if track_serial is None:
                    return
                await self._run_connection_task(
                    generation,
                    self._receive_video(
                        track,
                        connection,
                        track_serial=track_serial,
                    ),
                )

            connection.video.add_track_callback(receive_video)
            connection.video.switchVideoChannel(True)
            with self._lock:
                if self._connection is connection and self._connected:
                    self._video_channel_enabled_monotonic = time.monotonic()

        if stop_required_after_reconnect:
            await self._confirm_stop_after_reconnect(
                connection,
                generation=generation,
                rtc_topic=rtc_topic,
                sport_cmd=sport_cmd,
            )
            with self._lock:
                if self._connection is connection and self._connected:
                    self._motion_ready = True

        self._subscribe_companion_topics(connection, rtc_topic)
        if self.enable_low_state:
            low_topics = tuple(
                dict.fromkeys(
                    (
                        rtc_topic.get("LOW_STATE", "rt/lf/lowstate"),
                        "rt/lowstate",
                    )
                )
            )
            for topic in low_topics:
                connection.datachannel.pub_sub.subscribe(
                    topic, self._low_state_callback(topic, connection)
                )
            with self._lock:
                if self._connection is connection:
                    self._subscribed_topics = list(
                        dict.fromkeys((*self._subscribed_topics, *low_topics))
                    )
        if self.enable_audio:
            self._attach_audio_callback(connection, generation)
        else:
            audio = getattr(connection, "audio", None)
            if audio is not None and hasattr(audio, "switchAudioChannel"):
                audio.switchAudioChannel(False)
        if self.enable_sport_state:
            deadline = time.monotonic() + self.state_timeout_seconds
            while not self._first_state.is_set():
                if (
                    self._connection_lost_event is not None
                    and self._connection_lost_event.is_set()
                ):
                    raise RuntimeError("WebRTC connection lost before first state sample")
                if time.monotonic() >= deadline:
                    raise asyncio.TimeoutError("SportModeState first sample timed out")
                await asyncio.sleep(0.02)
        with self._lock:
            if generation != self._connection_generation or not self._connected:
                raise RuntimeError("WebRTC connection was lost during initialization")
            ready_monotonic = time.monotonic()
            validation_ms = max(
                0.0,
                (ready_monotonic - transport_connected_monotonic) * 1000.0,
            )
            current_trace = deepcopy(self._last_connect_trace)
            if current_trace is not None and current_trace.get("generation") == generation:
                current_trace["result"] = "ready"
                current_trace["validationMs"] = validation_ms
                current_trace["elapsedMs"] = max(
                    0.0, (ready_monotonic - connect_trace_started) * 1000.0
                )
            else:
                current_trace = None
        ready_trace = (
            None
            if current_trace is None
            else self._record_connect_trace(current_trace)
        )
        LOGGER.info(
            "WEBRTC_CONNECT_VALIDATION_DONE generation=%d duration_ms=%.3f",
            generation,
            validation_ms,
        )
        if ready_trace is not None:
            LOGGER.info(
                "WEBRTC_CONNECT_TRACE_DONE %s",
                json.dumps(ready_trace, ensure_ascii=True, separators=(",", ":")),
            )

    async def _confirm_stop_after_reconnect(
        self,
        connection: Any,
        *,
        generation: int,
        rtc_topic: dict[str, str],
        sport_cmd: dict[str, int],
        success_state: str = "STOP_CONFIRMED_AFTER_RECONNECT",
        success_log: str = "STOP_CONFIRMED_AFTER_RECONNECT",
    ) -> None:
        """Require a real StopMove ACK before exposing a reconnected transport."""

        topic = rtc_topic.get("SPORT_MOD")
        api_id = sport_cmd.get("StopMove")
        if topic is None or api_id is None:
            raise self._command_error(
                "Post-reconnect StopMove API metadata is unavailable"
            )
        request_started = time.monotonic()
        with self._lock:
            self._motion_request_in_flight = "StopMove"
            self._motion_request_started_monotonic = request_started
            self._last_motion_ack_error = None
        try:
            response = await asyncio.wait_for(
                connection.datachannel.pub_sub.publish_request_new(
                    topic, {"api_id": api_id}
                ),
                timeout=self.command_timeout_seconds,
            )
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}".rstrip()
            with self._lock:
                self._last_motion_ack_error = error_text
                if isinstance(exc, TimeoutError) or type(exc).__name__ == "TimeoutError":
                    self._motion_request_timeout_count += 1
                self._remote_stop_state = "STOP_UNCONFIRMED_TRANSPORT_LOST"
                self._stop_required_after_reconnect = True
            LOGGER.warning(
                "POST_RECONNECT_STOP_FAILED error=%s",
                error_text,
            )
            raise self._command_error(
                f"Post-reconnect StopMove request failed: {error_text}"
            ) from exc
        finally:
            with self._lock:
                self._motion_request_in_flight = None
                self._motion_request_started_monotonic = None

        latency_ms = (time.monotonic() - request_started) * 1000.0
        code = _api_status_code(response)
        if code != 0:
            error_text = f"WebRTC status={code}"
            with self._lock:
                self._last_motion_ack_error = error_text
                self._remote_stop_state = "STOP_UNCONFIRMED_TRANSPORT_LOST"
                self._stop_required_after_reconnect = True
            raise self._command_error(
                f"Post-reconnect StopMove was not acknowledged; {error_text}"
            )
        with self._lock:
            if (
                self._connection is not connection
                or self._connection_generation != generation
                or self._lost_generation == generation
            ):
                self._remote_stop_state = "STOP_UNCONFIRMED_TRANSPORT_LOST"
                self._stop_required_after_reconnect = True
                raise self._command_error(
                    "Connection was lost while confirming post-reconnect StopMove"
                )
            self._command_counts["StopMove"] = (
                self._command_counts.get("StopMove", 0) + 1
            )
            self._last_motion_ack_at = _now_iso()
            self._last_motion_ack_command = "StopMove"
            self._last_motion_ack_latency_ms = latency_ms
            self._last_motion_ack_error = None
            self._remote_stop_state = success_state
            self._stop_required_after_reconnect = False
        LOGGER.info(success_log)

    async def _cleanup_connection(self) -> None:
        with self._lock:
            connection = self._connection
            generation = self._connection_generation
            connected = self._connected
            self._connection = None
            self._connected = False
            self._data_channel_ready = False
            self._motion_ready = False
            self._audio_channel_available = False
            self._connected_since = None
            self._connected_monotonic = None
            if self._stop.is_set():
                self._connection_state = "disconnected"
        if connection is not None:
            pc = getattr(connection, "pc", None)
            peer_id = None if pc is None else f"0x{id(pc):x}"
            peer_state_before, ice_state_before = self._read_transport_states(
                connection
            )
            datachannel = getattr(connection, "datachannel", None)
            data_open_before = getattr(
                datachannel, "data_channel_opened", None
            )
            close_started = time.monotonic()
            LOGGER.info(
                "WEBRTC_PEER_CLOSE_BEGIN generation=%d peer_id=%s "
                "peer_state=%s ice_state=%s datachannel_open=%s",
                generation,
                peer_id,
                peer_state_before,
                ice_state_before,
                data_open_before,
            )
            self._quiesce_connection_helpers(connection)
            await self._cancel_sdk_network_status_tasks(connection)
            await self._cancel_connection_tasks(generation)
            close_error: Exception | None = None
            try:
                await connection.disconnect()
            except Exception as exc:
                close_error = exc
                LOGGER.debug("WebRTC cleanup failed: %s", exc)
            finally:
                closed_monotonic = time.monotonic()
                close_duration_ms = max(
                    0.0, (closed_monotonic - close_started) * 1000.0
                )
                peer_state_after = str(
                    getattr(pc, "connectionState", "unavailable")
                )
                ice_state_after = str(
                    getattr(pc, "iceConnectionState", "unavailable")
                )
                data_open_after = getattr(
                    datachannel, "data_channel_opened", None
                )
                with self._lock:
                    self._last_peer_closed_at = _now_iso()
                    self._last_peer_closed_monotonic = closed_monotonic
                    self._last_peer_close_duration_ms = close_duration_ms
                LOGGER.info(
                    "WEBRTC_PEER_CLOSE_END generation=%d peer_id=%s "
                    "duration_ms=%.3f peer_state=%s ice_state=%s "
                    "datachannel_open=%s error=%s",
                    generation,
                    peer_id,
                    close_duration_ms,
                    peer_state_after,
                    ice_state_after,
                    data_open_after,
                    "none" if close_error is None else type(close_error).__name__,
                )
        if connected:
            with self._lock:
                self._disconnect_count += 1
        self._clear_raw_frames()

    def _enter_transport_disconnect_grace(
        self, reason: str, connection: Any
    ) -> None:
        now = time.monotonic()
        entered = False
        with self._lock:
            if self._connection is not connection or not self._connected:
                return
            if self._transport_disconnected_since is None:
                self._transport_disconnected_since = now
                self._transport_disconnect_reason = str(reason)
                entered = True
            self._connection_state = "transport_grace"
            self._motion_ready = False
            self._remote_stop_state = "STOP_UNCONFIRMED_TRANSPORT_LOST"
            self._stop_required_after_reconnect = True
        if entered:
            LOGGER.warning(
                "TRANSPORT_DISCONNECT_GRACE_STARTED reason=%s "
                "grace_seconds=%.3f motion_ready=false",
                reason,
                self.disconnect_grace_seconds,
            )
            LOGGER.warning("STOP_UNCONFIRMED_TRANSPORT_LOST")

    def _schedule_transport_grace_recovery(self, connection: Any) -> None:
        with self._lock:
            if (
                self._connection is not connection
                or not self._connected
                or self._transport_disconnected_since is None
                or self._transport_grace_recovery_in_progress
            ):
                return
            self._transport_grace_recovery_in_progress = True
            generation = self._connection_generation
        asyncio.create_task(
            self._run_connection_task(
                generation,
                self._recover_transport_disconnect_grace(
                    connection, generation=generation
                ),
            )
        )

    async def _recover_transport_disconnect_grace(
        self, connection: Any, *, generation: int
    ) -> None:
        try:
            with self._lock:
                rtc_topic = dict(self._rtc_topic)
                sport_cmd = dict(self._sport_cmd)
                grace_started = self._transport_disconnected_since
                grace_reason = self._transport_disconnect_reason
            await self._confirm_stop_after_reconnect(
                connection,
                generation=generation,
                rtc_topic=rtc_topic,
                sport_cmd=sport_cmd,
                success_state="STOP_CONFIRMED_AFTER_TRANSPORT_GRACE",
                success_log="STOP_CONFIRMED_AFTER_TRANSPORT_GRACE",
            )
            peer_state, ice_state = self._read_transport_states(connection)
            datachannel = getattr(connection, "datachannel", None)
            data_open = getattr(
                datachannel, "data_channel_opened", True
            ) is not False
            healthy = bool(
                peer_state == "connected"
                and ice_state in {"connected", "completed"}
                and data_open
            )
            with self._lock:
                if (
                    self._connection is not connection
                    or self._connection_generation != generation
                    or not self._connected
                ):
                    return
                if not healthy:
                    self._motion_ready = False
                    self._remote_stop_state = (
                        "STOP_UNCONFIRMED_TRANSPORT_LOST"
                    )
                    self._stop_required_after_reconnect = True
                    return
                self._transport_disconnected_since = None
                self._transport_disconnect_reason = None
                self._connection_state = "connected"
                self._motion_ready = True
            LOGGER.info(
                "TRANSPORT_DISCONNECT_GRACE_RECOVERED reason=%s "
                "grace_duration_seconds=%.3f motion_ready=true",
                grace_reason,
                0.0
                if grace_started is None
                else max(0.0, time.monotonic() - grace_started),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.warning(
                "TRANSPORT_DISCONNECT_GRACE_STOP_FAILED error=%s: %s",
                type(exc).__name__,
                exc,
            )
        finally:
            with self._lock:
                if self._connection is connection:
                    self._transport_grace_recovery_in_progress = False

    def _attach_connection_listeners(self, connection: Any) -> None:
        pc = getattr(connection, "pc", None)
        if pc is not None and hasattr(pc, "on"):
            def on_peer_state_change() -> None:
                peer_state = str(getattr(pc, "connectionState", "unknown"))
                with self._lock:
                    if self._connection is connection:
                        self._peer_connection_state = peer_state
                if peer_state in {"closed", "failed", "disconnected"}:
                    reason = f"peer_connection_{peer_state}"
                    if peer_state == "disconnected":
                        self._enter_transport_disconnect_grace(
                            reason, connection
                        )
                    else:
                        self._handle_connection_lost(reason, connection)

            def on_ice_state_change() -> None:
                ice_state = str(getattr(pc, "iceConnectionState", "unknown"))
                with self._lock:
                    if self._connection is connection:
                        self._ice_connection_state = ice_state
                if ice_state in {"closed", "failed", "disconnected"}:
                    reason = f"ice_connection_{ice_state}"
                    if ice_state == "disconnected":
                        self._enter_transport_disconnect_grace(
                            reason, connection
                        )
                    else:
                        self._handle_connection_lost(reason, connection)

            pc.on("connectionstatechange", on_peer_state_change)
            pc.on("iceconnectionstatechange", on_ice_state_change)

        datachannel = getattr(connection, "datachannel", None)
        channel = getattr(datachannel, "channel", None)
        if channel is not None and hasattr(channel, "on"):
            def on_datachannel_close() -> None:
                self._handle_connection_lost("data_channel_closed", connection)

            channel.on("close", on_datachannel_close)

    def _handle_connection_lost(
        self,
        reason: str,
        connection: Any,
        *,
        diagnostic_reason: str | None = None,
    ) -> bool:
        now = time.monotonic()
        observed_peer_state, observed_ice_state = self._read_transport_states(
            connection
        )
        with self._lock:
            if self._connection is not connection:
                return False
            generation = self._connection_generation
            if self._lost_generation == generation:
                return False
            self._lost_generation = generation
            timestamp = _now_iso()
            raw_frame_age = (
                None
                if self._last_raw_frame_monotonic is None
                or self._last_raw_frame_generation != generation
                else max(0.0, now - self._last_raw_frame_monotonic)
            )
            encoded_frame_age = (
                None
                if self._last_frame_monotonic is None
                or self._last_frame_generation != generation
                else max(0.0, now - self._last_frame_monotonic)
            )
            sport_state_age = (
                None
                if self._state_received_monotonic is None
                else max(0.0, now - self._state_received_monotonic)
            )
            connection_age = (
                None
                if self._connected_monotonic is None
                else max(0.0, now - self._connected_monotonic)
            )
            peer_state = observed_peer_state
            ice_state = observed_ice_state
            self._peer_connection_state = peer_state
            self._ice_connection_state = ice_state
            datachannel = getattr(connection, "datachannel", None)
            channel_opened = getattr(datachannel, "data_channel_opened", None)
            data_channel_ready = bool(
                self._data_channel_ready and channel_opened is not False
            )
            resolved_diagnostic_reason = str(diagnostic_reason or reason)
            reconnect_count = self._reconnect_count
            snapshot = {
                "timestamp": timestamp,
                "reason": str(reason),
                "diagnosticReason": resolved_diagnostic_reason,
                "peerState": peer_state,
                "iceState": ice_state,
                "dataChannelReady": data_channel_ready,
                "connectionAgeSeconds": connection_age,
                "rawFrameAgeSeconds": raw_frame_age,
                "encodedFrameAgeSeconds": encoded_frame_age,
                "sportStateAgeSeconds": sport_state_age,
                "rawFrameCount": self._raw_frame_count,
                "encodedFrameCount": self._encoded_frame_count,
                "encodeQueueDepth": self._raw_frames.qsize(),
                "droppedFrameCount": self._dropped_frame_count,
                "encodeDurationMsLast": self._encode_duration_ms_last,
                "encodeDurationMsMax": self._encode_duration_ms_max,
                "encodeDurationMsEwma": self._encode_duration_ms_ewma,
                "reconnectCount": reconnect_count,
            }
            self._recent_disconnects.append(snapshot)
            if self._connected:
                self._disconnect_count += 1
            self._connected = False
            self._data_channel_ready = False
            self._motion_ready = False
            self._audio_channel_available = False
            self._connection_state = "disconnected"
            self._last_disconnect_at = timestamp
            self._last_disconnect_reason = str(reason)
            self._last_diagnostic_reason = resolved_diagnostic_reason
            self._last_disconnect_monotonic = now
            self._transport_disconnected_since = None
            self._transport_disconnect_reason = None
            self._transport_grace_recovery_in_progress = False
            self._remote_stop_state = "STOP_UNCONFIRMED_TRANSPORT_LOST"
            self._stop_required_after_reconnect = True
            if self.enable_video:
                if self._video_recovery_started_monotonic is None:
                    self._video_recovery_started_monotonic = now
                self._video_recovery_required_on_next_connection = True
                self._video_watchdog_state = "OFFLINE"
                self._video_degraded_reason = resolved_diagnostic_reason
                self._video_soft_attempted = False
        LOGGER.warning("STOP_UNCONFIRMED_TRANSPORT_LOST")
        if resolved_diagnostic_reason == "encoded_frame_stale":
            LOGGER.warning(
                "VIDEO_WATCHDOG_TRIGGERED reason=%s peer=%s ice=%s "
                "data_channel_ready=%s raw_age=%s encoded_age=%s sport_age=%s "
                "queue_depth=%d encode_ms=%s reconnect_count=%d",
                resolved_diagnostic_reason,
                peer_state,
                ice_state,
                data_channel_ready,
                self._diagnostic_number(raw_frame_age),
                self._diagnostic_number(encoded_frame_age),
                self._diagnostic_number(sport_state_age),
                snapshot["encodeQueueDepth"],
                self._diagnostic_number(snapshot["encodeDurationMsLast"]),
                reconnect_count,
            )
        else:
            LOGGER.warning(
                "WEBRTC_CONNECTION_LOST reason=%s diagnostic_reason=%s "
                "peer_state=%s ice_state=%s raw_age=%s encoded_age=%s "
                "sport_age=%s reconnect_count=%d",
                reason,
                resolved_diagnostic_reason,
                peer_state,
                ice_state,
                self._diagnostic_number(raw_frame_age),
                self._diagnostic_number(encoded_frame_age),
                self._diagnostic_number(sport_state_age),
                reconnect_count,
            )
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._wake_supervisor)
        return True

    def _wake_supervisor(self) -> None:
        if self._connection_lost_event is not None:
            self._connection_lost_event.set()

    async def _wait_for_connection_loss(self) -> bool:
        stable_window_reached = False
        while not self._stop.is_set():
            event = self._connection_lost_event
            if event is None:
                return stable_window_reached
            try:
                await asyncio.wait_for(event.wait(), timeout=0.25)
                return stable_window_reached
            except asyncio.TimeoutError:
                self._poll_connection_health()
                if stable_window_reached:
                    continue
                with self._lock:
                    connected_at = self._connected_monotonic
                    still_connected = self._connected
                    previous_streak = self._reconnect_failure_streak
                if (
                    still_connected
                    and connected_at is not None
                    and time.monotonic() - connected_at
                    >= self.reconnect_stable_reset_seconds
                ):
                    stable_window_reached = True
                    with self._lock:
                        self._reconnect_failure_streak = 0
                    if previous_streak > 0:
                        LOGGER.info(
                            "WEBRTC_BACKOFF_RESET stable_seconds=%.3f "
                            "previous_failure_streak=%d",
                            self.reconnect_stable_reset_seconds,
                            previous_streak,
                        )
        return stable_window_reached

    def _poll_connection_health(self) -> None:
        with self._lock:
            connection = self._connection
            connected = self._connected
            state_at = self._state_received_monotonic
            raw_frame_at = self._last_raw_frame_monotonic
            raw_frame_generation = self._last_raw_frame_generation
            encoded_frame_at = self._last_frame_monotonic
            encoded_frame_generation = self._last_frame_generation
            generation = self._connection_generation
            sport_enabled = self.enable_sport_state
            video_channel_enabled_at = self._video_channel_enabled_monotonic
            video_track_end_pending = (
                self._video_track_end_pending_serial is not None
            )
        if connection is None or not connected:
            return
        peer_state, ice_state = self._read_transport_states(connection)
        with self._lock:
            if self._connection is connection:
                self._peer_connection_state = peer_state
                self._ice_connection_state = ice_state
        if peer_state in {"closed", "failed"}:
            self._handle_connection_lost(f"peer_connection_{peer_state}", connection)
            return
        if ice_state in {"closed", "failed"}:
            self._handle_connection_lost(f"ice_connection_{ice_state}", connection)
            return
        datachannel = getattr(connection, "datachannel", None)
        if getattr(datachannel, "data_channel_opened", True) is False:
            self._handle_connection_lost("data_channel_closed", connection)
            return
        disconnected_reason = (
            "peer_connection_disconnected"
            if peer_state == "disconnected"
            else "ice_connection_disconnected"
            if ice_state == "disconnected"
            else None
        )
        if disconnected_reason is not None:
            self._enter_transport_disconnect_grace(
                disconnected_reason, connection
            )
            with self._lock:
                grace_started = self._transport_disconnected_since
                grace_reason = self._transport_disconnect_reason
            if (
                grace_started is not None
                and time.monotonic() - grace_started
                >= self.disconnect_grace_seconds
            ):
                self._handle_connection_lost(
                    f"{grace_reason or disconnected_reason}_grace_expired",
                    connection,
                    diagnostic_reason=(
                        grace_reason or disconnected_reason
                    ),
                )
            return
        with self._lock:
            grace_active = self._transport_disconnected_since is not None
        if grace_active:
            self._schedule_transport_grace_recovery(connection)
            return
        now = time.monotonic()
        sport_age = None if state_at is None else max(0.0, now - state_at)
        raw_age = (
            None
            if raw_frame_at is None or raw_frame_generation != generation
            else max(0.0, now - raw_frame_at)
        )
        encoded_age = (
            None
            if encoded_frame_at is None or encoded_frame_generation != generation
            else max(0.0, now - encoded_frame_at)
        )
        sport_stale = bool(
            sport_enabled
            and sport_age is not None
            and sport_age > self.state_stale_seconds
        )
        raw_stale = bool(
            self.enable_video
            and raw_age is not None
            and raw_age > self.frame_stale_seconds
        )
        encoded_stale = bool(
            self.enable_video
            and encoded_age is not None
            and encoded_age > self.frame_stale_seconds
        )
        first_frame_timeout = bool(
            self.enable_video
            and raw_age is None
            and video_channel_enabled_at is not None
            and now - video_channel_enabled_at
            >= self.video_first_frame_wait_seconds
        )
        video_reason = (
            "video_track_ended_pending"
            if video_track_end_pending
            else "raw_frame_stale"
            if raw_stale
            else "encoded_frame_stale"
            if encoded_stale
            else "first_raw_frame_timeout"
            if first_frame_timeout
            else None
        )
        data_reason = "sport_state_stale" if sport_stale else None
        if self.enable_video_active_recovery:
            self._advance_video_watchdog(
                connection,
                now=now,
                raw_stale=raw_stale,
                raw_age=raw_age,
                encoded_age=encoded_age,
                sport_age=sport_age,
            )
        self._update_degraded_signals(
            connection,
            video_reason=(
                None if self.enable_video_active_recovery else video_reason
            ),
            data_reason=data_reason,
            raw_age=raw_age,
            encoded_age=encoded_age,
            sport_age=sport_age,
        )

        should_reconnect = False
        if self.reconnect_on_multi_signal_stale and self.enable_video:
            with self._lock:
                if self._connection is not connection or not self._connected:
                    return
                if raw_stale and sport_stale:
                    if self._multi_signal_stale_since is None:
                        self._multi_signal_stale_since = now
                    should_reconnect = (
                        now - self._multi_signal_stale_since
                        >= self.multi_signal_stale_grace_seconds
                    )
                else:
                    self._multi_signal_stale_since = None
        else:
            with self._lock:
                if self._connection is connection:
                    if raw_stale and sport_stale:
                        if self._multi_signal_stale_since is None:
                            self._multi_signal_stale_since = now
                    else:
                        self._multi_signal_stale_since = None

        if should_reconnect:
            self._handle_connection_lost(
                "transport_health_stale",
                connection,
                diagnostic_reason="raw_and_sport_state_stale",
            )

    def _advance_video_watchdog(
        self,
        connection: Any,
        *,
        now: float,
        raw_stale: bool,
        raw_age: float | None,
        encoded_age: float | None,
        sport_age: float | None,
    ) -> None:
        """Run L1 video toggle recovery, then escalate to an L2 reconnect."""

        if not self.enable_video_active_recovery:
            return

        toggle: bool | None = None
        start_soft_log = False
        soft_trigger: str | None = None
        soft_trigger_age = 0.0
        full_reconnect = False
        full_reconnect_accounted = False
        degraded_log = False
        false_recovery = False
        with self._lock:
            if (
                self._connection is not connection
                or not self._connected
                or not self.enable_video
            ):
                return
            if raw_age is not None:
                self._video_max_raw_frame_age_seconds = max(
                    self._video_max_raw_frame_age_seconds,
                    raw_age,
                )
            state = self._video_watchdog_state
            has_current_raw = bool(
                self._last_raw_frame_monotonic is not None
                and self._last_raw_frame_generation
                == self._connection_generation
            )
            first_frame_age = (
                None
                if has_current_raw
                or self._video_channel_enabled_monotonic is None
                else max(0.0, now - self._video_channel_enabled_monotonic)
            )
            if first_frame_age is not None:
                self._video_max_raw_frame_age_seconds = max(
                    self._video_max_raw_frame_age_seconds,
                    first_frame_age,
                )
            if (
                state == "AWAITING_FIRST_FRAME"
                and first_frame_age is not None
                and first_frame_age >= self.video_first_frame_wait_seconds
            ):
                self._video_stale_count += 1
                self._video_stale_started_monotonic = (
                    self._video_channel_enabled_monotonic
                )
                self._video_recovery_started_monotonic = (
                    self._video_stale_started_monotonic
                )
                self._video_watchdog_state = "DEGRADED"
                self._video_degraded_reason = "first_raw_frame_timeout"
                degraded_log = True
                state = "DEGRADED"
            if raw_stale and state == "HEALTHY":
                last_recovered = self._video_last_recovered_monotonic
                last_raw = self._last_raw_frame_monotonic
                false_recovery = bool(
                    last_recovered is not None
                    and last_raw is not None
                    and 0.0 <= last_raw - last_recovered
                    < self.video_false_recovery_window_seconds
                )
                if false_recovery:
                    self._video_false_recovery_count += 1
                self._video_stale_count += 1
                self._video_stale_started_monotonic = (
                    last_raw if last_raw is not None else now
                )
                self._video_recovery_started_monotonic = (
                    self._video_stale_started_monotonic
                )
                self._video_recovery_candidate_started_monotonic = None
                self._video_recovery_last_frame_monotonic = None
                self._video_recovery_frame_count = 0
                self._video_recovery_max_gap_observed_seconds = 0.0
                self._video_watchdog_state = "DEGRADED"
                self._video_degraded_reason = "raw_frame_stale"
                degraded_log = True
                state = "DEGRADED"

            recovery_candidate_age = (
                0.0
                if self._video_recovery_candidate_started_monotonic is None
                else max(
                    0.0,
                    now - self._video_recovery_candidate_started_monotonic,
                )
            )
            degraded_age = (
                raw_age if raw_age is not None else first_frame_age
            )
            recovering_without_frame = bool(
                state == "RECOVERING" and not has_current_raw
            )
            frames_since_soft_recovery = max(
                0,
                self._raw_frame_count
                - self._video_soft_recovery_start_raw_frame_count,
            )
            raw_is_fresh = bool(
                raw_age is not None and raw_age < self.frame_stale_seconds
            )
            reconnect_cooldown_expired = bool(
                self._video_watchdog_cooldown_until_monotonic is None
                or now >= self._video_watchdog_cooldown_until_monotonic
            )
            should_start_soft = bool(
                not self._video_soft_attempted
                and (
                    (
                        state == "DEGRADED"
                        and degraded_age is not None
                        and (
                            (
                                not has_current_raw
                                and first_frame_age is not None
                                and first_frame_age
                                >= self.video_first_frame_wait_seconds
                            )
                            or (
                                has_current_raw
                                and degraded_age
                                >= self.video_soft_recovery_seconds
                            )
                        )
                    )
                    or (
                        state == "RECOVERING"
                        and (
                            (
                                recovering_without_frame
                                and first_frame_age is not None
                                and first_frame_age
                                >= self.video_first_frame_wait_seconds
                            )
                            or (
                                not recovering_without_frame
                                and recovery_candidate_age
                                >= self.video_soft_recovery_seconds
                            )
                        )
                    )
                )
            )
            if should_start_soft:
                if not has_current_raw:
                    soft_trigger = "no_first_frame"
                    soft_trigger_age = float(first_frame_age or 0.0)
                elif state == "RECOVERING":
                    soft_trigger = "unstable_recovery"
                    soft_trigger_age = recovery_candidate_age
                else:
                    soft_trigger = "raw_frame_stale"
                    soft_trigger_age = float(raw_age or 0.0)
                self._video_soft_attempted = True
                self._video_soft_recovery_count += 1
                self._video_watchdog_state = "SOFT_RECOVERY"
                self._video_soft_toggle_off_monotonic = now
                self._video_soft_toggle_on_monotonic = None
                self._video_soft_recovery_start_raw_frame_count = (
                    self._raw_frame_count
                )
                self._video_channel_enabled_monotonic = None
                toggle = False
                start_soft_log = True
                state = "SOFT_RECOVERY"
            elif state == "SOFT_RECOVERY":
                off_at = self._video_soft_toggle_off_monotonic
                on_at = self._video_soft_toggle_on_monotonic
                if (
                    on_at is None
                    and off_at is not None
                    and now - off_at >= self.video_soft_toggle_delay_seconds
                ):
                    self._video_soft_toggle_on_monotonic = now
                    self._video_channel_enabled_monotonic = now
                    self._video_soft_recovery_start_raw_frame_count = (
                        self._raw_frame_count
                    )
                    toggle = True
                elif (
                    on_at is not None
                    and now - on_at >= self.video_soft_observe_seconds
                    and frames_since_soft_recovery == 0
                    and not raw_is_fresh
                    and reconnect_cooldown_expired
                ):
                    full_reconnect = True

            if full_reconnect:
                self._video_full_reconnect_count += 1
                self._video_watchdog_state = "OFFLINE"
                self._video_recovery_required_on_next_connection = True
                # A failed L1 attempt must not be counted as a soft success
                # when stable frames arrive on the replacement connection.
                self._video_soft_attempted = False
                full_reconnect_accounted = True

        if degraded_log:
            LOGGER.warning(
                "HEALTH_SIGNAL_DEGRADED signal=video reason=%s "
                "raw_age=%s encoded_age=%s sport_age=%s "
                "action=wait_for_soft_recovery false_recovery=%s",
                self._video_degraded_reason,
                self._diagnostic_number(raw_age),
                self._diagnostic_number(encoded_age),
                self._diagnostic_number(sport_age),
                str(false_recovery).lower(),
            )
        if toggle is not None:
            try:
                connection.video.switchVideoChannel(toggle)
            except Exception as exc:
                LOGGER.warning(
                    "VIDEO_SOFT_RECOVERY_TOGGLE_FAILED enabled=%s error=%s: %s",
                    toggle,
                    type(exc).__name__,
                    exc,
                )
            else:
                LOGGER.info(
                    "VIDEO_SOFT_RECOVERY_CHANNEL enabled=%s", toggle
                )
        if start_soft_log:
            LOGGER.warning(
                "VIDEO_SOFT_RECOVERY_STARTED trigger=%s trigger_age=%.3f "
                "raw_age=%s threshold=%.3f",
                soft_trigger,
                soft_trigger_age,
                self._diagnostic_number(raw_age),
                self.video_soft_recovery_seconds,
            )
        if full_reconnect:
            if not full_reconnect_accounted:
                with self._lock:
                    if self._connection is connection and self._connected:
                        self._video_full_reconnect_count += 1
                        self._video_watchdog_state = "OFFLINE"
                        self._video_recovery_required_on_next_connection = True
                        self._video_soft_attempted = False
            LOGGER.warning(
                "VIDEO_SOFT_RECOVERY_FAILED action=full_reconnect raw_age=%s "
                "frames_since_soft=%d cooldown_expired=%s",
                self._diagnostic_number(raw_age),
                frames_since_soft_recovery,
                str(reconnect_cooldown_expired).lower(),
            )
            self._handle_connection_lost(
                "video_watchdog_reconnect",
                connection,
                diagnostic_reason="video_only_stale_after_soft_recovery",
            )

    def _update_degraded_signals(
        self,
        connection: Any,
        *,
        video_reason: str | None,
        data_reason: str | None,
        raw_age: float | None,
        encoded_age: float | None,
        sport_age: float | None,
    ) -> None:
        transitions: list[tuple[str, str | None, str | None]] = []
        with self._lock:
            if self._connection is not connection or not self._connected:
                return
            if (
                not self.enable_video_active_recovery
                and video_reason != self._video_degraded_reason
            ):
                transitions.append(
                    ("video", self._video_degraded_reason, video_reason)
                )
                self._video_degraded_reason = video_reason
                if video_reason is None:
                    if self.enable_video:
                        self._video_watchdog_state = "HEALTHY"
                    self._video_stale_started_monotonic = None
                    self._video_recovery_started_monotonic = None
                elif not self.enable_video_active_recovery:
                    self._video_stale_count += 1
                    self._video_watchdog_state = "DEGRADED"
                    self._video_stale_started_monotonic = time.monotonic()
                    self._video_recovery_started_monotonic = (
                        self._video_stale_started_monotonic
                    )
            if data_reason != self._data_degraded_reason:
                transitions.append(("data", self._data_degraded_reason, data_reason))
                self._data_degraded_reason = data_reason
        for signal, previous, current in transitions:
            if current is None:
                LOGGER.info(
                    "HEALTH_SIGNAL_RECOVERED signal=%s previous_reason=%s "
                    "raw_age=%s encoded_age=%s sport_age=%s",
                    signal,
                    previous,
                    self._diagnostic_number(raw_age),
                    self._diagnostic_number(encoded_age),
                    self._diagnostic_number(sport_age),
                )
            else:
                LOGGER.warning(
                    "HEALTH_SIGNAL_DEGRADED signal=%s reason=%s raw_age=%s "
                    "encoded_age=%s sport_age=%s action=keep_transport",
                    signal,
                    current,
                    self._diagnostic_number(raw_age),
                    self._diagnostic_number(encoded_age),
                    self._diagnostic_number(sport_age),
                )

    @staticmethod
    def _read_transport_states(connection: Any) -> tuple[str, str]:
        pc = getattr(connection, "pc", None)
        if pc is None:
            return "connected", "completed"
        return (
            str(getattr(pc, "connectionState", "unknown")),
            str(getattr(pc, "iceConnectionState", "unknown")),
        )

    @staticmethod
    def _diagnostic_number(value: Any) -> str:
        return "null" if value is None else f"{float(value):.3f}"

    async def _sleep_or_stop(self, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while (
            not self._stop.is_set()
            and not self._reconnect_blocked.is_set()
            and time.monotonic() < deadline
        ):
            await asyncio.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        return self._stop.is_set() or self._reconnect_blocked.is_set()

    def _is_current_connection(self, connection: Any) -> bool:
        with self._lock:
            return self._connection is connection and self._connected

    async def _run_connection_task(
        self, generation: int, coroutine: Any
    ) -> Any:
        task = asyncio.current_task()
        if task is not None:
            with self._lock:
                self._connection_tasks.setdefault(generation, set()).add(task)
        try:
            return await coroutine
        finally:
            if task is not None:
                with self._lock:
                    tasks = self._connection_tasks.get(generation)
                    if tasks is not None:
                        tasks.discard(task)
                        if not tasks:
                            self._connection_tasks.pop(generation, None)

    def _quiesce_connection_helpers(self, connection: Any) -> None:
        datachannel = getattr(connection, "datachannel", None)
        heartbeat = getattr(datachannel, "heartbeat", None)
        stop_heartbeat = getattr(heartbeat, "stop_heartbeat", None)
        if callable(stop_heartbeat):
            stop_heartbeat()
        rtc_inner_req = getattr(datachannel, "rtc_inner_req", None)
        network_status = getattr(rtc_inner_req, "network_status", None)
        stop_network_status = getattr(
            network_status, "stop_network_status_fetch", None
        )
        if callable(stop_network_status):
            stop_network_status()
        pub_sub = getattr(datachannel, "pub_sub", None)
        resolver = getattr(pub_sub, "future_resolver", None)
        pending_callbacks = getattr(resolver, "pending_callbacks", None)
        if isinstance(pending_callbacks, dict):
            for futures in tuple(pending_callbacks.values()):
                for future in tuple(futures):
                    if future is not None and not future.done():
                        future.cancel()
            pending_callbacks.clear()
        chunk_storage = getattr(resolver, "chunk_data_storage", None)
        if isinstance(chunk_storage, dict):
            chunk_storage.clear()
        subscriptions = getattr(pub_sub, "subscriptions", None)
        if isinstance(subscriptions, dict):
            subscriptions.clear()

    async def _cancel_sdk_network_status_tasks(self, connection: Any) -> None:
        datachannel = getattr(connection, "datachannel", None)
        rtc_inner_req = getattr(datachannel, "rtc_inner_req", None)
        network_status = getattr(rtc_inner_req, "network_status", None)
        if network_status is None:
            return
        current = asyncio.current_task()
        owned_tasks: list[asyncio.Task[Any]] = []
        for task in asyncio.all_tasks():
            if task is current or task.done():
                continue
            coroutine = task.get_coro()
            frame = getattr(coroutine, "cr_frame", None)
            if frame is not None and frame.f_locals.get("self") is network_status:
                owned_tasks.append(task)
        for task in owned_tasks:
            task.cancel()
        if owned_tasks:
            await asyncio.gather(*owned_tasks, return_exceptions=True)

    async def _cancel_connection_tasks(self, generation: int) -> None:
        current = asyncio.current_task()
        with self._lock:
            tasks = tuple(self._connection_tasks.pop(generation, set()))
        pending = [task for task in tasks if task is not current and not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _reset_connection_samples_locked(self) -> None:
        self._state_received_monotonic = None
        self._state_received_iso = None
        self._uwb_received_monotonic = None
        self._uwb_received_timestamp_ms = None
        self._uwb_received_iso = None
        self._multiple_state = None
        self._multiple_state_received_monotonic = None
        self._low_state_received_monotonic = None

    def _clear_raw_frames(self) -> None:
        while True:
            try:
                self._raw_frames.get_nowait()
            except queue.Empty:
                return

    async def _capture_microphone_async(
        self,
        duration_seconds: float,
        *,
        vad_enabled: bool,
        vad_trailing_silence_seconds: float,
        vad_min_capture_seconds: float,
        diagnostic_prefix: str | None,
    ) -> dict[str, object]:
        with self._lock:
            connection = self._connection
            available = self._audio_channel_available
        audio = None if connection is None else getattr(connection, "audio", None)
        if not available or audio is None:
            raise RuntimeError("Go2 WebRTC microphone channel is unavailable")
        with self._lock:
            self._microphone_frames = []
            self._microphone_sample_rate = 0
            self._microphone_channels = 0
            self._microphone_sample_count = 0
            self._microphone_frame_count = 0
            self._microphone_target_samples = 0
            self._pending_microphone_duration = duration_seconds
            self._microphone_error = None
            self._microphone_vad_enabled = bool(vad_enabled)
            self._microphone_speech_detected = False
            self._microphone_last_voice_sample = 0
            self._microphone_vad_min_samples = 0
            self._microphone_vad_silence_samples = 0
            self._microphone_endpoint_reason = (
                "max_duration" if vad_enabled else "fixed_duration"
            )
            self._microphone_vad_noise_rms = []
            self._microphone_vad_noise_peak = []
            self._microphone_vad_calibration_samples = 0
            self._microphone_vad_rms_threshold = 650.0
            self._microphone_vad_peak_threshold = 1800
            self._pending_microphone_vad_min_duration = max(
                0.2, float(vad_min_capture_seconds)
            )
            self._pending_microphone_vad_silence_duration = max(
                0.2, float(vad_trailing_silence_seconds)
            )
            self._microphone_recording = True
            self._microphone_complete.clear()
        audio.switchAudioChannel(True)
        if diagnostic_prefix:
            print(f"{diagnostic_prefix}_MIC_OPEN_OK", flush=True)
        first_frame_reported = False
        try:
            deadline = time.monotonic() + duration_seconds + 2.0
            while not self._microphone_complete.is_set():
                if diagnostic_prefix and not first_frame_reported:
                    with self._lock:
                        received_frame = self._microphone_frame_count > 0
                    if received_frame:
                        print(f"{diagnostic_prefix}_FIRST_FRAME", flush=True)
                        first_frame_reported = True
                if time.monotonic() >= deadline:
                    raise asyncio.TimeoutError("microphone did not deliver enough PCM")
                await asyncio.sleep(0.02)
        finally:
            audio.switchAudioChannel(False)
            with self._lock:
                self._microphone_recording = False
        with self._lock:
            if self._microphone_error:
                raise RuntimeError(self._microphone_error)
            return {
                "pcm": b"".join(self._microphone_frames),
                "sample_rate": self._microphone_sample_rate,
                "channels": self._microphone_channels,
                "frame_count": self._microphone_frame_count,
                "vad_enabled": self._microphone_vad_enabled,
                "speech_detected": self._microphone_speech_detected,
                "endpoint_reason": self._microphone_endpoint_reason,
                "trailing_silence_seconds": (
                    0.0
                    if not self._microphone_speech_detected
                    or self._microphone_sample_rate <= 0
                    else max(
                        0.0,
                        (
                            self._microphone_sample_count
                            - self._microphone_last_voice_sample
                        )
                        / self._microphone_sample_rate,
                    )
                ),
            }

    async def _receive_audio_frame(self, frame: Any) -> None:
        with self._lock:
            if not self._microphone_recording:
                return
        try:
            import numpy as np

            array = np.asarray(frame.to_ndarray())
            format_name = str(getattr(getattr(frame, "format", None), "name", ""))
            if array.dtype != np.int16 or format_name.endswith("p"):
                raise ValueError(
                    f"unsupported microphone PCM format={format_name or array.dtype}"
                )
            sample_rate = int(getattr(frame, "sample_rate", 0) or 0)
            layout = getattr(frame, "layout", None)
            layout_channels = getattr(layout, "channels", None)
            channels = len(layout_channels) if layout_channels is not None else 0
            channels = channels or 2
            if sample_rate <= 0:
                raise ValueError("microphone frame has no sample rate")
            pcm = np.ascontiguousarray(array).astype("<i2", copy=False).tobytes()
            samples = len(pcm) // 2 // channels
            values = np.frombuffer(pcm, dtype="<i2").astype(np.int32)
            frame_peak = int(np.max(np.abs(values))) if values.size else 0
            frame_rms = (
                float(np.sqrt(np.mean(values.astype(np.float64) ** 2)))
                if values.size
                else 0.0
            )
            with self._lock:
                if not self._microphone_recording:
                    return
                if self._microphone_sample_rate == 0:
                    self._microphone_sample_rate = sample_rate
                    self._microphone_channels = channels
                    self._microphone_target_samples = max(
                        1, int(sample_rate * self._pending_microphone_duration)
                    )
                    self._microphone_vad_min_samples = max(
                        1,
                        int(sample_rate * self._pending_microphone_vad_min_duration),
                    )
                    self._microphone_vad_silence_samples = max(
                        1,
                        int(
                            sample_rate
                            * self._pending_microphone_vad_silence_duration
                        ),
                    )
                    self._microphone_vad_calibration_samples = max(
                        1, int(sample_rate * 0.4)
                    )
                elif (
                    sample_rate != self._microphone_sample_rate
                    or channels != self._microphone_channels
                ):
                    raise ValueError("microphone PCM format changed during capture")
                remaining = self._microphone_target_samples - self._microphone_sample_count
                take = min(samples, remaining)
                self._microphone_frames.append(pcm[: take * channels * 2])
                self._microphone_sample_count += take
                self._microphone_frame_count += 1
                calibrating = (
                    self._microphone_vad_enabled
                    and self._microphone_sample_count
                    <= self._microphone_vad_calibration_samples
                )
                if calibrating:
                    self._microphone_vad_noise_rms.append(frame_rms)
                    self._microphone_vad_noise_peak.append(frame_peak)
                elif self._microphone_vad_enabled and self._microphone_vad_noise_rms:
                    noise_rms = float(np.median(self._microphone_vad_noise_rms))
                    noise_peak = float(np.median(self._microphone_vad_noise_peak))
                    self._microphone_vad_rms_threshold = max(650.0, noise_rms * 1.55)
                    self._microphone_vad_peak_threshold = int(
                        max(1800.0, noise_peak * 1.6)
                    )
                voiced = (
                    not calibrating
                    and (
                        frame_rms >= self._microphone_vad_rms_threshold
                        or frame_peak >= self._microphone_vad_peak_threshold
                    )
                )
                if self._microphone_vad_enabled and voiced:
                    self._microphone_speech_detected = True
                    self._microphone_last_voice_sample = self._microphone_sample_count
                if (
                    self._microphone_vad_enabled
                    and self._microphone_speech_detected
                    and self._microphone_sample_count >= self._microphone_vad_min_samples
                    and self._microphone_sample_count - self._microphone_last_voice_sample
                    >= self._microphone_vad_silence_samples
                ):
                    self._microphone_endpoint_reason = "vad_trailing_silence"
                    self._microphone_complete.set()
                if self._microphone_sample_count >= self._microphone_target_samples:
                    if self._microphone_vad_enabled:
                        self._microphone_endpoint_reason = "max_duration"
                    self._microphone_complete.set()
        except Exception as exc:
            with self._lock:
                self._microphone_error = str(exc)
                self._microphone_complete.set()

    def _publish_sport_command(
        self, command: str, parameter: dict[str, float] | None = None
    ) -> int:
        with self._lock:
            api_id = self._sport_cmd.get(command)
        if api_id is None:
            raise self._command_error(f"{command} API id is unavailable")
        options: dict[str, Any] = {"api_id": api_id}
        if parameter is not None:
            options["parameter"] = parameter
        return self._publish(options, command)

    async def _play_audio_file_async(self, upload_path: str, custom_name: str) -> None:
        unique_id = await self._preload_audio_file_async(upload_path, custom_name)
        with self._lock:
            audio_hub = self._audio_hub
        if audio_hub is None:
            raise RuntimeError("WebRTC AudioHub is unavailable")
        await audio_hub.play_by_uuid(unique_id)

    async def _preload_audio_files_async(
        self,
        prepared: list[tuple[str, str, str, float]],
        *,
        retry_attempts: int,
    ) -> dict[str, AudioPreloadResult]:
        with self._lock:
            connection = self._connection
            audio_hub = self._audio_hub
        if connection is None:
            raise RuntimeError("WebRTC connection is unavailable")
        if audio_hub is None:
            audio_hub = self._audio_hub_factory(connection)
            with self._lock:
                self._audio_hub = audio_hub

        catalogue = await self._query_audio_catalog(
            audio_hub,
            attempts=retry_attempts,
            operation="initial audio_list query",
        )
        self._audio_uuid_cache.update(catalogue)

        upload_attempts: dict[str, int] = {}
        upload_succeeded: dict[str, bool] = {}
        upload_errors: dict[str, str] = {}
        missing = [item for item in prepared if item[2] not in catalogue]

        for source, upload_path, custom_name, upload_timeout in missing:
            upload_succeeded[custom_name] = False
            for attempt in range(1, retry_attempts + 1):
                upload_attempts[custom_name] = attempt
                try:
                    with self._audiohub_upload_console(audio_hub):
                        await asyncio.wait_for(
                            audio_hub.upload_audio_file(upload_path),
                            timeout=upload_timeout,
                        )
                    upload_succeeded[custom_name] = True
                    upload_errors.pop(custom_name, None)
                    break
                except asyncio.TimeoutError:
                    upload_errors[custom_name] = (
                        "TimeoutError: AudioHub upload exceeded "
                        f"{upload_timeout:.1f}s for {os.path.basename(source)}"
                    )
                except Exception as exc:
                    upload_errors[custom_name] = self._exception_detail(exc)

                # A late robot response can arrive just as the local timeout
                # fires. Confirm absence before retrying so an acknowledged file
                # is never uploaded twice deliberately.
                try:
                    recovered = await self._query_audio_catalog(
                        audio_hub,
                        attempts=1,
                        operation=(
                            f"audio_list recovery after {os.path.basename(source)} "
                            f"attempt {attempt}"
                        ),
                    )
                except Exception as catalogue_exc:
                    recovery_detail = self._exception_detail(catalogue_exc)
                    upload_errors[custom_name] = (
                        f"{upload_errors[custom_name]}; recovery query failed: "
                        f"{recovery_detail}"
                    )
                else:
                    catalogue.update(recovered)
                    self._audio_uuid_cache.update(recovered)
                    if custom_name in recovered:
                        upload_succeeded[custom_name] = True
                        upload_errors.pop(custom_name, None)
                        break
                if attempt < retry_attempts:
                    await asyncio.sleep(0.25)

        final_catalogue_error: str | None = None
        if missing:
            try:
                refreshed = await self._query_audio_catalog(
                    audio_hub,
                    attempts=retry_attempts,
                    operation="final audio_list query",
                )
            except Exception as exc:
                final_catalogue_error = self._exception_detail(exc)
            else:
                catalogue.update(refreshed)
                self._audio_uuid_cache.update(refreshed)

        results: dict[str, AudioPreloadResult] = {}
        for source, _upload_path, custom_name, _upload_timeout in prepared:
            unique_id = catalogue.get(custom_name)
            was_missing = custom_name in upload_succeeded
            if unique_id:
                results[source] = AudioPreloadResult(
                    path=source,
                    custom_name=custom_name,
                    ready=True,
                    uploaded=was_missing,
                    attempts=upload_attempts.get(custom_name, 0),
                    unique_id=unique_id,
                )
                continue
            detail = upload_errors.get(custom_name)
            if not detail:
                if final_catalogue_error:
                    detail = (
                        "RuntimeError: upload could not be verified because the "
                        f"final audio_list query failed: {final_catalogue_error}"
                    )
                else:
                    detail = (
                        "RuntimeError: AudioHub upload completed but the preset was "
                        "not present in the refreshed audio_list"
                        if upload_succeeded.get(custom_name)
                        else "RuntimeError: preset is absent from AudioHub"
                    )
            results[source] = AudioPreloadResult(
                path=source,
                custom_name=custom_name,
                ready=False,
                uploaded=was_missing,
                attempts=upload_attempts.get(custom_name, 0),
                error=detail,
            )
        return results

    async def _query_audio_catalog(
        self,
        audio_hub: Any,
        *,
        attempts: int,
        operation: str,
    ) -> dict[str, str]:
        timeout = max(self.command_timeout_seconds + 1.0, 8.0)
        last_error: Exception | None = None
        for attempt in range(1, max(1, attempts) + 1):
            try:
                response = await asyncio.wait_for(
                    audio_hub.get_audio_list(), timeout=timeout
                )
                return self._audio_catalog(response)
            except Exception as exc:
                last_error = exc
                if attempt < attempts:
                    await asyncio.sleep(0.25)
        assert last_error is not None
        if isinstance(last_error, asyncio.TimeoutError):
            raise RuntimeError(
                f"{operation} timed out after {timeout:.1f}s"
            ) from last_error
        raise RuntimeError(
            f"{operation} failed: {self._exception_detail(last_error)}"
        ) from last_error

    @contextlib.contextmanager
    def _audiohub_upload_console(self, audio_hub: Any):
        """Suppress only the upstream raw Base64 print in competition mode."""

        if self._protocol_logs_verbose():
            yield
            return
        upload = getattr(audio_hub, "upload_audio_file", None)
        function = getattr(upload, "__func__", upload)
        namespace = getattr(function, "__globals__", None)
        if not isinstance(namespace, dict):
            yield
            return
        sentinel = object()
        previous = namespace.get("print", sentinel)
        namespace["print"] = lambda *_args, **_kwargs: None
        try:
            yield
        finally:
            if previous is sentinel:
                namespace.pop("print", None)
            else:
                namespace["print"] = previous

    async def _preload_audio_file_async(
        self,
        upload_path: str,
        custom_name: str,
        replace_prefix: str | None = None,
    ) -> str:
        with self._lock:
            connection = self._connection
            audio_hub = self._audio_hub
        if connection is None:
            raise RuntimeError("WebRTC connection is unavailable")
        if audio_hub is None:
            audio_hub = self._audio_hub_factory(connection)
            with self._lock:
                self._audio_hub = audio_hub

        unique_id = self._audio_uuid_cache.get(custom_name)
        if unique_id is None:
            unique_id = self._find_audio_uuid(await audio_hub.get_audio_list(), custom_name)
        if unique_id is None:
            # Keep the upstream wire format unchanged while hiding its raw
            # Base64 stdout in normal competition mode.
            with self._audiohub_upload_console(audio_hub):
                await audio_hub.upload_audio_file(upload_path)
            unique_id = self._find_audio_uuid(
                await audio_hub.get_audio_list(), custom_name
            )
        if unique_id is None:
            raise RuntimeError(
                f"AudioHub upload completed but {custom_name!r} was not listed"
            )
        self._audio_uuid_cache[custom_name] = unique_id
        if replace_prefix:
            response = await audio_hub.get_audio_list()
            for entry in self._audio_entries(response):
                observed_name = str(entry.get("CUSTOM_NAME") or "")
                observed_uuid = str(entry.get("UNIQUE_ID") or "")
                if (
                    observed_name.startswith(replace_prefix)
                    and observed_name != custom_name
                    and observed_uuid
                ):
                    await audio_hub.delete_record(observed_uuid)
                    self._audio_uuid_cache.pop(observed_name, None)
        return unique_id

    @staticmethod
    def _audio_entries(response: Any) -> list[dict[str, Any]]:
        if not isinstance(response, dict):
            return []
        try:
            raw = response["data"]["data"]
            payload = json.loads(raw) if isinstance(raw, str) else raw
            entries = payload.get("audio_list", [])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return []
        return [entry for entry in entries if isinstance(entry, dict)]

    @classmethod
    def _audio_catalog(cls, response: Any) -> dict[str, str]:
        catalogue: dict[str, str] = {}
        for entry in cls._audio_entries(response):
            custom_name = str(entry.get("CUSTOM_NAME") or "").strip()
            unique_id = str(entry.get("UNIQUE_ID") or "").strip()
            if custom_name and unique_id:
                catalogue[custom_name] = unique_id
        return catalogue

    @classmethod
    def _find_audio_uuid(cls, response: Any, custom_name: str) -> str | None:
        return cls._audio_catalog(response).get(custom_name)

    @staticmethod
    def _protocol_logs_verbose() -> bool:
        return str(os.environ.get("GO2_VERBOSE_PROTOCOL_LOG", "0")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    @staticmethod
    def _audiohub_upload_timeout(path: str) -> float:
        # Upstream AudioHub sleeps 100 ms after each 4 KiB Base64 chunk. Add
        # response/network headroom so longer emergency speech is not cut off by
        # the former fixed 15 second preload limit.
        encoded_bytes = math.ceil(os.path.getsize(path) * 4 / 3)
        chunks = max(1, math.ceil(encoded_bytes / 4096))
        return max(20.0, 10.0 + (chunks * 0.35))

    @staticmethod
    def _exception_detail(exc: BaseException) -> str:
        detail = str(exc).strip()
        return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__

    def _run_runtime_coroutine(
        self, coroutine: Any, command: str, *, timeout: float
    ) -> Any:
        with self._lock:
            loop = self._loop
            started = self._started
            connected = self._connected
        if not started or not connected or loop is None:
            if hasattr(coroutine, "close"):
                coroutine.close()
            raise self._command_error("Go2 Wireless Runtime is not connected")
        future = asyncio.run_coroutine_threadsafe(
            asyncio.wait_for(coroutine, timeout=timeout), loop
        )
        try:
            return future.result(timeout=timeout + 0.5)
        except Exception as exc:
            raise self._command_error(f"{command} failed: {exc}") from exc

    def _render_windows_speech(self, text: str, wav_path: str) -> None:
        script = r"""
Add-Type -AssemblyName System.Speech
$speaker = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $voice = [Environment]::GetEnvironmentVariable('GO2_TTS_VOICE', 'Process')
    if ($voice) { $speaker.SelectVoice($voice) }
    $target = [Environment]::GetEnvironmentVariable('GO2_TTS_WAV', 'Process')
    $speech = [Environment]::GetEnvironmentVariable('GO2_TTS_TEXT', 'Process')
    $speaker.SetOutputToWaveFile($target)
    $speaker.Speak($speech)
}
finally {
    $speaker.Dispose()
}
"""
        environment = os.environ.copy()
        environment.update(
            {
                "GO2_TTS_TEXT": text,
                "GO2_TTS_WAV": wav_path,
                "GO2_TTS_VOICE": self.tts_voice,
            }
        )
        try:
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    script,
                ],
                env=environment,
                capture_output=True,
                text=True,
                timeout=30.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"Windows TTS could not start: {exc}") from exc
        if result.returncode != 0 or not os.path.isfile(wav_path):
            detail = (result.stderr or result.stdout or "unknown TTS failure").strip()
            raise RuntimeError(f"Windows TTS failed: {detail}")

    @staticmethod
    def _safe_audio_name(name: str) -> str:
        sanitized = "".join(
            character if character.isalnum() or character in "-_" else "_"
            for character in name
        ).strip("_")
        return sanitized[:40] or "audio"

    @staticmethod
    def _file_sha256(path: str) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as stream:
            for block in iter(lambda: stream.read(65536), b""):
                digest.update(block)
        return digest.hexdigest()

    @classmethod
    def _audiohub_digest(cls, path: str) -> str:
        # Version the uploaded representation so already-cached streaming WAVs
        # with placeholder RIFF sizes are replaced by the canonicalized file.
        source_digest = cls._file_sha256(path)
        return hashlib.sha256(
            f"audiohub-pcm-v2:{source_digest}".encode("ascii")
        ).hexdigest()[:12]

    @staticmethod
    def _prepare_audiohub_wav(source: str, target: str) -> None:
        """Write a bounded PCM16 mono WAV with click-safe edges for Go2."""

        try:
            with wave.open(source, "rb") as stream:
                channels = stream.getnchannels()
                sample_width = stream.getsampwidth()
                sample_rate = stream.getframerate()
                compression = stream.getcomptype()
                raw = stream.readframes(stream.getnframes())
            if (
                channels < 1
                or sample_width != 2
                or sample_rate <= 0
                or compression != "NONE"
            ):
                raise ValueError("unsupported WAV format")

            import numpy as np

            samples = np.frombuffer(raw, dtype="<i2")
            usable = samples.size - (samples.size % channels)
            samples = samples[:usable].reshape(-1, channels).astype(np.float64)
            mono = np.mean(samples, axis=1) if channels > 1 else samples[:, 0]
            mono = np.clip(np.rint(mono), -32768, 32767).astype("<i2")
            fade_samples = min(mono.size // 2, max(1, int(sample_rate * 0.015)))
            if fade_samples:
                gain = np.linspace(0.0, 1.0, fade_samples, endpoint=True)
                mono[:fade_samples] = np.rint(
                    mono[:fade_samples].astype(np.float64) * gain
                ).astype("<i2")
                mono[-fade_samples:] = np.rint(
                    mono[-fade_samples:].astype(np.float64) * gain[::-1]
                ).astype("<i2")
            trailing_silence = np.zeros(int(sample_rate * 0.05), dtype="<i2")
            output = np.concatenate((mono, trailing_silence))
            with wave.open(target, "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(sample_rate)
                stream.writeframes(output.tobytes())
        except (EOFError, ValueError, wave.Error):
            # Preserve support for externally supplied non-PCM WAVs; normal
            # Qwen/preset speech always takes the canonical PCM path above.
            shutil.copyfile(source, target)

    def _state_callback(
        self, topic: str, connection: Any | None = None
    ) -> Callable[[Any], None]:
        def receive(message: Any) -> None:
            if not isinstance(message, dict) or not isinstance(message.get("data"), dict):
                return
            with self._lock:
                if connection is not None and self._connection is not connection:
                    return
                self._sport_state = deepcopy(message["data"])
                self._state_topic = topic
                self._state_received_monotonic = time.monotonic()
                self._state_received_iso = _now_iso()
                self._state_sample_counts[topic] = (
                    self._state_sample_counts.get(topic, 0) + 1
                )
            self._first_state.set()

        return receive

    def _uwb_callback(
        self, topic: str, connection: Any | None = None
    ) -> Callable[[Any], None]:
        def receive(message: Any) -> None:
            payload = self._decode_topic_payload(message)
            if payload is None:
                return
            fields = {
                "distance_est": self._first_field(
                    payload, "distance_est", "distanceEst"
                ),
                "orientation_est": self._first_field(
                    payload, "orientation_est", "orientationEst"
                ),
                "yaw_est": self._first_field(payload, "yaw_est", "yawEst"),
                "enabled_from_app": self._first_field(
                    payload, "enabled_from_app", "enabledFromApp"
                ),
                "error_state": self._first_field(
                    payload, "error_state", "errorState"
                ),
            }
            if all(value is None for value in fields.values()):
                return
            with self._lock:
                if connection is not None and self._connection is not connection:
                    return
                self._uwb_state = fields
                self._uwb_source_keys = sorted(str(key) for key in payload)
                self._uwb_topic = topic
                self._uwb_received_monotonic = time.monotonic()
                # UwbState has no reliable source timestamp on this firmware.
                # Preserve the local wall-clock receive time for consumers
                # which must compare freshness across computers.
                self._uwb_received_timestamp_ms = int(time.time() * 1000.0)
                self._uwb_received_iso = _now_iso()
                self._uwb_sample_counts[topic] = (
                    self._uwb_sample_counts.get(topic, 0) + 1
                )

        return receive

    def _low_state_callback(
        self, topic: str, connection: Any | None = None
    ) -> Callable[[Any], None]:
        def receive(message: Any) -> None:
            payload = self._decode_topic_payload(message)
            if payload is None:
                return
            with self._lock:
                if connection is not None and self._connection is not connection:
                    return
                self._low_state = payload
                self._low_state_received_monotonic = time.monotonic()
                self._low_state_sample_counts[topic] = (
                    self._low_state_sample_counts.get(topic, 0) + 1
                )

        return receive

    def _multiple_state_callback_for(
        self, connection: Any
    ) -> Callable[[Any], None]:
        def receive(message: Any) -> None:
            self._multiple_state_callback(message, connection)

        return receive

    def _multiple_state_callback(
        self, message: Any, connection: Any | None = None
    ) -> None:
        payload = self._decode_topic_payload(message)
        if payload is None:
            return
        with self._lock:
            if connection is not None and self._connection is not connection:
                return
            self._multiple_state = payload
            self._multiple_state_received_monotonic = time.monotonic()
            self._multiple_state_sample_count += 1

    @staticmethod
    def _decode_topic_payload(message: Any) -> dict[str, Any] | None:
        payload: Any = message.get("data") if isinstance(message, dict) else message
        for _ in range(3):
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except (TypeError, ValueError, json.JSONDecodeError):
                    return None
                continue
            if isinstance(payload, dict):
                nested = payload.get("data")
                if len(payload) == 1 and isinstance(nested, (dict, str)):
                    payload = nested
                    continue
                return deepcopy(payload)
            return None
        return deepcopy(payload) if isinstance(payload, dict) else None

    @staticmethod
    def _first_field(payload: dict[str, Any], *names: str) -> Any:
        for name in names:
            if name in payload:
                return payload[name]
        return None

    def _register_video_track(self, connection: Any) -> int | None:
        recovered_reason: str | None = None
        with self._lock:
            if self._connection is not connection or not self._connected:
                return None
            if self._video_track_end_pending_serial is not None:
                recovered_reason = self._video_track_end_reason
                self._video_track_end_recovered_count += 1
            self._video_track_serial += 1
            track_serial = self._video_track_serial
            self._active_video_track_serial = track_serial
            self._video_track_end_pending_serial = None
            self._video_track_end_started_monotonic = None
            self._video_track_end_reason = None
        if recovered_reason is not None:
            LOGGER.info(
                "VIDEO_TRACK_RECOVERED reason=%s replacement_track=%d "
                "action=keep_transport",
                recovered_reason,
                track_serial,
            )
        return track_serial

    def _handle_video_track_ended(
        self,
        reason: str,
        connection: Any,
        *,
        track_serial: int,
    ) -> None:
        peer_state, ice_state = self._read_transport_states(connection)
        datachannel = getattr(connection, "datachannel", None)
        data_open = getattr(datachannel, "data_channel_opened", True) is not False
        if (
            peer_state in {"closed", "failed"}
            or ice_state in {"closed", "failed"}
            or not data_open
        ):
            self._handle_connection_lost(reason, connection)
            return

        now = time.monotonic()
        with self._lock:
            if (
                self._connection is not connection
                or not self._connected
                or self._active_video_track_serial != track_serial
                or self._video_track_end_pending_serial == track_serial
            ):
                return
            generation = self._connection_generation
            raw_age = (
                None
                if self._last_raw_frame_monotonic is None
                or self._last_raw_frame_generation != generation
                else max(0.0, now - self._last_raw_frame_monotonic)
            )
            self._video_track_end_count += 1
            self._video_track_end_pending_serial = track_serial
            self._video_track_end_started_monotonic = now
            self._video_track_end_reason = reason
            self._video_watchdog_state = "DEGRADED"
            self._video_degraded_reason = "video_track_ended_pending"
            if self._video_stale_started_monotonic is None:
                self._video_stale_started_monotonic = now
            if self._video_recovery_started_monotonic is None:
                self._video_recovery_started_monotonic = now
        LOGGER.warning(
            "VIDEO_TRACK_ENDED_GRACE_STARTED reason=%s peer=%s ice=%s "
            "data_channel_ready=%s raw_age=%s grace_seconds=%.3f "
            "action=keep_transport",
            reason,
            peer_state,
            ice_state,
            str(data_open).lower(),
            self._diagnostic_number(raw_age),
            self.video_track_end_grace_seconds,
        )
        asyncio.create_task(
            self._run_connection_task(
                generation,
                self._confirm_video_track_loss(
                    connection,
                    generation=generation,
                    track_serial=track_serial,
                    reason=reason,
                ),
            )
        )

    async def _confirm_video_track_loss(
        self,
        connection: Any,
        *,
        generation: int,
        track_serial: int,
        reason: str,
    ) -> None:
        await asyncio.sleep(self.video_track_end_grace_seconds)
        with self._lock:
            if (
                self._connection is not connection
                or not self._connected
                or self._connection_generation != generation
                or self._video_track_end_pending_serial != track_serial
                or self._active_video_track_serial != track_serial
            ):
                return
        peer_state, ice_state = self._read_transport_states(connection)
        datachannel = getattr(connection, "datachannel", None)
        data_open = getattr(datachannel, "data_channel_opened", True) is not False
        with self._lock:
            if (
                self._connection is not connection
                or not self._connected
                or self._connection_generation != generation
                or self._video_track_end_pending_serial != track_serial
            ):
                return
            self._video_track_end_pending_serial = None
            self._video_track_end_started_monotonic = None
            self._video_track_end_reason = None
            self._video_track_end_confirmed_count += 1
            self._video_full_reconnect_count += 1
            self._video_watchdog_state = "OFFLINE"
            self._video_degraded_reason = "video_track_confirmed_lost"
            self._video_recovery_required_on_next_connection = True
        LOGGER.warning(
            "VIDEO_TRACK_CONFIRMED_LOST reason=%s peer=%s ice=%s "
            "data_channel_ready=%s grace_seconds=%.3f action=full_reconnect",
            reason,
            peer_state,
            ice_state,
            str(data_open).lower(),
            self.video_track_end_grace_seconds,
        )
        self._handle_connection_lost(
            "video_track_confirmed_lost",
            connection,
            diagnostic_reason=reason,
        )

    async def _receive_video(
        self,
        track: Any,
        connection: Any,
        *,
        track_serial: int,
    ) -> None:
        try:
            while not self._stop.is_set() and self._is_current_connection(connection):
                frame = await track.recv()
                raw_received_monotonic = time.monotonic()
                raw_received_at = _now_iso()
                recovery: dict[str, float | int] | None = None
                first_video_frame_ms: float | None = None
                with self._lock:
                    if self._connection is not connection or not self._connected:
                        return
                    generation = self._connection_generation
                    is_first_generation_frame = (
                        self._last_raw_frame_monotonic is None
                        or self._last_raw_frame_generation != generation
                    )
                    self._raw_frame_count += 1
                    self._last_raw_frame_monotonic = raw_received_monotonic
                    self._last_raw_frame_iso = raw_received_at
                    self._last_raw_frame_generation = generation
                    if (
                        is_first_generation_frame
                        and self._connect_trace_started_monotonic is not None
                    ):
                        first_video_frame_ms = max(
                            0.0,
                            (
                                raw_received_monotonic
                                - self._connect_trace_started_monotonic
                            )
                            * 1000.0,
                        )
                        current_trace = deepcopy(self._last_connect_trace)
                    else:
                        current_trace = None
                    recovery = self._record_video_recovery_frame_locked(
                        raw_received_monotonic
                    )
                if first_video_frame_ms is not None:
                    if (
                        current_trace is not None
                        and current_trace.get("generation") == generation
                    ):
                        current_trace["firstVideoFrameMs"] = first_video_frame_ms
                        self._record_connect_trace(current_trace)
                    LOGGER.info(
                        "WEBRTC_FIRST_VIDEO_FRAME generation=%d "
                        "elapsed_ms=%.3f",
                        generation,
                        first_video_frame_ms,
                    )
                if recovery is not None:
                    LOGGER.info(
                        "HEALTH_SIGNAL_RECOVERED signal=video "
                        "stable_frames=%d stable_duration=%.3f max_gap=%.3f "
                        "recovery_duration=%.3f",
                        recovery["stable_frames"],
                        recovery["stable_duration"],
                        recovery["max_gap"],
                        recovery["recovery_duration"],
                    )
                try:
                    self._raw_frames.put_nowait((frame, generation))
                except queue.Full:
                    try:
                        self._raw_frames.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        self._raw_frames.put_nowait((frame, generation))
                    except queue.Full:
                        pass
                    with self._lock:
                        self._dropped_frame_count += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._handle_video_track_ended(
                f"video_track_ended:{type(exc).__name__}",
                connection,
                track_serial=track_serial,
            )

    def _record_video_recovery_frame_locked(
        self, received_monotonic: float
    ) -> dict[str, float | int] | None:
        """Advance stable-frame recovery while ``self._lock`` is held."""

        state = self._video_watchdog_state
        if not self.enable_video_active_recovery:
            if state == "AWAITING_FIRST_FRAME":
                self._video_watchdog_state = "HEALTHY"
            return None
        if state == "AWAITING_FIRST_FRAME":
            if self._video_recovery_started_monotonic is None:
                self._video_recovery_started_monotonic = (
                    self._video_channel_enabled_monotonic
                    or received_monotonic
                )
        if state == "HEALTHY" or not self.enable_video:
            return None
        if (
            state == "SOFT_RECOVERY"
            and self._video_soft_toggle_on_monotonic is None
        ):
            # Ignore frames already buffered while the channel is being
            # switched off. Only frames received after Video ON can prove L1
            # recovery succeeded.
            return None

        soft_recovery_delivered_frame = bool(
            self._video_soft_attempted
            and self._video_soft_toggle_on_monotonic is not None
            and self._raw_frame_count
            > self._video_soft_recovery_start_raw_frame_count
        )
        if soft_recovery_delivered_frame:
            # One post-toggle frame proves the L1 recovery had an effect. Keep
            # collecting stable frames, but never let the old fixed timer tear
            # down a newly active media stream.
            self._video_soft_recovery_success_count += 1
            self._video_soft_attempted = False
            self._video_soft_toggle_off_monotonic = None
            self._video_soft_toggle_on_monotonic = None
            self._video_soft_recovery_start_raw_frame_count = (
                self._raw_frame_count
            )

        previous = self._video_recovery_last_frame_monotonic
        gap = None if previous is None else received_monotonic - previous
        if (
            previous is None
            or gap is None
            or gap < 0.0
            or gap >= self.video_recovery_max_gap_seconds
        ):
            self._video_recovery_candidate_started_monotonic = (
                received_monotonic
            )
            self._video_recovery_frame_count = 1
            self._video_recovery_max_gap_observed_seconds = 0.0
        else:
            self._video_recovery_frame_count += 1
            self._video_recovery_max_gap_observed_seconds = max(
                self._video_recovery_max_gap_observed_seconds,
                gap,
            )
        self._video_recovery_last_frame_monotonic = received_monotonic
        self._video_watchdog_state = "RECOVERING"
        candidate_started = self._video_recovery_candidate_started_monotonic
        stable_duration = (
            0.0
            if candidate_started is None
            else max(0.0, received_monotonic - candidate_started)
        )
        if (
            self._video_recovery_frame_count < self.video_recovery_min_frames
            or stable_duration < self.video_recovery_min_duration_seconds
            or self._video_recovery_max_gap_observed_seconds
            >= self.video_recovery_max_gap_seconds
        ):
            return None

        recovery_started = self._video_recovery_started_monotonic
        recovery_duration = (
            0.0
            if recovery_started is None
            else max(0.0, received_monotonic - recovery_started)
        )
        self._video_max_recovery_duration_seconds = max(
            self._video_max_recovery_duration_seconds,
            recovery_duration,
        )
        stable_frames = self._video_recovery_frame_count
        max_gap_observed = self._video_recovery_max_gap_observed_seconds
        initial_startup_recovery = bool(
            self._video_stale_count == 0
            and not self._video_recovery_required_on_next_connection
            and self._video_last_recovered_monotonic is None
        )
        self._video_watchdog_state = "HEALTHY"
        self._video_degraded_reason = None
        if not initial_startup_recovery:
            self._video_last_recovered_monotonic = received_monotonic
        self._video_stale_started_monotonic = None
        self._video_recovery_started_monotonic = None
        self._video_recovery_candidate_started_monotonic = None
        self._video_recovery_last_frame_monotonic = None
        self._video_recovery_frame_count = 0
        self._video_recovery_max_gap_observed_seconds = 0.0
        self._video_soft_toggle_off_monotonic = None
        self._video_soft_toggle_on_monotonic = None
        self._video_soft_recovery_start_raw_frame_count = self._raw_frame_count
        self._video_soft_attempted = False
        self._video_recovery_required_on_next_connection = False
        return {
            "stable_frames": stable_frames,
            "stable_duration": stable_duration,
            "max_gap": max_gap_observed,
            "recovery_duration": recovery_duration,
        }

    def _start_encoder_locked(self) -> None:
        if self._encoder_thread and self._encoder_thread.is_alive():
            return
        self._encoder_stop.clear()
        self._video_started_monotonic = time.monotonic()
        self._encoder_thread = threading.Thread(
            target=self._encode_frames,
            name="go2-video-encoder",
            daemon=True,
        )
        self._encoder_thread.start()

    def _encode_frames(self) -> None:
        import cv2

        interval = 1.0 / self.capture_fps
        while not self._encoder_stop.is_set():
            try:
                frame, generation = self._raw_frames.get(timeout=0.1)
            except queue.Empty:
                continue
            with self._lock:
                if generation != self._connection_generation or not self._connected:
                    continue
            now = time.monotonic()
            if now - self._last_encoded_monotonic < interval:
                with self._lock:
                    if generation == self._connection_generation and self._connected:
                        self._dropped_frame_count += 1
                continue
            encode_started = time.monotonic()
            try:
                image = frame.to_ndarray(format="bgr24")
                ok, encoded = cv2.imencode(
                    ".jpg",
                    image,
                    [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality],
                )
                if not ok:
                    raise RuntimeError("OpenCV JPEG encode returned false")
            except Exception as exc:
                encode_duration_ms = (time.monotonic() - encode_started) * 1000.0
                with self._lock:
                    if generation == self._connection_generation and self._connected:
                        self._record_encode_duration_locked(encode_duration_ms)
                        self._video_error_count += 1
                        self._last_video_error = f"{type(exc).__name__}: {exc}"
                continue
            encode_completed = time.monotonic()
            encode_duration_ms = (encode_completed - encode_started) * 1000.0
            encoded_at = _now_iso()
            with self._lock:
                if generation != self._connection_generation or not self._connected:
                    continue
                self._record_encode_duration_locked(encode_duration_ms)
                self._last_encoded_monotonic = encode_completed
                self._frame_sequence += 1
                self._encoded_frame_count += 1
                self._video_session_frame_count += 1
                elapsed = max(0.001, encode_completed - self._video_started_monotonic)
                self._latest_frame = LatestVideoFrame(
                    jpeg=encoded.tobytes(),
                    sequence=self._frame_sequence,
                    captured_at=encoded_at,
                    width=int(image.shape[1]),
                    height=int(image.shape[0]),
                    fps=self._video_session_frame_count / elapsed,
                )
                self._last_frame_monotonic = encode_completed
                self._last_encoded_frame_iso = encoded_at
                self._last_frame_generation = generation
                self._last_video_error = None

    def _record_encode_duration_locked(self, duration_ms: float) -> None:
        duration = max(0.0, float(duration_ms))
        self._encode_duration_ms_last = duration
        self._encode_duration_ms_max = (
            duration
            if self._encode_duration_ms_max is None
            else max(self._encode_duration_ms_max, duration)
        )
        self._encode_duration_ms_ewma = (
            duration
            if self._encode_duration_ms_ewma is None
            else (0.2 * duration) + (0.8 * self._encode_duration_ms_ewma)
        )

    def _publish(self, options: dict[str, Any], command: str) -> int:
        with self._sport_request_lock:
            with self._lock:
                loop = self._loop
                connection = self._connection
                started = self._started
                connected = self._connected
                motion_ready = self._motion_ready
                topic = self._rtc_topic.get("SPORT_MOD")
            if command != "StopMove" and not motion_ready:
                raise self._command_error(
                    "Motion is locked until the reconnect StopMove is confirmed"
                )
            if (
                not started
                or not connected
                or loop is None
                or connection is None
                or topic is None
            ):
                raise GatewayError(
                    ErrorCode.SDK_NOT_INITIALIZED,
                    "Go2 Wireless Runtime is not connected.",
                    503,
                )
            request_started = time.monotonic()
            is_motion_command = command in {"Move", "StopMove"}
            if is_motion_command:
                with self._lock:
                    self._motion_request_in_flight = command
                    self._motion_request_started_monotonic = request_started
                    self._last_motion_ack_error = None
            coroutine = connection.datachannel.pub_sub.publish_request_new(
                topic, options
            )
            future = asyncio.run_coroutine_threadsafe(
                asyncio.wait_for(coroutine, timeout=self.command_timeout_seconds),
                loop,
            )
            try:
                response = future.result(timeout=self.command_timeout_seconds + 0.5)
            except Exception as exc:
                future.cancel()
                error_text = f"{type(exc).__name__}: {exc}".rstrip()
                if is_motion_command:
                    with self._lock:
                        self._last_motion_ack_error = error_text
                        if (
                            isinstance(exc, TimeoutError)
                            or type(exc).__name__ == "TimeoutError"
                        ):
                            self._motion_request_timeout_count += 1
                raise self._command_error(
                    f"{command} request failed: {error_text}"
                ) from exc
            finally:
                if is_motion_command:
                    with self._lock:
                        self._motion_request_in_flight = None
                        self._motion_request_started_monotonic = None
            latency_ms = (time.monotonic() - request_started) * 1000.0
            code = _api_status_code(response)
            if code != 0:
                error_text = f"WebRTC status={code}"
                if is_motion_command:
                    with self._lock:
                        self._last_motion_ack_error = error_text
                raise self._command_error(
                    f"{command} was not acknowledged; {error_text}"
                )
            with self._lock:
                self._command_counts[command] = (
                    self._command_counts.get(command, 0) + 1
                )
                if is_motion_command:
                    self._last_motion_ack_at = _now_iso()
                    self._last_motion_ack_command = command
                    self._last_motion_ack_latency_ms = latency_ms
                    self._last_motion_ack_error = None
                    if command == "StopMove":
                        self._remote_stop_state = "STOP_CONFIRMED"
                        self._stop_required_after_reconnect = False
                    elif command == "Move":
                        self._remote_stop_state = "MOTION_ACTIVE"
            return 0

    def _shutdown_workers(self) -> None:
        self._stop.set()
        self._encoder_stop.set()
        with self._lock:
            loop = self._loop
            thread = self._thread
            encoder = self._encoder_thread
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        if encoder is not None and encoder is not threading.current_thread():
            encoder.join(timeout=2.0)
        with self._lock:
            self._loop = None
            self._thread = None
            self._encoder_thread = None
            self._loop_ready.clear()

    @staticmethod
    def _command_error(message: str) -> GatewayError:
        return GatewayError(ErrorCode.SDK_COMMAND_FAILED, message, 503)
