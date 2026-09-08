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
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
import sys

from app.iot.mqtt_contract import (
    build_session_end_message,
    build_session_start_message,
    build_speech_message,
)
from app.iot.protocol_layer import BMachineStateMachine, CommandMessage, MessageTransport, MockTransport
from app.webrtc.voice_intent import HealthNewASRService, WakeWordMatcher


SOURCE_NAME = "go2"
DEFAULT_DEVICE_ID = os.environ.get("GO2_DEVICE_ID", "DOG-LJG-001")
DEFAULT_TOPIC_PREFIX = os.environ.get("GO2_MQTT_TOPIC_PREFIX", "aiot")
DEFAULT_SESSION_TIMEOUT_SECONDS = 15.0
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

WAVE_MAPPER = ctypes.c_uint32(0xFFFFFFFF)
WIM_DATA = 0x03C0
CALLBACK_FUNCTION = 0x00030000


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
        target_count = max(1, int(round(mono.size * DEFAULT_SAMPLE_RATE / sample_rate)))
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
        self._latest_text = ""

    def feed_pcm(self, pcm: bytes, *, sample_rate: int, channels: int) -> str:
        import numpy as np

        normalized = _pcm16_mono_16k_from_pcm(
            pcm, sample_rate=sample_rate, channels=channels
        )
        if normalized.size == 0:
            return self._latest_text
        if self._buffer:
            self._buffer.append(normalized)
            pending = np.concatenate(self._buffer)
        else:
            pending = normalized
        self._buffer = [pending]
        emitted = self._run_chunks(is_final=False)
        return emitted or self._latest_text

    def finish(self) -> str:
        emitted = self._run_chunks(is_final=True)
        final = emitted or self._latest_text
        self.reset()
        return final

    def reset(self) -> None:
        self._cache = {}
        self._buffer = []
        self._latest_text = ""

    def _run_chunks(self, *, is_final: bool) -> str:
        import numpy as np

        if not self._buffer:
            return self._latest_text
        pending = self._buffer[0]
        if pending.size == 0:
            return self._latest_text
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
                    self._latest_text = text
                    emitted = text
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
            trust_remote_code=False,
        )
        return self._model

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
        latest_text = ""
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
                    latest_text = text
        return latest_text


class Go2ASRAudioBridge:
    def __init__(
        self,
        *,
        asr_service: FunASRLocalASRService,
        session_manager: LocalVoiceSessionManager,
        printer: Callable[[str], None] = print,
        vad_min_capture_seconds: float = 0.8,
        vad_trailing_silence_seconds: float = 0.35,
        queue_size: int = 200,
        is_playback_active: Callable[[], bool] | None = None,
    ) -> None:
        self.asr_service = asr_service
        self.session_manager = session_manager
        self._printer = printer
        self.vad_min_capture_seconds = max(0.2, float(vad_min_capture_seconds))
        self.vad_trailing_silence_seconds = max(
            0.2, float(vad_trailing_silence_seconds)
        )
        self._queue: "queue.Queue[tuple[bytes, int, int] | None]" = queue.Queue(
            maxsize=max(1, int(queue_size))
        )
        self._is_playback_active = is_playback_active or (lambda: False)
        self._last_final_text = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="go2-asr-bridge", daemon=True)
        self._thread_started = False

    def start(self) -> None:
        if self._thread_started:
            return
        self._thread_started = True
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread_started:
            self._thread.join(timeout=2.0)

    def push_pcm(self, pcm: bytes, sample_rate: int, channels: int) -> None:
        if self._stop.is_set() or not pcm:
            return
        if self._is_playback_active():
            self._printer("[ASR] dropped_pcm_frame: playback_active")
            return
        try:
            self._queue.put_nowait((bytes(pcm), int(sample_rate), int(channels)))
        except queue.Full:
            self._printer("[ASR] dropped_pcm_frame: queue_full")

    def _publish_final(self, final: str) -> None:
        normalized = str(final or "").strip()
        if not normalized:
            return
        if _normalize_text(normalized) == _normalize_text(self._last_final_text):
            self._printer(f"[ASR] duplicate_final_ignored: {normalized}")
            return
        self._last_final_text = normalized
        self._printer(f"[ASR] {normalized}")
        self.session_manager.process_transcript(normalized)

    def _run(self) -> None:
        import numpy as np

        current_stream: FunASRStreamingSession | None = None
        speech_detected = False
        sample_rate = 0
        channels = 0
        sample_count = 0
        voice_sample_count = 0
        trailing_silence_samples = 0
        min_capture_samples = 0
        silence_samples_limit = 0
        noise_rms: list[float] = []
        noise_peak: list[int] = []
        last_transcript = ""

        def reset_session() -> None:
            nonlocal current_stream, speech_detected, sample_rate, channels
            nonlocal sample_count, voice_sample_count, trailing_silence_samples
            nonlocal min_capture_samples, silence_samples_limit, noise_rms, noise_peak
            nonlocal last_transcript
            current_stream = None
            speech_detected = False
            sample_rate = 0
            channels = 0
            sample_count = 0
            voice_sample_count = 0
            trailing_silence_samples = 0
            min_capture_samples = 0
            silence_samples_limit = 0
            noise_rms = []
            noise_peak = []
            last_transcript = ""

        while not self._stop.is_set():
            item = self._queue.get()
            if item is None:
                break
            pcm, frame_rate, frame_channels = item
            normalized = _pcm16_mono_16k_from_pcm(
                pcm, sample_rate=frame_rate, channels=frame_channels
            )
            if normalized.size == 0:
                self.session_manager.expire_if_timed_out()
                continue
            if sample_rate == 0:
                sample_rate = DEFAULT_SAMPLE_RATE
                channels = 1
                min_capture_samples = int(sample_rate * self.vad_min_capture_seconds)
                silence_samples_limit = int(
                    sample_rate * self.vad_trailing_silence_seconds
                )
                current_stream = FunASRStreamingSession(self.asr_service)

            frame_peak = int(np.max(np.abs(normalized.astype(np.int32)))) if normalized.size else 0
            frame_rms = (
                float(np.sqrt(np.mean(normalized.astype(np.float64) ** 2)))
                if normalized.size
                else 0.0
            )
            sample_count += int(normalized.size)
            if not speech_detected and sample_count <= int(sample_rate * 0.4):
                noise_rms.append(frame_rms)
                noise_peak.append(frame_peak)
                self.session_manager.expire_if_timed_out()
                continue
            if noise_rms:
                rms_threshold = max(650.0, (sum(noise_rms) / len(noise_rms)) * 1.55)
                peak_threshold = max(1800.0, (sum(noise_peak) / len(noise_peak)) * 1.5)
            else:
                rms_threshold = 650.0
                peak_threshold = 1800.0
            voiced = frame_rms >= rms_threshold or frame_peak >= peak_threshold
            if voiced:
                speech_detected = True
                voice_sample_count = sample_count
                trailing_silence_samples = 0
            elif speech_detected:
                trailing_silence_samples += int(normalized.size)
            if current_stream is None:
                continue
            partial = current_stream.feed_pcm(
                normalized.tobytes(), sample_rate=sample_rate, channels=1
            )
            if partial and partial != last_transcript:
                last_transcript = partial
                self._printer(f"[ASR_PARTIAL] {partial}")
            if (
                speech_detected
                and sample_count >= min_capture_samples
                and trailing_silence_samples >= silence_samples_limit
            ):
                final = current_stream.finish() or last_transcript
                self._publish_final(final)
                reset_session()
                continue
            if not speech_detected:
                self.session_manager.expire_if_timed_out()
        if current_stream is not None:
            final = current_stream.finish() or last_transcript
            self._publish_final(final)


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
        emergency_bypass_enabled: bool = True,
        printer: Callable[[str], None] = print,
        monotonic_clock: Callable[[], float] = time.monotonic,
        state_machine: BMachineStateMachine | None = None,
    ) -> None:
        self.transport = transport
        self.device_id = str(device_id or DEFAULT_DEVICE_ID).strip()
        self.topic_prefix = str(topic_prefix or DEFAULT_TOPIC_PREFIX).strip()
        self.source = str(source or SOURCE_NAME).strip()
        self.session_timeout_seconds = max(1.0, float(session_timeout_seconds))
        self._emergency_bypass_enabled = bool(emergency_bypass_enabled)
        self._printer = printer
        self._clock = monotonic_clock
        self._state_machine = state_machine
        self._session: SessionState | None = None
        self._last_activity_monotonic: float | None = None

    @property
    def active_session_id(self) -> str | None:
        return None if self._session is None else self._session.session_id

    def process_transcript(
        self,
        transcript: str,
        *,
        asr_confidence: float | None = None,
    ) -> list[Any]:
        normalized = str(transcript or "").strip()
        if not normalized:
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

        if self._session is None:
            if _is_filler_text(normalized):
                self._printer(f"[VOICE] ignored: filler_or_short ({normalized})")
                return []
            self._printer(f"[VOICE] ignored: no_wake_word ({normalized})")
            return []

        if wake_word is not None and stripped == "":
            self._printer(f"[WAKE] matched: {wake_word}")
            return []

        if _is_user_exit_text(stripped or normalized):
            self._printer("[SESSION] user_exit")
            return self._end_session("user_exit")

        text = stripped or normalized
        if _is_filler_text(text):
            self._printer(f"[VOICE] ignored: filler_or_short ({text})")
            return []

        is_wake_turn = created_session and wake_word is not None
        if wake_word is not None:
            self._printer(f"[WAKE] matched: {wake_word}")

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
        self._printer(f"[SESSION] started: {self._session.session_id}")
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
        self._printer(
            f"[SPEECH] turn={self._session.turn} text={text} bypass_wake={str(bypass_wake).lower()}"
        )
        if self._state_machine is not None:
            self._state_machine.on_speech_submitted()
        self._publish(speech)
        return [speech]

    def _end_session(self, reason: str) -> list[Any]:
        if self._session is None:
            return []
        session = self._session
        self._session = None
        self._last_activity_monotonic = None
        message = build_session_end_message(
            self.device_id,
            session_id=session.session_id,
            reason=reason,
            turns=session.turn,
            source=self.source,
            topic_prefix=self.topic_prefix,
        )
        self._printer(f"[SESSION] ended: {session.session_id}")
        self._publish(message)
        return [message]


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
        microphone_device_index: int | None = None,
        capture_seconds: float = DEFAULT_CAPTURE_SECONDS,
        vad_trailing_silence_seconds: float = DEFAULT_TRAILING_SILENCE_SECONDS,
        printer: Callable[[str], None] = print,
    ) -> None:
        self.microphone = microphone
        self.asr_service = asr_service
        self.transport = transport or ConsoleMockTransport(printer=printer)
        self.device_id = str(device_id or DEFAULT_DEVICE_ID).strip()
        self.topic_prefix = str(topic_prefix or DEFAULT_TOPIC_PREFIX).strip()
        self.session_timeout_seconds = max(1.0, float(session_timeout_seconds))
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
        capture_seconds: float = DEFAULT_CAPTURE_SECONDS,
        vad_trailing_silence_seconds: float = DEFAULT_TRAILING_SILENCE_SECONDS,
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
            capture_seconds=capture_seconds,
            vad_trailing_silence_seconds=vad_trailing_silence_seconds,
            printer=printer,
        )
