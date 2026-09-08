from __future__ import annotations

from pathlib import Path
import time

import numpy as np

from app.iot.protocol_layer import MockTransport
from app.voice.local_voice import (
    Go2ASRAudioBridge,
    LocalVoicePipeline,
    LocalVoiceSessionManager,
    MicrophoneCaptureResult,
    MicrophoneDeviceInfo,
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
    assert any("[WAKE] matched: 小康" in line for line in logs)


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
