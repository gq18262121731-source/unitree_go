from __future__ import annotations

import pytest

from app.voice.clip_composer import (
    ClipCompositionError,
    clip_id_to_filename,
    compose_from_decision,
    compose_outing_allow_clips,
    dynamic_clip_phrases,
    health_temperature_clip,
    number_clip,
    spo2_clip,
    temperature_value_clip,
)


def test_outing_clips_are_ordered_and_use_complete_number_recordings() -> None:
    assert compose_outing_allow_clips(
        heart_rate=76,
        spo2=98,
        body_temperature=36.5,
        weather="sunny",
        temperature_c=24,
    ) == [
        "outing.allow.health_good",
        "health.hr.prefix",
        "num.76",
        "unit.bpm",
        "health.spo2.98",
        "health.temperature.prefix",
        "temperature.value.36_5",
        "weather.condition.sunny",
        "weather.temperature.prefix",
        "temperature.value.24",
        "outing.allow.suffix",
    ]


def test_outing_clips_can_include_medication_reminder() -> None:
    assert compose_outing_allow_clips(
        heart_rate=76,
        spo2=98,
        body_temperature=36.5,
        weather="sunny",
        temperature_c=24,
        medication_reminder=True,
    )[-2:] == [
        "medication.reminder.before_outing",
        "outing.allow.suffix",
    ]


def test_decision_payload_is_data_driven_and_does_not_accept_clip_ids() -> None:
    assert compose_from_decision(
        {
            "decision": "allow",
            "heart_rate": 98,
            "weather": "晴",
            "temperature": 25,
            "city": "北京",
        }
    )[2] == "num.98"


def test_number_clip_supports_negative_temperatures_and_rejects_out_of_range() -> None:
    assert number_clip(-10) == "num.minus10"
    assert number_clip(130) == "num.130"
    with pytest.raises(ClipCompositionError):
        number_clip(131)
    with pytest.raises(ClipCompositionError):
        number_clip(24.5)


def test_temperature_value_clip_uses_natural_unit_recordings() -> None:
    assert temperature_value_clip(17) == "temperature.value.17"
    assert temperature_value_clip(-3) == "temperature.value.-3"
    assert temperature_value_clip(36.5) == "temperature.value.36_5"
    assert health_temperature_clip(36.5) == "health.temperature.36_5"
    with pytest.raises(ClipCompositionError):
        temperature_value_clip(36.55)


def test_spo2_clip_uses_complete_sentence_recordings() -> None:
    assert spo2_clip(98) == "health.spo2.98"
    with pytest.raises(ClipCompositionError):
        spo2_clip(79)
    with pytest.raises(ClipCompositionError):
        spo2_clip(98.5)


def test_dynamic_phrase_set_contains_first_phase_numeric_range() -> None:
    phrases = dynamic_clip_phrases()
    assert phrases["num.24"] == "二十四"
    assert phrases["num.76"] == "七十六"
    assert phrases["num.minus10"] == "负十"
    assert phrases["temperature.value.17"] == "十七摄氏度。"
    assert phrases["temperature.value.-3"] == "零下三摄氏度。"
    assert phrases["temperature.value.36_5"] == "三十六点五摄氏度。"
    assert phrases["health.temperature.36_5"] == "体温三十六点五摄氏度。"
    assert phrases["health.spo2.98"] == "血氧饱和值为百分之九十八。"
    assert len([key for key in phrases if key.startswith("num.")]) == 141


def test_clip_id_to_filename_is_stable() -> None:
    assert clip_id_to_filename("num.76") == "num_76.wav"
    assert clip_id_to_filename("weather.today.beijing") == "weather_today_beijing.wav"
