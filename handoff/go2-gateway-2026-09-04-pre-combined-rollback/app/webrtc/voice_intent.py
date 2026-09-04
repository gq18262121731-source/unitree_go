from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
import wave
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol

import requests


class VoiceIntent(str, Enum):
    NONE = "NONE"
    START_COMPANION = "START_COMPANION"
    STOP_COMPANION = "STOP_COMPANION"
    RESUME_COMPANION = "RESUME_COMPANION"
    REQUEST_HELP = "REQUEST_HELP"
    CALL_FAMILY = "CALL_FAMILY"
    I_AM_OK = "I_AM_OK"


class CompanionLifecycleState(str, Enum):
    IDLE = "IDLE"
    FOLLOWING = "FOLLOWING"
    UWB_WAITING = "UWB_WAITING"
    WAIT_RESUME = "WAIT_RESUME"
    PAUSED_BY_FALL = "PAUSED_BY_FALL"


@dataclass(frozen=True)
class CompanionLifecycleSnapshot:
    state: CompanionLifecycleState
    webrtc_connected: bool
    uwb_fresh: bool
    uwb_valid: bool
    fall_active: bool = False
    manual_takeover: bool = False
    motion_writer_available: bool = True


@dataclass(frozen=True)
class AgentTurn:
    transcript: str
    reply: str
    intent: VoiceIntent
    confidence: float
    scope: str
    raw: dict[str, Any]


class VoiceFastIntentRouter:
    """Low-latency router for explicit high-level commands only.

    It never emits velocity and ambiguous language always falls back to the agent.
    """

    _START = (
        "陪我出去走走",
        "陪我走走",
        "陪我走一会儿",
        "开启伴随",
        "开始伴随",
        "启动伴随",
        "跟我走",
        "跟着我",
        "陪我散步",
        "我们出去走走",
        "陪我出去散步",
        "出去转转",
        "陪我出去转转",
        "出去走走",
    )
    _STOP = (
        "停一下",
        "停下来",
        "停止伴随",
        "别跟了",
        "不用跟了",
        "不要跟着我",
        "等一下",
    )
    _RESUME = (
        "继续走吧",
        "继续走",
        "继续吧",
        "接着走",
        "恢复伴随",
        "继续跟随",
        "继续跟着我",
    )
    _REQUEST_HELP = (
        "帮帮我",
        "我需要帮助",
        "需要帮助",
        "请帮我",
    )
    _CALL_FAMILY = (
        "联系家人",
        "帮我叫家人",
        "给家人打电话",
        "通知家人",
        "叫我家人",
    )
    _I_AM_OK = (
        "我没事",
        "我没关系",
        "不用帮忙",
        "不需要帮助",
        "我还好",
    )

    @classmethod
    def route(cls, transcript: str) -> AgentTurn | None:
        original = str(transcript or "").strip()
        normalized = re.sub(r"[\s，。！？、,.!?]+", "", original)
        if not normalized:
            return None
        if any(phrase in normalized for phrase in cls._I_AM_OK):
            intent = VoiceIntent.I_AM_OK
        elif any(phrase in normalized for phrase in cls._CALL_FAMILY):
            intent = VoiceIntent.CALL_FAMILY
        elif any(phrase in normalized for phrase in cls._REQUEST_HELP):
            intent = VoiceIntent.REQUEST_HELP
        elif any(phrase in normalized for phrase in cls._STOP):
            intent = VoiceIntent.STOP_COMPANION
        elif any(phrase in normalized for phrase in cls._RESUME):
            intent = VoiceIntent.RESUME_COMPANION
        elif (
            any(phrase in normalized for phrase in cls._START)
            and not any(term in normalized for term in ("不要", "不用", "别"))
        ):
            intent = VoiceIntent.START_COMPANION
        else:
            return None
        return AgentTurn(
            transcript=original,
            reply="",
            intent=intent,
            confidence=1.0,
            scope="companion",
            raw={"source": "local_explicit_command_router"},
        )


class WakeWordMatcher:
    """Conservative wake-word matching with known ASR homophone tolerance."""

    _ALIASES = {"小康", "小仓", "小汤", "晓康", "小刚"}

    @classmethod
    def matches(cls, transcript: str) -> bool:
        normalized = re.sub(r"[\s，。！？、,.!?]+", "", str(transcript or ""))
        return normalized in cls._ALIASES


@dataclass(frozen=True)
class WeatherSnapshot:
    city: str
    weather: str
    description: str
    temperature_c: float | None
    wind_level: int | None
    provider: str
    fetched_monotonic: float


class HealthNewWeatherCache:
    """Refresh weather off the voice response path and retain a safe stale copy."""

    def __init__(
        self,
        base_url: str,
        *,
        city: str = "北京",
        refresh_seconds: float = 300.0,
        max_stale_seconds: float = 3600.0,
        timeout_seconds: float = 5.0,
        session: HttpSession | None = None,
        clock=time.monotonic,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.city = str(city or "北京").strip() or "北京"
        self.refresh_seconds = max(30.0, float(refresh_seconds))
        self.max_stale_seconds = max(self.refresh_seconds, float(max_stale_seconds))
        self.timeout_seconds = float(timeout_seconds)
        self._session = session or requests.Session()
        self._clock = clock
        self._snapshot: WeatherSnapshot | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None

    def start(self) -> None:
        self.refresh()
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._refresh_loop,
            name="go2-weather-cache",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def refresh(self) -> bool:
        try:
            response = self._session.get(
                f"{self.base_url}/api/v1/go2-companion/weather",
                params={"city": self.city},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = dict(response.json())
            snapshot = WeatherSnapshot(
                city=str(payload.get("city") or self.city),
                weather=str(payload.get("weather") or "unknown"),
                description=str(payload.get("description") or "").strip(),
                temperature_c=(
                    None
                    if payload.get("temperature_c") is None
                    else float(payload["temperature_c"])
                ),
                wind_level=(
                    None
                    if payload.get("wind_level") is None
                    else int(payload["wind_level"])
                ),
                provider=str(payload.get("provider") or "unknown"),
                fetched_monotonic=self._clock(),
            )
            with self._lock:
                self._snapshot = snapshot
                self.last_error = None
            return True
        except Exception as exc:
            with self._lock:
                self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def snapshot(self) -> WeatherSnapshot | None:
        with self._lock:
            value = self._snapshot
        if value is None:
            return None
        if self._clock() - value.fetched_monotonic > self.max_stale_seconds:
            return None
        return value

    def _refresh_loop(self) -> None:
        while not self._stop.wait(self.refresh_seconds):
            self.refresh()


class CompanionSpeechRenderer:
    @classmethod
    def render_start(
        cls,
        *,
        elder_name: str,
        weather: WeatherSnapshot | None,
        now: datetime | None = None,
    ) -> str:
        acknowledgement, remainder = cls.render_start_parts(
            elder_name=elder_name,
            weather=weather,
            now=now,
        )
        return f"{acknowledgement}{remainder}"

    @classmethod
    def render_start_parts(
        cls,
        *,
        elder_name: str,
        weather: WeatherSnapshot | None,
        now: datetime | None = None,
    ) -> tuple[str, str]:
        acknowledgement, chunks = cls.render_start_chunks(
            elder_name=elder_name,
            weather=weather,
            now=now,
        )
        return acknowledgement, "".join(chunks)

    @classmethod
    def render_start_chunks(
        cls,
        *,
        elder_name: str,
        weather: WeatherSnapshot | None,
        now: datetime | None = None,
    ) -> tuple[str, list[str]]:
        name = str(elder_name or "").strip() or "您"
        current_time = cls._format_time(now or datetime.now())
        acknowledgement = f"好的，{name}。"
        time_chunk = f"现在是{current_time}。"
        if weather is None or not weather.description:
            return acknowledgement, [
                time_chunk,
                "我来陪您出去走走，路上请注意安全。",
            ]

        temperature = (
            ""
            if weather.temperature_c is None
            else f"，气温{weather.temperature_c:g}摄氏度"
        )
        condition = f"{weather.city}今天{weather.description}{temperature}"
        category = weather.weather
        if category == "rain":
            advice = "出行请带好雨具并注意路滑"
            ending = "如果您决定出门，我会陪着您。"
        elif category == "hot" or (weather.temperature_c or -100.0) >= 32.0:
            advice = "天气较热，外出请注意防晒并及时补充水分"
            ending = "如果您决定出门，我会陪着您。"
        elif category == "cold":
            advice = "外出请注意保暖"
            ending = "如果您决定出门，我会陪着您。"
        elif category == "windy" or (weather.wind_level or 0) >= 5:
            advice = "当前风力较大，请谨慎安排外出"
            ending = "如果您决定出门，我会陪着您。"
        elif category == "sunny":
            advice = "天气适合出行"
            ending = "我来陪您出去走走，路上请注意安全。"
        else:
            advice = "请结合现场情况谨慎出行"
            ending = "如果您决定出门，我会陪着您。"
        return acknowledgement, [
            time_chunk,
            f"{condition}，{advice}。",
            ending,
        ]

    @classmethod
    def _format_time(cls, value: datetime) -> str:
        hour = value.hour
        if hour < 6:
            period = "凌晨"
        elif hour < 12:
            period = "上午"
        elif hour < 14:
            period = "中午"
        elif hour < 18:
            period = "下午"
        else:
            period = "晚上"
        display_hour = hour % 12 or 12
        minute = value.minute
        suffix = "整" if minute == 0 else f"{cls._number_zh(minute)}分"
        return f"{period}{cls._number_zh(display_hour)}点{suffix}"

    @staticmethod
    def _number_zh(value: int) -> str:
        digits = "零一二三四五六七八九"
        if value < 10:
            return digits[value]
        tens, ones = divmod(value, 10)
        prefix = "十" if tens == 1 else f"{digits[tens]}十"
        return prefix if ones == 0 else f"{prefix}{digits[ones]}"


@dataclass(frozen=True)
class VoiceIntentDecision:
    intent: VoiceIntent
    authorized: bool
    executed: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["intent"] = self.intent.value
        return payload


class HttpSession(Protocol):
    def get(self, url: str, **kwargs: Any) -> Any: ...
    def post(self, url: str, **kwargs: Any) -> Any: ...


class HealthNewASRService:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 8.0,
        session: HttpSession | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self._session = session or requests.Session()

    def status(self) -> dict[str, Any]:
        response = self._session.get(
            f"{self.base_url}/api/v1/voice/status",
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return dict(response.json())

    def transcribe(self, wav_path: str | Path) -> str:
        source = Path(wav_path)
        upload_source = self._prepare_short_command_wav(source)
        try:
            with upload_source.open("rb") as stream:
                response = self._session.post(
                    f"{self.base_url}/api/v1/voice/asr",
                    files={"file": (upload_source.name, stream, "audio/wav")},
                    timeout=self.timeout_seconds,
                )
        finally:
            if upload_source != source:
                upload_source.unlink(missing_ok=True)
        response.raise_for_status()
        payload = dict(response.json())
        if payload.get("ok") is not True:
            raise RuntimeError(str(payload.get("error") or "health_new ASR failed"))
        transcript = str(payload.get("text") or "").strip()
        if not transcript:
            raise RuntimeError("health_new ASR returned empty transcript")
        return transcript

    @staticmethod
    def _prepare_short_command_wav(source: Path) -> Path:
        """Downmix PCM16 speech to 16 kHz mono before the HTTP upload."""

        try:
            import numpy as np

            with wave.open(str(source), "rb") as stream:
                channels = stream.getnchannels()
                sample_width = stream.getsampwidth()
                sample_rate = stream.getframerate()
                pcm = stream.readframes(stream.getnframes())
            if sample_width != 2 or channels < 1 or sample_rate <= 0:
                return source
            values = np.frombuffer(pcm, dtype="<i2")
            if values.size == 0 or values.size % channels:
                return source
            frames = values.reshape(-1, channels).astype(np.float64)
            mono = frames.mean(axis=1)
            target_rate = 16000
            if sample_rate != target_rate:
                target_count = max(
                    1, int(round(mono.size * target_rate / sample_rate))
                )
                positions = np.linspace(0, mono.size - 1, target_count)
                mono = np.interp(positions, np.arange(mono.size), mono)
            optimized = np.clip(np.rint(mono), -32768, 32767).astype("<i2")
            temporary = tempfile.NamedTemporaryFile(
                delete=False, suffix=".asr-16k-mono.wav"
            )
            temporary.close()
            target = Path(temporary.name)
            with wave.open(str(target), "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(target_rate)
                stream.writeframes(optimized.tobytes())
            return target
        except Exception:
            return source


class HealthNewTTSService:
    """Synthesize Qwen speech once and reuse the local WAV on later turns."""

    def __init__(
        self,
        base_url: str,
        *,
        cache_dir: str | Path,
        voice: str = "Cherry",
        speed: float = 1.0,
        timeout_seconds: float = 90.0,
        session: HttpSession | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.cache_dir = Path(cache_dir).resolve()
        self.voice = str(voice or "Cherry").strip()
        self.speed = float(speed)
        self.timeout_seconds = float(timeout_seconds)
        self._session = session or requests.Session()
        self._synthesis_lock = threading.Lock()

    def synthesize_to_wav(self, text: str) -> tuple[Path, bool]:
        with self._synthesis_lock:
            return self._synthesize_to_wav_locked(text)

    def _synthesize_to_wav_locked(self, text: str) -> tuple[Path, bool]:
        normalized = str(text or "").strip()
        if not normalized:
            raise ValueError("TTS text must not be empty")
        digest = hashlib.sha256(
            f"qwen3-tts-flash\0{self.voice}\0{self.speed}\0{normalized}".encode(
                "utf-8"
            )
        ).hexdigest()[:24]
        target = self.cache_dir / f"{digest}.wav"
        if self._valid_wav(target):
            return target, True

        response = self._session.post(
            f"{self.base_url}/api/v1/voice/tts",
            json={
                "text": normalized,
                "voice": self.voice,
                "speed": self.speed,
                "fmt": "wav",
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = dict(response.json())
        if payload.get("ok") is not True:
            raise RuntimeError(str(payload.get("error") or "health_new TTS failed"))
        audio = self._decode_audio(payload)
        if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise RuntimeError("health_new TTS returned invalid WAV audio")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(audio)
        temporary.replace(target)
        return target, False

    def _decode_audio(self, payload: dict[str, Any]) -> bytes:
        encoded = str(payload.get("audio_b64") or "").strip()
        audio_url = str(payload.get("audio_url") or "").strip()
        if encoded:
            return base64.b64decode(encoded)
        if audio_url.startswith("data:") and "," in audio_url:
            return base64.b64decode(audio_url.split(",", 1)[1])
        if audio_url.startswith(("http://", "https://")):
            response = self._session.get(audio_url, timeout=self.timeout_seconds)
            response.raise_for_status()
            return bytes(response.content)
        raise RuntimeError(str(payload.get("error") or "health_new TTS returned no audio"))

    @staticmethod
    def _valid_wav(path: Path) -> bool:
        if not path.is_file() or path.stat().st_size < 44:
            return False
        with path.open("rb") as stream:
            header = stream.read(12)
        return header[:4] == b"RIFF" and header[8:12] == b"WAVE"


class CompanionSpeechCache:
    """Precompile fixed companion speech outside the live voice path.

    The last valid WAV is never replaced until a complete new WAV has been
    synthesized. Callers can therefore keep using the previous version while
    weather refresh or TTS is slow/unavailable.
    """

    def __init__(
        self,
        tts_service: HealthNewTTSService,
        weather_cache: HealthNewWeatherCache,
        *,
        elder_name: str,
        cache_dir: str | Path,
        refresh_seconds: float = 300.0,
        now_provider: Callable[[], datetime] = datetime.now,
        wall_clock: Callable[[], float] = time.time,
        on_ready: Callable[[Path], Any] | None = None,
    ) -> None:
        self.tts_service = tts_service
        self.weather_cache = weather_cache
        self.elder_name = str(elder_name or "李四").strip() or "李四"
        self.cache_dir = Path(cache_dir).resolve()
        self.current_path = self.cache_dir / "start_companion_current.wav"
        self.metadata_path = self.cache_dir / "start_companion_current.json"
        self.refresh_seconds = max(1.0, float(refresh_seconds))
        self._now_provider = now_provider
        self._wall_clock = wall_clock
        self._on_ready = on_ready
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._text = ""
        self._signature = ""
        self._generated_at_epoch: float | None = None
        self._last_error: str | None = None
        self._load_existing_metadata()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._refresh_loop,
            name="go2-companion-speech-cache",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def refresh(self, *, force: bool = False) -> bool:
        now = self._now_provider()
        weather = self.weather_cache.snapshot()
        text = CompanionSpeechRenderer.render_start(
            elder_name=self.elder_name,
            weather=weather,
            now=now,
        )
        signature = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self._lock:
            unchanged = (
                not force
                and signature == self._signature
                and HealthNewTTSService._valid_wav(self.current_path)
            )
        if unchanged:
            return True

        temporary = self.current_path.with_name(
            f".{self.current_path.name}.{threading.get_ident()}.tmp"
        )
        metadata_temporary = self.metadata_path.with_suffix(".json.tmp")
        try:
            synthesized, _cache_hit = self.tts_service.synthesize_to_wav(text)
            if not HealthNewTTSService._valid_wav(synthesized):
                raise RuntimeError("speech cache TTS returned an invalid WAV")
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(synthesized, temporary)
            os.replace(temporary, self.current_path)
            generated_at = self._wall_clock()
            metadata = {
                "text": text,
                "signature": signature,
                "generated_at_epoch": generated_at,
                "generated_for_minute": now.strftime("%Y-%m-%dT%H:%M"),
                "weather_city": None if weather is None else weather.city,
                "weather_description": (
                    None if weather is None else weather.description
                ),
            }
            metadata_temporary.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(metadata_temporary, self.metadata_path)
            with self._lock:
                self._text = text
                self._signature = signature
                self._generated_at_epoch = generated_at
                self._last_error = None
            if self._on_ready is not None:
                self._on_ready(self.current_path)
            return True
        except Exception as exc:
            with self._lock:
                self._last_error = f"{type(exc).__name__}: {exc}"
            return False
        finally:
            for candidate in (temporary, metadata_temporary):
                try:
                    candidate.unlink(missing_ok=True)
                except OSError:
                    pass

    def lookup_start(self) -> dict[str, Any]:
        with self._lock:
            generated_at = self._generated_at_epoch
            text = self._text
            last_error = self._last_error
        ready = HealthNewTTSService._valid_wav(self.current_path)
        age = (
            None
            if generated_at is None
            else max(0.0, self._wall_clock() - generated_at)
        )
        return {
            "ready": ready,
            "path": self.current_path if ready else None,
            "text": text,
            "age_seconds": age,
            "last_error": last_error,
        }

    def _load_existing_metadata(self) -> None:
        if not HealthNewTTSService._valid_wav(self.current_path):
            return
        try:
            payload = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            with self._lock:
                self._text = str(payload.get("text") or "")
                self._signature = str(payload.get("signature") or "")
                self._generated_at_epoch = float(
                    payload.get("generated_at_epoch")
                    or self.current_path.stat().st_mtime
                )
        except Exception:
            with self._lock:
                self._generated_at_epoch = self.current_path.stat().st_mtime

    def _refresh_loop(self) -> None:
        existing = self.lookup_start()
        if existing["ready"] and self._on_ready is not None:
            try:
                self._on_ready(self.current_path)
            except Exception as exc:
                with self._lock:
                    self._last_error = f"preload {type(exc).__name__}: {exc}"
        if existing["ready"] and self._stop.wait(self.refresh_seconds):
            return
        while not self._stop.is_set():
            self.refresh()
            if self._stop.wait(self.refresh_seconds):
                return


class CompanionAgentClient:
    def __init__(
        self,
        base_url: str,
        *,
        elder_id: str,
        session_id: str,
        device_mac: str | None = None,
        location_hint: str | None = None,
        timeout_seconds: float = 25.0,
        session: HttpSession | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.elder_id = elder_id.strip()
        self.session_id = session_id.strip()
        self.device_mac = (device_mac or "").strip() or None
        self.location_hint = (location_hint or "").strip() or None
        self.timeout_seconds = float(timeout_seconds)
        self._session = session or requests.Session()
        if not self.elder_id or not self.session_id:
            raise ValueError("elder_id and session_id are required")

    def text_turn(
        self,
        transcript: str,
        lifecycle: CompanionLifecycleSnapshot,
    ) -> AgentTurn:
        payload: dict[str, object] = {
            "elder_id": self.elder_id,
            "session_id": self.session_id,
            "text": transcript.strip(),
            "robot_state": lifecycle.state.value,
            "companion_active": lifecycle.state
            in {CompanionLifecycleState.FOLLOWING, CompanionLifecycleState.UWB_WAITING},
            "fall_active": lifecycle.fall_active,
            "resume_required": lifecycle.state
            in {CompanionLifecycleState.WAIT_RESUME, CompanionLifecycleState.PAUSED_BY_FALL},
        }
        if self.device_mac:
            payload["device_mac"] = self.device_mac
        if self.location_hint:
            payload["location_hint"] = self.location_hint
        response = self._session.post(
            f"{self.base_url}/api/v1/go2-companion/text-turn",
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        raw = dict(response.json())
        try:
            intent = VoiceIntent(str(raw.get("intent") or "NONE").upper())
        except ValueError as exc:
            raise RuntimeError("health_new returned a non-whitelisted intent") from exc
        return AgentTurn(
            transcript=transcript.strip(),
            reply=str(raw.get("reply") or "").strip(),
            intent=intent,
            confidence=max(0.0, min(1.0, float(raw.get("intent_confidence") or 0.0))),
            scope=str(raw.get("intent_scope") or "companion"),
            raw=raw,
        )


class VoiceIntentAdapter:
    """Authorize only high-level intents; it never emits velocity commands."""

    def authorize(
        self,
        turn: AgentTurn,
        lifecycle: CompanionLifecycleSnapshot,
    ) -> VoiceIntentDecision:
        intent = turn.intent
        if intent is VoiceIntent.NONE:
            return VoiceIntentDecision(intent, False, False, "no_robot_action")
        if intent is VoiceIntent.I_AM_OK:
            allowed = lifecycle.state in {
                CompanionLifecycleState.PAUSED_BY_FALL,
                CompanionLifecycleState.WAIT_RESUME,
            }
            return VoiceIntentDecision(
                intent,
                allowed,
                False,
                "help_declined_motion_stays_stopped"
                if allowed
                else "i_am_ok_requires_fall_context",
            )
        if intent in {VoiceIntent.REQUEST_HELP, VoiceIntent.CALL_FAMILY}:
            return VoiceIntentDecision(intent, True, False, "emergency_lifecycle_event_allowed")
        if intent is VoiceIntent.STOP_COMPANION:
            return VoiceIntentDecision(intent, True, False, "stop_is_always_safety_authorized")
        health_failure = self._motion_health_failure(lifecycle)
        if health_failure:
            return VoiceIntentDecision(intent, False, False, health_failure)
        if intent is VoiceIntent.START_COMPANION:
            allowed = lifecycle.state is CompanionLifecycleState.IDLE
            return VoiceIntentDecision(
                intent,
                allowed,
                False,
                "idle_start_allowed" if allowed else "start_requires_idle",
            )
        if intent is VoiceIntent.RESUME_COMPANION:
            allowed = (
                lifecycle.state is CompanionLifecycleState.WAIT_RESUME
                and not lifecycle.fall_active
                and not lifecycle.manual_takeover
            )
            return VoiceIntentDecision(
                intent,
                allowed,
                False,
                "explicit_resume_allowed" if allowed else "resume_requires_safe_wait_resume",
            )
        return VoiceIntentDecision(VoiceIntent.NONE, False, False, "intent_not_allowed")

    @staticmethod
    def _motion_health_failure(
        lifecycle: CompanionLifecycleSnapshot,
    ) -> str | None:
        if not lifecycle.webrtc_connected:
            return "webrtc_not_connected"
        if not lifecycle.uwb_fresh:
            return "uwb_not_fresh"
        if not lifecycle.uwb_valid:
            return "uwb_not_valid"
        if lifecycle.fall_active:
            return "fall_active"
        if lifecycle.manual_takeover:
            return "manual_takeover_active"
        if not lifecycle.motion_writer_available:
            return "motion_writer_unavailable"
        return None
