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

    def fake_synthesize(
        *,
        health_url: str,
        text: str,
        voice: str,
        speed: float,
        model: str | None = None,
        instruction: str | None = None,
    ):
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


def test_dynamic_only_builds_one_clip_without_rebuilding_all(
    tmp_path, monkeypatch
) -> None:
    from tools import build_voice_presets

    calls: list[tuple[str, str, float, str | None, str | None]] = []

    def fake_synthesize(
        *,
        health_url: str,
        text: str,
        voice: str,
        speed: float,
        model: str | None = None,
        instruction: str | None = None,
    ):
        calls.append((text, voice, speed, model, instruction))
        return b"RIFF" + text.encode("utf-8"), {"provider": "fake"}

    monkeypatch.setattr(build_voice_presets, "_synthesize", fake_synthesize)
    monkeypatch.setattr(
        "sys.argv",
        [
            "build_voice_presets.py",
            "--dynamic-only",
            "fall.help.broadcast",
            "--voice",
            "Cherry",
            "--speed",
            "1.08",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert build_voice_presets.main() == 0

    assert calls == [
        (
            "这里有老人可能摔倒了，现在没有回应，已经通知了老人的家属，"
            "请附近的人过来帮忙查看，请附近的人过来帮忙查看。",
            "Cherry",
            1.08,
            None,
            None,
        )
    ]
    assert (tmp_path / "fall_help_broadcast.wav").is_file()
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert [Path(item["path"]).name for item in manifest["files"]] == [
        "fall_help_broadcast.wav"
    ]


def test_dynamic_only_accepts_text_override(tmp_path, monkeypatch) -> None:
    from tools import build_voice_presets

    calls: list[tuple[str, str, float, str | None, str | None]] = []

    def fake_synthesize(
        *,
        health_url: str,
        text: str,
        voice: str,
        speed: float,
        model: str | None = None,
        instruction: str | None = None,
    ):
        calls.append((text, voice, speed, model, instruction))
        return b"RIFF" + text.encode("utf-8"), {"provider": "fake"}

    monkeypatch.setattr(build_voice_presets, "_synthesize", fake_synthesize)
    monkeypatch.setattr(
        "sys.argv",
        [
            "build_voice_presets.py",
            "--dynamic-only",
            "fall.help.broadcast",
            "--text-override",
            "请注意！这里有老人可能摔倒了！",
            "--voice",
            "Bellona",
            "--speed",
            "0.92",
            "--model",
            "qwen3-tts-instruct-flash",
            "--instruction",
            "紧急广播语气。",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert build_voice_presets.main() == 0

    assert calls == [
        (
            "请注意！这里有老人可能摔倒了！",
            "Bellona",
            0.92,
            "qwen3-tts-instruct-flash",
            "紧急广播语气。",
        )
    ]
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"][0]["text"] == "请注意！这里有老人可能摔倒了！"
