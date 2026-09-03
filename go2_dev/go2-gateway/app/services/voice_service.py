from __future__ import annotations

import logging
import time
from copy import deepcopy

import httpx

from app.config import Settings
from app.schemas.common import now_iso


class VoiceService:
    """Voice interaction boundary for the fall-confirmation task."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._last_prompt: dict | None = None
        self._last_error: str | None = None
        self.logger = logging.getLogger("go2_gateway.voice")

    def is_ready(self) -> bool:
        return self._is_mock_mode() or bool(self.settings.voice_prompt_url)

    def status(self) -> dict:
        ready = self.is_ready()
        return {
            "voice": "ready" if ready else "not_configured",
            "ready": ready,
            "mode": self.settings.voice_mode,
            "delivery_mode": "mock" if self._is_mock_mode() else "http",
            "fall_prompt": self.settings.fall_prompt,
            "prompt_url_configured": bool(self.settings.voice_prompt_url),
            "prompt_url": self.settings.voice_prompt_url or None,
            "last_prompt": deepcopy(self._last_prompt),
            "last_error": self._last_error,
            "supports_speech_recognition": False,
            "supports_robot_speaker": self.settings.voice_mode != "mock" or bool(self.settings.voice_prompt_url),
            "elder_response_timeout_seconds": self.settings.elder_response_timeout_seconds,
            "mock_confirm_fall_outcome": self.settings.mock_confirm_fall_outcome if self._is_mock_mode() else None,
            "next_action": "dispatch" if ready else "configure GO2_VOICE_PROMPT_URL or set GO2_VOICE_MODE=mock",
        }

    def ask_elder_status(self, task_id: str, elder_id: str | None = None) -> dict:
        prompt = {
            "voice": "waiting",
            "voiceMode": self.settings.voice_mode,
            "voiceDelivery": self._initial_delivery_state(),
            "voicePrompt": self.settings.fall_prompt,
            "voiceResult": "awaiting_response",
            "taskId": task_id,
            "elderId": elder_id,
            "promptedAt": now_iso(),
        }
        if self.settings.voice_prompt_url:
            delivery = self._send_prompt(prompt)
            prompt.update(delivery)
        elif not self._is_mock_mode():
            message = "GO2_VOICE_PROMPT_URL is required when GO2_VOICE_MODE is not mock"
            self._last_error = message
            prompt.update(
                {
                    "voice": "failed",
                    "voiceDelivery": "not_configured",
                    "voicePromptUrl": None,
                    "voiceError": message,
                }
            )
        self._last_prompt = deepcopy(prompt)
        return prompt

    def _is_mock_mode(self) -> bool:
        return self.settings.voice_mode.lower() == "mock"

    def _initial_delivery_state(self) -> str:
        if self.settings.voice_prompt_url:
            return "pending"
        if self._is_mock_mode():
            return "mock"
        return "not_configured"

    def _send_prompt(self, prompt: dict) -> dict:
        payload = {
            "task_id": prompt["taskId"],
            "elder_id": prompt["elderId"],
            "prompt": prompt["voicePrompt"],
            "voice_mode": prompt["voiceMode"],
            "prompted_at": prompt["promptedAt"],
        }
        attempts = max(1, self.settings.voice_prompt_retries + 1)
        for attempt in range(1, attempts + 1):
            try:
                with httpx.Client(timeout=self.settings.voice_prompt_timeout_seconds) as client:
                    client.post(self.settings.voice_prompt_url, json=payload).raise_for_status()
                self._last_error = None
                return {"voiceDelivery": "sent", "voicePromptUrl": self.settings.voice_prompt_url}
            except Exception as exc:
                self._last_error = str(exc)
                if attempt >= attempts:
                    self.logger.warning("voice prompt failed task_id=%s error=%s", prompt["taskId"], exc)
                    return {
                        "voice": "failed",
                        "voiceDelivery": "failed",
                        "voicePromptUrl": self.settings.voice_prompt_url,
                        "voiceError": str(exc),
                    }
                time.sleep(self.settings.voice_prompt_retry_delay_seconds)
