from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol

from .mqtt_contract import (
    MQTT_ALLOWED_COMMANDS,
    MQTT_ALLOWED_SOURCES,
    MQTT_DEFAULT_QOS,
    MQTT_TOPIC_PREFIX_DEFAULT,
    MemoryMqttContractBus,
    MqttContractBus,
    MqttContractMessage,
    build_clip_done_message,
    build_command_message,
    build_follow_lost_message,
    build_fall_detected_message,
    build_pong_message,
    build_session_end_message,
    build_session_start_message,
    build_speech_message,
    build_status_message,
    build_telemetry_message,
    contract_topic,
    normalize_device_id,
    normalize_qos,
    normalize_source,
    normalize_topic_prefix,
)


class MessageTransport(Protocol):
    def publish(self, message: MqttContractMessage) -> None: ...

    def subscribe(
        self,
        topic: str,
        callback: Callable[[str, dict[str, Any]], None],
    ) -> None: ...


class MockTransport(MemoryMqttContractBus):
    """In-memory transport used while the real broker is unavailable."""


class BMachineState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    FOLLOWING = "FOLLOWING"
    PAUSED = "PAUSED"
    ERROR = "ERROR"


@dataclass
class BMachineStateMachine:
    state: BMachineState = BMachineState.IDLE
    last_reason: str = "init"

    def set_state(self, state: BMachineState | str, *, reason: str) -> BMachineState:
        self.state = BMachineState(state)
        self.last_reason = str(reason or "")
        return self.state

    def on_wake(self) -> BMachineState:
        return self.set_state(BMachineState.LISTENING, reason="wake_word")

    def on_speech_submitted(self) -> BMachineState:
        return self.set_state(BMachineState.THINKING, reason="speech_published")

    def on_playback_started(self) -> BMachineState:
        return self.set_state(BMachineState.SPEAKING, reason="playback_started")

    def on_playback_finished(self, previous: BMachineState | str | None = None) -> BMachineState:
        previous_state = BMachineState(previous) if previous is not None else None
        if previous_state is BMachineState.FOLLOWING:
            return self.set_state(BMachineState.FOLLOWING, reason="playback_finished")
        if previous_state is BMachineState.PAUSED:
            return self.set_state(BMachineState.PAUSED, reason="playback_finished")
        return self.set_state(BMachineState.LISTENING, reason="playback_finished")

    def snapshot(self) -> dict[str, str]:
        return {"state": self.state.value, "reason": self.last_reason}


@dataclass(frozen=True)
class CommandMessage:
    device_id: str
    command: str
    request_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: str | None = None
    source: str = "go2"
    qos: int = MQTT_DEFAULT_QOS
    retain: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "device_id", normalize_device_id(self.device_id))
        object.__setattr__(self, "command", str(self.command).strip())
        object.__setattr__(self, "request_id", str(self.request_id).strip())
        object.__setattr__(self, "source", normalize_source(self.source))
        object.__setattr__(self, "qos", normalize_qos(self.qos))
        if self.command not in MQTT_ALLOWED_COMMANDS:
            raise ValueError(
                f"command must be one of {sorted(MQTT_ALLOWED_COMMANDS)!r}"
            )
        if not self.request_id:
            raise ValueError("request_id must not be empty")
        if self.payload is None:
            object.__setattr__(self, "payload", {})

    def to_contract_message(
        self,
        *,
        topic_prefix: str = MQTT_TOPIC_PREFIX_DEFAULT,
    ) -> MqttContractMessage:
        return build_command_message(
            self.device_id,
            command=self.command,
            request_id=self.request_id,
            payload=dict(self.payload),
            ts=self.ts,
            source=self.source,
            topic_prefix=topic_prefix,
            qos=self.qos,
        )

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "CommandMessage":
        return cls(
            device_id=str(payload.get("device_id") or ""),
            command=str(payload.get("command") or ""),
            request_id=str(payload.get("request_id") or ""),
            payload=dict(payload.get("payload") or {}),
            ts=str(payload.get("ts") or "") or None,
            source=str(payload.get("source") or "go2"),
        )


@dataclass(frozen=True)
class SpeechMessage:
    device_id: str
    text: str
    session_id: str
    turn: int
    is_wake_turn: bool
    wake_word: str | None = None
    bypass_wake: bool = False
    asr_confidence: float | None = None
    ts: str | None = None
    source: str = "go2"
    qos: int = MQTT_DEFAULT_QOS
    retain: bool = False

    def to_contract_message(
        self,
        *,
        topic_prefix: str = MQTT_TOPIC_PREFIX_DEFAULT,
    ) -> MqttContractMessage:
        return build_speech_message(
            self.device_id,
            text=self.text,
            session_id=self.session_id,
            turn=self.turn,
            is_wake_turn=self.is_wake_turn,
            wake_word=self.wake_word,
            bypass_wake=self.bypass_wake,
            asr_confidence=self.asr_confidence,
            ts=self.ts,
            source=self.source,
            topic_prefix=topic_prefix,
            qos=self.qos,
        )


@dataclass(frozen=True)
class StatusMessage:
    device_id: str
    online: bool
    ts: str | None = None
    source: str = "go2"
    qos: int = MQTT_DEFAULT_QOS
    retain: bool = False

    def to_contract_message(
        self,
        *,
        topic_prefix: str = MQTT_TOPIC_PREFIX_DEFAULT,
    ) -> MqttContractMessage:
        return build_status_message(
            self.device_id,
            online=self.online,
            ts=self.ts,
            source=self.source,
            topic_prefix=topic_prefix,
            qos=self.qos,
        )


@dataclass(frozen=True)
class TelemetryMessage:
    device_id: str
    battery: int
    lat: float | None = None
    lng: float | None = None
    speed: float | None = None
    follow_mode: bool | None = None
    gait: str | None = None
    task_id: str | None = None
    ts: str | None = None
    source: str = "go2"
    qos: int = MQTT_DEFAULT_QOS
    retain: bool = False

    def to_contract_message(
        self,
        *,
        topic_prefix: str = MQTT_TOPIC_PREFIX_DEFAULT,
    ) -> MqttContractMessage:
        return build_telemetry_message(
            self.device_id,
            battery=self.battery,
            lat=self.lat,
            lng=self.lng,
            speed=self.speed,
            follow_mode=self.follow_mode,
            gait=self.gait,
            task_id=self.task_id,
            ts=self.ts,
            source=self.source,
            topic_prefix=topic_prefix,
            qos=self.qos,
        )


@dataclass(frozen=True)
class EventMessage:
    device_id: str
    event: str
    details: dict[str, Any] = field(default_factory=dict)
    ts: str | None = None
    source: str = "go2"
    qos: int = MQTT_DEFAULT_QOS
    retain: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "device_id", normalize_device_id(self.device_id))
        object.__setattr__(self, "event", str(self.event).strip())
        object.__setattr__(self, "source", normalize_source(self.source))
        object.__setattr__(self, "qos", normalize_qos(self.qos))
        if not self.event:
            raise ValueError("event must not be empty")
        if self.details is None:
            object.__setattr__(self, "details", {})

    def to_contract_message(
        self,
        *,
        topic_prefix: str = MQTT_TOPIC_PREFIX_DEFAULT,
    ) -> MqttContractMessage:
        if self.event == "session_start":
            return build_session_start_message(
                self.device_id,
                session_id=str(self.details.get("session_id") or ""),
                wake_word=self.details.get("wake_word"),
                ts=self.ts,
                source=self.source,
                topic_prefix=topic_prefix,
                qos=self.qos,
            )
        if self.event == "session_end":
            return build_session_end_message(
                self.device_id,
                session_id=str(self.details.get("session_id") or ""),
                reason=str(self.details.get("reason") or ""),
                turns=int(self.details.get("turns") or 0),
                ts=self.ts,
                source=self.source,
                topic_prefix=topic_prefix,
                qos=self.qos,
            )
        if self.event == "clip_done":
            return build_clip_done_message(
                self.device_id,
                session_id=str(self.details.get("session_id") or ""),
                request_id=str(self.details.get("request_id") or ""),
                clips=list(self.details.get("clips") or []),
                played=int(self.details.get("played") or 0),
                status=str(self.details.get("status") or ""),
                missing_clips=list(self.details.get("missing_clips") or []),
                ts=self.ts,
                source=self.source,
                topic_prefix=topic_prefix,
                qos=self.qos,
            )
        if self.event == "fall_detected":
            return build_fall_detected_message(
                self.device_id,
                confidence=float(self.details.get("confidence") or 0.0),
                detector=str(self.details.get("detector") or ""),
                lat=self.details.get("lat"),
                lng=self.details.get("lng"),
                ts=self.ts,
                source=self.source,
                topic_prefix=topic_prefix,
                qos=self.qos,
            )
        if self.event == "follow_lost":
            return build_follow_lost_message(
                self.device_id,
                detail=str(self.details.get("detail") or ""),
                lat=self.details.get("lat"),
                lng=self.details.get("lng"),
                ts=self.ts,
                source=self.source,
                topic_prefix=topic_prefix,
                qos=self.qos,
            )
        if self.event == "pong":
            return build_pong_message(
                self.device_id,
                nonce=str(self.details.get("nonce") or ""),
                battery=self.details.get("battery"),
                follow_mode=self.details.get("follow_mode"),
                ts=self.ts,
                source=self.source,
                topic_prefix=topic_prefix,
                qos=self.qos,
            )
        payload = {
            "device_id": self.device_id,
            "ts": self.ts or "",
            "source": self.source,
            "event": self.event,
            **dict(self.details),
        }
        return MqttContractMessage(
            topic=contract_topic(self.device_id, "event", topic_prefix=topic_prefix),
            payload=payload,
            qos=self.qos,
            retain=self.retain,
        )


ControlCallback = Callable[[CommandMessage], dict[str, Any]]
ClipCallback = Callable[[CommandMessage], dict[str, Any]]
StatusCallback = Callable[[str], dict[str, Any]]


@dataclass
class Go2ControlAdapter:
    start_follow: ControlCallback
    stop_follow: ControlCallback
    resume_follow: ControlCallback
    play_clips: ClipCallback
    ping: ControlCallback
    status_snapshot: StatusCallback | None = None
    state_machine: BMachineStateMachine = field(default_factory=BMachineStateMachine)
    default_follow_duration_minutes: int = 3

    def handle(self, message: CommandMessage) -> list[MqttContractMessage]:
        if message.command == "start_follow":
            self._handle_start_follow(message)
            return self._status_messages(message.device_id)
        if message.command == "stop_follow":
            self._handle_stop_follow(message)
            return self._status_messages(message.device_id)
        if message.command == "return_home":
            self._handle_stop_follow(message)
            return self._status_messages(message.device_id)
        if message.command == "resume_follow":
            self._handle_resume_follow(message)
            return self._status_messages(message.device_id)
        if message.command in {"tts_speak", "ask_confirm", "alarm_broadcast"}:
            previous_state = self.state_machine.state
            self.state_machine.on_playback_started()
            try:
                clip_result = self.play_clips(message)
            except Exception as exc:
                clip_result = {
                    "clips": list(message.payload.get("clips") or []),
                    "played": 0,
                    "status": "error",
                    "missing_clips": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            finally:
                if self.state_machine.state is BMachineState.SPEAKING:
                    self.state_machine.on_playback_finished(previous_state)
            result_message = EventMessage(
                device_id=message.device_id,
                event="clip_done",
                details={
                    "session_id": str(message.payload.get("session_id") or ""),
                    "request_id": message.request_id,
                    "clips": list(clip_result.get("clips") or message.payload.get("clips") or []),
                    "played": int(clip_result.get("played") or 0),
                    "status": str(clip_result.get("status") or "done"),
                    "missing_clips": list(clip_result.get("missing_clips") or []),
                    "error": str(clip_result.get("error") or ""),
                },
                ts=message.ts,
                source=message.source,
            )
            return [result_message.to_contract_message(), *self._status_messages(message.device_id)]
        if message.command == "ping":
            ping_result = self.ping(message)
            return [
                EventMessage(
                    device_id=message.device_id,
                    event="pong",
                    details={
                        "nonce": str(ping_result.get("nonce") or message.payload.get("nonce") or message.request_id),
                        "battery": ping_result.get("battery"),
                        "follow_mode": ping_result.get("follow_mode"),
                    },
                    ts=message.ts,
                    source=message.source,
                ).to_contract_message(),
                *self._status_messages(message.device_id),
            ]
        raise ValueError(f"unsupported command: {message.command}")

    @property
    def state(self) -> BMachineState:
        return self.state_machine.state

    def _handle_start_follow(self, message: CommandMessage) -> dict[str, Any]:
        if self.state_machine.state is BMachineState.FOLLOWING:
            return {"ok": True, "action": "noop", "reason": "already_following"}
        if self.state_machine.state is BMachineState.PAUSED:
            result = self.resume_follow(self._with_follow_defaults(message, action="RESUME"))
            if self._control_result_ok(result):
                self.state_machine.set_state(BMachineState.FOLLOWING, reason="resume_from_start_follow")
            else:
                self.state_machine.set_state(BMachineState.PAUSED, reason="resume_from_start_follow_failed")
            return result
        result = self.start_follow(self._with_follow_defaults(message, action="FOLLOW_3MIN"))
        if self._control_result_ok(result):
            self.state_machine.set_state(BMachineState.FOLLOWING, reason="start_follow")
        else:
            self.state_machine.set_state(BMachineState.IDLE, reason="start_follow_failed")
        return result

    def _handle_stop_follow(self, message: CommandMessage) -> dict[str, Any]:
        if self.state_machine.state is BMachineState.IDLE:
            return {"ok": True, "action": "noop", "reason": "already_idle"}
        result = self.stop_follow(message)
        if self._control_result_ok(result):
            self.state_machine.set_state(BMachineState.IDLE, reason="stop_follow")
        else:
            self.state_machine.set_state(self.state_machine.state, reason="stop_follow_failed")
        return result

    def _handle_resume_follow(self, message: CommandMessage) -> dict[str, Any]:
        if self.state_machine.state is BMachineState.FOLLOWING:
            return {"ok": True, "action": "noop", "reason": "already_following"}
        if self.state_machine.state is not BMachineState.PAUSED:
            return {"ok": False, "action": "reject", "reason": "resume_requires_paused"}
        result = self.resume_follow(self._with_follow_defaults(message, action="RESUME"))
        if self._control_result_ok(result):
            self.state_machine.set_state(BMachineState.FOLLOWING, reason="resume_follow")
        else:
            self.state_machine.set_state(BMachineState.PAUSED, reason="resume_follow_failed")
        return result

    @staticmethod
    def _control_result_ok(result: dict[str, Any] | None) -> bool:
        if result is None:
            return True
        if result.get("ok") is False:
            return False
        if str(result.get("action") or "").strip().lower() == "reject":
            return False
        return True

    def _with_follow_defaults(
        self,
        message: CommandMessage,
        *,
        action: str,
    ) -> CommandMessage:
        payload = dict(message.payload)
        payload.setdefault("duration_minutes", self.default_follow_duration_minutes)
        payload.setdefault("runtime_command", action)
        if int(payload["duration_minutes"]) == 3 and action == "FOLLOW_3MIN":
            payload.setdefault("follow_profile", "FOLLOW_3MIN")
        return CommandMessage(
            device_id=message.device_id,
            command=message.command,
            request_id=message.request_id,
            payload=payload,
            ts=message.ts,
            source=message.source,
            qos=message.qos,
            retain=message.retain,
        )

    def _status_messages(self, device_id: str) -> list[MqttContractMessage]:
        snapshot = self.status_snapshot(device_id) if self.status_snapshot is not None else {}
        if not snapshot:
            snapshot = {}
        if "device_id" not in snapshot:
            snapshot["device_id"] = device_id
        snapshot.setdefault("online", True)
        snapshot.setdefault("state", self.state_machine.state.value)
        snapshot.setdefault("state_reason", self.state_machine.last_reason)
        return [
            StatusMessage(
                device_id=str(snapshot.get("device_id") or device_id),
                online=bool(snapshot.get("online", True)),
                ts=str(snapshot.get("ts") or "") or None,
                source=str(snapshot.get("source") or "go2"),
            ).to_contract_message()
        ]


@dataclass
class CommandDispatcher:
    transport: MessageTransport
    adapter: Go2ControlAdapter
    topic_prefix: str = MQTT_TOPIC_PREFIX_DEFAULT
    last_command: CommandMessage | None = None
    last_outcome: list[MqttContractMessage] = field(default_factory=list)

    def bind(self, device_id: str) -> None:
        topic = contract_topic(device_id, "cmd", topic_prefix=self.topic_prefix)
        self.transport.subscribe(topic, self._on_command)

    def _on_command(self, topic: str, payload: dict[str, Any]) -> None:
        command = CommandMessage.from_payload(payload)
        self.last_command = command
        self.last_outcome = [
            self._with_bound_topic_prefix(message)
            for message in self.adapter.handle(command)
        ]
        for message in self.last_outcome:
            self.transport.publish(message)

    def _with_bound_topic_prefix(
        self,
        message: MqttContractMessage,
    ) -> MqttContractMessage:
        channel = str(message.topic or "").rstrip("/").split("/")[-1]
        if channel not in {"cmd", "speech", "event", "telemetry", "status"}:
            return message
        device_id = str(message.payload.get("device_id") or "").strip()
        if not device_id:
            return message
        return MqttContractMessage(
            topic=contract_topic(device_id, channel, topic_prefix=self.topic_prefix),
            payload=dict(message.payload),
            qos=message.qos,
            retain=message.retain,
        )


def build_command_dispatcher(
    transport: MessageTransport,
    adapter: Go2ControlAdapter,
    *,
    topic_prefix: str = MQTT_TOPIC_PREFIX_DEFAULT,
) -> CommandDispatcher:
    return CommandDispatcher(
        transport=transport,
        adapter=adapter,
        topic_prefix=normalize_topic_prefix(topic_prefix),
    )


@dataclass
class MockAMachine:
    """Rule-based A-machine simulator for the B-machine software loop."""

    transport: MessageTransport
    device_id: str
    topic_prefix: str = MQTT_TOPIC_PREFIX_DEFAULT
    outing_clip: str = "outing.allow"
    _pending_start_by_session: set[str] = field(default_factory=set)

    def bind(self) -> None:
        self.transport.subscribe(
            contract_topic(self.device_id, "speech", topic_prefix=self.topic_prefix),
            self._on_speech,
        )
        self.transport.subscribe(
            contract_topic(self.device_id, "event", topic_prefix=self.topic_prefix),
            self._on_event,
        )

    def _on_speech(self, _topic: str, payload: dict[str, Any]) -> None:
        text = str(payload.get("text") or "").strip()
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            return
        if any(term in text for term in ("出去", "走走", "散步", "转转")):
            self._pending_start_by_session.add(session_id)
            self.transport.publish(
                build_command_message(
                    self.device_id,
                    command="tts_speak",
                    request_id=f"mock-a-tts-{session_id}",
                    payload={
                        "session_id": session_id,
                        "clips": [self.outing_clip],
                        "interrupt": False,
                    },
                    source="simulator",
                    topic_prefix=self.topic_prefix,
                )
            )

    def _on_event(self, _topic: str, payload: dict[str, Any]) -> None:
        if payload.get("event") != "clip_done":
            return
        session_id = str(payload.get("session_id") or "").strip()
        if (
            session_id not in self._pending_start_by_session
            or payload.get("status") != "done"
            or self.outing_clip not in list(payload.get("clips") or [])
        ):
            return
        self._pending_start_by_session.remove(session_id)
        self.transport.publish(
            build_command_message(
                self.device_id,
                command="start_follow",
                request_id=f"mock-a-follow-{session_id}",
                payload={"session_id": session_id, "duration_minutes": 3},
                source="simulator",
                topic_prefix=self.topic_prefix,
            )
        )
