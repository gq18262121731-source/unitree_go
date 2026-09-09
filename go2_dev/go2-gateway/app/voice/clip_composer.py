from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Mapping


NUMBER_MIN = -10
NUMBER_MAX = 130
SPO2_MIN = 80
SPO2_MAX = 100
WEATHER_TEMPERATURE_MIN = -10
WEATHER_TEMPERATURE_MAX = 45
BODY_TEMPERATURE_MIN_TENTHS = 350
BODY_TEMPERATURE_MAX_TENTHS = 420
SUPPORTED_CITIES = {
    "北京": "beijing",
    "beijing": "beijing",
}
SUPPORTED_WEATHER = {
    "sunny": "sunny",
    "晴": "sunny",
    "clear": "sunny",
    "cloudy": "cloudy",
    "多云": "cloudy",
    "overcast": "overcast",
    "阴": "overcast",
    "rain": "rain",
    "雨": "rain",
    "有雨": "rain",
    "snow": "snow",
    "雪": "snow",
    "有雪": "snow",
}

STATIC_DYNAMIC_CLIP_PHRASES = {
    "outing.allow.health_good": "今天您的身体状况良好。",
    "health.hr.prefix": "目前心率是",
    "health.spo2.prefix": "血氧饱和值为百分之",
    "health.temperature.prefix": "体温",
    "health.temperature.36_5": "体温三十六点五摄氏度。",
    "unit.bpm": "次每分钟。",
    "weather.today.beijing": "今天北京天气",
    "weather.temp.prefix": "气温",
    "weather.temperature.prefix": "气温",
    "unit.celsius": "摄氏度。",
    "medication.reminder.before_outing": "您今天还没有确认服药，出门前请按既定安排服药。",
    "weather.sunny": "晴。",
    "weather.cloudy": "多云。",
    "weather.overcast": "阴。",
    "weather.rain": "有雨。",
    "weather.snow": "有雪。",
    "weather.condition.sunny": "今天北京晴，",
    "weather.condition.cloudy": "今天北京多云，",
    "weather.condition.overcast": "今天北京阴，",
    "weather.condition.rain": "今天北京有雨，",
    "weather.condition.snow": "今天北京有雪，",
    "outing.allow.suffix": "可以出去走走，我陪着您。",
    "outing.medication_check": "刚才提醒您的药已经吃过了吗？如果准备好了，也可以告诉我现在出发。",
    "outing.start": "好，咱们出发吧，您慢慢走，我跟着您。",
    "follow.resume.safe": "好，咱们继续走吧。您慢一点，注意脚下，我跟着您。",
    "follow.stop": "好，我先不跟着您了。需要我的时候，再叫我一声小康就好。",
    "sess.wake_ack": "我在，请说。",
    "fall.confirm": "我看到您可能摔倒了。您现在还好吗？",
    "fall.confirm.second": "您能听到我说话吗？如果可以，请回答我。",
    "fall.alert.sound": "请注意。",
    "fall.help.broadcast": (
        "这里有老人可能摔倒了，现在没有回应，已经通知了老人的家属，"
        "请附近的人过来帮忙查看，请附近的人过来帮忙查看。"
    ),
    "fall.recovered": "好的，看到您现在已经恢复了，这次情况我已经记录了。您先休息一下。",
    "fall.normal_activity": "看起来您只是坐下来看看书，没有发生跌倒。",
    "reading.ask_book": "您在看什么书呢？",
}


class ClipCompositionError(ValueError):
    """Raised when structured voice data cannot be represented by prepared clips."""


@dataclass(frozen=True)
class OutingVoiceFacts:
    decision: str
    heart_rate: int | None = None
    spo2: int | None = None
    body_temperature: int | float | None = None
    weather: str | None = None
    temperature_c: int | float | None = None
    city: str = "北京"


def number_clip(value: int | float) -> str:
    """Return the canonical clip ID for a prepared Chinese number recording."""

    if isinstance(value, bool):
        raise ClipCompositionError("numeric clip value must not be boolean")
    numeric = float(value)
    if not math.isfinite(numeric) or not numeric.is_integer():
        raise ClipCompositionError("numeric clip value must be an integer")
    integer = int(numeric)
    if not NUMBER_MIN <= integer <= NUMBER_MAX:
        raise ClipCompositionError(
            f"numeric clip value must be within [{NUMBER_MIN}, {NUMBER_MAX}]"
        )
    return f"num.minus{abs(integer)}" if integer < 0 else f"num.{integer}"


def temperature_value_clip(value: int | float) -> str:
    """Return the clip ID for a naturally spoken temperature value."""

    if isinstance(value, bool):
        raise ClipCompositionError("temperature value must not be boolean")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ClipCompositionError("temperature value must be finite")
    tenths = int(round(numeric * 10))
    if not math.isclose(numeric * 10, tenths, abs_tol=1e-6):
        raise ClipCompositionError("temperature value supports at most one decimal")
    if tenths % 10 == 0:
        integer = tenths // 10
        if not WEATHER_TEMPERATURE_MIN <= integer <= WEATHER_TEMPERATURE_MAX:
            raise ClipCompositionError(
                "integer temperature value must be within "
                f"[{WEATHER_TEMPERATURE_MIN}, {WEATHER_TEMPERATURE_MAX}]"
            )
        return f"temperature.value.{integer}"
    if not BODY_TEMPERATURE_MIN_TENTHS <= tenths <= BODY_TEMPERATURE_MAX_TENTHS:
        raise ClipCompositionError(
            "decimal temperature value must be within [35.0, 42.0]"
        )
    return f"temperature.value.{_temperature_clip_suffix_from_tenths(tenths)}"


def health_temperature_clip(value: int | float) -> str:
    """Return the optional full body-temperature sentence clip ID."""

    temperature_value_clip(value)
    tenths = int(round(float(value) * 10))
    return f"health.temperature.{_temperature_clip_suffix_from_tenths(tenths)}"


def spo2_clip(value: int | float) -> str:
    """Return the optional full blood-oxygen sentence clip ID."""

    if isinstance(value, bool):
        raise ClipCompositionError("spo2 value must not be boolean")
    numeric = float(value)
    if not math.isfinite(numeric) or not numeric.is_integer():
        raise ClipCompositionError("spo2 value must be an integer")
    integer = int(numeric)
    if not SPO2_MIN <= integer <= SPO2_MAX:
        raise ClipCompositionError(
            f"spo2 value must be within [{SPO2_MIN}, {SPO2_MAX}]"
        )
    return f"health.spo2.{integer}"


def weather_clip(value: str) -> str:
    normalized = str(value or "").strip().lower()
    try:
        condition = SUPPORTED_WEATHER[normalized]
    except KeyError as exc:
        raise ClipCompositionError(
            f"unsupported weather condition: {value!r}"
        ) from exc
    return f"weather.{condition}"


def weather_condition_clip(value: str) -> str:
    normalized = str(value or "").strip().lower()
    try:
        condition = SUPPORTED_WEATHER[normalized]
    except KeyError as exc:
        raise ClipCompositionError(
            f"unsupported weather condition: {value!r}"
        ) from exc
    return f"weather.condition.{condition}"


def city_clip(value: str) -> str:
    normalized = str(value or "").strip().lower()
    try:
        city_id = SUPPORTED_CITIES[normalized]
    except KeyError as exc:
        raise ClipCompositionError(
            f"unsupported weather city: {value!r}"
        ) from exc
    return f"weather.today.{city_id}"


def compose_outing_allow_clips(
    *,
    heart_rate: int | None,
    weather: str,
    temperature_c: int | float,
    spo2: int | None = None,
    body_temperature: int | float | None = None,
    city: str = "北京",
    medication_reminder: bool = False,
) -> list[str]:
    """Build the ordered clip IDs for a normal outing-allowed announcement.

    The caller supplies structured facts from the main system. No language
    model output is interpreted here beyond the typed values.
    """

    clips = ["outing.allow.health_good"]
    if heart_rate is not None:
        clips.extend(["health.hr.prefix", number_clip(heart_rate), "unit.bpm"])
    if spo2 is not None:
        clips.append(spo2_clip(spo2))
    if body_temperature is not None:
        clips.extend(["health.temperature.prefix", temperature_value_clip(body_temperature)])
    clips.extend(
        [
            weather_condition_clip(weather),
            "weather.temperature.prefix",
            temperature_value_clip(temperature_c),
        ]
    )
    if medication_reminder:
        clips.append("medication.reminder.before_outing")
    clips.append("outing.allow.suffix")
    return clips


def compose_from_decision(payload: Mapping[str, Any]) -> list[str]:
    """Convert a structured A-machine decision into ordered clip IDs."""

    decision = str(payload.get("decision") or "").strip().lower()
    if decision != "allow":
        raise ClipCompositionError(
            f"only decision='allow' is supported by this composer, got {decision!r}"
        )
    if payload.get("weather") is None:
        raise ClipCompositionError("weather is required for an outing announcement")
    if payload.get("temperature") is None and payload.get("temperature_c") is None:
        raise ClipCompositionError(
            "temperature or temperature_c is required for an outing announcement"
        )
    temperature = payload.get("temperature_c")
    if temperature is None:
        temperature = payload.get("temperature")
    return compose_outing_allow_clips(
        heart_rate=(
            None
            if payload.get("heart_rate") is None
            else int(payload["heart_rate"])
        ),
        spo2=(None if payload.get("spo2") is None else int(payload["spo2"])),
        body_temperature=payload.get("body_temperature", payload.get("body_temperature_c")),
        weather=str(payload["weather"]),
        temperature_c=temperature,
        city=str(payload.get("city") or "北京"),
        medication_reminder=bool(payload.get("medication_reminder", False)),
    )


def clip_id_to_filename(clip_id: str) -> str:
    """Map a logical clip ID to the stable WAV filename used by the builder."""

    normalized = str(clip_id or "").strip()
    if not normalized:
        raise ClipCompositionError("clip_id must not be empty")
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", normalized).strip("_")
    if not safe:
        raise ClipCompositionError(f"clip_id has no usable filename: {clip_id!r}")
    return f"{safe}.wav"


def dynamic_clip_phrases() -> dict[str, str]:
    """Return the complete first-phase phrase set for optional TTS generation."""

    phrases = dict(STATIC_DYNAMIC_CLIP_PHRASES)
    for value in range(0, NUMBER_MAX + 1):
        phrases[number_clip(value)] = _number_zh(value)
    for value in range(NUMBER_MIN, 0):
        phrases[number_clip(value)] = f"负{_number_zh(abs(value))}"
    for value in range(SPO2_MIN, SPO2_MAX + 1):
        phrases[spo2_clip(value)] = f"血氧饱和值为百分之{_number_zh(value)}。"
    for value in range(WEATHER_TEMPERATURE_MIN, WEATHER_TEMPERATURE_MAX + 1):
        phrases[temperature_value_clip(value)] = _temperature_value_zh(value)
    for tenths in range(BODY_TEMPERATURE_MIN_TENTHS, BODY_TEMPERATURE_MAX_TENTHS + 1):
        value = tenths / 10
        if tenths % 10:
            phrases[temperature_value_clip(value)] = _temperature_value_zh(value)
    for condition, clip_id in (
        ("sunny", "weather.sunny"),
        ("cloudy", "weather.cloudy"),
        ("overcast", "weather.overcast"),
        ("rain", "weather.rain"),
        ("snow", "weather.snow"),
    ):
        phrases.setdefault(clip_id, condition)
    return phrases


def _temperature_value_zh(value: int | float) -> str:
    numeric = float(value)
    sign = "零下" if numeric < 0 else ""
    absolute = abs(numeric)
    if absolute.is_integer():
        return f"{sign}{_number_zh(int(absolute))}摄氏度。"
    whole = int(absolute)
    decimal = int(round((absolute - whole) * 10))
    return f"{sign}{_number_zh(whole)}点{_number_zh(decimal)}摄氏度。"


def _temperature_clip_suffix_from_tenths(tenths: int) -> str:
    if tenths % 10 == 0:
        return str(tenths // 10)
    sign = "-" if tenths < 0 else ""
    absolute = abs(tenths)
    whole, decimal = divmod(absolute, 10)
    return f"{sign}{whole}_{decimal}"


def _number_zh(value: int) -> str:
    digits = "零一二三四五六七八九"
    if value < 10:
        return digits[value]
    if value < 100:
        if value < 20:
            return "十" if value == 10 else f"十{digits[value % 10]}"
        tens, ones = divmod(value, 10)
        prefix = f"{digits[tens]}十"
        return prefix if ones == 0 else f"{prefix}{digits[ones]}"
    hundreds, remainder = divmod(value, 100)
    prefix = f"{digits[hundreds]}百"
    if remainder == 0:
        return prefix
    if remainder < 10:
        return f"{prefix}零{digits[remainder]}"
    return f"{prefix}{_number_zh(remainder)}"
