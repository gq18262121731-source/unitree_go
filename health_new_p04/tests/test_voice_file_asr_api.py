from __future__ import annotations

import asyncio
import io

from starlette.datastructures import Headers, UploadFile

from backend.api import voice_api


class FakeFileASR:
    model = "qwen3-asr-flash"

    def __init__(self) -> None:
        self.calls = []

    def transcribe(self, audio_bytes: bytes, *, fmt: str):
        self.calls.append((audio_bytes, fmt))
        return {
            "ok": True,
            "text": "小康，陪我走走",
            "provider": "dashscope/qwen3-asr-flash",
        }


def test_voice_asr_upload_uses_file_model_not_realtime(monkeypatch) -> None:
    service = FakeFileASR()
    monkeypatch.setattr(voice_api, "_file_asr_service", service)
    upload = UploadFile(
        file=io.BytesIO(b"RIFF" + b"x" * 200),
        filename="go2.wav",
        headers=Headers({"content-type": "audio/wav"}),
    )

    result = asyncio.run(voice_api.asr_transcribe(request=None, file=upload))  # type: ignore[arg-type]

    assert result["text"] == "小康，陪我走走"
    assert service.calls == [(b"RIFF" + b"x" * 200, "wav")]


def test_voice_status_reports_file_asr_model(monkeypatch) -> None:
    monkeypatch.setattr(voice_api, "_file_asr_service", FakeFileASR())
    monkeypatch.setattr(
        voice_api,
        "_settings",
        type(
            "Settings",
            (),
            {
                "dashscope_api_key": "configured",
                "qwen_tts_model_id": "qwen3-tts-flash",
            },
        )(),
    )

    result = asyncio.run(voice_api.voice_status())

    assert result["configured"] is True
    assert result["asr_model"] == "qwen3-asr-flash"
