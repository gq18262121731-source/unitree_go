from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from backend.dependencies import (
    get_elder_companion_control_service,
    require_write_session_user,
)
from backend.models.elder_companion_model import ElderCompanionStatus
from backend.models.user_model import UserRole
from backend.services.elder_companion_control_service import (
    ElderCompanionControlError,
    ElderCompanionControlService,
)


router = APIRouter(prefix="/elders/{elder_id}/robot-companion", tags=["elder-robot-companion"])


def _require_companion_operator(authorization: str | None, elder_id: str) -> None:
    try:
        user = require_write_session_user(authorization)
    except ValueError as exc:
        code = str(exc)
        raise HTTPException(status_code=401 if code in {"AUTH_REQUIRED", "INVALID_SESSION"} else 403, detail=code) from exc
    if user.role in {UserRole.COMMUNITY, UserRole.ADMIN}:
        return
    if user.role == UserRole.ELDER and user.id == elder_id:
        return
    if user.role == UserRole.ELDER:
        raise HTTPException(status_code=403, detail="ELDER_SELF_CONTROL_ONLY")
    raise HTTPException(status_code=403, detail="COMPANION_CONTROL_FORBIDDEN")


def _translate_error(exc: ElderCompanionControlError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


@router.get("/status", response_model=ElderCompanionStatus)
async def elder_companion_status(
    elder_id: str,
    authorization: str | None = Header(default=None),
    service: ElderCompanionControlService = Depends(get_elder_companion_control_service),
) -> ElderCompanionStatus:
    _require_companion_operator(authorization, elder_id)
    try:
        return service.status(elder_id)
    except ElderCompanionControlError as exc:
        raise _translate_error(exc) from exc


@router.post("/start", response_model=ElderCompanionStatus)
async def elder_companion_start(
    elder_id: str,
    authorization: str | None = Header(default=None),
    service: ElderCompanionControlService = Depends(get_elder_companion_control_service),
) -> ElderCompanionStatus:
    _require_companion_operator(authorization, elder_id)
    try:
        return service.start(elder_id)
    except ElderCompanionControlError as exc:
        raise _translate_error(exc) from exc


@router.post("/stop", response_model=ElderCompanionStatus)
async def elder_companion_stop(
    elder_id: str,
    authorization: str | None = Header(default=None),
    service: ElderCompanionControlService = Depends(get_elder_companion_control_service),
) -> ElderCompanionStatus:
    _require_companion_operator(authorization, elder_id)
    try:
        return service.stop(elder_id)
    except ElderCompanionControlError as exc:
        raise _translate_error(exc) from exc
