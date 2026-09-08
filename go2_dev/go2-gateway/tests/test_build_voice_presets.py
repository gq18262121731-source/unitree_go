from __future__ import annotations

import json
from pathlib import Path


def test_repair_wav_sizes_fixes_dashscope_streaming_placeholder() -> None:
    from tools.build_voice_presets import _repair_wav_sizes

    pcm = (1000).to_bytes(2, "little", signed=True) * 1600
    audio = (
        b"RIFF"
        + (0x7FFFFFBF).to_bytes(4, "little")
        + b"WAVEfmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + (16000).to_bytes(4, "little")
        + (32000).to_bytes(4, "little")
        + (2).to_bytes(2, "little")
        + (16).to_bytes(2, "little")
        + b"data"
        + (0x7FFFFF9B).to_bytes(4, "little")
        + pcm
    )

    repaired = _repair_wav_sizes(audio)

    assert int.from_bytes(repaired[4:8], "little") == len(repaired) - 8
    assert int.from_bytes(repaired[40:44], "little") == len(pcm)


def test_dynamic_clip_build_merges_existing_manifest(tmp_path, monkeypatch) -> None:
    from tools import build_voice_presets

    existing_path = tmp_path / "START_COMPANION.wav"
    existing_path.write_bytes(b"RIFF" + b"\0" * 40)
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "provider": "old",
                "model": "old",
                "voice": "Cherry",
                "speed": 1.0,
                "files": [
                    {
                        "path": str(existing_path),
                        "voice": "Cherry",
                        "text": "伴随模式已启动。",
                        "bytes": existing_path.stat().st_size,
                        "provider": "old",
                    },
                    {
                        "path": str(tmp_path / "num_76.wav"),
                        "voice": "Cherry",
                        "text": "旧七十六",
                        "bytes": 1,
                        "provider": "old",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_synthesize(*, health_url: str, text: str, voice: str, speed: float):
        return b"RIFF" + text.encode("utf-8"), {"provider": "fake"}

    monkeypatch.setattr(build_voice_presets, "_synthesize", fake_synthesize)
    monkeypatch.setattr(
        "sys.argv",
        [
            "build_voice_presets.py",
            "--dynamic-clips",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert build_voice_presets.main() == 0

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    names = [Path(item["path"]).name for item in manifest["files"]]
    assert "START_COMPANION.wav" in names
    assert names.count("num_76.wav") == 1
    num_76 = next(item for item in manifest["files"] if Path(item["path"]).name == "num_76.wav")
    assert num_76["text"] == "七十六"
