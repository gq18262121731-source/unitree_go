from __future__ import annotations

from pathlib import Path
import time
import wave

import numpy as np

from app.iot.protocol_layer import MockTransport
from app.iot.mqtt_contract import MqttContractMessage, contract_topic
from app.voice.local_voice import (
    Go2ASRAudioBridge,
    LocalVoicePipeline,
    LocalVoiceSessionManager,
    MicrophoneCaptureResult,
    MicrophoneDeviceInfo,
    VoiceState,
    _pcm16_mono_16k_from_pcm,
)
from app.webrtc.voice_intent import WAKE_WORD, WakeWordMatcher


class FakeMicrophoneSource:
    def __init__(self, captures: list[MicrophoneCaptureResult]) -> None:
        self.captures = list(captures)
        self.device_calls: list[dict[str, object]] = []
        self.record_calls: list[dict[str, object]] = []

    def list_devices(self) -> list[MicrophoneDeviceInfo]:
        return [
            MicrophoneDeviceInfo(
                index=0,
                name="Microphone Array",
                channels=1,
                formats=0x00000001,
                reserved=0,
            )
        ]

    def record_utterance(self, output_path: Path, **kwargs):
        self.record_calls.append({"output_path": output_path, **kwargs})
        if not self.captures:
            raise RuntimeError("no more captures queued")
        capture = self.captures.pop(0)
        output_path = Path(output_path)
        output_path.write_bytes(b"RIFF" + b"\0" * 40)
        return MicrophoneCaptureResult(
            path=str(output_path),
            sample_rate=capture.sample_rate,
            channels=capture.channels,
            duration_seconds=capture.duration_seconds,
            sample_count=capture.sample_count,
            frame_count=capture.frame_count,
            peak=capture.peak,
            rms=capture.rms,
            byte_count=capture.byte_count,
            vad_enabled=capture.vad_enabled,
            speech_detected=capture.speech_detected,
            endpoint_reason=capture.endpoint_reason,
            trailing_silence_seconds=capture.trailing_silence_seconds,
        )


def test_local_pipeline_can_use_funasr_backend(monkeypatch) -> None:
    from app.voice.local_voice import FunASRLocalASRService

    service = FunASRLocalASRService(model="paraformer-zh-streaming")
    called = {}

    def fake_load_model(self):
        called["loaded"] = True

        class Model:
            def generate(self, **kwargs):
                chunk = kwargs.get("input")
                text = "小康，跟我走" if getattr(chunk, "size", 0) else ""
                return [{"text": text}]

        return Model()

    import app.voice.local_voice as local_voice_module
    import sys
    import types
    import numpy as np

    monkeypatch.setattr(FunASRLocalASRService, "_load_model", fake_load_model)
    monkeypatch.setitem(
        sys.modules,
        "soundfile",
        types.SimpleNamespace(
            read=lambda *_args, **_kwargs: (np.ones(1600, dtype=np.float32), 16000)
        ),
    )
    assert service.transcribe("capture.wav") == "小康，跟我走"
    assert called["loaded"] is True


def test_funasr_warmup_loads_model_once(monkeypatch) -> None:
    from app.voice.local_voice import FunASRLocalASRService

    service = FunASRLocalASRService(model="paraformer-zh-streaming")
    calls = {"load": 0, "generate": 0}

    def fake_load_model(self):
        calls["load"] += 1

        class Model:
            def generate(self, **kwargs):
                calls["generate"] += 1
                assert kwargs["is_final"] is True
                assert kwargs["input"].size > 0
                return [{"text": ""}]

        return Model()

    monkeypatch.setattr(FunASRLocalASRService, "_load_model", fake_load_model)

    service.warmup()

    assert calls == {"load": 1, "generate": 1}


class FakeASR:
    def __init__(self, transcripts: list[str]) -> None:
        self.transcripts = list(transcripts)
        self.calls: list[Path] = []

    def transcribe(self, wav_path: str | Path) -> str:
        self.calls.append(Path(wav_path))
        if not self.transcripts:
            raise RuntimeError("no more transcripts queued")
        return self.transcripts.pop(0)


def _capture(
    text: str,
    *,
    speech_detected: bool = True,
    endpoint_reason: str = "vad_trailing_silence",
) -> MicrophoneCaptureResult:
    return MicrophoneCaptureResult(
        path=str(Path("capture.wav").resolve()),
        sample_rate=16000,
        channels=1,
        duration_seconds=0.8,
        sample_count=12800,
        frame_count=4,
        peak=2000,
        rms=1000.0,
        byte_count=25600,
        vad_enabled=True,
        speech_detected=speech_detected,
        endpoint_reason=endpoint_reason,
        trailing_silence_seconds=0.3,
    )


def test_wake_word_strip_uses_small_kang_prefix_only() -> None:
    wake_word, stripped = WakeWordMatcher.strip_wake_word("小康，我想出去走走")
    assert wake_word == WAKE_WORD
    assert stripped == "我想出去走走"


def test_local_session_wake_sentence_publishes_session_and_speech() -> None:
    transport = MockTransport()
    logs: list[str] = []
    manager = LocalVoiceSessionManager(transport, printer=logs.append)

    manager.process_transcript("小康，我想出去走走")

    assert manager.voice_state is VoiceState.ACTIVE_LISTENING
    assert [message.payload.get("event") for message in transport.published] == [
        "session_start",
        None,
    ]
    speech = transport.published[-1]
    assert speech.topic == "aiot/dog/DOG-LJG-001/speech"
    assert speech.payload["text"] == "我想出去走走"
    assert speech.payload["wake_word"] == "小康"
    assert speech.payload["is_wake_turn"] is True
    assert speech.payload["bypass_wake"] is False
    assert any("[VOICE] wake: 小康" in line for line in logs)


def test_local_session_wake_only_then_follow_up_reuses_same_session() -> None:
    transport = MockTransport()
    manager = LocalVoiceSessionManager(transport)

    manager.process_transcript("小康")
    first_session_id = transport.published[0].payload["session_id"]
    assert transport.published[0].payload["event"] == "session_start"
    assert len(transport.published) == 1

    manager.process_transcript("我想看看今天能不能出去")
    assert len(transport.published) == 2
    speech = transport.published[1]
    assert speech.payload["session_id"] == first_session_id
    assert speech.payload["turn"] == 1
    assert speech.payload["is_wake_turn"] is False
    assert speech.payload["text"] == "我想看看今天能不能出去"


def test_local_session_emergency_bypasses_wake_without_prior_session() -> None:
    transport = MockTransport()
    manager = LocalVoiceSessionManager(transport)

    manager.process_transcript("救命")

    assert [message.payload.get("event") for message in transport.published] == [
        "session_start",
        None,
    ]
    speech = transport.published[-1]
    assert speech.payload["text"] == "救命"
    assert speech.payload["bypass_wake"] is True


def test_go2_session_does_not_use_emergency_bypass() -> None:
    transport = MockTransport()
    manager = LocalVoiceSessionManager(
        transport,
        emergency_bypass_enabled=False,
    )

    manager.process_transcript("救命")

    assert transport.published == []
    assert manager.active_session_id is None


def test_voice_guard_silently_ignores_non_wake_speech_by_default() -> None:
    transport = MockTransport()
    logs: list[str] = []
    manager = LocalVoiceSessionManager(
        transport,
        emergency_bypass_enabled=False,
        printer=logs.append,
    )

    manager.process_transcript("旁边的人随便聊天")

    assert transport.published == []
    assert logs == []


def test_voice_session_returns_to_wake_guard_after_two_turns() -> None:
    transport = MockTransport()
    logs: list[str] = []
    manager = LocalVoiceSessionManager(
        transport,
        emergency_bypass_enabled=False,
        max_turns=2,
        printer=logs.append,
    )

    manager.process_transcript("小康，我想出去走走")
    manager.process_transcript("今天会下雨吗")

    assert [message.payload.get("event") for message in transport.published] == [
        "session_start",
        None,
        None,
        "session_end",
    ]
    assert transport.published[-1].payload["reason"] == "max_turns"
    assert transport.published[-1].payload["turns"] == 2
    assert manager.active_session_id is None
    assert manager.voice_state is VoiceState.WAKE_GUARD
    assert logs == [
        "[VOICE] wake: 小康",
        "[VOICE] user: 我想出去走走",
        "[VOICE] user: 今天会下雨吗",
        "[VOICE] session_end: max_turns",
    ]

    manager.process_transcript("那我什么时候回来")
    assert len(transport.published) == 4

    manager.process_transcript("小康，什么时候回来比较合适")
    assert transport.published[-1].payload["text"] == "什么时候回来比较合适"
    assert transport.published[-1].payload["turn"] == 1


def test_go2_audio_bridge_emits_partial_but_publishes_only_final(monkeypatch) -> None:
    transport = MockTransport()
    manager = LocalVoiceSessionManager(
        transport,
        emergency_bypass_enabled=False,
    )
    logs: list[str] = []

    class FakeStreamingSession:
        def __init__(self, _service) -> None:
            self.feed_calls = 0

        def feed_pcm(self, _pcm: bytes, *, sample_rate: int, channels: int) -> str:
            assert sample_rate == 16000
            assert channels == 1
            self.feed_calls += 1
            return "小康，我想出"

        def finish(self) -> str:
            return "小康，我想出去走走"

    monkeypatch.setattr(
        "app.voice.local_voice.FunASRStreamingSession",
        FakeStreamingSession,
    )
    bridge = Go2ASRAudioBridge(
        asr_service=object(),  # type: ignore[arg-type]
        session_manager=manager,
        printer=logs.append,
        vad_min_capture_seconds=0.2,
        vad_trailing_silence_seconds=0.2,
        queue_size=4,
        voice_debug=True,
    )
    bridge.start()
    try:
        silence = np.zeros(6400, dtype=np.int16).tobytes()
        speech = np.full(6400, 5000, dtype=np.int16).tobytes()
        bridge.push_pcm(silence, 16000, 1)
        bridge.push_pcm(speech, 16000, 1)
        bridge.push_pcm(speech, 16000, 1)
        bridge.push_pcm(silence, 16000, 1)
        bridge.push_pcm(silence, 16000, 1)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not transport.published:
            time.sleep(0.01)
    finally:
        bridge.stop()

    assert any(line.startswith("[ASR_PARTIAL]") for line in logs)
    assert [message.payload.get("event") for message in transport.published] == [
        "session_start",
        None,
    ]
    speech_message = transport.published[-1]
    assert speech_message.payload["text"] == "我想出去走走"
    assert speech_message.payload["bypass_wake"] is False


def test_go2_audio_bridge_survives_transcript_publish_exception(monkeypatch) -> None:
    logs: list[str] = []

    class Manager:
        def process_transcript(self, _transcript: str) -> None:
            raise RuntimeError("playback callback failed")

        def expire_if_timed_out(self) -> None:
            pass

    class FakeStreamingSession:
        def __init__(self, _service) -> None:
            pass

        def feed_pcm(self, _pcm: bytes, *, sample_rate: int, channels: int) -> str:
            assert sample_rate == 16000
            assert channels == 1
            return "小康"

        def finish(self) -> str:
            return "小康"

    monkeypatch.setattr(
        "app.voice.local_voice.FunASRStreamingSession",
        FakeStreamingSession,
    )
    bridge = Go2ASRAudioBridge(
        asr_service=object(),  # type: ignore[arg-type]
        session_manager=Manager(),  # type: ignore[arg-type]
        printer=logs.append,
        vad_min_capture_seconds=0.2,
        vad_trailing_silence_seconds=0.2,
        queue_size=8,
        voice_debug=True,
    )
    bridge.start()
    try:
        speech = np.full(6400, 5000, dtype=np.int16).tobytes()
        silence = np.zeros(6400, dtype=np.int16).tobytes()
        bridge.push_pcm(speech, 16000, 1)
        bridge.push_pcm(speech, 16000, 1)
        bridge.push_pcm(silence, 16000, 1)
        bridge.push_pcm(silence, 16000, 1)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not any(
            "final_publish_failed" in line for line in logs
        ):
            time.sleep(0.01)
        assert any("final_publish_failed" in line for line in logs)
        assert bridge.is_alive() is True
    finally:
        bridge.stop()


def test_go2_audio_bridge_clear_pending_audio_resets_current_stream(monkeypatch) -> None:
    transport = MockTransport()
    manager = LocalVoiceSessionManager(transport, emergency_bypass_enabled=False)
    feed_calls = 0
    finish_calls = 0

    class FakeStreamingSession:
        def __init__(self, _service) -> None:
            pass

        def feed_pcm(self, _pcm: bytes, *, sample_rate: int, channels: int) -> str:
            nonlocal feed_calls
            feed_calls += 1
            return "小康"

        def finish(self) -> str:
            nonlocal finish_calls
            finish_calls += 1
            return "小康"

    monkeypatch.setattr(
        "app.voice.local_voice.FunASRStreamingSession",
        FakeStreamingSession,
    )
    bridge = Go2ASRAudioBridge(
        asr_service=object(),  # type: ignore[arg-type]
        session_manager=manager,
        printer=lambda _message: None,
        vad_min_capture_seconds=0.2,
        vad_trailing_silence_seconds=0.2,
        queue_size=16,
    )
    bridge.start()
    try:
        speech = np.full(6400, 5000, dtype=np.int16).tobytes()
        silence = np.zeros(6400, dtype=np.int16).tobytes()
        bridge.push_pcm(speech, 16000, 1)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and feed_calls == 0:
            time.sleep(0.01)

        assert feed_calls > 0
        bridge.clear_pending_audio()
        bridge.push_pcm(silence, 16000, 1)
        bridge.push_pcm(silence, 16000, 1)
        time.sleep(0.2)
    finally:
        bridge.stop()

    assert finish_calls == 0
    assert transport.published == []


def test_go2_audio_bridge_warmup_runs_before_audio_thread() -> None:
    transport = MockTransport()
    manager = LocalVoiceSessionManager(
        transport,
        emergency_bypass_enabled=False,
    )
    logs: list[str] = []

    class FakeWarmupASR:
        def __init__(self) -> None:
            self.warmed = False

        def warmup(self) -> None:
            self.warmed = True

    asr_service = FakeWarmupASR()
    bridge = Go2ASRAudioBridge(
        asr_service=asr_service,  # type: ignore[arg-type]
        session_manager=manager,
        printer=logs.append,
    )

    bridge.warmup()

    assert asr_service.warmed is True
    assert logs == ["[ASR] warmup_start", "[ASR] warmup_ready"]


def test_go2_audio_bridge_keeps_one_utterance_across_short_pause(monkeypatch) -> None:
    transport = MockTransport()
    manager = LocalVoiceSessionManager(transport, emergency_bypass_enabled=False)
    logs: list[str] = []
    finish_count = 0

    class FakeStreamingSession:
        def __init__(self, _service) -> None:
            pass

        def feed_pcm(self, _pcm: bytes, *, sample_rate: int, channels: int) -> str:
            return "小康我想"

        def finish(self) -> str:
            nonlocal finish_count
            finish_count += 1
            return "小康，我想出去走走"

    monkeypatch.setattr(
        "app.voice.local_voice.FunASRStreamingSession",
        FakeStreamingSession,
    )
    bridge = Go2ASRAudioBridge(
        asr_service=object(),  # type: ignore[arg-type]
        session_manager=manager,
        printer=logs.append,
        vad_min_capture_seconds=0.2,
        vad_trailing_silence_seconds=0.9,
        queue_size=128,
        voice_debug=True,
    )
    speech = np.full(320, 5000, dtype=np.int16).tobytes()
    silence = np.zeros(320, dtype=np.int16).tobytes()
    bridge.start()
    try:
        for frame in [speech] * 15 + [silence] * 20:
            bridge.push_pcm(frame, 16000, 1)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and bridge._queue.qsize() > 0:
            time.sleep(0.01)
        assert finish_count == 0
        assert transport.published == []

        for frame in [speech] * 15 + [silence] * 45:
            bridge.push_pcm(frame, 16000, 1)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not transport.published:
            time.sleep(0.01)
    finally:
        bridge.stop()

    assert finish_count == 1
    assert any(line.startswith("[VAD] speech_start") for line in logs)
    assert any(line == "[VAD] short_silence 200ms" for line in logs)
    assert any(line.startswith("[UTTERANCE] finalize silence_ms=900") for line in logs)
    assert [message.payload.get("event") for message in transport.published] == [
        "session_start",
        None,
    ]
    assert transport.published[-1].payload["text"] == "我想出去走走"


def test_go2_pcm_normalizes_48k_stereo_to_16k_mono() -> None:
    left = np.full(960, 1000, dtype=np.int16)
    right = np.full(960, 3000, dtype=np.int16)
    stereo = np.column_stack((left, right)).reshape(-1)

    normalized = _pcm16_mono_16k_from_pcm(
        stereo.tobytes(), sample_rate=48000, channels=2
    )

    assert normalized.dtype == np.int16
    assert normalized.size == 320
    assert 1900 <= int(np.mean(normalized)) <= 2100


def test_go2_audio_bridge_does_not_feed_idle_silence_to_asr(monkeypatch) -> None:
    transport = MockTransport()
    manager = LocalVoiceSessionManager(transport, emergency_bypass_enabled=False)
    logs: list[str] = []
    created_sessions: list[object] = []

    class FakeStreamingSession:
        def __init__(self, _service) -> None:
            created_sessions.append(self)

        def feed_pcm(self, _pcm: bytes, *, sample_rate: int, channels: int) -> str:
            return "不应该出现"

        def finish(self) -> str:
            return "不应该出现"

    monkeypatch.setattr(
        "app.voice.local_voice.FunASRStreamingSession",
        FakeStreamingSession,
    )
    bridge = Go2ASRAudioBridge(
        asr_service=object(),  # type: ignore[arg-type]
        session_manager=manager,
        printer=logs.append,
        vad_min_capture_seconds=0.2,
        vad_trailing_silence_seconds=0.2,
        queue_size=32,
    )
    bridge.start()
    try:
        silence = np.zeros(320, dtype=np.int16).tobytes()
        for _ in range(24):
            bridge.push_pcm(silence, 16000, 1)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and bridge._queue.qsize() > 0:
            time.sleep(0.01)
    finally:
        bridge.stop()

    assert created_sessions == []
    assert transport.published == []
    assert all(not line.startswith("[ASR_PARTIAL]") for line in logs)


def test_go2_audio_bridge_can_write_debug_wavs(tmp_path, monkeypatch) -> None:
    transport = MockTransport()
    manager = LocalVoiceSessionManager(transport, emergency_bypass_enabled=False)
    logs: list[str] = []

    class FakeStreamingSession:
        def __init__(self, _service) -> None:
            pass

        def feed_pcm(self, _pcm: bytes, *, sample_rate: int, channels: int) -> str:
            return ""

        def finish(self) -> str:
            return ""

    monkeypatch.setattr(
        "app.voice.local_voice.FunASRStreamingSession",
        FakeStreamingSession,
    )
    bridge = Go2ASRAudioBridge(
        asr_service=object(),  # type: ignore[arg-type]
        session_manager=manager,
        printer=logs.append,
        queue_size=8,
        debug_audio_dir=tmp_path,
        debug_audio_seconds=0.1,
    )
    bridge.start()
    try:
        pcm = np.full(1920, 1000, dtype=np.int16).tobytes()
        for _ in range(6):
            bridge.push_pcm(pcm, 48000, 2)
        deadline = time.monotonic() + 2.0
        while (
            time.monotonic() < deadline
            and not any(line.startswith("[ASR_AUDIO_DEBUG] ready") for line in logs)
        ):
            time.sleep(0.01)
        raw_files = list(tmp_path.glob("go2_raw_*.wav"))
        normalized_files = list(tmp_path.glob("go2_16k_mono_*.wav"))
        assert len(raw_files) == 1
        assert len(normalized_files) == 1
        with wave.open(str(raw_files[0]), "rb") as raw:
            assert raw.getframerate() == 48000
            assert raw.getnchannels() == 2
        with wave.open(str(normalized_files[0]), "rb") as normalized:
            assert normalized.getframerate() == 16000
            assert normalized.getnchannels() == 1
    finally:
        bridge.stop()

    assert any(line.startswith("[ASR_AUDIO_DEBUG] recording") for line in logs)
    assert any(line.startswith("[ASR_AUDIO_DEBUG] ready") for line in logs)


def test_go2_debug_recorder_spans_asr_final_reset(tmp_path, monkeypatch) -> None:
    transport = MockTransport()
    manager = LocalVoiceSessionManager(transport, emergency_bypass_enabled=False)
    logs: list[str] = []

    class FakeStreamingSession:
        def __init__(self, _service) -> None:
            pass

        def feed_pcm(self, _pcm: bytes, *, sample_rate: int, channels: int) -> str:
            return "小康，我想出去走走"

        def finish(self) -> str:
            return "小康，我想出去走走"

    monkeypatch.setattr(
        "app.voice.local_voice.FunASRStreamingSession",
        FakeStreamingSession,
    )
    bridge = Go2ASRAudioBridge(
        asr_service=object(),  # type: ignore[arg-type]
        session_manager=manager,
        printer=logs.append,
        vad_min_capture_seconds=0.02,
        vad_trailing_silence_seconds=0.02,
        queue_size=64,
        debug_audio_dir=tmp_path,
        debug_audio_seconds=0.2,
        voice_debug=True,
    )
    bridge.start()
    try:
        speech = np.full(1920, 4000, dtype=np.int16).tobytes()
        silence = np.zeros(1920, dtype=np.int16).tobytes()
        for frame in [speech, silence, speech, silence, speech, silence] * 2:
            bridge.push_pcm(frame, 48000, 2)
        deadline = time.monotonic() + 2.0
        while (
            time.monotonic() < deadline
            and not any(line.startswith("[ASR_AUDIO_DEBUG] ready") for line in logs)
        ):
            time.sleep(0.01)
    finally:
        bridge.stop()

    assert any(line == "[ASR] 小康，我想出去走走" for line in logs)
    assert any(line.startswith("[ASR_AUDIO_DEBUG] ready") for line in logs)
    normalized_files = list(tmp_path.glob("go2_16k_mono_*.wav"))
    assert len(normalized_files) == 1
    with wave.open(str(normalized_files[0]), "rb") as normalized:
        assert normalized.getnframes() == 3200


def test_local_session_ignores_filler_without_session() -> None:
    transport = MockTransport()
    manager = LocalVoiceSessionManager(transport)

    manager.process_transcript("嗯")

    assert transport.published == []
    assert manager.active_session_id is None


def test_local_session_timeout_ends_open_session() -> None:
    transport = MockTransport()
    manager = LocalVoiceSessionManager(transport)

    manager.process_transcript("小康")
    manager.expire_if_idle()

    assert [message.payload.get("event") for message in transport.published] == [
        "session_start",
        "session_end",
    ]
    assert transport.published[-1].payload["reason"] == "timeout"
    assert transport.published[-1].payload["turns"] == 0


def test_voice_listener_pause_hard_mutes_business_transcripts() -> None:
    transport = MockTransport()
    logs: list[str] = []
    manager = LocalVoiceSessionManager(
        transport,
        emergency_bypass_enabled=False,
        voice_debug=True,
        printer=logs.append,
    )

    manager.process_transcript("小康")
    ended = manager.set_listener_enabled(False)
    paused_count = len(transport.published)

    assert manager.listener_enabled is False
    assert manager.voice_state is VoiceState.PAUSED
    assert manager.active_session_id is None
    assert ended[-1].payload["event"] == "session_end"
    assert ended[-1].payload["reason"] == "listener_paused"

    assert manager.process_transcript("小康，陪我出去走走") == []
    assert manager.process_transcript("现在出发") == []
    assert manager.process_transcript("我没事") == []
    assert len(transport.published) == paused_count
    assert any("listener_paused" in line for line in logs)


def test_voice_listener_resume_requires_wake_word_again() -> None:
    transport = MockTransport()
    manager = LocalVoiceSessionManager(transport, emergency_bypass_enabled=False)

    manager.process_transcript("小康")
    manager.set_listener_enabled(False)
    manager.set_listener_enabled(True)

    assert manager.listener_enabled is True
    assert manager.voice_state is VoiceState.WAKE_GUARD
    assert manager.process_transcript("现在出发") == []
    manager.process_transcript("小康，现在出发")

    assert transport.published[-1].payload["text"] == "现在出发"
    assert transport.published[-1].payload["turn"] == 1


def test_voice_listener_pause_clears_safety_reply_window() -> None:
    transport = MockTransport()
    now = [0.0]
    manager = LocalVoiceSessionManager(
        transport,
        emergency_bypass_enabled=False,
        monotonic_clock=lambda: now[0],
    )
    transport.publish(
        MqttContractMessage(
            topic=contract_topic("DOG-LJG-001", "event"),
            payload={
                "device_id": "DOG-LJG-001",
                "source": "simulator",
                "ts": "",
                "event": "FALL_SUSPECTED",
                "session_id": "fall-1",
            },
        )
    )

    manager.set_listener_enabled(False)
    manager.set_listener_enabled(True)

    assert manager.process_transcript("我没事") == []


def test_local_pipeline_lists_audio_devices_without_real_hardware(capsys) -> None:
    source = FakeMicrophoneSource([])
    pipeline = LocalVoicePipeline(
        microphone=source,
        asr_service=FakeASR([]),
    )

    devices = pipeline.list_audio_devices()

    assert devices[0].name == "Microphone Array"
    output = capsys.readouterr().out
    assert "[AUDIO] available capture devices" in output
    assert "[0] Microphone Array" in output


def test_local_pipeline_processes_utterance_without_motion_commands() -> None:
    transport = MockTransport()
    manager = LocalVoiceSessionManager(transport)

    manager.process_transcript("小康，跟我走")

    assert [message.topic for message in transport.published] == [
        "aiot/dog/DOG-LJG-001/event",
        "aiot/dog/DOG-LJG-001/speech",
    ]
    assert transport.published[-1].payload["text"] == "跟我走"
    assert all(not message.topic.endswith("/cmd") for message in transport.published)
