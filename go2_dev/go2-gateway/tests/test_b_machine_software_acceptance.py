from __future__ import annotations

import threading
import time

import numpy as np

from app.iot import (
    BMachineState,
    CommandDispatcher,
    CommandMessage,
    Go2ControlAdapter,
    MqttContractMessage,
    MockAMachine,
    MockTransport,
    contract_topic,
)
from app.voice import (
    ClipManifest,
    ClipManifestEntry,
    ClipPlaybackController,
    FunASRStreamingSession,
    Go2ASRAudioBridge,
    LocalVoiceSessionManager,
)


def _adapter(*, play_clips=None, calls=None) -> Go2ControlAdapter:
    observed = calls if calls is not None else []

    def start_follow(message: CommandMessage):
        observed.append(("start", dict(message.payload)))
        return {"ok": True}

    def stop_follow(message: CommandMessage):
        observed.append(("stop", dict(message.payload)))
        return {"ok": True}

    def resume_follow(message: CommandMessage):
        observed.append(("resume", dict(message.payload)))
        return {"ok": True}

    return Go2ControlAdapter(
        start_follow=start_follow,
        stop_follow=stop_follow,
        resume_follow=resume_follow,
        play_clips=play_clips or (lambda message: {"clips": message.payload["clips"], "played": 1, "status": "done"}),
        ping=lambda message: {"nonce": message.request_id},
    )


def test_b_machine_control_decisions_are_stateful_and_idempotent() -> None:
    calls: list[tuple[str, dict]] = []
    adapter = _adapter(calls=calls)
    start = CommandMessage(
        device_id="DOG-LJG-001",
        command="start_follow",
        request_id="start-1",
        payload={},
    )

    adapter.handle(start)
    adapter.handle(start)
    adapter.handle(
        CommandMessage(
            device_id="DOG-LJG-001",
            command="stop_follow",
            request_id="stop-1",
            payload={},
        )
    )
    adapter.handle(
        CommandMessage(
            device_id="DOG-LJG-001",
            command="stop_follow",
            request_id="stop-2",
            payload={},
        )
    )
    adapter.state_machine.set_state(BMachineState.PAUSED, reason="test_pause")
    adapter.handle(start)

    assert [name for name, _payload in calls] == ["start", "stop", "resume"]
    assert calls[0][1]["duration_minutes"] == 3
    assert calls[0][1]["runtime_command"] == "FOLLOW_3MIN"
    assert calls[0][1]["follow_profile"] == "FOLLOW_3MIN"
    assert calls[-1][1]["runtime_command"] == "RESUME"
    assert adapter.state is BMachineState.FOLLOWING


def test_control_adapter_does_not_mark_following_when_real_start_fails() -> None:
    calls: list[tuple[str, dict]] = []
    adapter = _adapter(calls=calls)
    adapter.start_follow = lambda message: {"ok": False, "reason": "uwb_not_ready"}

    adapter.handle(
        CommandMessage(
            device_id="DOG-LJG-001",
            command="start_follow",
            request_id="start-fail",
            payload={},
        )
    )

    assert adapter.state is BMachineState.IDLE
    assert adapter.state_machine.last_reason == "start_follow_failed"


def test_control_adapter_stop_is_idempotent_when_idle() -> None:
    calls: list[tuple[str, dict]] = []
    adapter = _adapter(calls=calls)

    adapter.handle(
        CommandMessage(
            device_id="DOG-LJG-001",
            command="stop_follow",
            request_id="stop-idle",
            payload={},
        )
    )

    assert calls == []
    assert adapter.state is BMachineState.IDLE


def test_control_adapter_keeps_following_when_real_stop_fails() -> None:
    calls: list[tuple[str, dict]] = []
    adapter = _adapter(calls=calls)
    adapter.state_machine.set_state(BMachineState.FOLLOWING, reason="test")
    adapter.stop_follow = lambda message: {"ok": False, "reason": "stop_timeout"}

    adapter.handle(
        CommandMessage(
            device_id="DOG-LJG-001",
            command="stop_follow",
            request_id="stop-fail",
            payload={},
        )
    )

    assert adapter.state is BMachineState.FOLLOWING
    assert adapter.state_machine.last_reason == "stop_follow_failed"


def test_clip_manifest_resolves_ready_clips_and_reports_missing() -> None:
    manifest = ClipManifest(
        {
            "outing.allow": {"resource_id": "uuid-outing", "status": "ready"},
            "fall.confirm": {"resource_id": "uuid-fall", "status": "pending"},
            "num.76": {"resource_id": "uuid-76", "status": "ready"},
            "unit.bpm": {"resource_id": "uuid-bpm", "status": "ready"},
        }
    )

    resolved, missing = manifest.resolve(["outing.allow", "fall.confirm", "num.76"])

    assert [entry.resource_id for entry in resolved] == ["uuid-outing", "uuid-76"]
    assert missing == ["fall.confirm"]


def test_clip_playback_closes_mic_and_interrupts_current_clip() -> None:
    release_first = threading.Event()
    first_started = threading.Event()
    played: list[str] = []
    playback_flags: list[bool] = []

    def executor(entry: ClipManifestEntry) -> None:
        played.append(entry.clip_id)
        if entry.clip_id == "outing.allow":
            first_started.set()
            assert release_first.wait(timeout=2.0)

    controller = ClipPlaybackController(
        executor=executor,
        set_playback_active=playback_flags.append,
    )
    results: dict[str, dict] = {}
    thread = threading.Thread(
        target=lambda: results.setdefault(
            "first",
            controller.play_clips(["outing.allow"], request_id="req-outing"),
        )
    )

    thread.start()
    assert first_started.wait(timeout=2.0)
    assert controller.is_playback_active() is True
    results["second"] = controller.play_clips(
        ["fall.confirm"],
        request_id="req-fall",
        interrupt=True,
    )
    release_first.set()
    thread.join(timeout=2.0)

    assert results["first"]["status"] == "interrupted"
    assert results["second"] == {
        "clips": ["fall.confirm"],
        "played": 1,
        "status": "done",
        "missing_clips": [],
    }
    assert played == ["outing.allow", "fall.confirm"]
    assert playback_flags[0] is True
    assert playback_flags[-1] is False


def test_asr_bridge_drops_pcm_while_playback_is_active() -> None:
    transport = MockTransport()
    manager = LocalVoiceSessionManager(transport, emergency_bypass_enabled=False)
    logs: list[str] = []
    bridge = Go2ASRAudioBridge(
        asr_service=object(),  # type: ignore[arg-type]
        session_manager=manager,
        printer=logs.append,
        is_playback_active=lambda: True,
        voice_debug=True,
    )

    bridge.push_pcm(np.ones(1600, dtype=np.int16).tobytes(), 16000, 1)

    assert bridge._queue.qsize() == 0
    assert logs == ["[ASR] playback_muted dropped=1 frames"]


def test_asr_bridge_drops_final_while_playback_is_active() -> None:
    transport = MockTransport()
    manager = LocalVoiceSessionManager(transport, emergency_bypass_enabled=False)
    logs: list[str] = []
    bridge = Go2ASRAudioBridge(
        asr_service=object(),  # type: ignore[arg-type]
        session_manager=manager,
        printer=logs.append,
        is_playback_active=lambda: True,
        voice_debug=True,
    )

    bridge._publish_final("小康")

    assert transport.published == []
    assert logs == ["[ASR] final_dropped: playback_active (小康)"]


def test_funasr_stream_finish_clears_cache_for_next_utterance() -> None:
    class Service:
        chunk_size = (0, 1, 0)
        encoder_chunk_look_back = 4
        decoder_chunk_look_back = 1

        def _load_model(self):
            class Model:
                def generate(self, **kwargs):
                    kwargs["cache"]["seen"] = True
                    return [{"text": "小康，我想出去走走"}]

            return Model()

    session = FunASRStreamingSession(Service())  # type: ignore[arg-type]
    session.feed_pcm(np.ones(1200, dtype=np.int16).tobytes(), sample_rate=16000, channels=1)

    assert session.finish() == "小康，我想出去走走"
    assert session._cache == {}
    assert session.finish() == ""


def test_funasr_streaming_session_merges_incremental_chunk_text() -> None:
    class Service:
        chunk_size = (0, 1, 0)
        encoder_chunk_look_back = 4
        decoder_chunk_look_back = 1

        def _load_model(self):
            chunks = iter(["小康", "我想", "出去走走"])

            class Model:
                def generate(self, **_kwargs):
                    return [{"text": next(chunks)}]

            return Model()

    session = FunASRStreamingSession(Service())  # type: ignore[arg-type]
    pcm = np.ones(960, dtype=np.int16).tobytes()

    session.feed_pcm(pcm, sample_rate=16000, channels=1)
    session.feed_pcm(pcm, sample_rate=16000, channels=1)
    session.feed_pcm(pcm, sample_rate=16000, channels=1)

    assert session.finish() == "小康我想出去走走"


def test_funasr_streaming_session_accepts_cumulative_chunk_text() -> None:
    class Service:
        chunk_size = (0, 1, 0)
        encoder_chunk_look_back = 4
        decoder_chunk_look_back = 1

        def _load_model(self):
            chunks = iter(["小康", "小康我想", "小康我想出去走走"])

            class Model:
                def generate(self, **_kwargs):
                    return [{"text": next(chunks)}]

            return Model()

    session = FunASRStreamingSession(Service())  # type: ignore[arg-type]
    pcm = np.ones(960, dtype=np.int16).tobytes()

    session.feed_pcm(pcm, sample_rate=16000, channels=1)
    session.feed_pcm(pcm, sample_rate=16000, channels=1)
    session.feed_pcm(pcm, sample_rate=16000, channels=1)

    assert session.finish() == "小康我想出去走走"


def test_session_timeout_requires_wake_word_again() -> None:
    transport = MockTransport()
    now = [0.0]
    manager = LocalVoiceSessionManager(
        transport,
        session_timeout_seconds=15.0,
        emergency_bypass_enabled=False,
        monotonic_clock=lambda: now[0],
    )

    manager.process_transcript("小康")
    now[0] = 16.0
    manager.process_transcript("我想出去")

    assert [message.payload.get("event") for message in transport.published] == [
        "session_start",
        "session_end",
    ]
    assert transport.published[-1].payload["reason"] == "timeout"
    assert manager.active_session_id is None

    manager.process_transcript("小康，我想出去")
    assert transport.published[-1].payload["text"] == "我想出去"
    assert transport.published[-1].payload["turn"] == 1


def test_safety_event_reply_window_allows_response_without_wake_word() -> None:
    transport = MockTransport()
    now = [0.0]
    manager = LocalVoiceSessionManager(
        transport,
        session_timeout_seconds=8.0,
        emergency_bypass_enabled=False,
        monotonic_clock=lambda: now[0],
    )

    assert manager.process_transcript("我没事") == []
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
    messages = manager.process_transcript("我没事")

    assert messages
    assert messages[-1].payload["text"] == "我没事"
    assert messages[-1].payload.get("wake_word") is None
    assert messages[-1].payload["is_wake_turn"] is False

    manager.expire_if_idle()
    now[0] = 9.0
    assert manager.process_transcript("我没事") == []


def test_mock_a_machine_runs_speech_tts_clip_done_start_follow_loop() -> None:
    transport = MockTransport()
    playback = ClipPlaybackController()
    calls: list[tuple[str, dict]] = []
    adapter = _adapter(play_clips=playback.play_command, calls=calls)
    dispatcher = CommandDispatcher(transport, adapter)
    dispatcher.bind("DOG-LJG-001")
    MockAMachine(transport, "DOG-LJG-001").bind()
    manager = LocalVoiceSessionManager(
        transport,
        emergency_bypass_enabled=False,
        state_machine=adapter.state_machine,
    )

    manager.process_transcript("小康，我想出去走走")
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and adapter.state is not BMachineState.FOLLOWING:
        time.sleep(0.01)

    assert adapter.state is BMachineState.FOLLOWING
    assert calls == [
        (
            "start",
            {
                "session_id": transport.published[0].payload["session_id"],
                "duration_minutes": 3,
                "runtime_command": "FOLLOW_3MIN",
                "follow_profile": "FOLLOW_3MIN",
            },
        )
    ]
    assert any(
        message.payload.get("command") == "tts_speak"
        and message.payload["payload"]["clips"] == ["outing.allow"]
        for message in transport.published
    )
    assert any(
        message.payload.get("event") == "clip_done"
        and message.payload["status"] == "done"
        for message in transport.published
    )
