from __future__ import annotations

from app.config import Settings
from app.services.voice_service import VoiceService


def test_voice_service_posts_prompt_to_http_bridge(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def post(self, url, json):
            calls.append({"url": url, "json": json, "timeout": self.timeout})
            return FakeResponse()

    monkeypatch.setattr("app.services.voice_service.httpx.Client", FakeClient)
    service = VoiceService(
        Settings(
            mode="mock",
            voice_mode="http",
            fall_prompt="Need help?",
            voice_prompt_url="http://audio.local/api/speak",
            voice_prompt_timeout_seconds=1.5,
        )
    )

    result = service.ask_elder_status("task_001", "elder-001")
    status = service.status()

    assert result["voice"] == "waiting"
    assert result["voiceDelivery"] == "sent"
    assert result["voicePromptUrl"] == "http://audio.local/api/speak"
    assert calls[0]["url"] == "http://audio.local/api/speak"
    assert calls[0]["json"]["task_id"] == "task_001"
    assert calls[0]["json"]["elder_id"] == "elder-001"
    assert calls[0]["json"]["prompt"] == "Need help?"
    assert calls[0]["timeout"] == 1.5
    assert status["last_error"] is None
    assert status["last_prompt"]["voiceDelivery"] == "sent"
    assert status["supports_robot_speaker"] is True


def test_voice_service_reports_http_bridge_failure_after_retries(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            raise RuntimeError("speaker offline")

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def post(self, url, json):
            calls.append({"url": url, "json": json})
            return FakeResponse()

    monkeypatch.setattr("app.services.voice_service.httpx.Client", FakeClient)
    service = VoiceService(
        Settings(
            mode="mock",
            voice_mode="http",
            fall_prompt="Need help?",
            voice_prompt_url="http://audio.local/api/speak",
            voice_prompt_retries=1,
            voice_prompt_retry_delay_seconds=0,
        )
    )

    result = service.ask_elder_status("task_002", "elder-002")
    status = service.status()

    assert len(calls) == 2
    assert result["voice"] == "failed"
    assert result["voiceDelivery"] == "failed"
    assert result["voiceError"] == "speaker offline"
    assert status["last_error"] == "speaker offline"
    assert status["last_prompt"]["voice"] == "failed"


def test_voice_service_reports_missing_http_bridge_configuration():
    service = VoiceService(Settings(mode="mock", voice_mode="http", voice_prompt_url=""))

    status = service.status()
    result = service.ask_elder_status("task_003", "elder-003")
    updated_status = service.status()

    assert status["voice"] == "not_configured"
    assert status["ready"] is False
    assert status["delivery_mode"] == "http"
    assert status["prompt_url_configured"] is False
    assert status["next_action"] == "configure GO2_VOICE_PROMPT_URL or set GO2_VOICE_MODE=mock"
    assert result["voice"] == "failed"
    assert result["voiceDelivery"] == "not_configured"
    assert result["voicePromptUrl"] is None
    assert "GO2_VOICE_PROMPT_URL" in result["voiceError"]
    assert updated_status["last_error"] == result["voiceError"]
    assert updated_status["last_prompt"]["voiceDelivery"] == "not_configured"
