from __future__ import annotations

import asyncio
import base64
import binascii
import io
import urllib.request
import wave
from typing import Any, Callable

from backend.services.go2_companion_intent_service import (
    Go2CompanionIntentService,
    Go2VoiceIntent,
)
from backend.services.go2_companion_voice_service import (
    Go2CompanionVoiceError,
    Go2CompanionVoiceService,
)
from backend.services.robot_audio import (
    AudioCapture,
    RobotAudioService,
    RobotAudioServiceError,
)


class Go2HardwareVoiceTurnError(RuntimeError):
    def __init__(self, *, stage: str, message: str, status_code: int = 502) -> None:
        self.stage = stage
        self.message = str(message or "").strip() or f"{stage} failed"
        self.status_code = status_code
        super().__init__(self.message)


class Go2HardwareVoiceTurnService:
    FALL_PROMPT = "检测到您可能跌倒了，请问您还好吗？"
    DEFAULT_PROMPT = "您好，我在听，请说吧。"

    def __init__(
        self,
        *,
        audio_service: RobotAudioService,
        voice_service: Go2CompanionVoiceService,
        intent_service: Go2CompanionIntentService | None = None,
        asr_timeout_s: float = 20.0,
        tts_timeout_s: float = 20.0,
        dialogue_timeout_s: float = 15.0,
        record_max_duration_s: float = 8.0,
        silence_timeout_s: float = 1.2,
        playback_timeout_s: float = 20.0,
        audio_url_loader: Callable[[str, float], bytes] | None = None,
    ) -> None:
        self._audio = audio_service
        self._voice = voice_service
        self._intent = intent_service or Go2CompanionIntentService()
        self._asr_timeout_s = self._positive(asr_timeout_s, "asr_timeout_s")
        self._tts_timeout_s = self._positive(tts_timeout_s, "tts_timeout_s")
        self._dialogue_timeout_s = self._positive(
            dialogue_timeout_s, "dialogue_timeout_s"
        )
        self._record_max_duration_s = self._positive(
            record_max_duration_s, "record_max_duration_s"
        )
        self._silence_timeout_s = self._positive(
            silence_timeout_s, "silence_timeout_s"
        )
        self._playback_timeout_s = self._positive(
            playback_timeout_s, "playback_timeout_s"
        )
        self._audio_url_loader = audio_url_loader or self._load_audio_url
        self._turn_lock = asyncio.Lock()

    async def process_turn(
        self,
        *,
        session_id: str,
        voice: str,
        elder_id: str | None,
        device_mac: str | None,
        location_hint: str | None,
        prompt_text: str | None,
        fall_monitoring: bool,
        max_duration_s: float | None,
        silence_timeout_s: float | None,
        playback_timeout_s: float | None,
    ) -> dict[str, Any]:
        async with self._turn_lock:
            try:
                if not self._audio.microphone_available:
                    raise Go2HardwareVoiceTurnError(
                        stage="recording",
                        message="Go2 microphone is not configured",
                        status_code=503,
                    )
                if not self._audio.speaker_available:
                    raise Go2HardwareVoiceTurnError(
                        stage="playback",
                        message="Go2 speaker is not configured",
                        status_code=503,
                    )
                record_limit = (
                    self._record_max_duration_s
                    if max_duration_s is None
                    else self._positive(max_duration_s, "max_duration_s")
                )
                silence_limit = (
                    self._silence_timeout_s
                    if silence_timeout_s is None
                    else self._positive(silence_timeout_s, "silence_timeout_s")
                )
                playback_limit = (
                    self._playback_timeout_s
                    if playback_timeout_s is None
                    else self._positive(playback_timeout_s, "playback_timeout_s")
                )
                prompt = self.FALL_PROMPT if fall_monitoring else (
                    str(prompt_text or "").strip() or self.DEFAULT_PROMPT
                )
                prompt_tts = await self._run_sync_stage(
                    "tts",
                    self._tts_timeout_s,
                    self._voice.synthesize_reply,
                    prompt,
                    voice=voice,
                )
                await self._audio.play_audio(
                    await self._tts_audio_bytes(prompt_tts),
                    timeout_s=playback_limit,
                )

                capture = await self._audio.record_once(
                    max_duration_s=record_limit,
                    silence_timeout_s=silence_limit,
                )
                await self._audio.set_processing()
                wav_bytes = self._capture_as_wav(capture)
                asr = await self._run_sync_stage(
                    "asr",
                    self._asr_timeout_s,
                    self._voice.transcribe_audio,
                    wav_bytes,
                    audio_format="wav",
                    allow_empty=fall_monitoring,
                )
                transcript = str(asr.get("text") or "").strip()
                intent = self._intent.classify(
                    transcript,
                    fall_monitoring=fall_monitoring,
                )

                if intent.intent is Go2VoiceIntent.NO_RESPONSE:
                    dialogue = {
                        "reply": "我没有听到您的回答，我会保留求助提示，请身边的人协助确认。",
                        "llm_provider": "fixed/fall-monitoring",
                        "llm_model": "fixed-response-v1",
                        "grounded": False,
                        "context": None,
                        "health_metrics": None,
                    }
                else:
                    dialogue = await self._run_sync_stage(
                        "llm",
                        self._dialogue_timeout_s,
                        self._voice.generate_reply,
                        transcript,
                        session_id=session_id,
                        elder_id=elder_id,
                        device_mac=device_mac,
                        location_hint=location_hint,
                    )

                tts = await self._run_sync_stage(
                    "tts",
                    self._tts_timeout_s,
                    self._voice.synthesize_reply,
                    str(dialogue["reply"]),
                    voice=voice,
                )
                playback = await self._audio.play_audio(
                    await self._tts_audio_bytes(tts),
                    timeout_s=playback_limit,
                )
                status = await self._audio.status()
                return {
                    "agent": "go2_companion",
                    "version": "1.0",
                    "session_id": session_id,
                    "transcript": transcript,
                    "reply": str(dialogue["reply"]),
                    "intent": intent.intent.value,
                    "intent_confidence": intent.confidence,
                    "intent_scope": intent.scope,
                    "intent_executed": False,
                    "execution_message": (
                        "P0-2 only emits intent; P0-1 Lifecycle Service must execute it."
                    ),
                    "asr_provider": str(asr.get("provider") or "unknown"),
                    "llm_provider": str(dialogue.get("llm_provider") or "unknown"),
                    "llm_model": str(dialogue.get("llm_model") or "unknown"),
                    "tts_provider": str(tts.get("provider") or "unknown"),
                    "tts_voice": str(tts.get("voice") or voice),
                    "grounded": bool(dialogue.get("grounded")),
                    "context": dialogue.get("context"),
                    "health_metrics": dialogue.get("health_metrics"),
                    "playback": playback,
                    "audio_status": status,
                }
            except asyncio.CancelledError:
                await self._audio.cancel()
                raise
            except Go2HardwareVoiceTurnError:
                await self._audio.cancel()
                raise
            except RobotAudioServiceError as exc:
                await self._audio.cancel()
                raise Go2HardwareVoiceTurnError(
                    stage=exc.stage,
                    message=exc.message,
                    status_code=503,
                ) from exc
            except Go2CompanionVoiceError as exc:
                await self._audio.cancel()
                raise Go2HardwareVoiceTurnError(
                    stage=exc.stage,
                    message=exc.message,
                    status_code=exc.status_code,
                ) from exc
            except Exception as exc:
                await self._audio.cancel()
                raise Go2HardwareVoiceTurnError(
                    stage="audio_turn",
                    message=str(exc) or exc.__class__.__name__,
                ) from exc
            finally:
                self._audio.reset_processing()

    async def cancel(self) -> None:
        await self._audio.cancel()

    async def _run_sync_stage(
        self,
        stage: str,
        timeout_s: float,
        function: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(function, *args, **kwargs),
                timeout=timeout_s,
            )
        except TimeoutError as exc:
            raise Go2HardwareVoiceTurnError(
                stage=stage,
                message=f"{stage.upper()} exceeded {timeout_s:g} seconds",
                status_code=504,
            ) from exc

    async def _tts_audio_bytes(self, tts: dict[str, object]) -> bytes:
        encoded = str(tts.get("audio_b64") or "").strip()
        if encoded:
            try:
                return base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise Go2HardwareVoiceTurnError(
                    stage="tts",
                    message="TTS returned invalid base64 audio",
                ) from exc
        audio_url = str(tts.get("audio_url") or "").strip()
        if not audio_url:
            raise Go2HardwareVoiceTurnError(
                stage="tts", message="TTS returned no playable audio"
            )
        return await self._run_sync_stage(
            "tts",
            self._tts_timeout_s,
            self._audio_url_loader,
            audio_url,
            self._tts_timeout_s,
        )

    @staticmethod
    def _capture_as_wav(capture: AudioCapture) -> bytes:
        if "planar" in capture.format or capture.format not in {
            "pcm_s16le",
            "pcm_s32le",
        }:
            raise Go2HardwareVoiceTurnError(
                stage="recording",
                message=f"unsupported Go2 microphone format: {capture.format}",
                status_code=422,
            )
        output = io.BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(capture.channels)
            wav_file.setsampwidth(capture.sample_width_bytes)
            wav_file.setframerate(capture.sample_rate_hz)
            wav_file.writeframes(capture.data)
        return output.getvalue()

    @staticmethod
    def _load_audio_url(url: str, timeout_s: float) -> bytes:
        if not url.lower().startswith(("https://", "http://")):
            raise ValueError("TTS audio URL must use HTTP or HTTPS")
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            return response.read(10 * 1024 * 1024 + 1)[: 10 * 1024 * 1024]

    @staticmethod
    def _positive(value: float, name: str) -> float:
        normalized = float(value)
        if normalized <= 0:
            raise ValueError(f"{name} must be greater than zero")
        return normalized
