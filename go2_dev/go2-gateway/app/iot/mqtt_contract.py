from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Protocol


MQTT_TOPIC_PREFIX_DEFAULT = "aiot"
MQTT_DOG_NAMESPACE = "dog"
MQTT_DEFAULT_QOS = 1
MQTT_ALLOWED_SOURCES = {"go2", "simulator", "serial"}
MQTT_ALLOWED_COMMANDS = {
    "tts_speak",
    "ask_confirm",
    "alarm_broadcast",
    "start_follow",
    "stop_follow",
    "resume_follow",
    "return_home",
    "ping",
}
MQTT_ALLOWED_EVENT_NAMES = {
    "session_start",
    "session_end",
    "clip_done",
    "fall_detected",
    "follow_lost",
    "pong",
}
MQTT_ALLOWED_SESSION_END_REASONS = {
    "timeout",
    "user_exit",
    "interrupt",
    "error",
}
MQTT_ALLOWED_CLIP_STATUS = {
    "done",
    "interrupted",
    "missing",
    "error",
}


def _now_local_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def normalize_device_id(device_id: str) -> str:
    normalized = str(device_id or "").strip()
    if not normalized:
        raise ValueError("device_id must not be empty")
    return normalized


def normalize_topic_prefix(topic_prefix: str) -> str:
    normalized = str(topic_prefix or "").strip().strip("/")
    if not normalized:
        raise ValueError("topic_prefix must not be empty")
    return normalized


def normalize_source(source: str) -> str:
    normalized = str(source or "").strip().lower()
    if normalized not in MQTT_ALLOWED_SOURCES:
        raise ValueError(
            f"source must be one of {sorted(MQTT_ALLOWED_SOURCES)!r}"
        )
    return normalized


def normalize_qos(qos: int) -> int:
    value = int(qos)
    if value not in {0, 1, 2}:
        raise ValueError("qos must be 0, 1, or 2")
    return value


def contract_topic(
    device_id: str,
    suffix: str,
    *,
    topic_prefix: str = MQTT_TOPIC_PREFIX_DEFAULT,
) -> str:
    normalized_suffix = str(suffix or "").strip().strip("/")
    if not normalized_suffix:
        raise ValueError("suffix must not be empty")
    return (
        f"{normalize_topic_prefix(topic_prefix)}/"
        f"{MQTT_DOG_NAMESPACE}/"
        f"{normalize_device_id(device_id)}/"
        f"{normalized_suffix}"
    )


def build_common_fields(
    device_id: str,
    *,
    source: str = "go2",
    ts: str | None = None,
) -> dict[str, Any]:
    return {
        "device_id": normalize_device_id(device_id),
        "ts": str(ts).strip() if ts is not None else _now_local_iso(),
        "source": normalize_source(source),
    }


def _merge_common(
    device_id: str,
    *,
    source: str = "go2",
    ts: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    payload = build_common_fields(device_id, source=source, ts=ts)
    payload.update(extra)
    return payload


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _validate_confidence(value: float | None) -> float:
    confidence = 1.0 if value is None else float(value)
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("asr_confidence must be between 0 and 1")
    return confidence


@dataclass(frozen=True)
class MqttContractMessage:
    topic: str
    payload: dict[str, Any]
    qos: int = MQTT_DEFAULT_QOS
    retain: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "topic", str(self.topic).strip())
        object.__setattr__(self, "qos", normalize_qos(self.qos))
        if not self.topic:
            raise ValueError("topic must not be empty")

    def to_json(self) -> str:
        return json.dumps(self.payload, ensure_ascii=False, separators=(",", ":"))


class MqttContractBus(Protocol):
    def publish(self, message: MqttContractMessage) -> None: ...

    def subscribe(
        self,
        topic: str,
        callback: Callable[[str, dict[str, Any]], None],
    ) -> None: ...


@dataclass
class MemoryMqttContractBus:
    published: list[MqttContractMessage] = field(default_factory=list)
    _subscriptions: dict[str, list[Callable[[str, dict[str, Any]], None]]] = field(
        default_factory=dict
    )

    def publish(self, message: MqttContractMessage) -> None:
        self.published.append(message)
        for callback in self._subscriptions.get(message.topic, []):
            callback(message.topic, dict(message.payload))

    def subscribe(
        self,
        topic: str,
        callback: Callable[[str, dict[str, Any]], None],
    ) -> None:
        normalized = str(topic).strip()
        if not normalized:
            raise ValueError("topic must not be empty")
        self._subscriptions.setdefault(normalized, []).append(callback)


def build_status_message(
    device_id: str,
    *,
    online: bool,
    source: str = "go2",
    ts: str | None = None,
    topic_prefix: str = MQTT_TOPIC_PREFIX_DEFAULT,
    qos: int = MQTT_DEFAULT_QOS,
) -> MqttContractMessage:
    payload = _merge_common(
        device_id,
        source=source,
        ts=ts,
        status="online" if online else "offline",
    )
    return MqttContractMessage(
        topic=contract_topic(device_id, "status", topic_prefix=topic_prefix),
        payload=payload,
        qos=qos,
    )


def build_speech_message(
    device_id: str,
    *,
    text: str,
    session_id: str,
    turn: int,
    is_wake_turn: bool,
    source: str = "go2",
    ts: str | None = None,
    wake_word: str | None = None,
    bypass_wake: bool = False,
    asr_confidence: float | None = None,
    topic_prefix: str = MQTT_TOPIC_PREFIX_DEFAULT,
    qos: int = MQTT_DEFAULT_QOS,
) -> MqttContractMessage:
    normalized_text = str(text or "").strip()
    if not normalized_text:
        raise ValueError("text must not be empty")
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        raise ValueError("session_id must not be empty")
    turn_number = int(turn)
    if turn_number < 1:
        raise ValueError("turn must be at least 1")
    payload = _merge_common(
        device_id,
        source=source,
        ts=ts,
        text=normalized_text,
        asr_confidence=_validate_confidence(asr_confidence),
        session_id=normalized_session_id,
        turn=turn_number,
        is_wake_turn=bool(is_wake_turn),
        bypass_wake=bool(bypass_wake),
    )
    normalized_wake_word = _optional_text(wake_word)
    if normalized_wake_word is not None:
        payload["wake_word"] = normalized_wake_word
    return MqttContractMessage(
        topic=contract_topic(device_id, "speech", topic_prefix=topic_prefix),
        payload=payload,
        qos=qos,
    )


def build_session_start_message(
    device_id: str,
    *,
    session_id: str,
    wake_word: str | None = None,
    source: str = "go2",
    ts: str | None = None,
    topic_prefix: str = MQTT_TOPIC_PREFIX_DEFAULT,
    qos: int = MQTT_DEFAULT_QOS,
) -> MqttContractMessage:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        raise ValueError("session_id must not be empty")
    payload = _merge_common(
        device_id,
        source=source,
        ts=ts,
        event="session_start",
        session_id=normalized_session_id,
    )
    normalized_wake_word = _optional_text(wake_word)
    if normalized_wake_word is not None:
        payload["wake_word"] = normalized_wake_word
    return MqttContractMessage(
        topic=contract_topic(device_id, "event", topic_prefix=topic_prefix),
        payload=payload,
        qos=qos,
    )


def build_session_end_message(
    device_id: str,
    *,
    session_id: str,
    reason: str,
    turns: int,
    source: str = "go2",
    ts: str | None = None,
    topic_prefix: str = MQTT_TOPIC_PREFIX_DEFAULT,
    qos: int = MQTT_DEFAULT_QOS,
) -> MqttContractMessage:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        raise ValueError("session_id must not be empty")
    normalized_reason = str(reason or "").strip().lower()
    if normalized_reason not in MQTT_ALLOWED_SESSION_END_REASONS:
        raise ValueError(
            f"reason must be one of {sorted(MQTT_ALLOWED_SESSION_END_REASONS)!r}"
        )
    turn_count = int(turns)
    if turn_count < 0:
        raise ValueError("turns must not be negative")
    payload = _merge_common(
        device_id,
        source=source,
        ts=ts,
        event="session_end",
        session_id=normalized_session_id,
        reason=normalized_reason,
        turns=turn_count,
    )
    return MqttContractMessage(
        topic=contract_topic(device_id, "event", topic_prefix=topic_prefix),
        payload=payload,
        qos=qos,
    )


def build_clip_done_message(
    device_id: str,
    *,
    session_id: str,
    request_id: str,
    clips: list[str],
    played: int,
    status: str,
    missing_clips: list[str] | None = None,
    source: str = "go2",
    ts: str | None = None,
    topic_prefix: str = MQTT_TOPIC_PREFIX_DEFAULT,
    qos: int = MQTT_DEFAULT_QOS,
) -> MqttContractMessage:
    normalized_session_id = str(session_id or "").strip()
    normalized_request_id = str(request_id or "").strip()
    if not normalized_session_id:
        raise ValueError("session_id must not be empty")
    if not normalized_request_id:
        raise ValueError("request_id must not be empty")
    normalized_clips = [str(item).strip() for item in clips if str(item).strip()]
    if not normalized_clips:
        raise ValueError("clips must not be empty")
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in MQTT_ALLOWED_CLIP_STATUS:
        raise ValueError(
            f"status must be one of {sorted(MQTT_ALLOWED_CLIP_STATUS)!r}"
        )
    played_count = int(played)
    if played_count < 0:
        raise ValueError("played must not be negative")
    payload = _merge_common(
        device_id,
        source=source,
        ts=ts,
        event="clip_done",
        session_id=normalized_session_id,
        request_id=normalized_request_id,
        clips=normalized_clips,
        played=played_count,
        status=normalized_status,
        missing_clips=[str(item).strip() for item in (missing_clips or []) if str(item).strip()],
    )
    return MqttContractMessage(
        topic=contract_topic(device_id, "event", topic_prefix=topic_prefix),
        payload=payload,
        qos=qos,
    )


def build_pong_message(
    device_id: str,
    *,
    nonce: str,
    battery: int | None = None,
    follow_mode: bool | None = None,
    source: str = "go2",
    ts: str | None = None,
    topic_prefix: str = MQTT_TOPIC_PREFIX_DEFAULT,
    qos: int = MQTT_DEFAULT_QOS,
) -> MqttContractMessage:
    normalized_nonce = str(nonce or "").strip()
    if not normalized_nonce:
        raise ValueError("nonce must not be empty")
    payload = _merge_common(
        device_id,
        source=source,
        ts=ts,
        event="pong",
        nonce=normalized_nonce,
    )
    if battery is not None:
        payload["battery"] = int(battery)
    if follow_mode is not None:
        payload["follow_mode"] = bool(follow_mode)
    return MqttContractMessage(
        topic=contract_topic(device_id, "event", topic_prefix=topic_prefix),
        payload=payload,
        qos=qos,
    )


def build_fall_detected_message(
    device_id: str,
    *,
    confidence: float,
    detector: str,
    lat: float | None = None,
    lng: float | None = None,
    source: str = "go2",
    ts: str | None = None,
    topic_prefix: str = MQTT_TOPIC_PREFIX_DEFAULT,
    qos: int = MQTT_DEFAULT_QOS,
) -> MqttContractMessage:
    payload = _merge_common(
        device_id,
        source=source,
        ts=ts,
        event="fall_detected",
        confidence=_validate_confidence(confidence),
        detector=str(detector or "").strip(),
    )
    if not payload["detector"]:
        raise ValueError("detector must not be empty")
    if lat is not None:
        payload["lat"] = float(lat)
    if lng is not None:
        payload["lng"] = float(lng)
    return MqttContractMessage(
        topic=contract_topic(device_id, "event", topic_prefix=topic_prefix),
        payload=payload,
        qos=qos,
    )


def build_follow_lost_message(
    device_id: str,
    *,
    detail: str,
    lat: float | None = None,
    lng: float | None = None,
    source: str = "go2",
    ts: str | None = None,
    topic_prefix: str = MQTT_TOPIC_PREFIX_DEFAULT,
    qos: int = MQTT_DEFAULT_QOS,
) -> MqttContractMessage:
    normalized_detail = str(detail or "").strip()
    if not normalized_detail:
        raise ValueError("detail must not be empty")
    payload = _merge_common(
        device_id,
        source=source,
        ts=ts,
        event="follow_lost",
        detail=normalized_detail,
    )
    if lat is not None:
        payload["lat"] = float(lat)
    if lng is not None:
        payload["lng"] = float(lng)
    return MqttContractMessage(
        topic=contract_topic(device_id, "event", topic_prefix=topic_prefix),
        payload=payload,
        qos=qos,
    )


def build_telemetry_message(
    device_id: str,
    *,
    battery: int,
    lat: float | None = None,
    lng: float | None = None,
    speed: float | None = None,
    follow_mode: bool | None = None,
    gait: str | None = None,
    task_id: str | None = None,
    source: str = "go2",
    ts: str | None = None,
    topic_prefix: str = MQTT_TOPIC_PREFIX_DEFAULT,
    qos: int = MQTT_DEFAULT_QOS,
) -> MqttContractMessage:
    payload = _merge_common(device_id, source=source, ts=ts, battery=int(battery))
    if lat is not None:
        payload["lat"] = float(lat)
    if lng is not None:
        payload["lng"] = float(lng)
    if speed is not None:
        payload["speed"] = float(speed)
    if follow_mode is not None:
        payload["follow_mode"] = bool(follow_mode)
    if gait is not None:
        payload["gait"] = str(gait).strip()
    if task_id is not None:
        normalized_task_id = str(task_id).strip()
        if normalized_task_id:
            payload["task_id"] = normalized_task_id
    return MqttContractMessage(
        topic=contract_topic(device_id, "telemetry", topic_prefix=topic_prefix),
        payload=payload,
        qos=qos,
    )


def build_command_message(
    device_id: str,
    *,
    command: str,
    request_id: str,
    payload: dict[str, Any] | None = None,
    source: str = "go2",
    ts: str | None = None,
    topic_prefix: str = MQTT_TOPIC_PREFIX_DEFAULT,
    qos: int = MQTT_DEFAULT_QOS,
) -> MqttContractMessage:
    normalized_command = str(command or "").strip()
    if normalized_command not in MQTT_ALLOWED_COMMANDS:
        raise ValueError(
            f"command must be one of {sorted(MQTT_ALLOWED_COMMANDS)!r}"
        )
    normalized_request_id = str(request_id or "").strip()
    if not normalized_request_id:
        raise ValueError("request_id must not be empty")
    body = _merge_common(
        device_id,
        source=source,
        ts=ts,
        command=normalized_command,
        request_id=normalized_request_id,
        payload=dict(payload or {}),
    )
    return MqttContractMessage(
        topic=contract_topic(device_id, "cmd", topic_prefix=topic_prefix),
        payload=body,
        qos=qos,
    )


class MqttContractClient:
    def __init__(
        self,
        bus: MqttContractBus,
        *,
        topic_prefix: str = MQTT_TOPIC_PREFIX_DEFAULT,
        source: str = "go2",
        qos: int = MQTT_DEFAULT_QOS,
    ) -> None:
        self._bus = bus
        self._topic_prefix = normalize_topic_prefix(topic_prefix)
        self._source = normalize_source(source)
        self._qos = normalize_qos(qos)

    def publish(self, message: MqttContractMessage) -> None:
        self._bus.publish(message)

    def subscribe(
        self,
        device_id: str,
        *,
        callback: Callable[[str, dict[str, Any]], None],
    ) -> None:
        self._bus.subscribe(
            contract_topic(device_id, "cmd", topic_prefix=self._topic_prefix),
            callback,
        )

    def publish_status(self, device_id: str, *, online: bool) -> None:
        self.publish(
            build_status_message(
                device_id,
                online=online,
                source=self._source,
                topic_prefix=self._topic_prefix,
                qos=self._qos,
            )
        )

    def publish_speech(
        self,
        device_id: str,
        *,
        text: str,
        session_id: str,
        turn: int,
        is_wake_turn: bool,
        wake_word: str | None = None,
        bypass_wake: bool = False,
        asr_confidence: float | None = None,
    ) -> None:
        self.publish(
            build_speech_message(
                device_id,
                text=text,
                session_id=session_id,
                turn=turn,
                is_wake_turn=is_wake_turn,
                wake_word=wake_word,
                bypass_wake=bypass_wake,
                asr_confidence=asr_confidence,
                source=self._source,
                topic_prefix=self._topic_prefix,
                qos=self._qos,
            )
        )

    def publish_session_start(
        self,
        device_id: str,
        *,
        session_id: str,
        wake_word: str | None = None,
    ) -> None:
        self.publish(
            build_session_start_message(
                device_id,
                session_id=session_id,
                wake_word=wake_word,
                source=self._source,
                topic_prefix=self._topic_prefix,
                qos=self._qos,
            )
        )

    def publish_session_end(
        self,
        device_id: str,
        *,
        session_id: str,
        reason: str,
        turns: int,
    ) -> None:
        self.publish(
            build_session_end_message(
                device_id,
                session_id=session_id,
                reason=reason,
                turns=turns,
                source=self._source,
                topic_prefix=self._topic_prefix,
                qos=self._qos,
            )
        )

    def publish_clip_done(
        self,
        device_id: str,
        *,
        session_id: str,
        request_id: str,
        clips: list[str],
        played: int,
        status: str,
        missing_clips: list[str] | None = None,
    ) -> None:
        self.publish(
            build_clip_done_message(
                device_id,
                session_id=session_id,
                request_id=request_id,
                clips=clips,
                played=played,
                status=status,
                missing_clips=missing_clips,
                source=self._source,
                topic_prefix=self._topic_prefix,
                qos=self._qos,
            )
        )

    def publish_telemetry(
        self,
        device_id: str,
        *,
        battery: int,
        lat: float | None = None,
        lng: float | None = None,
        speed: float | None = None,
        follow_mode: bool | None = None,
        gait: str | None = None,
        task_id: str | None = None,
    ) -> None:
        self.publish(
            build_telemetry_message(
                device_id,
                battery=battery,
                lat=lat,
                lng=lng,
                speed=speed,
                follow_mode=follow_mode,
                gait=gait,
                task_id=task_id,
                source=self._source,
                topic_prefix=self._topic_prefix,
                qos=self._qos,
            )
        )

    def publish_command(
        self,
        device_id: str,
        *,
        command: str,
        request_id: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.publish(
            build_command_message(
                device_id,
                command=command,
                request_id=request_id,
                payload=payload,
                source=self._source,
                topic_prefix=self._topic_prefix,
                qos=self._qos,
            )
        )


def build_mqtt_contract_client(
    bus: MqttContractBus,
    *,
    topic_prefix: str = MQTT_TOPIC_PREFIX_DEFAULT,
    source: str = "go2",
    qos: int = MQTT_DEFAULT_QOS,
) -> MqttContractClient:
    return MqttContractClient(bus, topic_prefix=topic_prefix, source=source, qos=qos)
