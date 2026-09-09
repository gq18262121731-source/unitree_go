from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from app.iot.mqtt_contract import (
    MQTT_TOPIC_PREFIX_DEFAULT,
    build_command_message,
    contract_topic,
)
from app.iot.protocol_layer import MessageTransport
from app.voice.clip_composer import (
    health_temperature_clip,
    spo2_clip,
    temperature_value_clip,
)


class WeatherCondition(str, Enum):
    SUNNY = "sunny"
    CLOUDY = "cloudy"
    OVERCAST = "overcast"
    RAIN = "rain"
    SNOW = "snow"
    FOG = "fog"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class HealthContext:
    heart_rate: int
    spo2: int
    temperature: float
    blood_pressure_systolic: int
    blood_pressure_diastolic: int
    status: str = "good"


@dataclass(frozen=True)
class WeatherContext:
    city: str
    condition: WeatherCondition | str
    temperature: int | None
    feels_like: int | None = None
    precipitation: bool = False
    error: str | None = None


@dataclass(frozen=True)
class MedicationContext:
    required_today: bool
    taken: bool


@dataclass(frozen=True)
class XiaokangDecision:
    intent: str
    allowed: bool | None = None
    health_status: str | None = None
    heart_rate: int | None = None
    spo2: int | None = None
    body_temperature: float | None = None
    weather: str | None = None
    temperature: int | None = None
    medication_reminder: bool = False
    action: str | None = None
    clips: tuple[str, ...] = ()
    reply: str = ""


@dataclass(frozen=True)
class PendingAction:
    request_id: str
    session_id: str
    action: str


class HealthProvider(Protocol):
    def get_current_health(self) -> HealthContext: ...


class WeatherProvider(Protocol):
    def get_weather(self) -> WeatherContext: ...


class MedicationProvider(Protocol):
    def get_status(self) -> MedicationContext: ...


class StaticHealthProvider:
    def __init__(
        self,
        context: HealthContext | None = None,
        *,
        profiles: dict[str, HealthContext] | None = None,
    ) -> None:
        self._context = context or HealthContext(
            heart_rate=76,
            spo2=98,
            temperature=36.5,
            blood_pressure_systolic=125,
            blood_pressure_diastolic=78,
            status="good",
        )
        self._profiles = dict(profiles or {})

    @classmethod
    def from_json_file(cls, path: str | Path) -> "StaticHealthProvider":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if any(isinstance(value, dict) for value in payload.values()):
            profiles = {
                str(name): _health_context_from_payload(dict(value))
                for name, value in payload.items()
                if isinstance(value, dict)
            }
            return cls(
                profiles.get("health_query")
                or profiles.get("default")
                or next(iter(profiles.values())),
                profiles=profiles,
            )
        return cls(_health_context_from_payload(payload))

    def get_current_health(self) -> HealthContext:
        return self._context

    def get_profile(self, profile: str) -> HealthContext:
        return self._profiles.get(str(profile or "").strip(), self._context)


class MqttHealthProvider:
    """Placeholder provider for a later A-machine data source.

    It deliberately does not connect to MQTT during LOCAL FIRST mode.
    """

    def __init__(self, latest: HealthContext | None = None) -> None:
        self._latest = latest

    def get_current_health(self) -> HealthContext:
        if self._latest is None:
            raise RuntimeError("MQTT health source is not connected")
        return self._latest


class OpenMeteoWeatherProvider:
    def __init__(
        self,
        *,
        city: str = "北京",
        latitude: float = 39.9042,
        longitude: float = 116.4074,
        api_url: str = "https://api.open-meteo.com/v1/forecast",
        timeout_seconds: float = 3.0,
    ) -> None:
        self.city = str(city or "北京").strip() or "北京"
        self.latitude = float(latitude)
        self.longitude = float(longitude)
        self.api_url = str(api_url or "").strip()
        self.timeout_seconds = max(0.5, float(timeout_seconds))

    @classmethod
    def from_env(cls) -> "OpenMeteoWeatherProvider":
        return cls(
            city=os.environ.get(
                "XIAOKANG_WEATHER_CITY",
                os.environ.get("GO2_WEATHER_CITY", "北京"),
            ),
            latitude=float(os.environ.get("XIAOKANG_WEATHER_LAT", "39.9042")),
            longitude=float(os.environ.get("XIAOKANG_WEATHER_LON", "116.4074")),
            api_url=os.environ.get(
                "XIAOKANG_WEATHER_API_URL",
                "https://api.open-meteo.com/v1/forecast",
            ),
            timeout_seconds=float(os.environ.get("XIAOKANG_WEATHER_TIMEOUT", "3")),
        )

    def get_weather(self) -> WeatherContext:
        if not self.api_url:
            return WeatherContext(
                city=self.city,
                condition=WeatherCondition.UNKNOWN,
                temperature=None,
                error="weather api url is empty",
            )
        query = urllib.parse.urlencode(
            {
                "latitude": f"{self.latitude:.6f}",
                "longitude": f"{self.longitude:.6f}",
                "current": "temperature_2m,apparent_temperature,precipitation,weather_code",
                "timezone": "auto",
            }
        )
        separator = "&" if "?" in self.api_url else "?"
        url = f"{self.api_url}{separator}{query}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            return WeatherContext(
                city=self.city,
                condition=WeatherCondition.UNKNOWN,
                temperature=None,
                error=f"{type(exc).__name__}: {exc}",
            )
        current = dict(payload.get("current") or payload.get("current_weather") or {})
        temperature = _optional_rounded_int(
            current.get("temperature_2m", current.get("temperature"))
        )
        feels_like = _optional_rounded_int(current.get("apparent_temperature"))
        precipitation = float(current.get("precipitation") or 0.0) > 0.0
        condition = open_meteo_code_to_condition(current.get("weather_code"))
        if precipitation and condition is WeatherCondition.SUNNY:
            condition = WeatherCondition.RAIN
        return WeatherContext(
            city=self.city,
            condition=condition,
            temperature=temperature,
            feels_like=feels_like,
            precipitation=precipitation,
        )


class StaticMedicationProvider:
    def __init__(self, context: MedicationContext | None = None) -> None:
        self._context = context or MedicationContext(
            required_today=True,
            taken=False,
        )

    def get_status(self) -> MedicationContext:
        return self._context


class ClipAssembler:
    def __init__(
        self,
        *,
        is_clip_available=None,
        printer=print,
    ) -> None:
        self._is_clip_available = is_clip_available or (lambda _clip_id: True)
        self._printer = printer

    def outing_allow(self, decision: XiaokangDecision) -> list[str]:
        clips = ["outing.allow.health_good"]
        if decision.heart_rate is not None:
            self._extend_optional_group(
                clips,
                [
                    "health.hr.prefix",
                    _safe_number_clip(decision.heart_rate),
                    "unit.bpm",
                ],
            )
        if decision.spo2 is not None:
            self._extend_spo2(clips, decision.spo2)
        if decision.body_temperature is not None:
            self._extend_body_temperature(clips, decision.body_temperature)
        weather = _weather_value(decision.weather)
        if weather is not None and weather is not WeatherCondition.UNKNOWN:
            if decision.temperature is not None:
                if self._extend_optional_group(
                    clips,
                    [
                        f"weather.condition.{weather.value}",
                        "weather.temperature.prefix",
                        _safe_temperature_value_clip(decision.temperature),
                    ],
                ):
                    return self._finish_outing_clips(
                        clips,
                        medication_reminder=decision.medication_reminder,
                    )
            self._extend_optional_group(
                clips,
                ["weather.today.beijing", f"weather.{weather.value}"],
            )
        if decision.temperature is not None:
            self._extend_optional_group(
                clips,
                [
                    "weather.temp.prefix",
                    _safe_number_clip(decision.temperature),
                    "unit.celsius",
                ],
            )
        return self._finish_outing_clips(
            clips,
            medication_reminder=decision.medication_reminder,
        )

    def _finish_outing_clips(
        self,
        clips: list[str],
        *,
        medication_reminder: bool,
    ) -> list[str]:
        if medication_reminder:
            self._add_optional(clips, "medication.reminder.before_outing")
        clips.append("outing.allow.suffix")
        return clips

    def stop_follow(self) -> list[str]:
        return ["follow.stop"] if self._available("follow.stop") else []

    def wake_ack(self) -> list[str]:
        return ["sess.wake_ack"] if self._available("sess.wake_ack") else []

    def _extend_body_temperature(self, clips: list[str], value: float) -> None:
        full_clip = _safe_health_temperature_clip(value)
        if full_clip and self._available(full_clip):
            clips.append(full_clip)
            return
        value_clip = _safe_temperature_value_clip(value)
        if self._extend_optional_group(
            clips,
            ["health.temperature.prefix", value_clip],
        ):
            return
        fallback_number = _safe_number_clip(value)
        if fallback_number is not None:
            self._extend_optional_group(
                clips,
                ["health.temperature.prefix", fallback_number, "unit.celsius"],
            )

    def _extend_spo2(self, clips: list[str], value: int) -> None:
        full_clip = _safe_spo2_clip(value)
        if full_clip and self._available(full_clip):
            clips.append(full_clip)
            return
        self._extend_optional_group(
            clips,
            ["health.spo2.prefix", _safe_number_clip(value)],
        )

    def _extend_optional_group(self, clips: list[str], group: list[str | None]) -> bool:
        normalized = [str(item) for item in group if item]
        if len(normalized) != len(group):
            self._printer("[CLIP] optional_missing numeric")
            return False
        missing = [clip for clip in normalized if not self._available(clip)]
        if missing:
            for clip in missing:
                self._printer(f"[CLIP] optional_missing {clip}")
            return False
        clips.extend(normalized)
        return True

    def _add_optional(self, clips: list[str], clip_id: str) -> None:
        if self._available(clip_id):
            clips.append(clip_id)
        else:
            self._printer(f"[CLIP] optional_missing {clip_id}")

    def _available(self, clip_id: str) -> bool:
        try:
            return bool(self._is_clip_available(clip_id))
        except Exception:
            return False


class XiaokangAgentService:
    def __init__(
        self,
        *,
        health_provider: HealthProvider,
        weather_provider: WeatherProvider,
        medication_provider: MedicationProvider,
        clip_assembler: ClipAssembler | None = None,
        auto_follow: bool = False,
    ) -> None:
        self.health_provider = health_provider
        self.weather_provider = weather_provider
        self.medication_provider = medication_provider
        self.clip_assembler = clip_assembler or ClipAssembler()
        self.auto_follow = bool(auto_follow)

    def handle_text(self, text: str) -> XiaokangDecision:
        normalized = _normalize_text(text)
        if _is_stop_follow(normalized):
            return XiaokangDecision(
                intent="stop_follow",
                action="stop_follow",
                clips=tuple(self.clip_assembler.stop_follow()),
                reply="好，我先不跟着您了。",
            )
        if _is_outing_request(normalized):
            return self._handle_outing()
        return XiaokangDecision(
            intent="unknown",
            action=None,
            clips=tuple(self.clip_assembler.wake_ack()),
            reply="我在，您说。",
        )

    def _handle_outing(self) -> XiaokangDecision:
        health = self.health_provider.get_current_health()
        weather = self.weather_provider.get_weather()
        medication = self.medication_provider.get_status()
        condition = _weather_value(weather.condition)
        reminder = medication.required_today and not medication.taken
        decision = XiaokangDecision(
            intent="outing_request",
            allowed=True,
            health_status=health.status,
            heart_rate=health.heart_rate,
            spo2=health.spo2,
            body_temperature=health.temperature,
            weather=None if condition is WeatherCondition.UNKNOWN else condition.value,
            temperature=weather.temperature,
            medication_reminder=reminder,
            action="start_follow" if self.auto_follow else None,
            reply="可以出去走走，我陪着您。",
        )
        return XiaokangDecision(
            **{
                **decision.__dict__,
                "clips": tuple(self.clip_assembler.outing_allow(decision)),
            }
        )


class LocalFirstXiaokangAgent:
    """Local A-machine replacement wired through the same MQTT contract bus."""

    def __init__(
        self,
        transport: MessageTransport,
        device_id: str,
        agent: XiaokangAgentService,
        *,
        topic_prefix: str = MQTT_TOPIC_PREFIX_DEFAULT,
        printer=print,
    ) -> None:
        self.transport = transport
        self.device_id = device_id
        self.agent = agent
        self.topic_prefix = topic_prefix
        self._printer = printer
        self._pending_actions: dict[str, PendingAction] = {}

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
        if not text or not session_id:
            return
        self._clear_pending_for_session(session_id)
        decision = self.agent.handle_text(text)
        self._publish_decision(decision, payload)

    def _publish_decision(self, decision: XiaokangDecision, payload: dict[str, Any]) -> None:
        session_id = str(payload.get("session_id") or "").strip()
        self._printer(f"[AGENT] intent: {decision.intent}")
        if decision.heart_rate is not None or decision.health_status is not None:
            self._printer(
                f"[HEALTH] hr={decision.heart_rate} status={decision.health_status}"
            )
        if decision.weather is not None or decision.temperature is not None:
            self._printer(f"[WEATHER] {decision.weather} {decision.temperature}C")
        if decision.action == "stop_follow":
            self.transport.publish(
                build_command_message(
                    self.device_id,
                    command="stop_follow",
                    request_id=f"xiaokang-stop-{session_id}",
                    payload={"session_id": session_id},
                    source="simulator",
                    topic_prefix=self.topic_prefix,
                )
            )
        elif decision.action == "clear_pending":
            self._clear_pending_for_session(session_id)
        if decision.clips:
            clips = list(decision.clips)
            turn_suffix = str(payload.get("turn") or "").strip()
            request_id = (
                f"xiaokang-tts-{session_id}-{turn_suffix}"
                if turn_suffix
                else f"xiaokang-tts-{session_id}"
            )
            if decision.action:
                self._pending_actions[request_id] = PendingAction(
                    request_id=request_id,
                    session_id=session_id,
                    action=decision.action,
                )
            self._printer(f"[VOICE] reply clips={clips}")
            self.transport.publish(
                build_command_message(
                    self.device_id,
                    command="tts_speak",
                    request_id=request_id,
                    payload={
                        "session_id": session_id,
                        "clips": clips,
                        "interrupt": False,
                    },
                    source="simulator",
                    topic_prefix=self.topic_prefix,
                )
            )

    def _on_event(self, _topic: str, payload: dict[str, Any]) -> None:
        if payload.get("event") != "clip_done":
            handle_event = getattr(self.agent, "handle_event", None)
            if callable(handle_event):
                event = str(payload.get("event") or "").strip()
                if event == "FALL_SUSPECTED":
                    self._pending_actions.clear()
                for decision in handle_event(event, payload):
                    self._publish_decision(decision, payload)
            return
        if payload.get("status") in {"missing", "error", "interrupted"}:
            self._clear_pending_for_session(str(payload.get("session_id") or "").strip())
            return
        session_id = str(payload.get("session_id") or "").strip()
        request_id = str(payload.get("request_id") or "").strip()
        if request_id:
            pending = self._pending_actions.pop(request_id, None)
        else:
            pending = None
            self._clear_pending_for_session(session_id)
        if pending is None or pending.session_id != session_id:
            return
        if payload.get("status") != "done":
            return
        if pending.action != "start_follow":
            return
        self._printer("[ROBOT] start_follow")
        self.transport.publish(
            build_command_message(
                self.device_id,
                command="start_follow",
                request_id=f"xiaokang-follow-{session_id}",
                payload={
                    "session_id": session_id,
                    "duration_minutes": 3,
                    "skip_start_announcement": True,
                },
                source="simulator",
                topic_prefix=self.topic_prefix,
            )
        )

    def _clear_pending_for_session(self, session_id: str) -> None:
        stale_keys = [
            key
            for key, action in self._pending_actions.items()
            if action.session_id == session_id
        ]
        for key in stale_keys:
            self._pending_actions.pop(key, None)


def build_default_health_provider(root: Path) -> HealthProvider:
    source = os.environ.get("XIAOKANG_HEALTH_SOURCE", "static").strip().lower()
    if source == "mqtt":
        return MqttHealthProvider()
    config_path = Path(
        os.environ.get(
            "XIAOKANG_HEALTH_CONFIG",
            str(root / "config" / "xiaokang_health_demo.json"),
        )
    )
    if config_path.is_file():
        return StaticHealthProvider.from_json_file(config_path)
    return StaticHealthProvider()


def build_default_medication_provider() -> MedicationProvider:
    required = _env_bool("XIAOKANG_MEDICATION_REQUIRED_TODAY", True)
    taken = _env_bool("XIAOKANG_MEDICATION_TAKEN", False)
    return StaticMedicationProvider(
        MedicationContext(required_today=required, taken=taken)
    )


def _normalize_text(text: str) -> str:
    return "".join(ch for ch in str(text or "").strip() if ch not in " ，。！？、,.!?")


def _health_context_from_payload(payload: dict[str, Any]) -> HealthContext:
    return HealthContext(
        heart_rate=int(payload.get("heart_rate", 76)),
        spo2=int(payload.get("spo2", 98)),
        temperature=float(payload.get("temperature", 36.5)),
        blood_pressure_systolic=int(payload.get("blood_pressure_systolic", 125)),
        blood_pressure_diastolic=int(payload.get("blood_pressure_diastolic", 78)),
        status=str(payload.get("status", "good")),
    )


def _is_outing_request(text: str) -> bool:
    return any(term in text for term in ("出去", "走走", "散步", "转转", "陪我出门"))


def _is_stop_follow(text: str) -> bool:
    return any(
        term in text
        for term in ("停一下", "不用跟着", "停止伴随", "别跟着", "不要跟着")
    )


def _optional_rounded_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except Exception:
        return None


def open_meteo_code_to_condition(value: Any) -> WeatherCondition:
    try:
        code = int(value)
    except Exception:
        return WeatherCondition.UNKNOWN
    if code in {0, 1}:
        return WeatherCondition.SUNNY
    if code in {2}:
        return WeatherCondition.CLOUDY
    if code in {3, 45, 48}:
        return WeatherCondition.OVERCAST
    if 71 <= code <= 77 or 85 <= code <= 86:
        return WeatherCondition.SNOW
    if 51 <= code <= 82 or 95 <= code <= 99:
        return WeatherCondition.RAIN
    return WeatherCondition.UNKNOWN


def _weather_value(value: WeatherCondition | str | None) -> WeatherCondition | None:
    if value is None:
        return None
    if isinstance(value, WeatherCondition):
        return value
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    try:
        return WeatherCondition(normalized)
    except ValueError:
        return WeatherCondition.UNKNOWN


def _safe_number_clip(value: int | float) -> str | None:
    try:
        from app.voice.clip_composer import number_clip

        return number_clip(value)
    except Exception:
        return None


def _safe_temperature_value_clip(value: int | float) -> str | None:
    try:
        return temperature_value_clip(value)
    except Exception:
        return None


def _safe_health_temperature_clip(value: int | float) -> str | None:
    try:
        return health_temperature_clip(value)
    except Exception:
        return None


def _safe_spo2_clip(value: int | float) -> str | None:
    try:
        return spo2_clip(value)
    except Exception:
        return None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
