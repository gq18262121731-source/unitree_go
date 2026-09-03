from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Go2VoiceIntent(str, Enum):
    START_COMPANION = "START_COMPANION"
    STOP_COMPANION = "STOP_COMPANION"
    RESUME_COMPANION = "RESUME_COMPANION"
    REQUEST_HELP = "REQUEST_HELP"
    CALL_FAMILY = "CALL_FAMILY"
    CHAT = "CHAT"
    I_AM_OK = "I_AM_OK"
    NEED_HELP = "NEED_HELP"
    NO_RESPONSE = "NO_RESPONSE"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True)
class Go2IntentResult:
    intent: Go2VoiceIntent
    confidence: float
    scope: str


class Go2CompanionIntentService:
    """Deterministic P0-2 intent output; it never executes robot motion."""

    _START = ("陪我去散步", "陪我散步", "跟我走", "开始陪伴", "开始跟随")
    _STOP = ("停一下", "停下来", "停止陪伴", "停止跟随", "别跟了", "不要走了")
    _RESUME = ("继续走吧", "继续走", "继续陪伴", "继续跟随")
    _HELP = ("帮帮我", "帮助我", "救命", "需要帮助", "帮我一下")
    _FAMILY = ("联系家人", "联系我的家人", "呼叫家人", "给家人打电话")
    _OK = ("我没事", "我很好", "还好", "没关系", "不用帮忙")

    def classify(self, text: str, *, fall_monitoring: bool = False) -> Go2IntentResult:
        normalized = "".join(str(text or "").strip().lower().split())
        if fall_monitoring:
            if not normalized:
                return Go2IntentResult(Go2VoiceIntent.NO_RESPONSE, 1.0, "fall_monitoring")
            if self._contains(normalized, self._RESUME):
                return Go2IntentResult(
                    Go2VoiceIntent.RESUME_COMPANION,
                    0.99,
                    "fall_monitoring",
                )
            if self._contains(normalized, self._HELP + self._FAMILY):
                return Go2IntentResult(Go2VoiceIntent.NEED_HELP, 0.99, "fall_monitoring")
            if self._contains(normalized, self._OK):
                return Go2IntentResult(Go2VoiceIntent.I_AM_OK, 0.98, "fall_monitoring")
            return Go2IntentResult(Go2VoiceIntent.UNCERTAIN, 0.55, "fall_monitoring")

        # Explicit resume is checked independently. Saying "I am OK" alone is CHAT.
        if self._contains(normalized, self._RESUME):
            return Go2IntentResult(Go2VoiceIntent.RESUME_COMPANION, 0.99, "companion")
        if self._contains(normalized, self._STOP):
            return Go2IntentResult(Go2VoiceIntent.STOP_COMPANION, 0.99, "companion")
        if self._contains(normalized, self._START):
            return Go2IntentResult(Go2VoiceIntent.START_COMPANION, 0.98, "companion")
        if self._contains(normalized, self._FAMILY):
            return Go2IntentResult(Go2VoiceIntent.CALL_FAMILY, 0.99, "companion")
        if self._contains(normalized, self._HELP):
            return Go2IntentResult(Go2VoiceIntent.REQUEST_HELP, 0.99, "companion")
        return Go2IntentResult(Go2VoiceIntent.CHAT, 0.7, "companion")

    @staticmethod
    def _contains(text: str, phrases: tuple[str, ...]) -> bool:
        return any(phrase in text for phrase in phrases)
