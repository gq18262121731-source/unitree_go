from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from agent.go2_companion.agent import Go2CompanionAgentError
from agent.robot_companion.context_manager import RobotCompanionContextError
from backend.dependencies import (
    get_go2_companion_dialogue_service,
    get_go2_companion_voice_service,
    get_go2_hardware_voice_turn_service,
    get_robot_audio_service,
)
from backend.schemas.go2_companion_schema import (
    Go2CompanionTextTurnRequest,
    Go2CompanionTextTurnResponse,
    Go2CompanionVoiceStatusResponse,
    Go2CompanionVoiceTurnResponse,
    Go2HardwareVoiceTurnRequest,
    Go2HardwareVoiceTurnResponse,
)
from backend.services.go2_companion_dialogue_service import Go2CompanionDialogueService
from backend.services.go2_companion_voice_service import (
    Go2CompanionVoiceError,
    Go2CompanionVoiceService,
)
from backend.services.go2_hardware_voice_turn_service import (
    Go2HardwareVoiceTurnError,
    Go2HardwareVoiceTurnService,
)
from backend.services.robot_audio import RobotAudioService


router = APIRouter(prefix="/go2-companion", tags=["go2-companion"])
_MAX_AUDIO_BYTES = 10 * 1024 * 1024


@router.post("/text-turn", response_model=Go2CompanionTextTurnResponse)
async def go2_companion_text_turn(
    payload: Go2CompanionTextTurnRequest,
    service: Go2CompanionDialogueService = Depends(get_go2_companion_dialogue_service),
) -> Go2CompanionTextTurnResponse:
    try:
        return await asyncio.to_thread(service.process_turn, payload)
    except RobotCompanionContextError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"stage": "context", "code": exc.code, "message": exc.message},
        ) from exc
    except Go2CompanionAgentError as exc:
        status_code = 503 if "未配置" in str(exc) else 502
        raise HTTPException(
            status_code=status_code,
            detail={"stage": "llm", "message": str(exc)},
        ) from exc


@router.post("/voice-turn", response_model=Go2CompanionVoiceTurnResponse)
async def go2_companion_voice_turn(
    file: UploadFile = File(...),
    session_id: str = Form(..., min_length=1, max_length=100),
    voice: str = Form("Serena", min_length=1, max_length=80),
    elder_id: str | None = Form(default=None, max_length=160),
    device_mac: str | None = Form(default=None, max_length=64),
    location_hint: str | None = Form(default=None, max_length=200),
    service: Go2CompanionVoiceService = Depends(get_go2_companion_voice_service),
) -> Go2CompanionVoiceTurnResponse:
    if not _is_supported_audio(
        filename=file.filename or "",
        content_type=file.content_type or "",
    ):
        raise HTTPException(status_code=400, detail="Unsupported audio format")

    audio_bytes = await file.read()
    if len(audio_bytes) < 100:
        raise HTTPException(status_code=400, detail="Audio file too small")
    if len(audio_bytes) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio file exceeds 10 MiB")

    audio_format = _resolve_audio_format(
        filename=file.filename or "",
        content_type=file.content_type or "",
    )
    try:
        result = await asyncio.to_thread(
            service.process_turn,
            audio_bytes,
            audio_format=audio_format,
            session_id=session_id.strip(),
            voice=voice.strip(),
            elder_id=_strip_optional_form(elder_id),
            device_mac=_strip_optional_form(device_mac),
            location_hint=_strip_optional_form(location_hint),
        )
    except Go2CompanionVoiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"stage": exc.stage, "message": exc.message},
        ) from exc
    return Go2CompanionVoiceTurnResponse.model_validate(result)


@router.get("/status", response_model=Go2CompanionVoiceStatusResponse)
async def go2_companion_voice_status(
    service: Go2CompanionVoiceService = Depends(get_go2_companion_voice_service),
    audio_service: RobotAudioService = Depends(get_robot_audio_service),
) -> Go2CompanionVoiceStatusResponse:
    audio_status = (
        await audio_service.status()
        if isinstance(audio_service, RobotAudioService)
        else None
    )
    return Go2CompanionVoiceStatusResponse.model_validate(
        service.status(audio_status=audio_status)
    )


@router.post("/go2-voice-turn", response_model=Go2HardwareVoiceTurnResponse)
async def go2_hardware_voice_turn(
    payload: Go2HardwareVoiceTurnRequest,
    service: Go2HardwareVoiceTurnService = Depends(
        get_go2_hardware_voice_turn_service
    ),
) -> Go2HardwareVoiceTurnResponse:
    try:
        result = await service.process_turn(
            session_id=payload.session_id,
            voice=payload.voice,
            elder_id=payload.elder_id,
            device_mac=payload.device_mac,
            location_hint=payload.location_hint,
            prompt_text=payload.prompt_text,
            fall_monitoring=payload.fall_monitoring,
            max_duration_s=payload.max_duration_s,
            silence_timeout_s=payload.silence_timeout_s,
            playback_timeout_s=payload.playback_timeout_s,
        )
    except Go2HardwareVoiceTurnError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"stage": exc.stage, "message": exc.message},
        ) from exc
    return Go2HardwareVoiceTurnResponse.model_validate(result)


def _resolve_audio_format(*, filename: str, content_type: str) -> str:
    lower_name = filename.lower()
    lower_type = content_type.lower()
    if lower_name.endswith(".mp3") or "mpeg" in lower_type:
        return "mp3"
    if lower_name.endswith(".pcm") or "pcm" in lower_type:
        return "pcm"
    return "wav"


def _strip_optional_form(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _is_supported_audio(*, filename: str, content_type: str) -> bool:
    lower_name = filename.lower()
    lower_type = content_type.split(";", maxsplit=1)[0].strip().lower()
    return (
        lower_type
        in {
            "audio/wav",
            "audio/wave",
            "audio/x-wav",
            "audio/mpeg",
            "audio/mp3",
            "audio/pcm",
            "audio/l16",
            "application/octet-stream",
        }
        or lower_name.endswith((".wav", ".wave", ".mp3", ".pcm"))
    )
