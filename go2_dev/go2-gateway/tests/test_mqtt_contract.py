from __future__ import annotations

import json

from app.iot.mqtt_contract import (
    MemoryMqttContractBus,
    build_clip_done_message,
    build_command_message,
    build_mqtt_contract_client,
    build_session_end_message,
    build_session_start_message,
    build_speech_message,
    build_status_message,
    build_telemetry_message,
    contract_topic,
)
from app.iot.protocol_layer import (
    CommandDispatcher,
    Go2ControlAdapter,
    MockTransport,
)


def test_contract_topics_match_the_document() -> None:
    assert contract_topic("DOG-LJG-001", "speech") == "aiot/dog/DOG-LJG-001/speech"
    assert contract_topic("DOG-LJG-001", "event") == "aiot/dog/DOG-LJG-001/event"
    assert contract_topic("DOG-LJG-001", "telemetry") == "aiot/dog/DOG-LJG-001/telemetry"
    assert contract_topic("DOG-LJG-001", "status") == "aiot/dog/DOG-LJG-001/status"
    assert contract_topic("DOG-LJG-001", "cmd") == "aiot/dog/DOG-LJG-001/cmd"


def test_speech_message_includes_common_fields_and_wake_metadata() -> None:
    message = build_speech_message(
        "DOG-LJG-001",
        text="我想下楼遛个弯",
        session_id="session-001",
        turn=1,
        is_wake_turn=True,
        wake_word="小安",
        bypass_wake=False,
        asr_confidence=0.93,
        ts="2026-09-06T18:12:03.412",
    )

    assert message.topic == "aiot/dog/DOG-LJG-001/speech"
    assert message.qos == 1
    assert json.loads(message.to_json()) == message.payload
    assert message.payload == {
        "device_id": "DOG-LJG-001",
        "ts": "2026-09-06T18:12:03.412",
        "source": "go2",
        "text": "我想下楼遛个弯",
        "asr_confidence": 0.93,
        "session_id": "session-001",
        "turn": 1,
        "is_wake_turn": True,
        "bypass_wake": False,
        "wake_word": "小安",
    }


def test_event_and_telemetry_messages_are_contract_stable() -> None:
    start = build_session_start_message(
        "DOG-LJG-001",
        session_id="session-001",
        wake_word="小安",
        ts="2026-09-06T18:12:03.412",
    )
    end = build_session_end_message(
        "DOG-LJG-001",
        session_id="session-001",
        reason="timeout",
        turns=3,
        ts="2026-09-06T18:12:33.412",
    )
    clip_done = build_clip_done_message(
        "DOG-LJG-001",
        session_id="session-001",
        request_id="req-001",
        clips=["sess.wake_ack", "chat.comfort"],
        played=2,
        status="done",
        missing_clips=[],
        ts="2026-09-06T18:12:10.000",
    )
    telemetry = build_telemetry_message(
        "DOG-LJG-001",
        battery=76,
        lat=30.279512,
        lng=120.133087,
        speed=0.82,
        follow_mode=False,
        gait="idle",
        task_id="task-001",
        ts="2026-09-06T18:12:20.000",
    )
    command = build_command_message(
        "DOG-LJG-001",
        command="tts_speak",
        request_id="req-002",
        payload={"clips": ["chat.greeting_morning"], "interrupt": True},
        ts="2026-09-06T18:12:21.000",
    )

    assert start.topic == "aiot/dog/DOG-LJG-001/event"
    assert start.payload["event"] == "session_start"
    assert end.payload["reason"] == "timeout"
    assert clip_done.payload["clips"] == ["sess.wake_ack", "chat.comfort"]
    assert clip_done.payload["played"] == 2
    assert telemetry.topic == "aiot/dog/DOG-LJG-001/telemetry"
    assert telemetry.payload["battery"] == 76
    assert telemetry.payload["follow_mode"] is False
    assert telemetry.payload["task_id"] == "task-001"
    assert command.topic == "aiot/dog/DOG-LJG-001/cmd"
    assert command.payload["command"] == "tts_speak"
    assert command.payload["payload"] == {
        "clips": ["chat.greeting_morning"],
        "interrupt": True,
    }


def test_memory_bus_records_and_routes_commands() -> None:
    bus = MemoryMqttContractBus()
    client = build_mqtt_contract_client(bus)
    seen: list[tuple[str, dict[str, object]]] = []

    client.subscribe("DOG-LJG-001", callback=lambda topic, payload: seen.append((topic, payload)))
    client.publish_status("DOG-LJG-001", online=True)
    client.publish_command(
        "DOG-LJG-001",
        command="ping",
        request_id="req-003",
        payload={"nonce": "abc123"},
    )

    assert len(bus.published) == 2
    assert bus.published[0].topic == "aiot/dog/DOG-LJG-001/status"
    assert bus.published[1].topic == "aiot/dog/DOG-LJG-001/cmd"
    assert seen == [
        (
            "aiot/dog/DOG-LJG-001/cmd",
            {
                "device_id": "DOG-LJG-001",
                "ts": bus.published[1].payload["ts"],
                "source": "go2",
                "command": "ping",
                "request_id": "req-003",
                "payload": {"nonce": "abc123"},
            },
        )
    ]


def test_mock_command_dispatcher_runs_b_machine_control_flow() -> None:
    transport = MockTransport()
    calls: list[str] = []

    def start_follow(message):
        calls.append(f"start:{message.request_id}")
        return {"started": True}

    def stop_follow(message):
        calls.append(f"stop:{message.request_id}")
        return {"stopped": True}

    def resume_follow(message):
        calls.append(f"resume:{message.request_id}")
        return {"resumed": True}

    def play_clips(message):
        calls.append(f"play:{message.request_id}")
        return {
            "clips": message.payload["clips"],
            "played": len(message.payload["clips"]),
            "status": "done",
            "missing_clips": [],
        }

    def ping(message):
        calls.append(f"ping:{message.request_id}")
        return {"nonce": message.payload["nonce"], "battery": 76, "follow_mode": False}

    adapter = Go2ControlAdapter(
        start_follow=start_follow,
        stop_follow=stop_follow,
        resume_follow=resume_follow,
        play_clips=play_clips,
        ping=ping,
    )
    dispatcher = CommandDispatcher(transport, adapter)
    dispatcher.bind("DOG-LJG-001")

    transport.publish(
        build_command_message(
            "DOG-LJG-001",
            command="start_follow",
            request_id="start-001",
            payload={"task_id": "task-001", "duration_minutes": 30},
        )
    )
    transport.publish(
        build_command_message(
            "DOG-LJG-001",
            command="tts_speak",
            request_id="speech-001",
            payload={
                "clips": ["outing.allow", "outing.start"],
                "session_id": "session-001",
            },
        )
    )
    transport.publish(
        build_command_message(
            "DOG-LJG-001",
            command="ping",
            request_id="ping-001",
            payload={"nonce": "nonce-001"},
        )
    )

    assert calls == ["start:start-001", "play:speech-001", "ping:ping-001"]
    assert [message.payload.get("event") for message in transport.published if "event" in message.payload] == [
        "clip_done",
        "pong",
    ]
    clip_done = next(message for message in transport.published if message.payload.get("event") == "clip_done")
    pong = next(message for message in transport.published if message.payload.get("event") == "pong")
    assert clip_done.payload["played"] == 2
    assert pong.payload["nonce"] == "nonce-001"
