from __future__ import annotations

import ctypes
import json
import math
import os
import re
import queue
import tempfile
import threading
import time
import uuid
import wave
from array import array
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
import sys

from app.iot.mqtt_contract import (
    build_command_message,
    build_session_end_message,
    build_session_start_message,
    build_speech_message,
    contract_topic,
)
from app.iot.protocol_layer import BMachineStateMachine, CommandMessage, MessageTransport, MockTransport
from app.webrtc.voice_intent import HealthNewASRService, WakeWordMatcher


SOURCE_NAME = "go2"
DEFAULT_DEVICE_ID = os.environ.get("GO2_DEVICE_ID", "DOG-LJG-001")
DEFAULT_TOPIC_PREFIX = os.environ.get("GO2_MQTT_TOPIC_PREFIX", "aiot")
DEFAULT_SESSION_TIMEOUT_SECONDS = 10.0
DEFAULT_SESSION_MAX_TURNS = 2
DEFAULT_CAPTURE_SECONDS = 15.0
DEFAULT_TRAILING_SILENCE_SECONDS = 0.3
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHANNELS = 1
DEFAULT_BITS_PER_SAMPLE = 16
DEFAULT_BLOCK_ALIGN = (DEFAULT_CHANNELS * DEFAULT_BITS_PER_SAMPLE) // 8
DEFAULT_BYTES_PER_SECOND = DEFAULT_SAMPLE_RATE * DEFAULT_BLOCK_ALIGN
DEFAULT_BUFFER_MS = 100
DEFAULT_BUFFER_SIZE = int(DEFAULT_BYTES_PER_SECOND * DEFAULT_BUFFER_MS / 1000)
EMERGENCY_PHRASES = {
    "救命",
    "救我",
    "疼",
    "痛",
    "摔",
    "摔倒",
    "起不来",
    "帮我",
    "帮忙",
    "晕",
    "喘不上",
}
FILLER_PHRASES = {
    "嗯",
    "啊",
    "哦",
    "呃",
    "然后",
    "这个",
    "那个",
    "就是",
    "对",
    "对啊",
    "行",
    "好",
    "好的",
    "好吧",
}
USER_EXIT_PHRASES = {
    "不用了",
    "没事了",
    "你歇着吧",
}
POST_PLAYBACK_TRANSCRIPT_GUARD_SECONDS = max(
    0.0,
    min(5.0, float(os.environ.get("GO2_POST_PLAYBACK_TRANSCRIPT_GUARD_SECONDS", "2.0"))),
)
POST_PLAYBACK_QUIET_MIN_MUTE_SECONDS = max(
    0.0,
    min(2.0, float(os.environ.get("GO2_ASR_POST_PLAYBACK_MIN_MUTE_SECONDS", "0.5"))),
)
POST_PLAYBACK_QUIET_SECONDS = max(
    0.0,
    min(1.5, float(os.environ.get("GO2_ASR_POST_PLAYBACK_QUIET_SECONDS", "0.4"))),
)
POST_PLAYBACK_QUIET_MAX_SECONDS = max(
    0.2,
    min(5.0, float(os.environ.get("GO2_ASR_POST_PLAYBACK_QUIET_MAX_SECONDS", "2.5"))),
)
POST_PLAYBACK_QUIET_RMS_THRESHOLD = max(
    50.0,
    float(os.environ.get("GO2_ASR_POST_PLAYBACK_QUIET_RMS_THRESHOLD", "650")),
)
POST_PLAYBACK_QUIET_PEAK_THRESHOLD = max(
    100.0,
    float(os.environ.get("GO2_ASR_POST_PLAYBACK_QUIET_PEAK_THRESHOLD", "1800")),
)
OUTING_REQUEST_TERMS = (
    "出去",
    "出门",
    "走走",
    "走一走",
    "散步",
    "转转",
    "遛弯",
    "陪我走",
    "陪我走走",
    "陪我出去",
    "陪我出门",
    "带我出去",
    "带我出门",
    "跟我走",
    "一起出去",
)
HEALTH_WEATHER_TERMS = ("身体", "健康", "心率", "血氧", "天气", "气温", "体温")
WEATHER_QUERY_TERMS = ("下雨", "雨", "晴", "阴", "多云", "冷", "热")
STOP_FOLLOW_TERMS = ("停一下", "不用跟着", "停止伴随", "别跟着", "不要跟着")
MEDICATION_TERMS = ("吃过了", "吃了", "服过了", "服药了", "已经吃", "已经服")
DEPARTURE_TERMS = ("现在出发", "出发", "走吧", "可以走了", "开始走")
USER_OK_TERMS = ("我没事", "没事", "还好", "不用帮忙", "没有摔")

WAVE_MAPPER = ctypes.c_uint32(0xFFFFFFFF)
WIM_DATA = 0x03C0
CALLBACK_FUNCTION = 0x00030000


class VoiceState(str, Enum):
    WAKE_GUARD = "WAKE_GUARD"
    ACTIVE_LISTENING = "ACTIVE_LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    PAUSED = "PAUSED"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _normalize_text(value: str) -> str:
    return re.sub(r"[\s，。！？、,.!?]+", "", str(value or "").strip())


def _is_filler_text(text: str) -> bool:
    normalized = _normalize_text(text)
    return not normalized or normalized in FILLER_PHRASES or len(normalized) < 2


def _is_emergency_text(text: str) -> tuple[bool, str | None]:
    normalized = _normalize_text(text)
    for phrase in sorted(EMERGENCY_PHRASES, key=len, reverse=True):
        if phrase in normalized:
            return True, phrase
    return False, None


def _is_user_exit_text(text: str) -> bool:
    normalized = _normalize_text(text)
    return any(phrase in normalized for phrase in USER_EXIT_PHRASES)


def _is_known_business_text(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    term_groups = (
        OUTING_REQUEST_TERMS,
        HEALTH_WEATHER_TERMS,
        WEATHER_QUERY_TERMS,
        STOP_FOLLOW_TERMS,
        MEDICATION_TERMS,
        DEPARTURE_TERMS,
        USER_OK_TERMS,
        USER_EXIT_PHRASES,
        EMERGENCY_PHRASES,
    )
    return any(term in normalized for terms in term_groups for term in terms)


def _merge_streaming_text(accumulated: str, chunk_text: str) -> str:
    """Merge FunASR streaming chunk text while tolerating cumulative output."""

    chunk_text = str(chunk_text or "").strip()
    if not chunk_text:
        return accumulated
    if not accumulated:
        return chunk_text
    if chunk_text == accumulated or accumulated.endswith(chunk_text):
        return accumulated
    if chunk_text.startswith(accumulated):
        return chunk_text
    max_overlap = min(len(accumulated), len(chunk_text))
    for size in range(max_overlap, 0, -1):
        if accumulated[-size:] == chunk_text[:size]:
            return accumulated + chunk_text[size:]
    return accumulated + chunk_text


@dataclass(frozen=True)
class MicrophoneDeviceInfo:
    index: int
    name: str
    channels: int
    formats: int
    reserved: int


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


class MicrophoneSource(Protocol):
    def list_devices(self) -> list[MicrophoneDeviceInfo]: ...

    def record_utterance(
        self,
        output_path: Path,
        *,
        device_index: int | None = None,
        duration_seconds: float = DEFAULT_CAPTURE_SECONDS,
        vad_enabled: bool = True,
        vad_trailing_silence_seconds: float = DEFAULT_TRAILING_SILENCE_SECONDS,
    ) -> MicrophoneCaptureResult: ...


class SpeechToTextService(Protocol):
    def transcribe(self, wav_path: str | Path) -> str: ...


def _pcm16_mono_16k_from_pcm(
    pcm: bytes, *, sample_rate: int, channels: int
) -> "Any":
    import numpy as np

    if sample_rate <= 0 or channels <= 0 or not pcm:
        return np.zeros(0, dtype=np.int16)
    samples = np.frombuffer(pcm, dtype="<i2")
    if samples.size == 0:
        return np.zeros(0, dtype=np.int16)
    usable = samples.size - (samples.size % channels)
    samples = samples[:usable]
    if channels > 1:
        samples = samples.reshape(-1, channels).astype(np.float32)
        mono = np.mean(samples, axis=1)
    else:
        mono = samples.astype(np.float32)
    if sample_rate != DEFAULT_SAMPLE_RATE:
        divisor = math.gcd(int(sample_rate), DEFAULT_SAMPLE_RATE)
        up = DEFAULT_SAMPLE_RATE // divisor
        down = int(sample_rate) // divisor
        try:
            from scipy.signal import resample_poly

            mono = resample_poly(mono, up, down)
        except Exception:
            target_count = max(
                1, int(round(mono.size * DEFAULT_SAMPLE_RATE / sample_rate))
            )
            positions = np.linspace(0.0, max(0, mono.size - 1), target_count)
            mono = np.interp(positions, np.arange(mono.size), mono)
    return np.clip(np.round(mono), -32768, 32767).astype(np.int16)


class FunASRStreamingSession:
    def __init__(self, service: "FunASRLocalASRService") -> None:
        self._service = service
        self._model = service._load_model()
        self._chunk_size = list(service.chunk_size)
        self._chunk_stride = max(1, int(self._chunk_size[1] * 960))
        self._cache: dict[str, Any] = {}
        self._buffer = []
        self._utterance_text = ""

    def feed_pcm(self, pcm: bytes, *, sample_rate: int, channels: int) -> str:
        import numpy as np

        normalized = _pcm16_mono_16k_from_pcm(
            pcm, sample_rate=sample_rate, channels=channels
        )
        if normalized.size == 0:
            return self._utterance_text
        if self._buffer:
            self._buffer.append(normalized)
            pending = np.concatenate(self._buffer)
        else:
            pending = normalized
        self._buffer = [pending]
        emitted = self._run_chunks(is_final=False)
        return emitted or self._utterance_text

    def finish(self) -> str:
        emitted = self._run_chunks(is_final=True)
        final = emitted or self._utterance_text
        self.reset()
        return final

    def reset(self) -> None:
        self._cache = {}
        self._buffer = []
        self._utterance_text = ""

    def _run_chunks(self, *, is_final: bool) -> str:
        import numpy as np

        if not self._buffer:
            return self._utterance_text
        pending = self._buffer[0]
        if pending.size == 0:
            return self._utterance_text
        emitted = ""
        while pending.size >= self._chunk_stride or is_final:
            if pending.size == 0:
                break
            if pending.size >= self._chunk_stride:
                chunk = pending[: self._chunk_stride]
                pending = pending[self._chunk_stride :]
            else:
                chunk = pending
                pending = np.zeros(0, dtype=np.int16)
            audio = chunk.astype(np.float32) / 32768.0
            result = self._model.generate(
                input=audio,
                cache=self._cache,
                is_final=is_final and pending.size == 0,
                chunk_size=self._chunk_size,
                encoder_chunk_look_back=self._service.encoder_chunk_look_back,
                decoder_chunk_look_back=self._service.decoder_chunk_look_back,
            )
            if result:
                text = str(result[0].get("text") or "").strip()
                if text:
                    self._utterance_text = _merge_streaming_text(
                        self._utterance_text, text
                    )
                    emitted = self._utterance_text
            if is_final:
                if pending.size == 0:
                    break
            else:
                break
        self._buffer = [pending] if pending.size else []
        return emitted


class FunASRLocalASRService:
    """Local FunASR wrapper for Paraformer-zh-streaming."""

    def __init__(
        self,
        *,
        model: str = "paraformer-zh-streaming",
        hub: str = "ms",
        device: str = "cpu",
        ncpu: int = 4,
        chunk_size: tuple[int, int, int] = (0, 10, 5),
        encoder_chunk_look_back: int = 4,
        decoder_chunk_look_back: int = 1,
    ) -> None:
        self.model_id = str(model or "paraformer-zh-streaming").strip()
        self.hub = str(hub or "ms").strip() or "ms"
        self.device = str(device or "cpu").strip() or "cpu"
        self.ncpu = max(1, int(ncpu))
        self.chunk_size = tuple(int(value) for value in chunk_size)
        self.encoder_chunk_look_back = int(encoder_chunk_look_back)
        self.decoder_chunk_look_back = int(decoder_chunk_look_back)
        self._model: Any | None = None

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from funasr import AutoModel
        except Exception as exc:  # pragma: no cover - import-time environment issue
            raise RuntimeError(
                "funasr is not installed. Run: pip install funasr soundfile"
            ) from exc
        self._model = AutoModel(
            model=self.model_id,
            hub=self.hub,
            device=self.device,
            ncpu=self.ncpu,
            disable_update=True,
            disable_pbar=True,
            trust_remote_code=False,
        )
        return self._model

    def warmup(self) -> None:
        """Load the local ASR model before live audio starts arriving."""
        model = self._load_model()
        generate = getattr(model, "generate", None)
        if not callable(generate):
            return
        import numpy as np

        cache: dict[str, Any] = {}
        silence = np.zeros(max(1, int(self.chunk_size[1] * 960)), dtype=np.float32)
        generate(
            input=silence,
            cache=cache,
            is_final=True,
            chunk_size=list(self.chunk_size),
            encoder_chunk_look_back=self.encoder_chunk_look_back,
            decoder_chunk_look_back=self.decoder_chunk_look_back,
        )

    def transcribe(self, wav_path: str | Path) -> str:
        model = self._load_model()
        try:
            import numpy as np
            import soundfile as sf
        except Exception as exc:  # pragma: no cover - import-time environment issue
            raise RuntimeError(
                "soundfile/numpy are required for local FunASR transcription"
            ) from exc

        audio, sample_rate = sf.read(str(wav_path), dtype="float32")
        if getattr(audio, "ndim", 1) > 1:
            audio = np.asarray(audio, dtype=np.float32).mean(axis=1)
        else:
            audio = np.asarray(audio, dtype=np.float32)
        if audio.size == 0:
            return ""
        if sample_rate != DEFAULT_SAMPLE_RATE:
            target_count = max(1, int(round(audio.size * DEFAULT_SAMPLE_RATE / sample_rate)))
            positions = np.linspace(0.0, max(0, audio.size - 1), target_count)
            audio = np.interp(positions, np.arange(audio.size), audio).astype(np.float32)

        chunk_size = self.chunk_size
        chunk_stride = max(1, int(chunk_size[1] * 960))
        total_chunks = max(1, (len(audio) - 1) // chunk_stride + 1)
        cache: dict[str, Any] = {}
        utterance_text = ""
        for index in range(total_chunks):
            chunk = audio[index * chunk_stride : (index + 1) * chunk_stride]
            if chunk.size == 0:
                continue
            result = model.generate(
                input=chunk,
                cache=cache,
                is_final=index == total_chunks - 1,
                chunk_size=list(chunk_size),
                encoder_chunk_look_back=self.encoder_chunk_look_back,
                decoder_chunk_look_back=self.decoder_chunk_look_back,
            )
            if result:
                text = str(result[0].get("text") or "").strip()
                if text:
                    utterance_text = _merge_streaming_text(utterance_text, text)
        return utterance_text


class Go2ASRAudioBridge:
    def __init__(
        self,
        *,
        asr_service: FunASRLocalASRService,
        session_manager: LocalVoiceSessionManager,
        printer: Callable[[str], None] = print,
        vad_min_capture_seconds: float = 0.8,
        vad_trailing_silence_seconds: float | None = None,
        vad_preroll_seconds: float = 0.25,
        queue_size: int = 200,
        is_playback_active: Callable[[], bool] | None = None,
        debug_audio_dir: str | Path | None = None,
        debug_audio_seconds: float | None = None,
        voice_debug: bool | None = None,
        post_playback_quiet_min_mute_seconds: float | None = None,
        post_playback_quiet_seconds: float | None = None,
        post_playback_quiet_max_seconds: float | None = None,
    ) -> None:
        self.asr_service = asr_service
        self.session_manager = session_manager
        self._printer = printer
        if voice_debug is None:
            voice_debug = (
                str(os.environ.get("GO2_VOICE_DEBUG", "0")).strip().lower()
                in {"1", "true", "yes", "on"}
            )
        self._voice_debug = bool(voice_debug)
        self.vad_min_capture_seconds = max(0.2, float(vad_min_capture_seconds))
        if vad_trailing_silence_seconds is None:
            vad_trailing_silence_seconds = float(
                os.environ.get("GO2_ASR_VAD_TRAILING_SILENCE_SECONDS", "0.9")
                or 0.9
            )
        self.vad_trailing_silence_seconds = max(
            0.2, float(vad_trailing_silence_seconds)
        )
        self.vad_preroll_seconds = max(0.0, float(vad_preroll_seconds))
        self._vad_event_log_enabled = (
            self._voice_debug
            or str(os.environ.get("GO2_ASR_VAD_LOG", "0")).strip().lower()
            in {"1", "true", "yes", "on"}
        )
        self._queue: "queue.Queue[tuple[bytes, int, int] | None]" = queue.Queue(
            maxsize=max(1, int(queue_size))
        )
        self._is_playback_active = is_playback_active or (lambda: False)
        self._post_playback_quiet_min_mute_seconds = (
            POST_PLAYBACK_QUIET_MIN_MUTE_SECONDS
            if post_playback_quiet_min_mute_seconds is None
            else max(0.0, float(post_playback_quiet_min_mute_seconds))
        )
        self._post_playback_quiet_seconds = (
            POST_PLAYBACK_QUIET_SECONDS
            if post_playback_quiet_seconds is None
            else max(0.0, float(post_playback_quiet_seconds))
        )
        self._post_playback_quiet_max_seconds = (
            POST_PLAYBACK_QUIET_MAX_SECONDS
            if post_playback_quiet_max_seconds is None
            else max(0.2, float(post_playback_quiet_max_seconds))
        )
        self._post_playback_quiet_lock = threading.Lock()
        self._post_playback_quiet_active = False
        self._post_playback_quiet_started_at = 0.0
        self._post_playback_quiet_last_muted_at = 0.0
        self._post_playback_quiet_samples = 0
        self._last_final_text = ""
        if debug_audio_seconds is None:
            debug_audio_seconds = float(os.environ.get("GO2_ASR_DEBUG_AUDIO_SECONDS", "0") or 0)
        if debug_audio_dir is None:
            debug_audio_dir = os.environ.get(
                "GO2_ASR_DEBUG_AUDIO_DIR",
                str(Path("data") / "diagnostics" / "go2_asr"),
            )
        self._debug_audio_seconds = max(0.0, float(debug_audio_seconds))
        self._debug_audio_dir = Path(debug_audio_dir)
        self._debug_audio_prefix = ""
        self._debug_raw_writer: wave.Wave_write | None = None
        self._debug_normalized_writer: wave.Wave_write | None = None
        self._debug_raw_path: Path | None = None
        self._debug_normalized_path: Path | None = None
        self._debug_raw_samples_written = 0
        self._debug_normalized_samples_written = 0
        self._debug_raw_sample_rate = 0
        self._debug_raw_channels = 0
        self._debug_audio_complete = False
        self._stop = threading.Event()
        self._reset_stream = threading.Event()
        self._thread = threading.Thread(target=self._run, name="go2-asr-bridge", daemon=True)
        self._thread_started = False
        self._drop_lock = threading.Lock()
        self._drop_counts: dict[str, int] = {}
        self._last_drop_log_at = 0.0
        self._drop_log_interval_seconds = 1.0

    def start(self) -> None:
        if self._thread_started:
            return
        self._thread_started = True
        self._thread.start()

    def is_alive(self) -> bool:
        return self._thread_started and self._thread.is_alive()

    def warmup(self) -> None:
        self._printer("[ASR] warmup_start")
        warmup = getattr(self.asr_service, "warmup", None)
        if callable(warmup):
            warmup()
        self._printer("[ASR] warmup_ready")

    def stop(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread_started:
            self._thread.join(timeout=2.0)
        self._flush_drop_summary(force=True)

    def clear_pending_audio(self) -> int:
        self._reset_stream.set()
        drained = 0
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                try:
                    self._queue.put_nowait(None)
                except queue.Full:
                    pass
                break
            drained += 1
        if drained:
            self._printer(f"[ASR] cleared_stale_pcm_frames: {drained}")
        return drained

    def arm_post_playback_quiet_gate(self) -> None:
        now = time.monotonic()
        with self._post_playback_quiet_lock:
            was_active = self._post_playback_quiet_active
            self._post_playback_quiet_active = True
            self._post_playback_quiet_started_at = now
            self._post_playback_quiet_last_muted_at = now
            self._post_playback_quiet_samples = 0
        if not was_active:
            self._debug("[ASR] post_playback_quiet_gate armed")

    def push_pcm(self, pcm: bytes, sample_rate: int, channels: int) -> None:
        if self._stop.is_set() or not pcm:
            return
        if self._is_playback_active():
            self._record_drop("playback_active")
            return
        try:
            self._queue.put_nowait((bytes(pcm), int(sample_rate), int(channels)))
        except queue.Full:
            self._record_drop("queue_full")

    def _publish_final(self, final: str) -> None:
        normalized = str(final or "").strip()
        if not normalized:
            return
        if self._is_playback_active():
            self._debug(f"[ASR] final_dropped: playback_active ({normalized})")
            return
        if _normalize_text(normalized) == _normalize_text(self._last_final_text):
            self._debug(f"[ASR] duplicate_final_ignored: {normalized}")
            return
        self._last_final_text = normalized
        self._debug(f"[ASR] {normalized}")
        try:
            self.session_manager.process_transcript(normalized)
        except Exception as exc:
            self._printer(
                "[ASR] final_publish_failed: "
                f"{type(exc).__name__}: {exc}"
            )

    def _record_drop(self, reason: str) -> None:
        with self._drop_lock:
            self._drop_counts[reason] = self._drop_counts.get(reason, 0) + 1
            now = time.monotonic()
            if now - self._last_drop_log_at >= self._drop_log_interval_seconds:
                self._flush_drop_summary_locked(now=now)

    def _flush_drop_summary(self, *, force: bool = False) -> None:
        with self._drop_lock:
            now = time.monotonic()
            if force or now - self._last_drop_log_at >= self._drop_log_interval_seconds:
                self._flush_drop_summary_locked(now=now)

    def _flush_drop_summary_locked(self, *, now: float) -> None:
        for reason, count in list(self._drop_counts.items()):
            if count <= 0:
                continue
            if reason == "playback_active":
                self._printer(f"[ASR] playback_muted dropped={count} frames")
            elif reason == "queue_full":
                self._printer(f"[ASR] queue_full dropped={count} frames")
            else:
                self._printer(f"[ASR] dropped_pcm_frame reason={reason} dropped={count} frames")
            self._drop_counts[reason] = 0
        self._last_drop_log_at = now

    def _debug(self, message: str) -> None:
        if self._voice_debug:
            self._printer(message)

    def _log_vad_event(self, message: str) -> None:
        if self._vad_event_log_enabled:
            self._printer(message)

    def _should_hold_post_playback_quiet_gate(
        self,
        *,
        frame_rms: float,
        frame_peak: int,
        frame_samples: int,
        sample_rate: int,
    ) -> bool:
        with self._post_playback_quiet_lock:
            if not self._post_playback_quiet_active:
                return False
            now = time.monotonic()
            elapsed = max(0.0, now - self._post_playback_quiet_started_at)
            if elapsed >= self._post_playback_quiet_max_seconds:
                self._post_playback_quiet_active = False
                self._post_playback_quiet_samples = 0
                self._debug("[ASR] post_playback_quiet_gate max_wait_elapsed")
                return False
            quiet = (
                frame_rms <= POST_PLAYBACK_QUIET_RMS_THRESHOLD
                and frame_peak <= POST_PLAYBACK_QUIET_PEAK_THRESHOLD
            )
            since_muted = max(0.0, now - self._post_playback_quiet_last_muted_at)
            if since_muted < self._post_playback_quiet_min_mute_seconds:
                self._post_playback_quiet_samples = 0
                return True
            if quiet:
                self._post_playback_quiet_samples += int(frame_samples)
            else:
                self._post_playback_quiet_samples = 0
                return True
            quiet_ms = (
                self._post_playback_quiet_samples * 1000.0 / max(1, sample_rate)
            )
            if quiet_ms < self._post_playback_quiet_seconds * 1000.0:
                return True
            self._post_playback_quiet_active = False
            self._post_playback_quiet_samples = 0
            self._debug(
                "[ASR] post_playback_quiet_gate ready "
                f"quiet_ms={int(round(quiet_ms))}"
            )
            return True

    def _write_debug_audio(
        self,
        *,
        raw_pcm: bytes,
        raw_sample_rate: int,
        raw_channels: int,
        normalized_pcm: bytes,
    ) -> None:
        if self._debug_audio_seconds <= 0:
            return
        if self._debug_audio_complete:
            return
        if not raw_pcm and not normalized_pcm:
            return
        try:
            if self._debug_raw_writer is None or self._debug_normalized_writer is None:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                self._debug_audio_dir.mkdir(parents=True, exist_ok=True)
                self._debug_audio_prefix = timestamp
                self._debug_raw_sample_rate = int(raw_sample_rate)
                self._debug_raw_channels = int(raw_channels)
                raw_path = self._debug_audio_dir / f"go2_raw_{timestamp}.wav"
                normalized_path = self._debug_audio_dir / f"go2_16k_mono_{timestamp}.wav"
                self._debug_raw_path = raw_path
                self._debug_normalized_path = normalized_path
                self._debug_raw_writer = wave.open(str(raw_path), "wb")
                self._debug_raw_writer.setnchannels(self._debug_raw_channels)
                self._debug_raw_writer.setsampwidth(2)
                self._debug_raw_writer.setframerate(self._debug_raw_sample_rate)
                self._debug_normalized_writer = wave.open(str(normalized_path), "wb")
                self._debug_normalized_writer.setnchannels(1)
                self._debug_normalized_writer.setsampwidth(2)
                self._debug_normalized_writer.setframerate(DEFAULT_SAMPLE_RATE)
                self._printer(
                    "[ASR_AUDIO_DEBUG] recording "
                    f"raw={raw_path} normalized={normalized_path} "
                    f"seconds={self._debug_audio_seconds:g}"
                )
            if (
                self._debug_raw_writer is not None
                and raw_sample_rate == self._debug_raw_sample_rate
                and raw_channels == self._debug_raw_channels
            ):
                raw_limit = int(self._debug_audio_seconds * self._debug_raw_sample_rate)
                raw_samples = len(raw_pcm) // 2 // max(1, raw_channels)
                raw_remaining = max(0, raw_limit - self._debug_raw_samples_written)
                raw_take = min(raw_samples, raw_remaining)
                if raw_take > 0:
                    self._debug_raw_writer.writeframes(
                        raw_pcm[: raw_take * max(1, raw_channels) * 2]
                    )
                    self._debug_raw_samples_written += raw_take
            if self._debug_normalized_writer is not None:
                normalized_limit = int(self._debug_audio_seconds * DEFAULT_SAMPLE_RATE)
                normalized_samples = len(normalized_pcm) // 2
                normalized_remaining = max(
                    0, normalized_limit - self._debug_normalized_samples_written
                )
                normalized_take = min(normalized_samples, normalized_remaining)
                if normalized_take > 0:
                    self._debug_normalized_writer.writeframes(
                        normalized_pcm[: normalized_take * 2]
                    )
                    self._debug_normalized_samples_written += normalized_take
            raw_limit = int(self._debug_audio_seconds * max(1, self._debug_raw_sample_rate))
            normalized_limit = int(self._debug_audio_seconds * DEFAULT_SAMPLE_RATE)
            if (
                self._debug_raw_samples_written >= raw_limit
                and self._debug_normalized_samples_written >= normalized_limit
            ):
                raw_path = self._debug_raw_path
                normalized_path = self._debug_normalized_path
                self._close_debug_audio()
                self._debug_audio_complete = True
                self._printer(
                    "[ASR_AUDIO_DEBUG] ready "
                    f"raw={raw_path} normalized={normalized_path}"
                )
        except Exception as exc:
            self._printer(f"[ASR_AUDIO_DEBUG] failed: {exc}")
            self._close_debug_audio()

    def _close_debug_audio(self) -> None:
        for writer in (self._debug_raw_writer, self._debug_normalized_writer):
            if writer is not None:
                try:
                    writer.close()
                except Exception:
                    pass
        self._debug_raw_writer = None
        self._debug_normalized_writer = None

    def _run(self) -> None:
        import numpy as np

        current_stream: FunASRStreamingSession | None = None
        speech_detected = False
        sample_rate = 0
        channels = 0
        sample_count = 0
        voice_sample_count = 0
        utterance_sample_count = 0
        trailing_silence_samples = 0
        min_capture_samples = 0
        silence_samples_limit = 0
        preroll_samples_limit = 0
        preroll_sample_count = 0
        preroll_frames: deque[Any] = deque()
        noise_rms: list[float] = []
        noise_peak: list[int] = []
        last_transcript = ""
        normalizer_probe_logged = False
        silence_reported_ms = 0

        def reset_session() -> None:
            nonlocal current_stream, speech_detected, sample_rate, channels
            nonlocal sample_count, voice_sample_count, trailing_silence_samples
            nonlocal utterance_sample_count, min_capture_samples, silence_samples_limit
            nonlocal preroll_samples_limit, preroll_sample_count, preroll_frames
            nonlocal noise_rms, noise_peak, last_transcript, silence_reported_ms
            current_stream = None
            speech_detected = False
            sample_rate = 0
            channels = 0
            sample_count = 0
            voice_sample_count = 0
            utterance_sample_count = 0
            trailing_silence_samples = 0
            min_capture_samples = 0
            silence_samples_limit = 0
            preroll_samples_limit = 0
            preroll_sample_count = 0
            preroll_frames = deque()
            noise_rms = []
            noise_peak = []
            last_transcript = ""
            silence_reported_ms = 0

        try:
            while not self._stop.is_set():
                if self._reset_stream.is_set():
                    self._reset_stream.clear()
                    reset_session()
                    self._debug("[ASR] streaming_state_reset")
                item = self._queue.get()
                if item is None:
                    break
                if self._reset_stream.is_set():
                    self._reset_stream.clear()
                    reset_session()
                    self._debug("[ASR] streaming_state_reset")
                pcm, frame_rate, frame_channels = item
                normalized = _pcm16_mono_16k_from_pcm(
                    pcm, sample_rate=frame_rate, channels=frame_channels
                )
                if normalized.size == 0:
                    self.session_manager.expire_if_timed_out()
                    continue
                self._write_debug_audio(
                    raw_pcm=pcm,
                    raw_sample_rate=frame_rate,
                    raw_channels=frame_channels,
                    normalized_pcm=normalized.tobytes(),
                )
                if sample_rate == 0:
                    sample_rate = DEFAULT_SAMPLE_RATE
                    channels = 1
                    min_capture_samples = int(sample_rate * self.vad_min_capture_seconds)
                    silence_samples_limit = int(
                        sample_rate * self.vad_trailing_silence_seconds
                    )
                    preroll_samples_limit = int(sample_rate * self.vad_preroll_seconds)
                    if not normalizer_probe_logged:
                        normalizer_probe_logged = True
                        self._debug(
                            "[ASR_AUDIO] normalized "
                            f"source_sample_rate={frame_rate} source_channels={frame_channels} "
                            f"sample_rate={sample_rate} channels={channels} "
                            f"chunk_samples={normalized.size}"
                        )

                frame_peak = int(np.max(np.abs(normalized.astype(np.int32)))) if normalized.size else 0
                frame_rms = (
                    float(np.sqrt(np.mean(normalized.astype(np.float64) ** 2)))
                    if normalized.size
                    else 0.0
                )
                if self._should_hold_post_playback_quiet_gate(
                    frame_rms=frame_rms,
                    frame_peak=frame_peak,
                    frame_samples=int(normalized.size),
                    sample_rate=sample_rate,
                ):
                    self.session_manager.expire_if_timed_out()
                    continue
                sample_count += int(normalized.size)
                if noise_rms:
                    rms_threshold = max(650.0, (sum(noise_rms) / len(noise_rms)) * 1.55)
                    peak_threshold = max(1800.0, (sum(noise_peak) / len(noise_peak)) * 1.5)
                else:
                    rms_threshold = 650.0
                    peak_threshold = 1800.0
                voiced = frame_rms >= rms_threshold or frame_peak >= peak_threshold
                calibrating = (
                    not speech_detected and sample_count <= int(sample_rate * 0.4)
                )
                if calibrating and not voiced:
                    noise_rms.append(frame_rms)
                    noise_peak.append(frame_peak)
                    if preroll_samples_limit > 0:
                        preroll_frames.append(normalized.copy())
                        preroll_sample_count += int(normalized.size)
                        while (
                            preroll_sample_count > preroll_samples_limit
                            and preroll_frames
                        ):
                            dropped = preroll_frames.popleft()
                            preroll_sample_count -= int(dropped.size)
                    self.session_manager.expire_if_timed_out()
                    continue
                if voiced and not speech_detected:
                    speech_detected = True
                    voice_sample_count = sample_count
                    trailing_silence_samples = 0
                    silence_reported_ms = 0
                    self._log_vad_event(
                        "[VAD] speech_start "
                        f"rms={frame_rms:.1f} peak={frame_peak} "
                        f"rms_threshold={rms_threshold:.1f} "
                        f"peak_threshold={peak_threshold:.1f}"
                    )
                    current_stream = FunASRStreamingSession(self.asr_service)
                    for preroll in preroll_frames:
                        partial = current_stream.feed_pcm(
                            preroll.tobytes(), sample_rate=sample_rate, channels=channels
                        )
                        utterance_sample_count += int(preroll.size)
                        if partial and partial != last_transcript:
                            last_transcript = partial
                            self._debug(f"[ASR_PARTIAL] {partial}")
                    preroll_frames.clear()
                    preroll_sample_count = 0
                elif voiced:
                    voice_sample_count = sample_count
                    trailing_silence_samples = 0
                    silence_reported_ms = 0
                elif speech_detected:
                    trailing_silence_samples += int(normalized.size)
                    silence_ms = int(
                        round(trailing_silence_samples * 1000 / sample_rate)
                    )
                    if (
                        silence_ms < int(self.vad_trailing_silence_seconds * 1000)
                        and silence_ms >= silence_reported_ms + 200
                    ):
                        silence_reported_ms = silence_ms
                        self._log_vad_event(f"[VAD] short_silence {silence_ms}ms")
                else:
                    if preroll_samples_limit > 0:
                        preroll_frames.append(normalized.copy())
                        preroll_sample_count += int(normalized.size)
                        while (
                            preroll_sample_count > preroll_samples_limit
                            and preroll_frames
                        ):
                            dropped = preroll_frames.popleft()
                            preroll_sample_count -= int(dropped.size)
                    self.session_manager.expire_if_timed_out()
                    continue
                if current_stream is None:
                    continue
                partial = current_stream.feed_pcm(
                    normalized.tobytes(), sample_rate=sample_rate, channels=1
                )
                utterance_sample_count += int(normalized.size)
                if partial and partial != last_transcript:
                    last_transcript = partial
                    self._debug(f"[ASR_PARTIAL] {partial}")
                if (
                    speech_detected
                    and utterance_sample_count >= min_capture_samples
                    and trailing_silence_samples >= silence_samples_limit
                ):
                    silence_ms = int(
                        round(trailing_silence_samples * 1000 / sample_rate)
                    )
                    utterance_ms = int(
                        round(utterance_sample_count * 1000 / sample_rate)
                    )
                    self._log_vad_event(
                        "[UTTERANCE] finalize "
                        f"silence_ms={silence_ms} utterance_ms={utterance_ms}"
                    )
                    final = current_stream.finish() or last_transcript
                    self._publish_final(final)
                    reset_session()
                    continue
                if not speech_detected:
                    self.session_manager.expire_if_timed_out()
            if current_stream is not None:
                final = current_stream.finish() or last_transcript
                self._publish_final(final)
        except Exception as exc:
            self._printer(f"[ASR] bridge worker crashed: {type(exc).__name__}: {exc}")
        finally:
            self._flush_drop_summary(force=True)
            self._close_debug_audio()


class WindowsWaveInMicrophoneSource:
    class WAVEFORMATEX(ctypes.Structure):
        _fields_ = [
            ("wFormatTag", ctypes.c_ushort),
            ("nChannels", ctypes.c_ushort),
            ("nSamplesPerSec", ctypes.c_uint32),
            ("nAvgBytesPerSec", ctypes.c_uint32),
            ("nBlockAlign", ctypes.c_ushort),
            ("wBitsPerSample", ctypes.c_ushort),
            ("cbSize", ctypes.c_ushort),
        ]

    class WAVEINCAPSW(ctypes.Structure):
        _fields_ = [
            ("wMid", ctypes.c_ushort),
            ("wPid", ctypes.c_ushort),
            ("vDriverVersion", ctypes.c_uint32),
            ("szPname", ctypes.c_wchar * 32),
            ("dwFormats", ctypes.c_uint32),
            ("wChannels", ctypes.c_ushort),
            ("wReserved1", ctypes.c_ushort),
        ]

    class WAVEHDR(ctypes.Structure):
        _fields_ = [
            ("lpData", ctypes.c_void_p),
            ("dwBufferLength", ctypes.c_uint32),
            ("dwBytesRecorded", ctypes.c_uint32),
            ("dwUser", ctypes.c_size_t),
            ("dwFlags", ctypes.c_uint32),
            ("dwLoops", ctypes.c_uint32),
            ("lpNext", ctypes.c_void_p),
            ("reserved", ctypes.c_size_t),
        ]

    HWAVEIN = ctypes.c_void_p
    LPHWAVEIN = ctypes.POINTER(HWAVEIN)
    WAVEINPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
        None,
        HWAVEIN,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )

    def __init__(
        self,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        bits_per_sample: int = DEFAULT_BITS_PER_SAMPLE,
        buffer_ms: int = DEFAULT_BUFFER_MS,
    ) -> None:
        if os.name != "nt":
            raise OSError("WindowsWaveInMicrophoneSource is only available on Windows")
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.bits_per_sample = int(bits_per_sample)
        self.buffer_ms = max(20, int(buffer_ms))
        self._winmm = ctypes.windll.winmm
        self._wave_in_proc: Any | None = None
        self._active_handle: WindowsWaveInMicrophoneSource.HWAVEIN | None = None
        self._buffers: list[dict[str, object]] = []
        self._frames: list[bytes] = []
        self._lock = threading.Lock()
        self._complete = threading.Event()
        self._speech_detected = False
        self._sample_count = 0
        self._frame_count = 0
        self._last_voice_sample = 0
        self._noise_rms: list[float] = []
        self._noise_peak: list[int] = []
        self._speech_sample_floor = 0
        self._silence_sample_floor = 0
        self._target_samples = 0
        self._endpoint_reason = "fixed_duration"
        self._vad_enabled = False
        self._device_index: int | None = None

        self._winmm.waveInGetNumDevs.restype = ctypes.c_uint32
        self._winmm.waveInGetDevCapsW.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(self.WAVEINCAPSW),
            ctypes.c_uint32,
        ]
        self._winmm.waveInGetDevCapsW.restype = ctypes.c_uint32
        self._winmm.waveInOpen.argtypes = [
            ctypes.POINTER(self.HWAVEIN),
            ctypes.c_uint32,
            ctypes.POINTER(self.WAVEFORMATEX),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        self._winmm.waveInOpen.restype = ctypes.c_uint32
        self._winmm.waveInPrepareHeader.argtypes = [
            self.HWAVEIN,
            ctypes.POINTER(self.WAVEHDR),
            ctypes.c_uint32,
        ]
        self._winmm.waveInPrepareHeader.restype = ctypes.c_uint32
        self._winmm.waveInAddBuffer.argtypes = [
            self.HWAVEIN,
            ctypes.POINTER(self.WAVEHDR),
            ctypes.c_uint32,
        ]
        self._winmm.waveInAddBuffer.restype = ctypes.c_uint32
        self._winmm.waveInStart.argtypes = [self.HWAVEIN]
        self._winmm.waveInStart.restype = ctypes.c_uint32
        self._winmm.waveInStop.argtypes = [self.HWAVEIN]
        self._winmm.waveInStop.restype = ctypes.c_uint32
        self._winmm.waveInReset.argtypes = [self.HWAVEIN]
        self._winmm.waveInReset.restype = ctypes.c_uint32
        self._winmm.waveInUnprepareHeader.argtypes = [
            self.HWAVEIN,
            ctypes.POINTER(self.WAVEHDR),
            ctypes.c_uint32,
        ]
        self._winmm.waveInUnprepareHeader.restype = ctypes.c_uint32
        self._winmm.waveInClose.argtypes = [self.HWAVEIN]
        self._winmm.waveInClose.restype = ctypes.c_uint32

    def list_devices(self) -> list[MicrophoneDeviceInfo]:
        count = int(self._winmm.waveInGetNumDevs())
        devices: list[MicrophoneDeviceInfo] = []
        for index in range(count):
            caps = self.WAVEINCAPSW()
            result = self._winmm.waveInGetDevCapsW(
                ctypes.c_uint32(index),
                ctypes.byref(caps),
                ctypes.c_uint32(ctypes.sizeof(caps)),
            )
            if result != 0:
                continue
            devices.append(
                MicrophoneDeviceInfo(
                    index=index,
                    name=str(caps.szPname).rstrip("\x00").strip(),
                    channels=int(caps.wChannels),
                    formats=int(caps.dwFormats),
                    reserved=int(caps.wReserved1),
                )
            )
        return devices

    def record_utterance(
        self,
        output_path: Path,
        *,
        device_index: int | None = None,
        duration_seconds: float = DEFAULT_CAPTURE_SECONDS,
        vad_enabled: bool = True,
        vad_trailing_silence_seconds: float = DEFAULT_TRAILING_SILENCE_SECONDS,
    ) -> MicrophoneCaptureResult:
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        self._reset_state()
        self._vad_enabled = bool(vad_enabled)
        self._device_index = device_index
        self._speech_sample_floor = max(1, int(self.sample_rate * 0.2))
        self._silence_sample_floor = max(
            1, int(self.sample_rate * max(0.1, float(vad_trailing_silence_seconds)))
        )
        self._target_samples = max(1, int(self.sample_rate * float(duration_seconds)))
        self._endpoint_reason = "fixed_duration"

        handle = self.HWAVEIN()
        fmt = self.WAVEFORMATEX(
            wFormatTag=1,
            nChannels=self.channels,
            nSamplesPerSec=self.sample_rate,
            nAvgBytesPerSec=self.sample_rate * self.channels * (self.bits_per_sample // 8),
            nBlockAlign=self.channels * (self.bits_per_sample // 8),
            wBitsPerSample=self.bits_per_sample,
            cbSize=0,
        )
        callback = self._build_callback()
        self._wave_in_proc = callback
        device = (
            WAVE_MAPPER.value
            if device_index is None
            else ctypes.c_uint32(int(device_index)).value
        )
        open_result = self._winmm.waveInOpen(
            ctypes.byref(handle),
            ctypes.c_uint32(device),
            ctypes.byref(fmt),
            ctypes.cast(callback, ctypes.c_void_p),
            None,
            ctypes.c_uint32(CALLBACK_FUNCTION),
        )
        if open_result != 0:
            raise RuntimeError(f"waveInOpen failed with code {open_result}")
        self._active_handle = handle
        try:
            self._queue_buffers(handle, duration_seconds)
            start_result = self._winmm.waveInStart(handle)
            if start_result != 0:
                raise RuntimeError(f"waveInStart failed with code {start_result}")
            if not self._complete.wait(timeout=float(duration_seconds) + 1.5):
                self._endpoint_reason = "max_duration"
                self._complete.set()
            self._winmm.waveInStop(handle)
            self._winmm.waveInReset(handle)
        finally:
            self._finalize_capture(handle)
            self._active_handle = None
            self._wave_in_proc = None

        pcm = b"".join(self._frames)
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_path), "wb") as stream:
            stream.setnchannels(self.channels)
            stream.setsampwidth(self.bits_per_sample // 8)
            stream.setframerate(self.sample_rate)
            stream.writeframes(pcm)

        sample_count = self._sample_count
        rms = 0.0
        peak = 0
        if pcm:
            samples = array("h")
            samples.frombytes(pcm)
            if samples:
                if sys.byteorder != "little":
                    samples.byteswap()
                peak = max(abs(item) for item in samples)
                total = sum(float(item) * float(item) for item in samples)
                rms = math.sqrt(total / len(samples))

        duration = 0.0 if self.sample_rate <= 0 else sample_count / float(self.sample_rate)
        trailing_silence = 0.0
        if self._speech_detected and self.sample_rate > 0:
            trailing_silence = max(
                0.0,
                (self._sample_count - self._last_voice_sample) / float(self.sample_rate),
            )
        return MicrophoneCaptureResult(
            path=str(output_path),
            sample_rate=self.sample_rate,
            channels=self.channels,
            duration_seconds=duration,
            sample_count=sample_count,
            frame_count=self._frame_count,
            peak=peak,
            rms=rms,
            byte_count=len(pcm),
            vad_enabled=self._vad_enabled,
            speech_detected=self._speech_detected,
            endpoint_reason=self._endpoint_reason,
            trailing_silence_seconds=trailing_silence,
        )

    def _reset_state(self) -> None:
        self._buffers = []
        self._frames = []
        self._complete = threading.Event()
        self._speech_detected = False
        self._sample_count = 0
        self._frame_count = 0
        self._last_voice_sample = 0
        self._noise_rms = []
        self._noise_peak = []
        self._speech_sample_floor = 0
        self._silence_sample_floor = 0
        self._target_samples = 0
        self._endpoint_reason = "fixed_duration"

    def _queue_buffers(self, handle: HWAVEIN, duration_seconds: float) -> None:
        buffer_size = max(1024, int(self.sample_rate * self.channels * (self.bits_per_sample // 8) * self.buffer_ms / 1000))
        buffer_count = max(4, int(math.ceil(duration_seconds * 1000.0 / self.buffer_ms)) + 2)
        self._buffers = []
        for _ in range(buffer_count):
            raw_buffer = ctypes.create_string_buffer(buffer_size)
            header = self.WAVEHDR()
            header.lpData = ctypes.addressof(raw_buffer)
            header.dwBufferLength = buffer_size
            header.dwBytesRecorded = 0
            header.dwUser = 0
            header.dwFlags = 0
            header.dwLoops = 0
            header.lpNext = None
            header.reserved = 0
            prepare = self._winmm.waveInPrepareHeader(
                handle, ctypes.byref(header), ctypes.sizeof(header)
            )
            if prepare != 0:
                raise RuntimeError(f"waveInPrepareHeader failed with code {prepare}")
            add = self._winmm.waveInAddBuffer(
                handle, ctypes.byref(header), ctypes.sizeof(header)
            )
            if add != 0:
                raise RuntimeError(f"waveInAddBuffer failed with code {add}")
            self._buffers.append(
                {
                    "raw": raw_buffer,
                    "header": header,
                }
            )

    def _build_callback(self) -> Any:
        @self.WAVEINPROC
        def callback(
            _handle: HWAVEIN,
            message: int,
            _instance: ctypes.c_void_p,
            param1: ctypes.c_void_p,
            _param2: ctypes.c_void_p,
        ) -> None:
            if message != WIM_DATA or self._complete.is_set():
                return
            header = ctypes.cast(param1, ctypes.POINTER(self.WAVEHDR)).contents
            byte_count = int(header.dwBytesRecorded or 0)
            if byte_count <= 0:
                return
            pcm = ctypes.string_at(header.lpData, byte_count)
            with self._lock:
                self._frames.append(pcm)
                self._frame_count += 1
                samples = array("h")
                samples.frombytes(pcm)
                if sys.byteorder != "little":
                    samples.byteswap()
                if not samples:
                    return
                sample_abs = [abs(item) for item in samples]
                frame_peak = max(sample_abs)
                frame_rms = math.sqrt(
                    sum(float(item) * float(item) for item in samples) / len(samples)
                )
                self._sample_count += len(samples)
                if not self._vad_enabled:
                    if self._sample_count >= self._target_samples:
                        self._endpoint_reason = "fixed_duration"
                        self._complete.set()
                    return
                if not self._speech_detected and self._sample_count <= self.sample_rate * 0.2:
                    self._noise_rms.append(frame_rms)
                    self._noise_peak.append(frame_peak)
                    return
                if self._vad_enabled and self._noise_rms:
                    noise_rms = float(sum(self._noise_rms) / len(self._noise_rms))
                    noise_peak = float(sum(self._noise_peak) / len(self._noise_peak))
                    rms_threshold = max(650.0, noise_rms * 1.55)
                    peak_threshold = max(1800.0, noise_peak * 1.5)
                else:
                    rms_threshold = 650.0
                    peak_threshold = 1800.0
                voiced = frame_rms >= rms_threshold or frame_peak >= peak_threshold
                if voiced:
                    self._speech_detected = True
                    self._last_voice_sample = self._sample_count
                if (
                    self._speech_detected
                    and self._sample_count >= self._speech_sample_floor
                    and self._sample_count - self._last_voice_sample >= self._silence_sample_floor
                ):
                    self._endpoint_reason = "vad_trailing_silence"
                    self._complete.set()
                elif self._sample_count >= self._target_samples:
                    self._endpoint_reason = "max_duration"
                    self._complete.set()

        return callback

    def _finalize_capture(self, handle: HWAVEIN) -> None:
        for entry in self._buffers:
            header = ctypes.cast(ctypes.byref(entry["header"]), ctypes.POINTER(self.WAVEHDR))
            self._winmm.waveInUnprepareHeader(handle, header, ctypes.sizeof(self.WAVEHDR))
        self._winmm.waveInClose(handle)


@dataclass
class SessionState:
    session_id: str
    wake_word: str | None
    turn: int = 0
    business_turn: int = 0
    started_at: str = field(default_factory=_now_iso)


class ConsoleMockTransport(MockTransport):
    def __init__(self, *, printer: Callable[[str], None] = print) -> None:
        super().__init__()
        self._printer = printer

    def publish(self, message):  # type: ignore[override]
        super().publish(message)
        self._printer("[MOCK PUBLISH]")
        self._printer(f"topic = {message.topic}")
        self._printer(f"payload = {json.dumps(message.payload, ensure_ascii=False)}")


@dataclass(frozen=True)
class ClipManifestEntry:
    clip_id: str
    resource_id: str
    status: str = "ready"
    path: str | None = None


class ClipManifest:
    def __init__(self, entries: Mapping[str, Mapping[str, Any] | ClipManifestEntry]) -> None:
        self._entries: dict[str, ClipManifestEntry] = {}
        for clip_id, value in entries.items():
            normalized = str(clip_id or "").strip()
            if not normalized:
                continue
            if isinstance(value, ClipManifestEntry):
                entry = value
            else:
                entry = ClipManifestEntry(
                    clip_id=normalized,
                    resource_id=str(value.get("resource_id") or value.get("uuid") or normalized),
                    status=str(value.get("status") or "ready").strip().lower(),
                    path=(None if value.get("path") is None else str(value.get("path"))),
                )
            self._entries[normalized] = entry

    @classmethod
    def default(cls) -> "ClipManifest":
        entries: dict[str, dict[str, str]] = {
            "outing.allow": {"resource_id": "mock://outing.allow", "status": "ready"},
            "outing.start": {"resource_id": "mock://outing.start", "status": "ready"},
            "fall.confirm": {"resource_id": "mock://fall.confirm", "status": "ready"},
            "health.hr.prefix": {"resource_id": "mock://health.hr.prefix", "status": "ready"},
            "weather.temp.prefix": {"resource_id": "mock://weather.temp.prefix", "status": "ready"},
            "unit.bpm": {"resource_id": "mock://unit.bpm", "status": "ready"},
            "unit.celsius": {"resource_id": "mock://unit.celsius", "status": "ready"},
            "medication.reminder.before_outing": {
                "resource_id": "mock://medication.reminder.before_outing",
                "status": "ready",
            },
            "follow.stop": {"resource_id": "mock://follow.stop", "status": "ready"},
            "sess.wake_ack": {"resource_id": "mock://sess.wake_ack", "status": "ready"},
        }
        for value in range(0, 131):
            entries[f"num.{value}"] = {
                "resource_id": f"mock://num.{value}",
                "status": "ready",
            }
        return cls(entries)

    @classmethod
    def from_json_file(cls, path: str | Path) -> "ClipManifest":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("clips"), dict):
            return cls(payload["clips"])
        if isinstance(payload, dict) and isinstance(payload.get("files"), list):
            entries = {}
            for item in payload["files"]:
                if not isinstance(item, dict):
                    continue
                source_path = Path(str(item.get("path") or ""))
                if not source_path.name:
                    continue
                clip_id = source_path.stem.lower().replace("_", ".")
                entries[clip_id] = {
                    "resource_id": str(item.get("resource_id") or source_path.stem),
                    "status": str(item.get("status") or "ready"),
                    "path": str(item.get("path") or ""),
                }
            if "stop.companion" in entries and "follow.stop" not in entries:
                entries["follow.stop"] = entries["stop.companion"]
            if "wake.ready" in entries and "sess.wake_ack" not in entries:
                entries["sess.wake_ack"] = entries["wake.ready"]
            return cls(entries)
        if isinstance(payload, dict):
            return cls(payload)
        raise ValueError("clip manifest must be a JSON object")

    def resolve(self, clips: list[str]) -> tuple[list[ClipManifestEntry], list[str]]:
        resolved: list[ClipManifestEntry] = []
        missing: list[str] = []
        for clip in clips:
            clip_id = str(clip or "").strip()
            if not clip_id:
                continue
            entry = self._entries.get(clip_id)
            if entry is None or entry.status != "ready":
                missing.append(clip_id)
            else:
                resolved.append(entry)
        return resolved, missing

    def to_dict(self) -> dict[str, dict[str, str | None]]:
        return {
            key: {
                "resource_id": value.resource_id,
                "status": value.status,
                "path": value.path,
            }
            for key, value in self._entries.items()
        }


class ClipPlaybackController:
    def __init__(
        self,
        manifest: ClipManifest | None = None,
        *,
        executor: Callable[[ClipManifestEntry], Any] | None = None,
        set_playback_active: Callable[[bool], Any] | None = None,
    ) -> None:
        self.manifest = manifest or ClipManifest.default()
        self._executor = executor or (lambda _entry: None)
        self._set_playback_active = set_playback_active
        self._lock = threading.Lock()
        self._active_request_id: str | None = None
        self._playback_active = False

    def is_playback_active(self) -> bool:
        with self._lock:
            return self._playback_active

    def interrupt_current(self) -> None:
        with self._lock:
            if self._active_request_id is None:
                return
            self._active_request_id = None
            self._set_active_locked(False)

    def play_command(self, message: CommandMessage) -> dict[str, Any]:
        return self.play_clips(
            [str(item) for item in list(message.payload.get("clips") or [])],
            request_id=message.request_id,
            interrupt=bool(message.payload.get("interrupt", False)),
        )

    def play_clips(
        self,
        clips: list[str],
        *,
        request_id: str | None = None,
        interrupt: bool = False,
    ) -> dict[str, Any]:
        requested = [str(item).strip() for item in clips if str(item).strip()]
        resolved, missing = self.manifest.resolve(requested)
        if missing:
            return {
                "clips": requested,
                "played": 0,
                "status": "missing",
                "missing_clips": missing,
            }
        if not requested:
            return {"clips": [], "played": 0, "status": "error", "missing_clips": []}

        active_request_id = str(request_id or uuid.uuid4().hex)
        with self._lock:
            if self._active_request_id is not None and not interrupt:
                return {
                    "clips": requested,
                    "played": 0,
                    "status": "error",
                    "missing_clips": [],
                }
            if interrupt and self._active_request_id is not None:
                self._active_request_id = None
                self._set_active_locked(False)
            self._active_request_id = active_request_id
            self._set_active_locked(True)

        played = 0
        status = "done"
        try:
            for entry in resolved:
                with self._lock:
                    if self._active_request_id != active_request_id:
                        status = "interrupted"
                        break
                self._executor(entry)
                with self._lock:
                    if self._active_request_id != active_request_id:
                        status = "interrupted"
                        break
                played += 1
        except Exception:
            status = "error"
        finally:
            with self._lock:
                if self._active_request_id == active_request_id:
                    self._active_request_id = None
                    self._set_active_locked(False)

        return {
            "clips": requested,
            "played": played,
            "status": status,
            "missing_clips": [],
        }

    def _set_active_locked(self, active: bool) -> None:
        if self._playback_active == active:
            return
        self._playback_active = active
        if self._set_playback_active is not None:
            self._set_playback_active(active)


class LocalVoiceSessionManager:
    def __init__(
        self,
        transport: MessageTransport,
        *,
        device_id: str = DEFAULT_DEVICE_ID,
        topic_prefix: str = DEFAULT_TOPIC_PREFIX,
        source: str = SOURCE_NAME,
        session_timeout_seconds: float = DEFAULT_SESSION_TIMEOUT_SECONDS,
        max_turns: int = DEFAULT_SESSION_MAX_TURNS,
        emergency_bypass_enabled: bool = True,
        wake_only: bool = False,
        voice_debug: bool | None = None,
        printer: Callable[[str], None] = print,
        monotonic_clock: Callable[[], float] = time.monotonic,
        state_machine: BMachineStateMachine | None = None,
    ) -> None:
        self.transport = transport
        self.device_id = str(device_id or DEFAULT_DEVICE_ID).strip()
        self.topic_prefix = str(topic_prefix or DEFAULT_TOPIC_PREFIX).strip()
        self.source = str(source or SOURCE_NAME).strip()
        self.session_timeout_seconds = max(1.0, float(session_timeout_seconds))
        self.max_turns = max(1, int(max_turns))
        self._emergency_bypass_enabled = bool(emergency_bypass_enabled)
        self._wake_only = bool(wake_only)
        if voice_debug is None:
            voice_debug = (
                str(os.environ.get("GO2_VOICE_DEBUG", "0")).strip().lower()
                in {"1", "true", "yes", "on"}
            )
        self._voice_debug = bool(voice_debug)
        self._printer = printer
        self._clock = monotonic_clock
        self._state_machine = state_machine
        self._session: SessionState | None = None
        self._last_activity_monotonic: float | None = None
        self._reply_without_wake_until: float | None = None
        self._post_playback_guard_until: float | None = None
        self._listener_enabled = True
        self.voice_state = VoiceState.WAKE_GUARD
        try:
            self.transport.subscribe(
                contract_topic(self.device_id, "event", topic_prefix=self.topic_prefix),
                self._on_interaction_event,
            )
        except Exception:
            pass

    @property
    def active_session_id(self) -> str | None:
        return None if self._session is None else self._session.session_id

    @property
    def listener_enabled(self) -> bool:
        return self._listener_enabled

    def set_listener_enabled(self, enabled: bool) -> list[Any]:
        desired = bool(enabled)
        if desired == self._listener_enabled:
            self._printer(
                "[VOICE] LISTENER ACTIVE - waiting for Xiaokang"
                if desired
                else "[VOICE] LISTENER PAUSED"
            )
            return []
        if desired:
            self._listener_enabled = True
            self.voice_state = VoiceState.WAKE_GUARD
            self._reply_without_wake_until = None
            self._post_playback_guard_until = None
            self._printer("[VOICE] LISTENER ACTIVE - waiting for Xiaokang")
            return []
        self._listener_enabled = False
        self._reply_without_wake_until = None
        self._post_playback_guard_until = None
        ended = self._end_session("listener_paused")
        self.voice_state = VoiceState.PAUSED
        self._printer("[VOICE] LISTENER PAUSED")
        return ended

    def toggle_listener(self) -> tuple[bool, list[Any]]:
        messages = self.set_listener_enabled(not self._listener_enabled)
        return self._listener_enabled, messages

    def process_transcript(
        self,
        transcript: str,
        *,
        asr_confidence: float | None = None,
    ) -> list[Any]:
        normalized = str(transcript or "").strip()
        if not normalized:
            return []
        if not self._listener_enabled:
            self._debug(f"[VOICE] ignored: listener_paused ({normalized})")
            return []
        self.expire_if_timed_out()
        if self._emergency_bypass_enabled:
            emergency, phrase = _is_emergency_text(normalized)
            if emergency:
                self._printer(f"[EMERGENCY] bypass wake: {phrase}")
                return self._publish_speech(
                    normalized,
                    bypass_wake=True,
                    wake_word=None,
                    asr_confidence=asr_confidence,
                    emergency=True,
                )

        wake_word, stripped = WakeWordMatcher.strip_wake_word(normalized)
        created_session = False
        if wake_word is not None and self._session is None:
            self._start_session(wake_word)
            created_session = True
        if wake_word is not None and self._session is not None and self._session.wake_word is None:
            self._session.wake_word = wake_word
        if wake_word is not None and self._state_machine is not None:
            self._state_machine.on_wake()

        if self._wake_only:
            if wake_word is not None:
                self._printer(f"[VOICE] wake: {wake_word}")
                return self._publish_wake_ack()
            if self._session is not None:
                self._debug(f"[VOICE] ignored: wake_only ({normalized})")
            return []

        if self._session is None:
            if self._reply_without_wake_active():
                self._start_session(None)
                return self._publish_speech(
                    normalized,
                    bypass_wake=False,
                    wake_word=None,
                    asr_confidence=asr_confidence,
                    is_wake_turn=False,
                )
            if _is_filler_text(normalized):
                self._debug(f"[VOICE] ignored: filler_or_short ({normalized})")
                return []
            self._debug(f"[VOICE] ignored: no_wake_word ({normalized})")
            return []

        if wake_word is not None and stripped == "":
            self._printer(f"[VOICE] wake: {wake_word}")
            return self._publish_wake_ack()

        if _is_user_exit_text(stripped or normalized):
            self._printer("[SESSION] user_exit")
            return self._end_session("user_exit")

        text = stripped or normalized
        if self._should_drop_post_playback_transcript(text, wake_word=wake_word):
            self._printer(f"[VOICE] ignored: post_playback_echo ({text})")
            return []
        if _is_filler_text(text):
            self._printer(f"[VOICE] ignored: filler_or_short ({text})")
            return []
        is_wake_turn = created_session and wake_word is not None
        if wake_word is not None:
            self._printer(f"[VOICE] wake: {wake_word}")

        return self._publish_speech(
            text,
            bypass_wake=False,
            wake_word=wake_word,
            asr_confidence=asr_confidence,
            is_wake_turn=is_wake_turn,
        )

    def expire_if_idle(self) -> list[Any]:
        if self._session is None:
            return []
        self._printer("[SESSION] timeout")
        return self._end_session("timeout")

    def expire_if_timed_out(self) -> list[Any]:
        if self._session is None or self._last_activity_monotonic is None:
            return []
        if self._clock() - self._last_activity_monotonic < self.session_timeout_seconds:
            return []
        return self.expire_if_idle()

    def _start_session(self, wake_word: str | None) -> None:
        self._session = SessionState(session_id=uuid.uuid4().hex, wake_word=wake_word)
        self._last_activity_monotonic = self._clock()
        self.voice_state = VoiceState.ACTIVE_LISTENING
        self._debug(f"[SESSION] started: {self._session.session_id}")
        message = build_session_start_message(
            self.device_id,
            session_id=self._session.session_id,
            wake_word=wake_word,
            source=self.source,
            topic_prefix=self.topic_prefix,
        )
        self._publish(message)

    def _publish(self, message) -> None:
        self.transport.publish(message)

    def _debug(self, message: str) -> None:
        if self._voice_debug:
            self._printer(message)

    def _publish_wake_ack(self) -> list[Any]:
        if self._session is None:
            return []
        command = build_command_message(
            self.device_id,
            command="tts_speak",
            request_id=f"xiaokang-wake-{self._session.session_id}",
            payload={
                "session_id": self._session.session_id,
                "clips": ["sess.wake_ack"],
                "interrupt": False,
            },
            source="simulator",
            topic_prefix=self.topic_prefix,
        )
        self._publish(command)
        return [command]

    def _publish_speech(
        self,
        text: str,
        *,
        bypass_wake: bool,
        wake_word: str | None,
        asr_confidence: float | None,
        emergency: bool = False,
        is_wake_turn: bool | None = None,
    ) -> list[Any]:
        if self._session is None:
            self._start_session(wake_word if not emergency else None)
        assert self._session is not None
        if is_wake_turn is None:
            is_wake_turn = self._session.turn == 0 and not bypass_wake
        self._session.turn += 1
        if emergency or _is_known_business_text(text):
            self._session.business_turn += 1
        self._last_activity_monotonic = self._clock()
        speech = build_speech_message(
            self.device_id,
            text=text,
            session_id=self._session.session_id,
            turn=self._session.turn,
            is_wake_turn=bool(is_wake_turn),
            wake_word=wake_word,
            bypass_wake=bypass_wake,
            asr_confidence=asr_confidence,
            source=self.source,
            topic_prefix=self.topic_prefix,
        )
        self._printer(f"[VOICE] user: {text}")
        self._debug(
            f"[SPEECH] turn={self._session.turn} text={text} "
            f"bypass_wake={str(bypass_wake).lower()}"
        )
        if self._state_machine is not None:
            self._state_machine.on_speech_submitted()
        self._publish(speech)
        messages = [speech]
        if (
            not emergency
            and self._session is not None
            and self._session.business_turn >= self.max_turns
        ):
            messages.extend(self._end_session("max_turns"))
        return messages

    def _end_session(self, reason: str) -> list[Any]:
        if self._session is None:
            return []
        session = self._session
        self._session = None
        self._last_activity_monotonic = None
        self.voice_state = VoiceState.WAKE_GUARD
        message = build_session_end_message(
            self.device_id,
            session_id=session.session_id,
            reason=reason,
            turns=session.turn,
            source=self.source,
            topic_prefix=self.topic_prefix,
        )
        self._printer(f"[VOICE] session_end: {reason}")
        self._debug(f"[SESSION] ended: {session.session_id}")
        self._publish(message)
        return [message]

    def _on_interaction_event(self, _topic: str, payload: dict[str, Any]) -> None:
        event = str(payload.get("event") or "").strip().upper()
        if event in {"FALL_SUSPECTED", "NORMAL_ACTIVITY_READING"}:
            self._reply_without_wake_until = self._clock() + self.session_timeout_seconds
        elif event in {"FALL_RECOVERED", "SESSION_END"}:
            self._reply_without_wake_until = None
        elif event == "CLIP_DONE":
            session_id = str(payload.get("session_id") or "").strip()
            status = str(payload.get("status") or "").strip().lower()
            if (
                self._session is not None
                and session_id == self._session.session_id
                and status == "done"
            ):
                self._last_activity_monotonic = self._clock()
                self._post_playback_guard_until = (
                    self._clock() + POST_PLAYBACK_TRANSCRIPT_GUARD_SECONDS
                )
                self._debug(f"[SESSION] playback_done: {session_id}")

    def _reply_without_wake_active(self) -> bool:
        if self._reply_without_wake_until is None:
            return False
        if self._clock() <= self._reply_without_wake_until:
            return True
        self._reply_without_wake_until = None
        return False

    def _should_drop_post_playback_transcript(
        self,
        text: str,
        *,
        wake_word: str | None,
    ) -> bool:
        if self._post_playback_guard_until is None:
            return False
        if self._clock() > self._post_playback_guard_until:
            self._post_playback_guard_until = None
            return False
        if wake_word is not None:
            return False
        if _is_known_business_text(text):
            self._post_playback_guard_until = None
            return False
        return True


class LocalVoicePipeline:
    def __init__(
        self,
        *,
        microphone: MicrophoneSource,
        asr_service: SpeechToTextService,
        transport: MessageTransport | None = None,
        device_id: str = DEFAULT_DEVICE_ID,
        topic_prefix: str = DEFAULT_TOPIC_PREFIX,
        session_timeout_seconds: float = DEFAULT_SESSION_TIMEOUT_SECONDS,
        max_turns: int = DEFAULT_SESSION_MAX_TURNS,
        microphone_device_index: int | None = None,
        capture_seconds: float = DEFAULT_CAPTURE_SECONDS,
        vad_trailing_silence_seconds: float = DEFAULT_TRAILING_SILENCE_SECONDS,
        wake_only: bool = False,
        voice_debug: bool | None = None,
        printer: Callable[[str], None] = print,
    ) -> None:
        self.microphone = microphone
        self.asr_service = asr_service
        self.transport = transport or ConsoleMockTransport(printer=printer)
        self.device_id = str(device_id or DEFAULT_DEVICE_ID).strip()
        self.topic_prefix = str(topic_prefix or DEFAULT_TOPIC_PREFIX).strip()
        self.session_timeout_seconds = max(1.0, float(session_timeout_seconds))
        self.max_turns = max(1, int(max_turns))
        self.microphone_device_index = microphone_device_index
        self.capture_seconds = max(1.0, float(capture_seconds))
        self.vad_trailing_silence_seconds = max(
            0.1, float(vad_trailing_silence_seconds)
        )
        self._printer = printer
        self.sessions = LocalVoiceSessionManager(
            self.transport,
            device_id=self.device_id,
            topic_prefix=self.topic_prefix,
            session_timeout_seconds=self.session_timeout_seconds,
            max_turns=self.max_turns,
            wake_only=wake_only,
            voice_debug=voice_debug,
            printer=printer,
        )

    def list_audio_devices(self) -> list[MicrophoneDeviceInfo]:
        devices = self.microphone.list_devices()
        self._printer("[AUDIO] available capture devices")
        for device in devices:
            self._printer(
                f"[{device.index}] {device.name} (channels={device.channels}, formats=0x{device.formats:08x})"
            )
        return devices

    def run_forever(self) -> None:
        self._printer("[AUDIO] microphone started")
        while True:
            capture_seconds = max(self.capture_seconds, self.session_timeout_seconds)
            fd, raw_path = tempfile.mkstemp(prefix="go2-local-mic-", suffix=".wav")
            os.close(fd)
            capture_path = Path(raw_path)
            try:
                result = self.microphone.record_utterance(
                    capture_path,
                    device_index=self.microphone_device_index,
                    duration_seconds=capture_seconds,
                    vad_enabled=True,
                    vad_trailing_silence_seconds=self.vad_trailing_silence_seconds,
                )
                if not result.speech_detected:
                    if self.sessions.active_session_id is not None:
                        self.sessions.expire_if_idle()
                    continue
                try:
                    transcript = self.asr_service.transcribe(result.path)
                except Exception as exc:
                    self._printer(f"[ASR] failed: {type(exc).__name__}: {exc}")
                    if self.sessions.active_session_id is not None:
                        self.sessions.expire_if_idle()
                    continue
                self._printer(f"[ASR] {transcript}")
                messages = self.sessions.process_transcript(transcript)
                if not messages and self.sessions.active_session_id is not None:
                    # Keep listening in the same session.
                    continue
            finally:
                try:
                    Path(capture_path).unlink(missing_ok=True)
                except Exception:
                    pass

    @staticmethod
    def create_default(
        *,
        health_new_url: str,
        device_id: str = DEFAULT_DEVICE_ID,
        topic_prefix: str = DEFAULT_TOPIC_PREFIX,
        microphone_device_index: int | None = None,
        session_timeout_seconds: float = DEFAULT_SESSION_TIMEOUT_SECONDS,
        max_turns: int = DEFAULT_SESSION_MAX_TURNS,
        capture_seconds: float = DEFAULT_CAPTURE_SECONDS,
        vad_trailing_silence_seconds: float = DEFAULT_TRAILING_SILENCE_SECONDS,
        wake_only: bool = False,
        voice_debug: bool | None = None,
        asr_backend: str = "remote",
        funasr_model: str = "paraformer-zh-streaming",
        funasr_hub: str = "ms",
        funasr_device: str = "cpu",
        funasr_ncpu: int = 4,
        printer: Callable[[str], None] = print,
    ) -> "LocalVoicePipeline":
        microphone = WindowsWaveInMicrophoneSource()
        if str(asr_backend or "remote").strip().lower() in {"funasr", "funasr-local", "local"}:
            asr_service: SpeechToTextService = FunASRLocalASRService(
                model=funasr_model,
                hub=funasr_hub,
                device=funasr_device,
                ncpu=funasr_ncpu,
            )
        else:
            asr_service = HealthNewASRService(health_new_url)
        return LocalVoicePipeline(
            microphone=microphone,
            asr_service=asr_service,
            device_id=device_id,
            topic_prefix=topic_prefix,
            microphone_device_index=microphone_device_index,
            session_timeout_seconds=session_timeout_seconds,
            max_turns=max_turns,
            capture_seconds=capture_seconds,
            vad_trailing_silence_seconds=vad_trailing_silence_seconds,
            wake_only=wake_only,
            voice_debug=voice_debug,
            printer=printer,
        )
