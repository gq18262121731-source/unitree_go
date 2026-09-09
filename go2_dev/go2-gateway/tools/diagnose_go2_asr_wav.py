from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.voice.local_voice import FunASRLocalASRService


def _wav_info(path: Path) -> dict[str, Any]:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        frames = wav.getnframes()
        sample_width = wav.getsampwidth()
    return {
        "path": str(path),
        "sample_rate": sample_rate,
        "channels": channels,
        "sample_width_bytes": sample_width,
        "frames": frames,
        "duration_seconds": round(frames / sample_rate, 3) if sample_rate else 0.0,
    }


def _audio_stats(path: Path) -> dict[str, Any]:
    try:
        import numpy as np
        import soundfile as sf
    except Exception:
        return {}
    audio, _sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    if audio.size == 0:
        return {"peak": 0.0, "rms": 0.0}
    mono = np.mean(audio, axis=1)
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    rms = float(np.sqrt(np.mean(mono**2))) if mono.size else 0.0
    return {"peak": round(peak, 6), "rms": round(rms, 6)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run offline FunASR diagnostics against Go2 debug WAV files."
    )
    parser.add_argument("wav", nargs="+", type=Path, help="WAV file(s) to inspect")
    parser.add_argument("--model", default="paraformer-zh-streaming")
    parser.add_argument("--hub", default="ms")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--ncpu", type=int, default=4)
    parser.add_argument("--json", action="store_true", help="print JSON lines")
    args = parser.parse_args(argv)

    service = FunASRLocalASRService(
        model=args.model,
        hub=args.hub,
        device=args.device,
        ncpu=args.ncpu,
    )
    exit_code = 0
    for wav_path in args.wav:
        wav_path = wav_path.resolve()
        if not wav_path.exists():
            print(f"missing: {wav_path}", file=sys.stderr)
            exit_code = 2
            continue
        result = {
            **_wav_info(wav_path),
            **_audio_stats(wav_path),
            "text": service.transcribe(wav_path),
        }
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(f"WAV: {result['path']}")
            print(
                "  format: "
                f"{result['sample_rate']} Hz, channels={result['channels']}, "
                f"sample_width={result['sample_width_bytes']} bytes, "
                f"duration={result['duration_seconds']}s"
            )
            if "peak" in result:
                print(f"  level : peak={result['peak']} rms={result['rms']}")
            print(f"  ASR   : {result['text']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
