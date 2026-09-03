from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from backend.schemas.robot_companion_schema import RobotCompanionContext


class Go2CompanionPlaybackStatus(BaseModel):
    mode: Literal["response_only"]
    go2_status: Literal["not_configured"]
    ready_for_client_playback: bool
    message: str


class Go2CompanionHealthMetrics(BaseModel):
    available: bool
    source: Literal["realtime_stream"]
    observed_at: datetime | None = None
    freshness: Literal["fresh", "stale", "missing"]
    risk_level: Literal["low", "medium", "high", "unknown"]
    heart_rate: int | None = None
    blood_oxygen: int | None = None
    temperature: float | None = None
    blood_pressure: str | None = None
    health_score: int | None = None
    steps: int | None = None
    recent_fall: bool
    sos: bool


class Go2CompanionVoiceTurnResponse(BaseModel):
    agent: Literal["go2_companion"]
    version: Literal["1.0"]
    session_id: str
    transcript: str
    reply: str
    audio_b64: str
    audio_url: str
    audio_format: str
    asr_provider: str
    llm_provider: str
    llm_model: str
    tts_provider: str
    tts_voice: str
    playback: Go2CompanionPlaybackStatus
    grounded: bool = False
    context: RobotCompanionContext | None = None
    health_metrics: Go2CompanionHealthMetrics | None = None


class Go2CompanionVoiceStatusResponse(BaseModel):
    pipeline: list[str]
    asr_configured: bool
    llm_configured: bool
    tts_configured: bool
    asr_model: str
    llm_model: str
    tts_model: str
    playback_mode: Literal["response_only", "half_duplex"]
    go2_microphone: Literal[
        "not_configured", "configured", "connected", "disconnected"
    ]
    go2_speaker: Literal[
        "not_configured", "configured", "connected", "disconnected"
    ]
    context_grounding_supported: bool
    audio_mode: Literal["response_only", "half_duplex"] = "response_only"
    state: Literal[
        "idle", "playing", "wait_after_playback", "recording", "processing", "error"
    ] = "idle"
    recording: bool = False
    playing: bool = False
    last_error: str | None = None
    post_playback_silence_ms: int = 0


class Go2HardwareVoiceTurnRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=100)
    voice: str = Field(default="Serena", min_length=1, max_length=80)
    elder_id: str | None = Field(default=None, max_length=160)
    device_mac: str | None = Field(default=None, max_length=64)
    location_hint: str | None = Field(default=None, max_length=200)
    prompt_text: str | None = Field(default=None, max_length=300)
    fall_monitoring: bool = False
    max_duration_s: float | None = Field(default=None, gt=0, le=60)
    silence_timeout_s: float | None = Field(default=None, gt=0, le=10)
    playback_timeout_s: float | None = Field(default=None, gt=0, le=120)

    @field_validator("session_id", "voice")
    @classmethod
    def strip_hardware_required_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("elder_id", "location_hint", "prompt_text")
    @classmethod
    def strip_hardware_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class Go2HardwareVoiceTurnResponse(BaseModel):
    agent: Literal["go2_companion"]
    version: Literal["1.0"]
    session_id: str
    transcript: str
    reply: str
    intent: Literal[
        "START_COMPANION",
        "STOP_COMPANION",
        "RESUME_COMPANION",
        "REQUEST_HELP",
        "CALL_FAMILY",
        "CHAT",
        "I_AM_OK",
        "NEED_HELP",
        "NO_RESPONSE",
        "UNCERTAIN",
    ]
    intent_confidence: float = Field(ge=0, le=1)
    intent_scope: Literal["companion", "fall_monitoring"]
    intent_executed: Literal[False]
    execution_message: str
    asr_provider: str
    llm_provider: str
    llm_model: str
    tts_provider: str
    tts_voice: str
    grounded: bool = False
    context: RobotCompanionContext | None = None
    health_metrics: Go2CompanionHealthMetrics | None = None
    playback: dict[str, object]
    audio_status: dict[str, object]


class Go2CompanionTextTurnRequest(BaseModel):
    elder_id: str = Field(..., min_length=1, max_length=160)
    text: str = Field(..., min_length=1, max_length=1000)
    session_id: str | None = Field(default=None, max_length=100)
    device_mac: str | None = Field(default=None, max_length=64)
    location_hint: str | None = Field(default=None, max_length=200)

    @field_validator("elder_id", "text")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("session_id", "location_hint")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("device_mac")
    @classmethod
    def normalize_device_mac(cls, value: str | None) -> str | None:
        if value is None:
            return None
        compact = "".join(character for character in value if character.isalnum()).upper()
        if len(compact) == 12:
            return ":".join(compact[index : index + 2] for index in range(0, 12, 2))
        return value.strip().upper() or None


class Go2CompanionTextTurnResponse(BaseModel):
    agent: Literal["go2_companion"] = "go2_companion"
    version: Literal["1.1"] = "1.1"
    session_id: str
    reply: str
    llm_provider: str
    llm_model: str
    context: RobotCompanionContext
    health_metrics: Go2CompanionHealthMetrics
