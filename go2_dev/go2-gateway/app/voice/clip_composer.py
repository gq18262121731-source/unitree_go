from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Mapping


NUMBER_MIN = -10
NUMBER_MAX = 130
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
    "unit.bpm": "次每分钟。",
    "weather.today.beijing": "今天北京天气",
    "weather.temp.prefix": "气温",
    "unit.celsius": "摄氏度。",
    "weather.sunny": "晴。",
    "weather.cloudy": "多云。",
    "weather.overcast": "阴。",
    "weather.rain": "有雨。",
    "weather.snow": "有雪。",
    "outing.allow.suffix": "可以出去走走，我陪着您。",
}


class ClipCompositionError(ValueError):
    """Raised when structured voice data cannot be represented by prepared clips."""


@dataclass(frozen=True)
class OutingVoiceFacts:
    decision: str
    heart_rate: int | None = None
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


def weather_clip(value: str) -> str:
    normalized = str(value or "").strip().lower()
    try:
        condition = SUPPORTED_WEATHER[normalized]
    except KeyError as exc:
        raise ClipCompositionError(
            f"unsupported weather condition: {value!r}"
        ) from exc
    return f"weather.{condition}"


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
    city: str = "北京",
) -> list[str]:
    """Build the ordered clip IDs for a normal outing-allowed announcement.

    The caller supplies structured facts from the main system. No language
    model output is interpreted here beyond the typed values.
    """

    clips = ["outing.allow.health_good"]
    if heart_rate is not None:
        clips.extend(["health.hr.prefix", number_clip(heart_rate), "unit.bpm"])
    clips.extend(
        [
            city_clip(city),
            weather_clip(weather),
            "weather.temp.prefix",
            number_clip(temperature_c),
            "unit.celsius",
            "outing.allow.suffix",
        ]
    )
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
        weather=str(payload["weather"]),
        temperature_c=temperature,
        city=str(payload.get("city") or "北京"),
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
    for condition, clip_id in (
        ("sunny", "weather.sunny"),
        ("cloudy", "weather.cloudy"),
        ("overcast", "weather.overcast"),
        ("rain", "weather.rain"),
        ("snow", "weather.snow"),
    ):
        phrases.setdefault(clip_id, condition)
    return phrases


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
