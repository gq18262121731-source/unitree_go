from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.voice.clip_composer import (
    clip_id_to_filename,
    compose_outing_allow_clips,
    dynamic_clip_phrases,
)
from app.voice.xiaokang_agent import (
    OpenMeteoWeatherProvider,
    WeatherCondition,
    WeatherContext,
    open_meteo_code_to_condition,
)


def _wav_info(path: Path) -> dict[str, int]:
    with wave.open(str(path), "rb") as stream:
        return {
            "channels": stream.getnchannels(),
            "sample_rate": stream.getframerate(),
            "sample_width": stream.getsampwidth(),
            "frames": stream.getnframes(),
        }


def _format_key(info: dict[str, int]) -> str:
    return (
        f"{info['channels']}ch/"
        f"{info['sample_rate']}Hz/"
        f"{info['sample_width'] * 8}bit"
    )


def _clip_path(preset_dir: Path, clip_id: str) -> Path:
    return preset_dir / clip_id_to_filename(clip_id)


def _read_weather_json(path: Path) -> WeatherContext:
    payload = json.loads(path.read_text(encoding="utf-8"))
    current = dict(payload.get("current") or payload.get("current_weather") or payload)
    temperature_value = current.get("temperature_2m", current.get("temperature"))
    apparent_value = current.get("apparent_temperature")
    temperature = None if temperature_value is None else int(round(float(temperature_value)))
    feels_like = None if apparent_value is None else int(round(float(apparent_value)))
    precipitation = float(current.get("precipitation") or 0.0) > 0.0
    condition = open_meteo_code_to_condition(current.get("weather_code"))
    if precipitation and condition is WeatherCondition.SUNNY:
        condition = WeatherCondition.RAIN
    return WeatherContext(
        city=str(payload.get("city") or "北京"),
        condition=condition,
        temperature=temperature,
        feels_like=feels_like,
        precipitation=precipitation,
    )


def _fetch_weather(args: argparse.Namespace) -> WeatherContext:
    if args.weather_json is not None:
        return _read_weather_json(args.weather_json)
    return OpenMeteoWeatherProvider(
        city=args.city,
        latitude=args.latitude,
        longitude=args.longitude,
        api_url=args.weather_api_url,
        timeout_seconds=args.weather_timeout,
    ).get_weather()


def _stitch_wavs(clip_paths: list[Path], output_path: Path) -> dict[str, Any]:
    if not clip_paths:
        raise ValueError("no clips to stitch")
    first_info = _wav_info(clip_paths[0])
    frames = bytearray()
    total_frames = 0
    for path in clip_paths:
        with wave.open(str(path), "rb") as stream:
            info = {
                "channels": stream.getnchannels(),
                "sample_rate": stream.getframerate(),
                "sample_width": stream.getsampwidth(),
            }
            expected = {
                "channels": first_info["channels"],
                "sample_rate": first_info["sample_rate"],
                "sample_width": first_info["sample_width"],
            }
            if info != expected:
                raise ValueError(
                    f"clip format mismatch for {path}: got {info}, expected {expected}"
                )
            count = stream.getnframes()
            frames.extend(stream.readframes(count))
            total_frames += count
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as stream:
        stream.setnchannels(first_info["channels"])
        stream.setsampwidth(first_info["sample_width"])
        stream.setframerate(first_info["sample_rate"])
        stream.writeframes(bytes(frames))
    return {
        "path": str(output_path.resolve()),
        "bytes": output_path.stat().st_size,
        "duration_seconds": round(total_frames / first_info["sample_rate"], 3),
        "format": {
            "channels": first_info["channels"],
            "sample_rate": first_info["sample_rate"],
            "bits": first_info["sample_width"] * 8,
        },
    }


def _play_wav(path: Path) -> dict[str, str | bool]:
    try:
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME)
        return {"attempted": True, "ok": True, "error": ""}
    except Exception as exc:
        return {
            "attempted": True,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _weather_to_report(weather: WeatherContext) -> dict[str, Any]:
    condition = (
        weather.condition.value
        if isinstance(weather.condition, WeatherCondition)
        else str(weather.condition)
    )
    return {
        "city": weather.city,
        "condition": condition,
        "temperature": weather.temperature,
        "feels_like": weather.feels_like,
        "precipitation": weather.precipitation,
        "error": weather.error,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Check prepared Xiaokang voice clips, fetch Beijing weather, and "
            "write a stitched demo WAV."
        )
    )
    parser.add_argument(
        "--preset-dir",
        type=Path,
        default=ROOT / "data" / "voice" / "presets" / "current",
    )
    parser.add_argument(
        "--output-wav",
        type=Path,
        default=ROOT / "artifacts" / "voice_smoke" / "beijing_outing_live_smoke.wav",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "artifacts" / "voice_smoke" / "beijing_outing_live_report.json",
    )
    parser.add_argument("--city", default="北京")
    parser.add_argument("--latitude", type=float, default=39.9042)
    parser.add_argument("--longitude", type=float, default=116.4074)
    parser.add_argument(
        "--weather-api-url",
        default="https://api.open-meteo.com/v1/forecast",
    )
    parser.add_argument("--weather-timeout", type=float, default=5.0)
    parser.add_argument("--weather-json", type=Path)
    parser.add_argument("--heart-rate", type=int, default=76)
    parser.add_argument("--spo2", type=int, default=98)
    parser.add_argument("--body-temperature", type=float, default=36.5)
    parser.add_argument("--medication-reminder", action="store_true")
    parser.add_argument("--play", action="store_true")
    args = parser.parse_args()

    phrases = dynamic_clip_phrases()
    existing_wavs = sorted(args.preset_dir.glob("*.wav"))
    existing_by_name = {path.name.lower(): path for path in existing_wavs}
    missing_dynamic = [
        clip_id
        for clip_id in sorted(phrases)
        if clip_id_to_filename(clip_id).lower() not in existing_by_name
    ]
    wav_formats = Counter(_format_key(_wav_info(path)) for path in existing_wavs)

    weather = _fetch_weather(args)
    if weather.error:
        report = {
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "preset_dir": str(args.preset_dir.resolve()),
            "defined_dynamic_clips": len(phrases),
            "existing_wav_files": len(existing_wavs),
            "missing_dynamic_clips": missing_dynamic,
            "wav_formats": dict(sorted(wav_formats.items())),
            "weather_api": _weather_to_report(weather),
            "clips": [],
            "missing_for_sentence": [],
            "output_wav": None,
            "playback": {"attempted": False, "ok": False, "error": "weather failed"},
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    if weather.temperature is None:
        raise RuntimeError("weather API returned no current temperature")
    clips = compose_outing_allow_clips(
        heart_rate=args.heart_rate,
        spo2=args.spo2,
        body_temperature=args.body_temperature,
        weather=str(weather.condition.value if isinstance(weather.condition, WeatherCondition) else weather.condition),
        temperature_c=weather.temperature,
        city=args.city,
        medication_reminder=args.medication_reminder,
    )
    missing_for_sentence = [
        clip for clip in clips if not _clip_path(args.preset_dir, clip).is_file()
    ]
    output = None
    playback: dict[str, Any] = {"attempted": False, "ok": False, "error": ""}
    if not missing_for_sentence:
        output = _stitch_wavs(
            [_clip_path(args.preset_dir, clip) for clip in clips],
            args.output_wav,
        )
        if args.play:
            playback = _play_wav(args.output_wav)

    report = {
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "preset_dir": str(args.preset_dir.resolve()),
        "defined_dynamic_clips": len(phrases),
        "existing_wav_files": len(existing_wavs),
        "missing_dynamic_clips": missing_dynamic,
        "wav_formats": dict(sorted(wav_formats.items())),
        "weather_api": _weather_to_report(weather),
        "clips": clips,
        "missing_for_sentence": missing_for_sentence,
        "output_wav": output,
        "playback": playback,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if missing_dynamic or missing_for_sentence else 0


if __name__ == "__main__":
    raise SystemExit(main())
