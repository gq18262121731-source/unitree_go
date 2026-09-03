from __future__ import annotations

import asyncio
import base64
from pathlib import Path
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.go2_companion_api import router
from backend.dependencies import get_go2_hardware_voice_turn_service
from backend.services.go2_companion_intent_service import (
    Go2CompanionIntentService,
    Go2VoiceIntent,
)
from backend.services.go2_hardware_voice_turn_service import (
    Go2HardwareVoiceTurnError,
    Go2HardwareVoiceTurnService,
)
from backend.services.robot_audio import (
    AudioCapture,
    AudioCaptureError,
    AudioSink,
    MockAudioSink,
    MockAudioSource,
    RobotAudioService,
    RobotAudioServiceError,
    utc_now,
)


def _capture() -> AudioCapture:
    return AudioCapture(
        audio_id="capture-001",
        data=b"\x01\x00" * 1600,
        format="pcm_s16le",
        sample_rate_hz=16_000,
        channels=1,
        sample_width_bytes=2,
        captured_at=utc_now(),
    )


class _OrderedSink(AudioSink):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events

    async def _play_file(self, audio_file: Path) -> None:
        self.events.append("play_start")
        assert audio_file.read_bytes().startswith(b"RIFF")
        await asyncio.sleep(0)
        self.events.append("play_done")


class _OrderedSource:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def record_once(self, *, max_duration_s: float, silence_timeout_s: float):
        assert max_duration_s > silence_timeout_s
        self.events.append("record_start")
        await asyncio.sleep(0)
        self.events.append("record_done")
        return _capture()


class _Pipeline:
    def __init__(self, events: list[str], *, transcript: str = "陪我去散步吧") -> None:
        self.events = events
        self.transcript = transcript
        self.asr_calls = 0
        self.tts_calls = 0

    def synthesize_reply(self, text: str, *, voice: str):
        self.tts_calls += 1
        self.events.append("tts")
        return {
            "ok": True,
            "audio_b64": base64.b64encode(b"RIFF-test-wave").decode("ascii"),
            "audio_url": "",
            "fmt": "wav",
            "provider": "mock/tts",
            "voice": voice,
        }

    def transcribe_audio(self, audio_bytes: bytes, *, audio_format: str, allow_empty: bool):
        self.asr_calls += 1
        self.events.append("asr")
        assert audio_bytes.startswith(b"RIFF")
        assert audio_format == "wav"
        return {"ok": True, "text": self.transcript, "provider": "mock/asr"}

    def generate_reply(self, transcript: str, **kwargs):
        self.events.append("llm")
        return {
            "reply": f"收到：{transcript}",
            "llm_provider": "mock/qwen",
            "llm_model": "qwen-test",
            "grounded": False,
            "context": None,
            "health_metrics": None,
        }


def _hardware_service(
    events: list[str],
    *,
    transcript: str = "陪我去散步吧",
    guard_ms: int = 0,
) -> tuple[Go2HardwareVoiceTurnService, _Pipeline, RobotAudioService]:
    pipeline = _Pipeline(events, transcript=transcript)
    audio = RobotAudioService(
        source=_OrderedSource(events),  # type: ignore[arg-type]
        sink=_OrderedSink(events),
        post_playback_silence_ms=guard_ms,
    )
    service = Go2HardwareVoiceTurnService(
        audio_service=audio,
        voice_service=pipeline,  # type: ignore[arg-type]
        asr_timeout_s=1,
        tts_timeout_s=1,
        dialogue_timeout_s=1,
    )
    return service, pipeline, audio


def _run_turn(service: Go2HardwareVoiceTurnService):
    return asyncio.run(
        service.process_turn(
            session_id="elder-001",
            voice="Serena",
            elder_id=None,
            device_mac=None,
            location_hint=None,
            prompt_text=None,
            fall_monitoring=False,
            max_duration_s=2,
            silence_timeout_s=0.2,
            playback_timeout_s=1,
        )
    )


def test_initialization_failures_are_independent_and_fail_gracefully() -> None:
    source_failed = RobotAudioService(
        source=None,
        sink=MockAudioSink(),
        source_initialization_error="source init failed",
    )
    sink_failed = RobotAudioService(
        source=MockAudioSource(),
        sink=None,
        sink_initialization_error="sink init failed",
    )

    source_status = asyncio.run(source_failed.status())
    sink_status = asyncio.run(sink_failed.status())

    assert source_status["go2_microphone"] == "disconnected"
    assert source_status["go2_speaker"] == "configured"
    assert sink_status["go2_microphone"] == "configured"
    assert sink_status["go2_speaker"] == "disconnected"


def test_unconfigured_hardware_fails_before_tts() -> None:
    events: list[str] = []
    pipeline = _Pipeline(events)
    service = Go2HardwareVoiceTurnService(
        audio_service=RobotAudioService(source=None, sink=None),
        voice_service=pipeline,  # type: ignore[arg-type]
    )

    with pytest.raises(Go2HardwareVoiceTurnError, match="microphone is not configured"):
        _run_turn(service)
    assert pipeline.tts_calls == 0


def test_speaker_playback_succeeds_and_returns_idle() -> None:
    audio = RobotAudioService(source=MockAudioSource(), sink=MockAudioSink())

    playback = asyncio.run(audio.play_audio(b"RIFF-test", timeout_s=1))
    status = asyncio.run(audio.status())

    assert playback["status"] == "finished"
    assert status["playing"] is False
    assert status["go2_speaker"] == "connected"
    assert status["last_error"] is None


def test_playback_finishes_before_recording_and_asr() -> None:
    events: list[str] = []
    service, pipeline, _ = _hardware_service(events)

    result = _run_turn(service)

    assert events.index("play_done") < events.index("record_start") < events.index("asr")
    assert pipeline.asr_calls == 1
    assert pipeline.tts_calls == 2
    assert result["intent"] == "START_COMPANION"
    assert result["intent_executed"] is False


def test_three_turns_never_run_asr_during_playback() -> None:
    events: list[str] = []
    service, pipeline, _ = _hardware_service(events, transcript="今天天气怎么样")

    for _ in range(3):
        _run_turn(service)

    active_playback = False
    for event in events:
        if event == "play_start":
            assert active_playback is False
            active_playback = True
        elif event == "play_done":
            active_playback = False
        elif event in {"record_start", "asr"}:
            assert active_playback is False
    assert pipeline.asr_calls == 3


def test_recording_timeout_releases_lock_and_returns_idle() -> None:
    audio = RobotAudioService(
        source=MockAudioSource(capture_delay_seconds=0.05),
        sink=MockAudioSink(),
    )

    with pytest.raises(RobotAudioServiceError, match="audio capture exceeded"):
        asyncio.run(audio.record_once(max_duration_s=0.001, silence_timeout_s=0.001))

    assert audio.state.value == "idle"
    assert asyncio.run(audio.status())["last_error"] is not None


def test_configured_guard_window_delays_recording_after_playback() -> None:
    class TimedSource:
        recorded_at = 0.0

        async def record(self, *, timeout_seconds: float | None = None):
            self.recorded_at = time.perf_counter()
            return _capture()

    source = TimedSource()
    audio = RobotAudioService(
        source=source,  # type: ignore[arg-type]
        sink=MockAudioSink(),
        post_playback_silence_ms=30,
    )

    async def scenario() -> float:
        await audio.play_audio(b"RIFF-test", timeout_s=1)
        playback_finished_at = time.perf_counter()
        await audio.record_once(max_duration_s=1, silence_timeout_s=0.1)
        return source.recorded_at - playback_finished_at

    assert asyncio.run(scenario()) >= 0.02


def test_cancel_stops_active_recording_and_returns_idle() -> None:
    async def scenario() -> None:
        audio = RobotAudioService(
            source=MockAudioSource(capture_delay_seconds=5),
            sink=MockAudioSink(),
        )
        task = asyncio.create_task(
            audio.record_once(max_duration_s=10, silence_timeout_s=1)
        )
        await asyncio.sleep(0)
        assert (await audio.status())["recording"] is True
        await audio.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert audio.state.value == "idle"

    asyncio.run(scenario())


def test_webrtc_disconnect_can_recover_on_next_recording() -> None:
    class FlakySource:
        calls = 0

        async def record(self, *, timeout_seconds: float | None = None):
            self.calls += 1
            if self.calls == 1:
                raise AudioCaptureError("WebRTC disconnected")
            return _capture()

    source = FlakySource()
    audio = RobotAudioService(source=source, sink=MockAudioSink())  # type: ignore[arg-type]

    with pytest.raises(RobotAudioServiceError, match="WebRTC disconnected"):
        asyncio.run(audio.record_once(max_duration_s=1, silence_timeout_s=0.1))
    capture = asyncio.run(audio.record_once(max_duration_s=1, silence_timeout_s=0.1))

    assert capture.audio_id == "capture-001"
    assert asyncio.run(audio.status())["go2_microphone"] == "connected"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("陪我去散步吧", Go2VoiceIntent.START_COMPANION),
        ("停一下", Go2VoiceIntent.STOP_COMPANION),
        ("继续走吧", Go2VoiceIntent.RESUME_COMPANION),
        ("帮帮我", Go2VoiceIntent.REQUEST_HELP),
        ("联系家人", Go2VoiceIntent.CALL_FAMILY),
        ("我没事", Go2VoiceIntent.CHAT),
    ],
)
def test_frozen_companion_intents(text: str, expected: Go2VoiceIntent) -> None:
    result = Go2CompanionIntentService().classify(text)
    assert result.intent is expected


def test_fall_i_am_ok_is_not_resume_and_empty_is_no_response() -> None:
    classifier = Go2CompanionIntentService()

    safe = classifier.classify("我没事", fall_monitoring=True)
    explicit_resume = classifier.classify("我没事，继续走吧", fall_monitoring=True)
    no_response = classifier.classify("", fall_monitoring=True)

    assert safe.intent is Go2VoiceIntent.I_AM_OK
    assert safe.intent is not Go2VoiceIntent.RESUME_COMPANION
    assert explicit_resume.intent is Go2VoiceIntent.RESUME_COMPANION
    assert no_response.intent is Go2VoiceIntent.NO_RESPONSE


def test_asr_timeout_returns_idle_without_playing_a_reply() -> None:
    events: list[str] = []
    service, pipeline, audio = _hardware_service(events)

    def slow_asr(*args, **kwargs):
        time.sleep(0.05)
        return {"ok": True, "text": "你好", "provider": "mock/asr"}

    pipeline.transcribe_audio = slow_asr  # type: ignore[method-assign]
    service._asr_timeout_s = 0.001

    with pytest.raises(Go2HardwareVoiceTurnError, match="ASR exceeded"):
        _run_turn(service)
    assert audio.state.value == "idle"
    assert events.count("play_start") == 1


def test_tts_timeout_returns_idle_before_speaker_playback() -> None:
    events: list[str] = []
    service, pipeline, audio = _hardware_service(events)

    def slow_tts(*args, **kwargs):
        time.sleep(0.05)
        return {}

    pipeline.synthesize_reply = slow_tts  # type: ignore[method-assign]
    service._tts_timeout_s = 0.001

    with pytest.raises(Go2HardwareVoiceTurnError, match="TTS exceeded"):
        _run_turn(service)
    assert audio.state.value == "idle"
    assert "play_start" not in events


def test_cancelled_hardware_turn_cancels_recording() -> None:
    class BlockingSource:
        async def record_once(self, **kwargs):
            await asyncio.sleep(30)
            return _capture()

    async def scenario() -> None:
        events: list[str] = []
        pipeline = _Pipeline(events)
        audio = RobotAudioService(
            source=BlockingSource(),  # type: ignore[arg-type]
            sink=_OrderedSink(events),
            post_playback_silence_ms=0,
        )
        service = Go2HardwareVoiceTurnService(
            audio_service=audio,
            voice_service=pipeline,  # type: ignore[arg-type]
            asr_timeout_s=1,
            tts_timeout_s=1,
            dialogue_timeout_s=1,
        )
        task = asyncio.create_task(
            service.process_turn(
                session_id="elder-001",
                voice="Serena",
                elder_id=None,
                device_mac=None,
                location_hint=None,
                prompt_text=None,
                fall_monitoring=False,
                max_duration_s=5,
                silence_timeout_s=1,
                playback_timeout_s=1,
            )
        )
        while (await audio.status())["recording"] is False:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert audio.state.value == "idle"

    asyncio.run(scenario())


def test_go2_voice_turn_api_returns_structured_unexecuted_intent() -> None:
    events: list[str] = []
    service, _, _ = _hardware_service(events, transcript="联系家人")
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_go2_hardware_voice_turn_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/api/v1/go2-companion/go2-voice-turn",
        json={"session_id": "elder-001"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "CALL_FAMILY"
    assert payload["intent_executed"] is False
    assert payload["audio_status"]["audio_mode"] == "half_duplex"
