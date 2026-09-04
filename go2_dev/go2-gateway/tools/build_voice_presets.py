from __future__ import annotations

import argparse
import base64
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path


AUDITION_TEXT = "好的，我陪您出去走走。"
BUILTIN_VOICES = ("Cherry", "Serena", "Ethan", "Chelsie")
PRESET_PHRASES = {
    "WAKE_READY": "我在，请说。",
    "START_ACK": "好的，李四。",
    "START_DYNAMIC_FALLBACK": "天气信息已经获取，我来陪您出去走走。",
    "START_COMPANION": "伴随模式已启动。",
    "STOP_COMPANION": "伴随已停止。",
    "RESUME_COMPANION": "正在恢复伴随。",
    "REQUEST_HELP": "已收到您的求助。",
    "CALL_FAMILY": "已为您联系家人。",
    "I_AM_OK": "好的，我会继续在这里陪着您。",
    "START_REJECTED": "暂时无法启动伴随，请检查设备状态。",
    "RESUME_REJECTED": "当前状态不允许恢复伴随。",
    "CONTROL_REJECTED": "当前状态不允许执行这个操作。",
    "VOICE_CHECK": "检测到异常，请问您现在是否需要帮助？",
    "VOICE_RECHECK": "我再确认一次，您现在是否需要帮助？",
    "NO_RESPONSE_ESCALATED": (
        "暂未收到您的有效回应，已为您联系家属和社区工作人员，"
        "请保持原位等待帮助。"
    ),
    "WALK_FOLLOW": (
        "您当前心率为76次每分钟，血氧为98%，状态正常。"
        "伴随模式已启动，请注意出行安全。"
    ),
}


def _post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))


def _decode_audio(payload: dict[str, object]) -> bytes:
    encoded = str(payload.get("audio_b64") or "").strip()
    audio_url = str(payload.get("audio_url") or "").strip()
    if encoded:
        return base64.b64decode(encoded)
    if audio_url.startswith("data:") and "," in audio_url:
        return base64.b64decode(audio_url.split(",", 1)[1])
    if audio_url.startswith(("http://", "https://")):
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                with urllib.request.urlopen(audio_url, timeout=90) as response:
                    return response.read()
            except (urllib.error.HTTPError, urllib.error.URLError) as exc:
                last_error = exc
                if attempt < 3:
                    time.sleep(float(attempt))
        raise RuntimeError(
            f"TTS audio download failed after 3 attempts: {last_error}"
        ) from last_error
    raise RuntimeError(str(payload.get("error") or "TTS returned no audio"))


def _synthesize(
    *, health_url: str, text: str, voice: str, speed: float
) -> tuple[bytes, dict[str, object]]:
    payload = _post_json(
        f"{health_url.rstrip('/')}/api/v1/voice/tts",
        {"text": text, "voice": voice, "speed": speed, "fmt": "wav"},
    )
    if payload.get("ok") is not True:
        raise RuntimeError(str(payload.get("error") or "TTS request failed"))
    audio = _decode_audio(payload)
    if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise RuntimeError("TTS response is not a valid WAV file")
    return audio, payload


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_") or "voice"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build local Qwen TTS WAV files for the Go2 speaker."
    )
    parser.add_argument("--health-url", default="http://127.0.0.1:8765")
    parser.add_argument("--voice", default="Serena", choices=BUILTIN_VOICES)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--audition", action="store_true")
    parser.add_argument(
        "--only",
        choices=tuple(PRESET_PHRASES),
        help="Build only one named preset instead of the full preset set.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/voice/presets"),
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    items = (
        [(voice, AUDITION_TEXT, f"audition_{voice}.wav") for voice in BUILTIN_VOICES]
        if args.audition
        else [
            (
                args.voice,
                PRESET_PHRASES[args.only],
                f"{args.only}.wav",
            )
        ]
        if args.only
        else [
            (args.voice, text, f"{intent}.wav")
            for intent, text in PRESET_PHRASES.items()
        ]
    )
    manifest: dict[str, object] = {
        "provider": "health_new/api/v1/voice/tts",
        "model": "qwen3-tts-flash",
        "voice": "audition" if args.audition else args.voice,
        "speed": args.speed,
        "files": [],
    }
    manifest_path = args.output_dir / "manifest.json"
    if args.only and manifest_path.is_file():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            existing = None
        if isinstance(existing, dict) and isinstance(existing.get("files"), list):
            manifest = existing
            manifest["provider"] = "health_new/api/v1/voice/tts"
            manifest["model"] = "qwen3-tts-flash"
            manifest["voice"] = args.voice
            manifest["speed"] = args.speed
            target_name = f"{args.only}.wav"
            manifest["files"] = [
                item
                for item in manifest["files"]
                if not (
                    isinstance(item, dict)
                    and Path(str(item.get("path") or "")).name == target_name
                )
            ]
    for voice, text, filename in items:
        audio, response = _synthesize(
            health_url=args.health_url,
            text=text,
            voice=voice,
            speed=args.speed,
        )
        target = args.output_dir / _safe_name(filename.removesuffix(".wav"))
        target = target.with_suffix(".wav")
        target.write_bytes(audio)
        manifest["files"].append(
            {
                "path": str(target.resolve()),
                "voice": voice,
                "text": text,
                "bytes": len(audio),
                "provider": response.get("provider"),
            }
        )
        print(f"VOICE_PRESET_WRITTEN voice={voice} path={target} bytes={len(audio)}")

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
