from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import time
import wave

import pytest

from app.webrtc.voice_intent import (
    AgentTurn,
    CompanionAgentClient,
    CompanionLifecycleSnapshot,
    CompanionLifecycleState,
    CompanionSpeechCache,
    CompanionSpeechRenderer,
    HealthNewASRService,
    HealthNewTTSService,
    HealthNewWeatherCache,
    WeatherSnapshot,
    VoiceIntent,
    VoiceIntentAdapter,
    VoiceFastIntentRouter,
    WakeWordMatcher,
)
from tools.go2_wireless_runtime import RuntimeConsole


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return FakeResponse(self.responses.pop(0))

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return FakeResponse(self.responses.pop(0))


def snapshot(state=CompanionLifecycleState.IDLE, **overrides):
    values = {
        "state": state,
        "webrtc_connected": True,
        "uwb_fresh": True,
        "uwb_valid": True,
        "fall_active": False,
        "manual_takeover": False,
        "motion_writer_available": True,
    }
    values.update(overrides)
    return CompanionLifecycleSnapshot(**values)


def test_fast_router_accepts_explicit_start_but_rejects_negated_or_ambiguous_text() -> None:
    start = VoiceFastIntentRouter.route("小康，陪我出去走走。")
    assert start is not None
    assert start.intent is VoiceIntent.START_COMPANION
    assert start.raw["source"] == "local_explicit_command_router"

    assert VoiceFastIntentRouter.route("小康，不要陪我出去走走。") is None
    assert VoiceFastIntentRouter.route("今天天气怎么样？") is None
    assert WakeWordMatcher.matches("小汤。") is True


@pytest.mark.parametrize(
    ("phrase", "intent"),
    [
        ("跟我走", VoiceIntent.START_COMPANION),
        ("陪我散步", VoiceIntent.START_COMPANION),
        ("出去转转", VoiceIntent.START_COMPANION),
        ("开启伴随", VoiceIntent.START_COMPANION),
        ("开始伴随", VoiceIntent.START_COMPANION),
        ("停下来", VoiceIntent.STOP_COMPANION),
        ("不用跟了", VoiceIntent.STOP_COMPANION),
        ("接着走", VoiceIntent.RESUME_COMPANION),
        ("继续跟着我", VoiceIntent.RESUME_COMPANION),
        ("恢复伴随", VoiceIntent.RESUME_COMPANION),
        ("帮我叫家人", VoiceIntent.CALL_FAMILY),
    ],
)
def test_fast_router_accepts_only_finite_command_synonyms(
    phrase: str, intent: VoiceIntent
) -> None:
    turn = VoiceFastIntentRouter.route(phrase)
    assert turn is not None
    assert turn.intent is intent
    assert turn.raw["source"] == "local_explicit_command_router"


def test_wake_word_matcher_uses_exact_confusion_whitelist_not_substrings() -> None:
    for accepted in ("小康", "小仓。", "小汤！", "晓康", "小刚"):
        assert WakeWordMatcher.matches(accepted) is True

    for rejected in ("小张", "老康", "健康", "我要小康复训练", "你好小康"):
        assert WakeWordMatcher.matches(rejected) is False


def test_readonly_voice_intent_command_needs_no_second_confirmation(
    monkeypatch, capsys
) -> None:
    class Runtime:
        def status(self):
            return {
                "robotIp": "192.168.8.252",
                "connected": True,
                "connectionCount": 1,
                "dataChannelReady": True,
                "sportStateReady": True,
                "videoReady": True,
            }

    class Controller:
        def emergency_stop(self):
            return 0

    console = RuntimeConsole(
        runtime=Runtime(),  # type: ignore[arg-type]
        service=object(),  # type: ignore[arg-type]
        controller=Controller(),  # type: ignore[arg-type]
        video_host="127.0.0.1",
        video_port=8093,
        lan_ip=None,
    )
    calls: list[str] = []
    monkeypatch.setattr(console, "_voice_intent_gate", lambda: calls.append("voice"))
    commands = iter(["VOICE_INTENT_GATE", "EXIT"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))

    assert console.run() == 0
    assert calls == ["voice"]
    output = capsys.readouterr().out
    assert "VOICE_INTENT_GATE: read-only; confirmation not required" in output
    assert "Type VOICE_INTENT_READONLY_GATE" not in output


def test_voice_gate_captures_one_complete_utterance_locally(
    monkeypatch, capsys
) -> None:
    class Runtime:
        def __init__(self):
            self.played = []
            self.spoken = []

        def play_audio_file(self, path, **_kwargs):
            self.played.append(Path(path).name)
            return 0

        def speak(self, text):
            self.spoken.append(text)
            return 0

    class ASR:
        def status(self):
            raise AssertionError("live gate must not wait for /voice/status")

        def transcribe(self, _path):
            return "小康，陪我出去走走。"

    runtime = Runtime()
    console = RuntimeConsole(
        runtime=runtime,  # type: ignore[arg-type]
        service=object(),  # type: ignore[arg-type]
        controller=object(),  # type: ignore[arg-type]
        video_host="127.0.0.1",
        video_port=8093,
        lan_ip=None,
        asr_service=ASR(),  # type: ignore[arg-type]
        agent_client=None,
    )
    captures = []

    def capture(**kwargs):
        captures.append(kwargs)
        return SimpleNamespace(
            path=Path("command.wav"),
            speech_detected=True,
            trailing_silence_seconds=0.3,
        )

    monkeypatch.setattr(console, "_mic_gate", capture)
    monkeypatch.setattr(console, "_voice_lifecycle_snapshot", lambda: snapshot())

    console._voice_intent_gate()

    output = capsys.readouterr().out
    assert "VOICE_STAGE: SINGLE_UTTERANCE_LISTENING" in output
    assert "ASR_STATUS_CHECK: skipped_live_path" in output
    assert "VOICE_MIC_OPEN_BEGIN:" in output
    assert "VOICE_RECORD_DONE:" in output
    assert "TRANSCRIPT: 小康，陪我出去走走。" in output
    assert "VOICE_T0_VAD_END:" in output
    assert "VOICE_T1_ASR_FINAL:" in output
    assert "VOICE_T2_INTENT_READY:" in output
    assert "ASR_LATENCY_MS:" in output
    assert "INTENT_LATENCY_MS:" in output
    assert "VOICE_VAD_TRAILING_SILENCE_MS: 300" in output
    assert "INTENT_ROUTE_BEGIN:" in output
    assert "INTENT_ROUTE_END:" in output
    assert "INTENT_ROUTE_MS:" in output
    assert "FAST_PATH=true" in output
    assert "AGENT_BYPASSED=true" in output
    assert "HEALTH_NEW_SKIPPED: local_explicit_command" in output
    assert "VOICE_FEEDBACK_SKIPPED: read_only_gate" in output
    assert "INTENT: START_COMPANION" in output
    assert len(captures) == 1
    assert captures[0]["vad_trailing_silence_seconds"] == pytest.approx(0.3)
    assert runtime.played == []
    assert runtime.spoken == []


def test_wav_duration_uses_physical_pcm_when_streaming_header_is_unbounded(
    tmp_path: Path,
) -> None:
    wav = tmp_path / "streaming.wav"
    pcm = b"\0\0" * 24000
    wav.write_bytes(
        b"RIFF"
        + (0x7FFFFFFF).to_bytes(4, "little")
        + b"WAVEfmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + (24000).to_bytes(4, "little")
        + (48000).to_bytes(4, "little")
        + (2).to_bytes(2, "little")
        + (16).to_bytes(2, "little")
        + b"data"
        + (0x7FFFFFFF).to_bytes(4, "little")
        + pcm
    )

    assert RuntimeConsole._wav_duration_seconds(wav) == 1.0


def test_start_speech_uses_stable_preset_when_dynamic_audiohub_upload_times_out(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    dynamic = tmp_path / "start_companion_current.wav"
    stable = tmp_path / "START_COMPANION.wav"
    dynamic.write_bytes(b"RIFF" + b"\0" * 40)
    stable.write_bytes(b"RIFF" + b"\0" * 40)

    class Runtime:
        def __init__(self):
            self.played = []

        def play_audio_file(self, path, **_kwargs):
            name = Path(path).name
            self.played.append(name)
            if name == dynamic.name:
                raise TimeoutError("dynamic upload too slow")
            return 0

    class SpeechCache:
        def lookup_start(self):
            return {
                "ready": True,
                "path": dynamic,
                "text": "完整动态回复",
                "age_seconds": 1.0,
                "last_error": None,
            }

    runtime = Runtime()
    console = RuntimeConsole(
        runtime=runtime,  # type: ignore[arg-type]
        service=object(),  # type: ignore[arg-type]
        controller=object(),  # type: ignore[arg-type]
        video_host="127.0.0.1",
        video_port=8093,
        lan_ip=None,
        speech_cache=SpeechCache(),  # type: ignore[arg-type]
    )
    monkeypatch.setattr("tools.go2_wireless_runtime.VOICE_PRESET_DIR", tmp_path)

    source = console._play_cached_start(
        start_ack=tmp_path / "START_ACK.wav",
        end_of_speech=time.monotonic(),
    )

    assert source == "local_start_fallback_audiohub_cache_miss"
    assert runtime.played == [dynamic.name, stable.name]
    output = capsys.readouterr().out
    assert "GO2_AUDIO_COMMAND_FAILED: speech_cache" in output
    assert "AGENT_REPLY_FALLBACK: stable_local_preset" in output


def test_health_new_asr_posts_wav_to_existing_endpoint(tmp_path: Path) -> None:
    wav = tmp_path / "turn.wav"
    wav.write_bytes(b"RIFF" + b"x" * 200)
    session = FakeSession([{"ok": True, "text": "小康，陪我走走"}])

    transcript = HealthNewASRService(
        "http://127.0.0.1:8000", session=session
    ).transcribe(wav)

    assert transcript == "小康，陪我走走"
    assert session.calls[0][1].endswith("/api/v1/voice/asr")
    assert "files" in session.calls[0][2]
    assert session.calls[0][2]["timeout"] == 8.0


def test_health_new_asr_downmixes_48k_stereo_before_upload(tmp_path: Path) -> None:
    source = tmp_path / "go2-mic.wav"
    with wave.open(str(source), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes(
            (1000).to_bytes(2, "little", signed=True) * 48000 * 2
        )

    class InspectSession:
        observed = None

        def post(self, _url, **kwargs):
            _name, upload, _mime = kwargs["files"]["file"]
            with wave.open(upload, "rb") as stream:
                self.observed = {
                    "channels": stream.getnchannels(),
                    "rate": stream.getframerate(),
                    "frames": stream.getnframes(),
                }
            return FakeResponse({"ok": True, "text": "小康"})

    session = InspectSession()
    transcript = HealthNewASRService(
        "http://127.0.0.1:8765", session=session
    ).transcribe(source)

    assert transcript == "小康"
    assert session.observed == {"channels": 1, "rate": 16000, "frames": 16000}


def test_health_new_tts_generates_then_reuses_local_wav(tmp_path: Path) -> None:
    wav = b"RIFF" + b"\0" * 4 + b"WAVE" + b"\0" * 32
    session = FakeSession(
        [
            {
                "ok": True,
                "audio_b64": base64.b64encode(wav).decode("ascii"),
                "fmt": "wav",
                "provider": "dashscope/qwen3-tts-flash",
                "voice": "Cherry",
            }
        ]
    )
    service = HealthNewTTSService(
        "http://127.0.0.1:8765",
        cache_dir=tmp_path,
        voice="Cherry",
        session=session,
    )

    first_path, first_cached = service.synthesize_to_wav("我在呢。")
    second_path, second_cached = service.synthesize_to_wav("我在呢。")

    assert first_path == second_path
    assert first_path.read_bytes() == wav
    assert first_cached is False
    assert second_cached is True
    assert len(session.calls) == 1
    assert session.calls[0][1].endswith("/api/v1/voice/tts")
    assert session.calls[0][2]["json"]["voice"] == "Cherry"


def test_companion_speech_cache_atomically_keeps_last_valid_wav(tmp_path: Path) -> None:
    generated = tmp_path / "generated.wav"
    with wave.open(str(generated), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\1\0" * 160)

    class TTS:
        def __init__(self):
            self.fail = False
            self.texts = []

        def synthesize_to_wav(self, text):
            self.texts.append(text)
            if self.fail:
                raise RuntimeError("offline")
            return generated, False

    class Weather:
        def snapshot(self):
            return WeatherSnapshot(
                city="北京",
                weather="sunny",
                description="晴",
                temperature_c=26,
                wind_level=2,
                provider="qweather",
                fetched_monotonic=0.0,
            )

    tts = TTS()
    preloaded = []
    values = iter(
        [
            datetime(2026, 8, 27, 17, 13),
            datetime(2026, 8, 27, 17, 14),
        ]
    )
    cache = CompanionSpeechCache(
        tts,  # type: ignore[arg-type]
        Weather(),  # type: ignore[arg-type]
        elder_name="李四",
        cache_dir=tmp_path / "speech-cache",
        now_provider=lambda: next(values),
        wall_clock=lambda: 1000.0,
        on_ready=lambda path: preloaded.append(Path(path).name),
    )

    assert cache.refresh() is True
    first = cache.lookup_start()
    first_bytes = Path(first["path"]).read_bytes()
    assert first["ready"] is True
    assert first["age_seconds"] == 0.0
    assert preloaded == ["start_companion_current.wav"]
    assert "下午五点十三分" in tts.texts[0]

    tts.fail = True
    assert cache.refresh(force=True) is False
    second = cache.lookup_start()
    assert second["ready"] is True
    assert Path(second["path"]).read_bytes() == first_bytes
    assert "RuntimeError: offline" in second["last_error"]


def test_existing_speech_cache_delays_background_tts_refresh(tmp_path: Path) -> None:
    class TTS:
        def synthesize_to_wav(self, _text):
            raise AssertionError("existing cache must not refresh immediately")

    class Weather:
        def snapshot(self):
            return None

    cache = CompanionSpeechCache(
        TTS(),  # type: ignore[arg-type]
        Weather(),  # type: ignore[arg-type]
        elder_name="李四",
        cache_dir=tmp_path,
        refresh_seconds=300.0,
    )
    with wave.open(str(cache.current_path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\0\0" * 160)

    observed_waits = []

    class StopAfterFirstWait:
        def is_set(self):
            return False

        def wait(self, seconds):
            observed_waits.append(seconds)
            return True

    cache._stop = StopAfterFirstWait()  # type: ignore[assignment]
    cache._refresh_loop()

    assert observed_waits == [300.0]


def test_weather_cache_and_start_renderer_use_time_and_cached_beijing_weather() -> None:
    session = FakeSession(
        [
            {
                "city": "北京",
                "weather": "sunny",
                "description": "晴",
                "temperature_c": 26,
                "wind_level": 2,
                "provider": "qweather",
            }
        ]
    )
    cache = HealthNewWeatherCache(
        "http://127.0.0.1:8765",
        city="北京",
        session=session,
        clock=lambda: 100.0,
    )

    assert cache.refresh() is True
    weather = cache.snapshot()
    assert weather is not None
    reply = CompanionSpeechRenderer.render_start(
        elder_name="李四",
        weather=weather,
        now=datetime(2026, 8, 27, 15, 20),
    )

    assert reply == (
        "好的，李四。现在是下午三点二十分。"
        "北京今天晴，气温26摄氏度，天气适合出行。"
        "我来陪您出去走走，路上请注意安全。"
    )


def test_start_renderer_uses_warning_instead_of_suitable_for_rain() -> None:
    reply = CompanionSpeechRenderer.render_start(
        elder_name="李四",
        weather=WeatherSnapshot(
            city="北京",
            weather="rain",
            description="小雨",
            temperature_c=18.0,
            wind_level=3,
            provider="qweather",
            fetched_monotonic=0.0,
        ),
        now=datetime(2026, 8, 27, 9, 5),
    )

    assert "上午九点五分" in reply
    assert "带好雨具并注意路滑" in reply
    assert "天气适合出行" not in reply


def test_start_renderer_treats_33_degrees_as_heat_warning() -> None:
    reply = CompanionSpeechRenderer.render_start(
        elder_name="李四",
        weather=WeatherSnapshot(
            city="北京",
            weather="sunny",
            description="晴",
            temperature_c=33.0,
            wind_level=4,
            provider="qweather",
            fetched_monotonic=0.0,
        ),
        now=datetime(2026, 8, 27, 16, 11),
    )

    assert "天气较热" in reply
    assert "注意防晒并及时补充水分" in reply
    assert "天气适合出行" not in reply


def test_agent_client_accepts_only_frozen_intent_and_sends_robot_context() -> None:
    session = FakeSession(
        [
            {
                "reply": "好的，我陪您。",
                "intent": "START_COMPANION",
                "intent_confidence": 0.98,
                "intent_scope": "companion",
            }
        ]
    )
    client = CompanionAgentClient(
        "http://127.0.0.1:8000",
        elder_id="elder01_02",
        session_id="voice-demo",
        session=session,
    )

    turn = client.text_turn("陪我走走", snapshot())

    assert turn.intent is VoiceIntent.START_COMPANION
    payload = session.calls[0][2]["json"]
    assert payload["robot_state"] == "IDLE"
    assert payload["companion_active"] is False
    assert payload["resume_required"] is False


def test_agent_client_rejects_non_whitelisted_action() -> None:
    session = FakeSession([{"reply": "", "intent": "MOVE", "intent_confidence": 1}])
    client = CompanionAgentClient(
        "http://localhost:8000",
        elder_id="elder",
        session_id="session",
        session=session,
    )

    with pytest.raises(RuntimeError, match="non-whitelisted"):
        client.text_turn("向前走", snapshot())


def turn(intent: VoiceIntent) -> AgentTurn:
    return AgentTurn("text", "reply", intent, 1.0, "companion", {})


def test_lifecycle_start_only_from_idle_and_resume_only_from_safe_wait_resume() -> None:
    adapter = VoiceIntentAdapter()

    assert adapter.authorize(turn(VoiceIntent.START_COMPANION), snapshot()).authorized
    assert not adapter.authorize(
        turn(VoiceIntent.START_COMPANION), snapshot(CompanionLifecycleState.FOLLOWING)
    ).authorized
    assert adapter.authorize(
        turn(VoiceIntent.RESUME_COMPANION),
        snapshot(CompanionLifecycleState.WAIT_RESUME),
    ).authorized
    assert not adapter.authorize(
        turn(VoiceIntent.RESUME_COMPANION),
        snapshot(CompanionLifecycleState.WAIT_RESUME, fall_active=True),
    ).authorized


def test_stop_is_authorized_but_adapter_never_executes_or_emits_velocity() -> None:
    decision = VoiceIntentAdapter().authorize(
        turn(VoiceIntent.STOP_COMPANION),
        snapshot(CompanionLifecycleState.FOLLOWING, uwb_fresh=False),
    )

    assert decision.authorized is True
    assert decision.executed is False
    assert "vx" not in decision.to_dict()
    assert "wz" not in decision.to_dict()


def test_unconfigured_asr_fails_gate_without_raising_or_executing(capsys) -> None:
    class UnconfiguredASR:
        def status(self):
            return {"configured": False}

    console = RuntimeConsole(
        runtime=object(),  # type: ignore[arg-type]
        service=object(),  # type: ignore[arg-type]
        controller=object(),  # type: ignore[arg-type]
        video_host="127.0.0.1",
        video_port=8093,
        lan_ip=None,
        asr_service=UnconfiguredASR(),  # type: ignore[arg-type]
        agent_client=object(),  # type: ignore[arg-type]
    )

    console._voice_intent_gate()

    output = capsys.readouterr().out
    assert "VOICE_INTENT_GATE_FAILED" in output
    assert "INTENT: NONE" in output
    assert "EXECUTED: false" in output
    assert "WIRELESS_RUNTIME=CONTINUES" in output


def test_voice_gate_rejects_silence_without_calling_asr(monkeypatch, capsys) -> None:
    class ASR:
        def status(self):
            return {"configured": True}

        def transcribe(self, _path):
            raise AssertionError("silent capture must not be sent to ASR")

    console = RuntimeConsole(
        runtime=object(),  # type: ignore[arg-type]
        service=object(),  # type: ignore[arg-type]
        controller=object(),  # type: ignore[arg-type]
        video_host="127.0.0.1",
        video_port=8093,
        lan_ip=None,
        asr_service=ASR(),  # type: ignore[arg-type]
        agent_client=object(),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        console,
        "_mic_gate",
        lambda **_kwargs: SimpleNamespace(
            path=Path("silent.wav"),
            speech_detected=False,
        ),
    )

    console._voice_intent_gate()

    output = capsys.readouterr().out
    assert "VOICE_COMMAND_REJECTED: no_speech_detected" in output
    assert "EXECUTED: false" in output


def test_voice_intent_gate_is_read_only_and_does_not_play_control_feedback(
    monkeypatch, capsys
) -> None:
    class Runtime:
        def __init__(self):
            self.spoken = []
            self.played = []

        def play_audio_file(self, path, **_kwargs):
            self.played.append(Path(path).name)
            return 0

        def speak(self, text):
            self.spoken.append(text)
            return 0

    class ASR:
        def status(self):
            return {"configured": True}

        def transcribe(self, _path):
            return "小康，陪我出去走走。"

    class Agent:
        def text_turn(self, transcript, _lifecycle):
            return AgentTurn(
                transcript,
                "好的，我来为您准备。",
                VoiceIntent.START_COMPANION,
                0.95,
                "companion",
                {},
            )

    class MotionMustNotBeUsed:
        def __getattr__(self, name):
            raise AssertionError(f"motion controller used during read-only gate: {name}")

    runtime = Runtime()
    console = RuntimeConsole(
        runtime=runtime,  # type: ignore[arg-type]
        service=object(),  # type: ignore[arg-type]
        controller=MotionMustNotBeUsed(),  # type: ignore[arg-type]
        video_host="127.0.0.1",
        video_port=8093,
        lan_ip=None,
        asr_service=ASR(),  # type: ignore[arg-type]
        agent_client=Agent(),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        console,
        "_mic_gate",
        lambda seconds=5.0, **_kwargs: SimpleNamespace(path=Path("turn.wav")),
    )
    monkeypatch.setattr(console, "_voice_lifecycle_snapshot", lambda: snapshot())

    console._voice_intent_gate()

    output = capsys.readouterr().out
    assert runtime.spoken == []
    assert runtime.played == []
    assert "VOICE_FEEDBACK_SKIPPED: read_only_gate" in output
    assert "AGENT_REPLY_PLAYBACK: complete" in output
    assert "INTENT: START_COMPANION" in output
    assert "EXECUTED: false" in output


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("我没事", VoiceIntent.I_AM_OK),
        ("不用帮忙", VoiceIntent.I_AM_OK),
        ("帮帮我", VoiceIntent.REQUEST_HELP),
        ("联系家人", VoiceIntent.CALL_FAMILY),
    ],
)
def test_fast_router_covers_frozen_emergency_intents(text, expected) -> None:
    result = VoiceFastIntentRouter.route(text)
    assert result is not None
    assert result.intent is expected


def test_voice_control_executes_lifecycle_before_fixed_feedback(
    monkeypatch, capsys, tmp_path
) -> None:
    feedback = tmp_path / "START_COMPANION.wav"
    feedback.write_bytes(b"RIFF\0\0\0\0WAVE" + b"\0" * 40)
    events: list[str] = []

    class Runtime:
        def play_audio_file(self, path, **_kwargs):
            events.append(f"audio:{Path(path).name}")
            return 0

    class ASR:
        def transcribe(self, _path):
            return "小康，开启伴随。"

    console = RuntimeConsole(
        runtime=Runtime(),  # type: ignore[arg-type]
        service=object(),  # type: ignore[arg-type]
        controller=object(),  # type: ignore[arg-type]
        video_host="127.0.0.1",
        video_port=8093,
        lan_ip=None,
        asr_service=ASR(),  # type: ignore[arg-type]
        agent_client=None,
    )
    monkeypatch.setattr("tools.go2_wireless_runtime.VOICE_PRESET_DIR", tmp_path)
    monkeypatch.setattr(
        console,
        "_mic_gate",
        lambda **_kwargs: SimpleNamespace(
            path=Path("command.wav"),
            speech_detected=True,
            trailing_silence_seconds=0.3,
        ),
    )
    monkeypatch.setattr(console, "_voice_lifecycle_snapshot", lambda: snapshot())

    def apply_voice_intent(intent: str):
        events.append(f"control:{intent}")
        return {"executed": True}

    monkeypatch.setattr(console, "apply_voice_intent", apply_voice_intent)

    console._voice_intent_gate(execute=True)

    output = capsys.readouterr().out
    assert events == [
        "control:START_COMPANION",
        "audio:START_COMPANION.wav",
    ]
    assert "VOICE_T3_LIFECYCLE_ACCEPTED:" in output
    assert "VOICE_T4_CONTROL_EXECUTED:" in output
    assert "VOICE_T5_AUDIO_ACCEPTED:" in output
    assert "CONTROL_LATENCY_MS:" in output
    assert "SPEECH_FEEDBACK_LATENCY_MS:" in output
    assert "EXECUTED: true" in output


def test_control_feedback_uses_rejection_preset_when_lifecycle_rejects(
    monkeypatch, tmp_path
) -> None:
    rejected = tmp_path / "START_REJECTED.wav"
    rejected.write_bytes(b"placeholder")
    monkeypatch.setattr("tools.go2_wireless_runtime.VOICE_PRESET_DIR", tmp_path)

    selected = RuntimeConsole._control_feedback_preset(
        VoiceIntent.START_COMPANION,
        authorized=False,
        executed=False,
    )

    assert selected == rejected
