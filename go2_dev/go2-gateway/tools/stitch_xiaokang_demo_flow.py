from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.voice.clip_composer import clip_id_to_filename
from app.voice.xiaokang_agent import ClipAssembler, XiaokangDecision


def _wav_info(path: Path) -> dict[str, int]:
    with wave.open(str(path), "rb") as stream:
        return {
            "channels": stream.getnchannels(),
            "sample_rate": stream.getframerate(),
            "sample_width": stream.getsampwidth(),
            "frames": stream.getnframes(),
        }


def _clip_path(preset_dir: Path, clip_id: str) -> Path:
    aliases = {
        "sess.wake_ack": "WAKE_READY.wav",
    }
    filename = aliases.get(clip_id, clip_id_to_filename(clip_id))
    return preset_dir / filename


def _append_clip(
    frames: bytearray,
    path: Path,
    *,
    expected: dict[str, int],
) -> int:
    with wave.open(str(path), "rb") as stream:
        observed = {
            "channels": stream.getnchannels(),
            "sample_rate": stream.getframerate(),
            "sample_width": stream.getsampwidth(),
        }
        required = {
            "channels": expected["channels"],
            "sample_rate": expected["sample_rate"],
            "sample_width": expected["sample_width"],
        }
        if observed != required:
            raise ValueError(f"clip format mismatch for {path}: {observed} != {required}")
        data = stream.readframes(stream.getnframes())
        frames.extend(data)
        frame_size = stream.getnchannels() * stream.getsampwidth()
        return len(data) // frame_size


def _append_silence(frames: bytearray, *, info: dict[str, int], seconds: float) -> int:
    count = int(round(max(0.0, seconds) * info["sample_rate"]))
    frame_size = info["channels"] * info["sample_width"]
    frames.extend(b"\0" * count * frame_size)
    return count


def _play_wav(path: Path) -> dict[str, object]:
    try:
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME)
        return {"attempted": True, "ok": True, "error": ""}
    except Exception as exc:
        return {"attempted": True, "ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _outing_assessment_clips(
    *,
    heart_rate: int,
    spo2: int,
    body_temperature: float,
    weather: str,
    temperature: int,
    medication_reminder: bool,
    preset_dir: Path,
) -> list[str]:
    assembler = ClipAssembler(
        is_clip_available=lambda clip_id: _clip_path(preset_dir, clip_id).is_file(),
        printer=lambda _line: None,
    )
    return assembler.outing_allow(
        XiaokangDecision(
            intent="outing_request",
            allowed=True,
            health_status="good",
            heart_rate=heart_rate,
            spo2=spo2,
            body_temperature=body_temperature,
            weather=weather,
            temperature=temperature,
            medication_reminder=medication_reminder,
        )
    )


def _demo_segments(preset_dir: Path) -> list[dict[str, object]]:
    return [
        {
            "name": "scene2_first_outing_assessment_medication_reminder",
            "clips": _outing_assessment_clips(
                heart_rate=78,
                spo2=98,
                body_temperature=36.6,
                weather="overcast",
                temperature=17,
                medication_reminder=True,
                preset_dir=preset_dir,
            ),
            "pause_after_seconds": 1.2,
        },
        {
            "name": "scene2_second_outing_reassessment_medication_question",
            "clips": _outing_assessment_clips(
                heart_rate=77,
                spo2=98,
                body_temperature=36.5,
                weather="overcast",
                temperature=17,
                medication_reminder=False,
                preset_dir=preset_dir,
            )
            + ["outing.medication_check"],
            "pause_after_seconds": 1.2,
        },
        {
            "name": "scene2_start_follow_after_confirmation",
            "clips": ["outing.start"],
            "pause_after_seconds": 0.8,
        },
        {
            "name": "scene2_stop_follow",
            "clips": ["follow.stop"],
            "pause_after_seconds": 1.2,
        },
        {
            "name": "scene3_resume_follow",
            "clips": ["follow.resume.safe"],
            "pause_after_seconds": 1.2,
        },
        {
            "name": "scene3_fall_confirm_first",
            "clips": ["fall.confirm"],
            "pause_after_seconds": 1.2,
        },
        {
            "name": "scene3_fall_confirm_second",
            "clips": ["fall.confirm.second"],
            "pause_after_seconds": 1.2,
        },
        {
            "name": "scene3_help_broadcast",
            "clips": ["fall.alert.sound", "fall.help.broadcast"],
            "pause_after_seconds": 1.2,
            "pause_between_clips_seconds": 0.25,
        },
        {
            "name": "scene3_fall_recovered",
            "clips": ["fall.recovered"],
            "pause_after_seconds": 1.2,
        },
        {
            "name": "scene3_reading_normal_activity",
            "clips": ["fall.normal_activity", "reading.ask_book"],
            "pause_after_seconds": 1.2,
        },
        {
            "name": "scene3_resume_after_reading",
            "clips": ["follow.resume.safe"],
            "pause_after_seconds": 1.2,
        },
        {
            "name": "scene3_final_stop",
            "clips": ["follow.stop"],
            "pause_after_seconds": 0.0,
        },
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Stitch Xiaokang scene 2/3 demo voice clips.")
    parser.add_argument(
        "--preset-dir",
        type=Path,
        default=ROOT / "data" / "voice" / "presets" / "current",
    )
    parser.add_argument(
        "--output-wav",
        type=Path,
        default=ROOT / "artifacts" / "voice_smoke" / "xiaokang_scene2_3_full_flow.wav",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "artifacts" / "voice_smoke" / "xiaokang_scene2_3_full_flow_report.json",
    )
    parser.add_argument("--play", action="store_true")
    args = parser.parse_args()

    segments = _demo_segments(args.preset_dir)
    all_clips = [clip for segment in segments for clip in segment["clips"]]
    missing = [clip for clip in all_clips if not _clip_path(args.preset_dir, clip).is_file()]
    if missing:
        print(json.dumps({"status": "missing", "missing_clips": missing}, ensure_ascii=False, indent=2))
        return 1

    first = _clip_path(args.preset_dir, all_clips[0])
    info = _wav_info(first)
    frames = bytearray()
    total_frames = 0
    rendered_segments: list[dict[str, object]] = []
    for segment in segments:
        segment_frames = 0
        clips = list(segment["clips"])
        for index, clip in enumerate(clips):
            segment_frames += _append_clip(
                frames,
                _clip_path(args.preset_dir, clip),
                expected=info,
            )
            if index + 1 < len(clips):
                segment_frames += _append_silence(
                    frames,
                    info=info,
                    seconds=float(segment.get("pause_between_clips_seconds", 0.0)),
                )
        segment_frames += _append_silence(
            frames,
            info=info,
            seconds=float(segment.get("pause_after_seconds", 0.0)),
        )
        total_frames += segment_frames
        rendered_segments.append(
            {
                "name": segment["name"],
                "clips": clips,
                "duration_seconds": round(segment_frames / info["sample_rate"], 3),
            }
        )

    args.output_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(args.output_wav), "wb") as stream:
        stream.setnchannels(info["channels"])
        stream.setsampwidth(info["sample_width"])
        stream.setframerate(info["sample_rate"])
        stream.writeframes(bytes(frames))

    playback: dict[str, object] = {"attempted": False, "ok": False, "error": ""}
    if args.play:
        playback = _play_wav(args.output_wav)

    report = {
        "status": "done",
        "output_wav": str(args.output_wav.resolve()),
        "bytes": args.output_wav.stat().st_size,
        "duration_seconds": round(total_frames / info["sample_rate"], 3),
        "format": {
            "channels": info["channels"],
            "sample_rate": info["sample_rate"],
            "bits": info["sample_width"] * 8,
        },
        "segments": rendered_segments,
        "playback": playback,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
