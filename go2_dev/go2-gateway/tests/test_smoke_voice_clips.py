from __future__ import annotations

import json
import wave

from app.voice.clip_composer import clip_id_to_filename, dynamic_clip_phrases


def _write_wav(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        stream.writeframes((1000).to_bytes(2, "little", signed=True) * 120)


def test_smoke_voice_clips_builds_weather_sentence_report(tmp_path, monkeypatch) -> None:
    from tools import smoke_voice_clips

    preset_dir = tmp_path / "presets"
    for clip_id in dynamic_clip_phrases():
        _write_wav(preset_dir / clip_id_to_filename(clip_id))
    weather_json = tmp_path / "weather.json"
    weather_json.write_text(
        json.dumps(
            {
                "current": {
                    "temperature_2m": 24.2,
                    "apparent_temperature": 23.8,
                    "precipitation": 0,
                    "weather_code": 0,
                }
            }
        ),
        encoding="utf-8",
    )
    output_wav = tmp_path / "out" / "sentence.wav"
    report = tmp_path / "out" / "report.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "smoke_voice_clips.py",
            "--preset-dir",
            str(preset_dir),
            "--weather-json",
            str(weather_json),
            "--output-wav",
            str(output_wav),
            "--report",
            str(report),
            "--medication-reminder",
        ],
    )

    assert smoke_voice_clips.main() == 0

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["defined_dynamic_clips"] == len(dynamic_clip_phrases())
    assert payload["missing_dynamic_clips"] == []
    assert payload["weather_api"]["condition"] == "sunny"
    assert payload["weather_api"]["temperature"] == 24
    assert "health.spo2.98" in payload["clips"]
    assert "health.temperature.prefix" in payload["clips"]
    assert "temperature.value.36_5" in payload["clips"]
    assert "weather.condition.sunny" in payload["clips"]
    assert "weather.temperature.prefix" in payload["clips"]
    assert "temperature.value.24" in payload["clips"]
    assert "unit.celsius" not in payload["clips"]
    assert payload["missing_for_sentence"] == []
    assert payload["output_wav"]["format"] == {
        "channels": 1,
        "sample_rate": 24000,
        "bits": 16,
    }
    assert output_wav.is_file()
