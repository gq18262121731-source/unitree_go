from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import wave
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


VOICE_PRESET_DIR = Path(
    os.environ.get(
        "GO2_VOICE_PRESET_DIR",
        str(ROOT / "data" / "voice" / "presets" / "current"),
    )
).resolve()
DEFAULT_TONE_PATH = (
    ROOT / "data" / "diagnostics" / "audiohub" / "AUDIO_DIAG_TONE_1000HZ.wav"
)


def _scan_markers(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    result: dict[str, Any] = {
        "filename": path.name,
        "path": str(path),
        "byte_size": len(data),
    }
    for marker in (b"RIFF", b"WAVE", b"fmt ", b"data"):
        offsets: list[int] = []
        start = 0
        while True:
            index = data.find(marker, start)
            if index < 0:
                break
            offsets.append(index)
            start = index + 1
        label = marker.decode("ascii").strip().lower()
        result[f"{label}_count"] = len(offsets)
        result[f"{label}_offsets"] = offsets
    try:
        with wave.open(str(path), "rb") as stream:
            rate = stream.getframerate()
            frames = stream.getnframes()
            result.update(
                {
                    "channels": stream.getnchannels(),
                    "sample_rate": rate,
                    "sample_width": stream.getsampwidth(),
                    "frames": frames,
                    "duration_seconds": round(frames / rate, 3) if rate else 0.0,
                }
            )
    except Exception as exc:
        result["wave_error"] = f"{type(exc).__name__}: {exc}"
    return result


def _generate_tone(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 24000
    frames = sample_rate
    amplitude = int(32767 * 0.30)
    fade_frames = int(sample_rate * 0.01)
    pcm = bytearray()
    for index in range(frames):
        gain = 1.0
        if index < fade_frames:
            gain = index / max(1, fade_frames)
        elif index >= frames - fade_frames:
            gain = (frames - index - 1) / max(1, fade_frames)
        sample = int(
            round(
                amplitude
                * gain
                * math.sin(2.0 * math.pi * 1000.0 * index / sample_rate)
            )
        )
        pcm.extend(sample.to_bytes(2, "little", signed=True))
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(bytes(pcm))


def _print_scan(result: dict[str, Any]) -> None:
    print(
        "[AUDIOHUB] WAV_SCAN "
        f"filename={result['filename']} duration={result.get('duration_seconds')} "
        f"byte_size={result['byte_size']} riff_count={result['riff_count']} "
        f"wave_count={result['wave_count']} fmt_count={result['fmt_count']} "
        f"data_count={result['data_count']} riff_offsets={result['riff_offsets']} "
        f"data_offsets={result['data_offsets']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the 1s AudioHub diagnostic tone and scan WAV chunk markers."
    )
    parser.add_argument(
        "--tone-path",
        type=Path,
        default=DEFAULT_TONE_PATH,
        help="path for the generated 1s 1000Hz mono PCM16 diagnostic WAV",
    )
    parser.add_argument(
        "--scan",
        type=Path,
        action="append",
        default=[],
        help="additional WAV path to scan; may be passed multiple times",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print scan output as JSON lines",
    )
    parser.add_argument(
        "--play",
        action="store_true",
        help="connect to Go2 AudioHub, upload/play only the diagnostic tone, then pause",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="required with --play so diagnostics cannot start the robot speaker accidentally",
    )
    parser.add_argument(
        "--robot-ip",
        default=os.environ.get("GO2_ROBOT_IP", ""),
        help="override robot IP; defaults to app settings when omitted",
    )
    parser.add_argument(
        "--aes-key",
        default=os.environ.get("GO2_AES_KEY", ""),
        help="override Go2 AES key",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="AudioHub command timeout in seconds",
    )
    args = parser.parse_args(argv)

    tone_path = args.tone_path.resolve()
    _generate_tone(tone_path)
    scan_paths = [tone_path, VOICE_PRESET_DIR / "WAKE_READY.wav", *args.scan]
    seen: set[Path] = set()
    for scan_path in scan_paths:
        scan_path = scan_path.resolve()
        if scan_path in seen:
            continue
        seen.add(scan_path)
        if not scan_path.is_file():
            print(f"[AUDIOHUB] WAV_SCAN_MISSING path={scan_path}", file=sys.stderr)
            continue
        result = _scan_markers(scan_path)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            _print_scan(result)

    if not args.play:
        return 0
    if not args.execute:
        print("AUDIOHUB_DIAG_REJECTED: pass --execute with --play", file=sys.stderr)
        return 2

    from app.config import load_settings
    from app.webrtc.go2_wireless_runtime import Go2WirelessRuntime

    settings = load_settings()
    robot_ip = args.robot_ip.strip() or settings.robot_ip
    runtime = Go2WirelessRuntime(
        robot_ip,
        aes_key=args.aes_key.strip() or None,
        command_timeout_seconds=max(0.5, args.timeout),
        connect_timeout_seconds=max(5.0, args.timeout + 5.0),
        enable_video=False,
        enable_sport_state=False,
        enable_uwb=False,
        enable_multiple_state=False,
        enable_low_state=False,
        enable_audio=True,
    )
    runtime.start()
    try:
        play_path = tone_path.with_name(
            f"AUDIO_DIAG_TONE_1000HZ_{int(time.time() * 1000)}.wav"
        )
        _generate_tone(play_path)
        print("[AUDIOHUB] DIAG_TONE_PLAY_BEGIN api1002_expected=1")
        runtime.play_audio_file(play_path, timeout_seconds=max(3.0, args.timeout))
        time.sleep(1.3)
        runtime.stop_audio_playback(reason="audiohub_diag_tone_complete", timeout_seconds=3.0)
        print(
            "[AUDIOHUB] DIAG_TONE_PLAY_DONE "
            f"api1002_expected=1 watchdog_pause=sent file={play_path.name}"
        )
    finally:
        runtime.close(send_stop=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
