from __future__ import annotations

from app.iot import (
    BMachineState,
    CommandDispatcher,
    CommandMessage,
    Go2ControlAdapter,
    MqttContractMessage,
    MockTransport,
    build_clip_done_message,
    build_command_message,
    build_speech_message,
    contract_topic,
)
from app.voice import (
    ClipAssembler,
    HealthContext,
    InteractionFlowController,
    LocalFirstXiaokangAgent,
    MedicationContext,
    StaticHealthProvider,
    StaticMedicationProvider,
    WeatherCondition,
    WeatherContext,
    XiaokangAgentService,
    XiaokangDecision,
    open_meteo_code_to_condition,
)


class WeatherProvider:
    def __init__(self, weather: WeatherContext) -> None:
        self.weather = weather

    def get_weather(self) -> WeatherContext:
        return self.weather


def _agent(*, auto_follow: bool = False) -> XiaokangAgentService:
    return XiaokangAgentService(
        health_provider=StaticHealthProvider(
            HealthContext(
                heart_rate=76,
                spo2=98,
                temperature=36.5,
                blood_pressure_systolic=125,
                blood_pressure_diastolic=78,
                status="good",
            )
        ),
        weather_provider=WeatherProvider(
            WeatherContext(
                city="北京",
                condition=WeatherCondition.SUNNY,
                temperature=24,
                feels_like=23,
                precipitation=False,
            )
        ),
        medication_provider=StaticMedicationProvider(
            MedicationContext(required_today=True, taken=False)
        ),
        auto_follow=auto_follow,
    )


def _flow_agent(*, auto_follow: bool = True) -> InteractionFlowController:
    return InteractionFlowController(
        health_provider=StaticHealthProvider(
            HealthContext(
                heart_rate=76,
                spo2=98,
                temperature=36.5,
                blood_pressure_systolic=125,
                blood_pressure_diastolic=78,
                status="good",
            ),
            profiles={
                "health_query": HealthContext(
                    heart_rate=76,
                    spo2=98,
                    temperature=36.5,
                    blood_pressure_systolic=125,
                    blood_pressure_diastolic=78,
                    status="good",
                ),
                "outing_before_medication": HealthContext(
                    heart_rate=78,
                    spo2=98,
                    temperature=36.6,
                    blood_pressure_systolic=126,
                    blood_pressure_diastolic=79,
                    status="good",
                ),
                "outing_after_medication": HealthContext(
                    heart_rate=77,
                    spo2=98,
                    temperature=36.5,
                    blood_pressure_systolic=125,
                    blood_pressure_diastolic=78,
                    status="good",
                ),
            },
        ),
        weather_provider=WeatherProvider(
            WeatherContext(
                city="北京",
                condition=WeatherCondition.SUNNY,
                temperature=24,
                feels_like=23,
                precipitation=False,
            )
        ),
        medication_provider=StaticMedicationProvider(
            MedicationContext(required_today=True, taken=False)
        ),
        clip_assembler=ClipAssembler(is_clip_available=lambda _clip: True),
        auto_follow=auto_follow,
    )


def _publish_flow_event(
    transport: MockTransport,
    event: str,
    *,
    session_id: str = "scenario-session",
) -> None:
    transport.publish(
        MqttContractMessage(
            topic=contract_topic("DOG-LJG-001", "event"),
            payload={
                "device_id": "DOG-LJG-001",
                "source": "simulator",
                "ts": "",
                "event": event,
                "session_id": session_id,
            },
        )
    )


def _tts_clips_after(transport: MockTransport, start_index: int) -> list[list[str]]:
    clips: list[list[str]] = []
    for message in transport.published[start_index:]:
        payload = message.payload
        if payload.get("command") != "tts_speak":
            continue
        command_payload = payload.get("payload")
        if isinstance(command_payload, dict):
            clips.append(list(command_payload.get("clips") or []))
    return clips


def test_xiaokang_agent_builds_structured_outing_decision() -> None:
    decision = _agent(auto_follow=False).handle_text("陪我出去走走")

    assert decision.intent == "outing_request"
    assert decision.allowed is True
    assert decision.heart_rate == 76
    assert decision.spo2 == 98
    assert decision.body_temperature == 36.5
    assert decision.weather == "sunny"
    assert decision.temperature == 24
    assert decision.medication_reminder is True
    assert decision.action is None
    assert list(decision.clips) == [
        "outing.allow.health_good",
        "health.hr.prefix",
        "num.76",
        "unit.bpm",
        "health.spo2.98",
        "health.temperature.36_5",
        "weather.condition.sunny",
        "weather.temperature.prefix",
        "temperature.value.24",
        "medication.reminder.before_outing",
        "outing.allow.suffix",
    ]


def test_outing_phrase_variants_are_shared_by_agent_entrypoints() -> None:
    phrases = [
        "陪我出去走走",
        "陪我走吧",
        "陪我出门",
        "带我出去",
        "跟我走",
        "咱们出去转转",
    ]

    for phrase in phrases:
        assert _agent(auto_follow=False).handle_text(phrase).intent == "outing_request"
        assert _flow_agent(auto_follow=False).handle_text(phrase).intent == "outing_request"


def test_unknown_intent_stays_silent_in_active_session() -> None:
    assert _agent(auto_follow=False).handle_text("喝水子").clips == ()
    assert _flow_agent(auto_follow=False).handle_text("喝水子").clips == ()


def test_local_first_agent_can_be_bound_for_events_without_speech_business() -> None:
    transport = MockTransport()
    logs: list[str] = []
    LocalFirstXiaokangAgent(
        transport,
        "DOG-LJG-001",
        _flow_agent(auto_follow=False),
        speech_enabled=False,
        printer=logs.append,
    ).bind()

    transport.publish(
        build_speech_message(
            "DOG-LJG-001",
            text="陪我出去走走",
            session_id="speech-disabled",
            turn=1,
            is_wake_turn=False,
            wake_word=None,
            bypass_wake=False,
        )
    )

    assert not any(message.payload.get("command") == "tts_speak" for message in transport.published)
    assert logs == []


def test_weather_condition_mapping_uses_internal_enum() -> None:
    assert open_meteo_code_to_condition(0) is WeatherCondition.SUNNY
    assert open_meteo_code_to_condition(2) is WeatherCondition.CLOUDY
    assert open_meteo_code_to_condition(45) is WeatherCondition.OVERCAST
    assert open_meteo_code_to_condition(61) is WeatherCondition.RAIN
    assert open_meteo_code_to_condition(71) is WeatherCondition.SNOW
    assert open_meteo_code_to_condition(None) is WeatherCondition.UNKNOWN


def test_clip_assembler_falls_back_when_numeric_clip_is_missing() -> None:
    missing = {"num.76", "health.spo2.98"}
    logs: list[str] = []
    assembler = ClipAssembler(
        is_clip_available=lambda clip: clip not in missing,
        printer=logs.append,
    )

    clips = assembler.outing_allow(
        XiaokangDecision(
            intent="outing_request",
            heart_rate=76,
            spo2=98,
            weather="sunny",
            temperature=24,
            medication_reminder=False,
        )
    )

    assert "num.76" not in clips
    assert "health.hr.prefix" not in clips
    assert "health.spo2.98" not in clips
    assert "health.spo2.prefix" in clips
    assert "num.98" in clips
    assert "weather.temp.prefix" not in clips
    assert "temperature.value.24" in clips
    assert "weather.condition.sunny" in clips
    assert clips[-1] == "outing.allow.suffix"
    assert logs == ["[CLIP] optional_missing num.76"]


def test_clip_assembler_supports_negative_temperature_when_clip_exists() -> None:
    assembler = ClipAssembler(is_clip_available=lambda _clip: True)

    clips = assembler.outing_allow(
        XiaokangDecision(
            intent="outing_request",
            heart_rate=None,
            weather="snow",
            temperature=-3,
            medication_reminder=False,
        )
    )

    assert "weather.condition.snow" in clips
    assert "temperature.value.-3" in clips


def test_clip_assembler_falls_back_to_legacy_temperature_when_natural_clip_missing() -> None:
    missing = {"weather.condition.sunny", "temperature.value.24"}
    assembler = ClipAssembler(is_clip_available=lambda clip: clip not in missing)

    clips = assembler.outing_allow(
        XiaokangDecision(
            intent="outing_request",
            heart_rate=None,
            weather="sunny",
            temperature=24,
            medication_reminder=False,
        )
    )

    assert "weather.today.beijing" in clips
    assert "weather.sunny" in clips
    assert "weather.temp.prefix" in clips
    assert "num.24" in clips
    assert "unit.celsius" in clips


def test_clip_assembler_skips_unknown_weather_and_optional_medication_missing() -> None:
    logs: list[str] = []
    assembler = ClipAssembler(
        is_clip_available=lambda clip: clip != "medication.reminder.before_outing",
        printer=logs.append,
    )

    clips = assembler.outing_allow(
        XiaokangDecision(
            intent="outing_request",
            heart_rate=None,
            weather=WeatherCondition.UNKNOWN.value,
            temperature=None,
            medication_reminder=True,
        )
    )

    assert all(not clip.startswith("weather.") for clip in clips)
    assert "medication.reminder.before_outing" not in clips
    assert clips == ["outing.allow.health_good", "outing.allow.suffix"]
    assert logs == ["[CLIP] optional_missing medication.reminder.before_outing"]


def test_local_first_agent_starts_follow_only_after_clip_done() -> None:
    transport = MockTransport()
    calls: list[tuple[str, dict]] = []

    def start_follow(message: CommandMessage):
        calls.append(("start", dict(message.payload)))
        return {"ok": True}

    def stop_follow(message: CommandMessage):
        calls.append(("stop", dict(message.payload)))
        return {"ok": True}

    adapter = Go2ControlAdapter(
        start_follow=start_follow,
        stop_follow=stop_follow,
        resume_follow=lambda message: {"ok": True},
        play_clips=lambda message: {
            "clips": message.payload["clips"],
            "played": len(message.payload["clips"]),
            "status": "done",
            "missing_clips": [],
        },
        ping=lambda message: {"nonce": message.request_id},
    )
    CommandDispatcher(transport, adapter).bind("DOG-LJG-001")
    LocalFirstXiaokangAgent(
        transport,
        "DOG-LJG-001",
        _agent(auto_follow=True),
        printer=lambda _line: None,
    ).bind()

    transport.publish(
        build_command_message(
            "DOG-LJG-001",
            command="ping",
            request_id="keep-dispatcher-covered",
            payload={"nonce": "n"},
        )
    )
    transport.publish(
        build_speech_message(
            "DOG-LJG-001",
            text="陪我出去走走",
            session_id="session-001",
            turn=1,
            is_wake_turn=True,
            wake_word="小康",
            bypass_wake=False,
        )
    )

    assert any(message.payload.get("command") == "tts_speak" for message in transport.published)
    assert any(message.payload.get("event") == "clip_done" for message in transport.published)
    assert calls == [
        (
            "start",
            {
                "session_id": "session-001",
                "duration_minutes": 3,
                "skip_start_announcement": True,
                "runtime_command": "FOLLOW_3MIN",
                "follow_profile": "FOLLOW_3MIN",
            },
        )
    ]
    assert adapter.state is BMachineState.FOLLOWING


def test_voice_stop_and_terminal_stop_share_control_adapter() -> None:
    transport = MockTransport()
    calls: list[tuple[str, dict]] = []

    def stop_follow(message: CommandMessage):
        calls.append(("stop", dict(message.payload)))
        return {"ok": True}

    adapter = Go2ControlAdapter(
        start_follow=lambda message: {"ok": True},
        stop_follow=stop_follow,
        resume_follow=lambda message: {"ok": True},
        play_clips=lambda message: {
            "clips": message.payload["clips"],
            "played": len(message.payload["clips"]),
            "status": "done",
            "missing_clips": [],
        },
        ping=lambda message: {"nonce": message.request_id},
    )
    adapter.state_machine.set_state(BMachineState.FOLLOWING, reason="test")
    CommandDispatcher(transport, adapter).bind("DOG-LJG-001")
    LocalFirstXiaokangAgent(
        transport,
        "DOG-LJG-001",
        _agent(auto_follow=False),
        printer=lambda _line: None,
    ).bind()

    transport.publish(
        build_speech_message(
            "DOG-LJG-001",
            text="不用跟着我了",
            session_id="session-002",
            turn=1,
            is_wake_turn=True,
            wake_word="小康",
            bypass_wake=False,
        )
    )
    adapter.state_machine.set_state(BMachineState.FOLLOWING, reason="test")
    transport.publish(
        build_command_message(
            "DOG-LJG-001",
            command="stop_follow",
            request_id="terminal-stop",
            payload={},
        )
    )

    assert [name for name, _payload in calls] == ["stop", "stop"]
    assert calls[0][1]["session_id"] == "session-002"
    assert calls[1][1] == {}
    assert adapter.state is BMachineState.IDLE


def test_pending_start_follow_is_cleared_when_clip_playback_fails() -> None:
    for status in ("missing", "error", "interrupted"):
        transport = MockTransport()
        calls: list[tuple[str, dict]] = []

        adapter = Go2ControlAdapter(
            start_follow=lambda message: calls.append(("start", dict(message.payload))) or {"ok": True},
            stop_follow=lambda message: {"ok": True},
            resume_follow=lambda message: {"ok": True},
            play_clips=lambda message, status=status: {
                "clips": message.payload["clips"],
                "played": 0,
                "status": status,
                "missing_clips": list(message.payload["clips"]) if status == "missing" else [],
            },
            ping=lambda message: {"nonce": message.request_id},
        )
        CommandDispatcher(transport, adapter).bind("DOG-LJG-001")
        LocalFirstXiaokangAgent(
            transport,
            "DOG-LJG-001",
            _agent(auto_follow=True),
            printer=lambda _line: None,
        ).bind()

        transport.publish(
            build_speech_message(
                "DOG-LJG-001",
                text="陪我出去走走",
                session_id=f"session-{status}",
                turn=1,
                is_wake_turn=True,
                wake_word="小康",
                bypass_wake=False,
            )
        )
        transport.publish(
            build_clip_done_message(
                "DOG-LJG-001",
                session_id=f"session-{status}",
                request_id=f"unrelated-{status}",
                clips=["unrelated.later.clip"],
                played=1,
                status="done",
            )
        )

        assert calls == []
        assert adapter.state is not BMachineState.FOLLOWING


def test_local_first_agent_and_dispatcher_share_custom_topic_prefix() -> None:
    transport = MockTransport()
    calls: list[tuple[str, dict]] = []
    topic_prefix = "eldercare"

    adapter = Go2ControlAdapter(
        start_follow=lambda message: calls.append(("start", dict(message.payload))) or {"ok": True},
        stop_follow=lambda message: {"ok": True},
        resume_follow=lambda message: {"ok": True},
        play_clips=lambda message: {
            "clips": message.payload["clips"],
            "played": len(message.payload["clips"]),
            "status": "done",
            "missing_clips": [],
        },
        ping=lambda message: {"nonce": message.request_id},
    )
    CommandDispatcher(
        transport,
        adapter,
        topic_prefix=topic_prefix,
    ).bind("DOG-LJG-001")
    LocalFirstXiaokangAgent(
        transport,
        "DOG-LJG-001",
        _agent(auto_follow=True),
        topic_prefix=topic_prefix,
        printer=lambda _line: None,
    ).bind()

    transport.publish(
        build_speech_message(
            "DOG-LJG-001",
            text="陪我出去走走",
            session_id="session-004",
            turn=1,
            is_wake_turn=True,
            wake_word="小康",
            bypass_wake=False,
            topic_prefix=topic_prefix,
        )
    )

    assert calls == [
        (
            "start",
            {
                "session_id": "session-004",
                "duration_minutes": 3,
                "skip_start_announcement": True,
                "runtime_command": "FOLLOW_3MIN",
                "follow_profile": "FOLLOW_3MIN",
            },
        )
    ]
    assert any(
        message.topic.startswith(f"{topic_prefix}/dog/DOG-LJG-001/event")
        and message.payload.get("event") == "clip_done"
        for message in transport.published
    )


def test_interaction_flow_runs_competition_scenario_without_counting_turns() -> None:
    transport = MockTransport()
    calls: list[tuple[str, dict]] = []

    adapter = Go2ControlAdapter(
        start_follow=lambda message: calls.append(("start", dict(message.payload))) or {"ok": True},
        stop_follow=lambda message: calls.append(("stop", dict(message.payload))) or {"ok": True},
        resume_follow=lambda message: calls.append(("resume", dict(message.payload))) or {"ok": True},
        play_clips=lambda message: {
            "clips": message.payload["clips"],
            "played": len(message.payload["clips"]),
            "status": "done",
            "missing_clips": [],
        },
        ping=lambda message: {"nonce": message.request_id},
    )
    CommandDispatcher(transport, adapter).bind("DOG-LJG-001")
    flow = _flow_agent(auto_follow=True)
    LocalFirstXiaokangAgent(
        transport,
        "DOG-LJG-001",
        flow,  # type: ignore[arg-type]
        printer=lambda _line: None,
    ).bind()

    def say(text: str, *, session_id: str, turn: int) -> None:
        transport.publish(
            build_speech_message(
                "DOG-LJG-001",
                text=text,
                session_id=session_id,
                turn=turn,
                is_wake_turn=turn == 1,
                wake_word="小康" if turn == 1 else None,
                bypass_wake=False,
            )
        )

    marker = len(transport.published)
    say("看一下我的身体和天气", session_id="health", turn=1)
    assert calls == []
    assert _tts_clips_after(transport, marker)[-1][0] == "outing.allow.health_good"

    marker = len(transport.published)
    say("陪我出去走走", session_id="outing-1", turn=1)
    assert calls == []
    first_outing_clips = _tts_clips_after(transport, marker)[-1]
    assert "medication.reminder.before_outing" in first_outing_clips
    assert "outing.start" not in first_outing_clips

    say("好的", session_id="outing-1", turn=2)
    assert calls == []
    assert flow.context.medication_acknowledged is True
    assert flow.context.medication_taken is False

    marker = len(transport.published)
    say("陪我出去走走", session_id="outing-2", turn=1)
    assert calls == []
    second_outing_clips = _tts_clips_after(transport, marker)[-1]
    assert second_outing_clips[:4] == [
        "outing.allow.health_good",
        "health.hr.prefix",
        "num.77",
        "unit.bpm",
    ]
    assert "health.spo2.98" in second_outing_clips
    assert "health.temperature.36_5" in second_outing_clips
    assert second_outing_clips[-2:] == [
        "outing.allow.suffix",
        "outing.medication_check",
    ]
    assert "medication.reminder.before_outing" not in second_outing_clips

    marker = len(transport.published)
    say("吃过了，现在出发", session_id="outing-2", turn=2)
    assert _tts_clips_after(transport, marker)[-1] == ["outing.start"]
    assert calls == [
        (
            "start",
            {
                "session_id": "outing-2",
                "duration_minutes": 3,
                "skip_start_announcement": True,
                "runtime_command": "FOLLOW_3MIN",
                "follow_profile": "FOLLOW_3MIN",
            },
        )
    ]
    assert flow.context.medication_taken is True
    assert flow.context.departure_confirmed is True
    assert adapter.state is BMachineState.FOLLOWING

    say("停一下", session_id="stop-1", turn=1)
    assert [name for name, _payload in calls] == ["start", "stop"]
    assert adapter.state is not BMachineState.FOLLOWING

    marker = len(transport.published)
    say("陪我走吧", session_id="outing-3", turn=1)
    assert [name for name, _payload in calls] == ["start", "stop", "start"]
    assert _tts_clips_after(transport, marker)[-1] == ["follow.resume.safe"]

    _publish_flow_event(transport, "FALL_SUSPECTED", session_id="fall-1")
    assert [name for name, _payload in calls][-1] == "stop"
    assert flow.context.safety_state == "fall_check_1"
    assert any(
        message.payload.get("payload", {}).get("clips") == ["fall.confirm"]
        for message in transport.published
        if isinstance(message.payload.get("payload"), dict)
    )

    _publish_flow_event(transport, "FALL_RESPONSE_TIMEOUT", session_id="fall-1")
    assert flow.context.safety_state == "fall_check_2"
    assert any(
        message.payload.get("payload", {}).get("clips") == ["fall.confirm.second"]
        for message in transport.published
        if isinstance(message.payload.get("payload"), dict)
    )

    _publish_flow_event(transport, "FALL_RESPONSE_TIMEOUT", session_id="fall-1")
    assert flow.context.safety_state == "helping"
    assert any(
        message.payload.get("payload", {}).get("clips") == [
            "fall.alert.sound",
            "fall.help.broadcast",
        ]
        for message in transport.published
        if isinstance(message.payload.get("payload"), dict)
    )

    flow.context.safety_state = "fall_check_2"
    say("我没事", session_id="fall-1", turn=1)
    assert flow.context.fall_user_response == "ok"
    _publish_flow_event(transport, "FALL_RECOVERED", session_id="fall-1")
    assert flow.context.safety_state == "normal"
    assert [name for name, _payload in calls].count("start") == 2

    _publish_flow_event(transport, "NORMAL_ACTIVITY_READING", session_id="reading-1")
    assert flow.context.expected_reply == "reading_reply"
    assert any(
        message.payload.get("payload", {}).get("clips") == [
            "fall.normal_activity",
            "reading.ask_book",
        ]
        for message in transport.published
        if isinstance(message.payload.get("payload"), dict)
    )

    say("陪我走吧", session_id="outing-4", turn=1)
    assert flow.context.expected_reply is None
    assert [name for name, _payload in calls].count("start") == 3

    say("停一下", session_id="stop-2", turn=1)
    assert [name for name, _payload in calls][-1] == "stop"
