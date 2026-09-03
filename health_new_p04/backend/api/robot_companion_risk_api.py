from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.dependencies import get_companion_risk_service
from backend.models.companion_risk_model import (
    CompanionResumeRequest,
    CompanionRiskEvent,
    CompanionRiskStatus,
    CompanionRiskTransition,
)
from backend.services.companion_risk_service import CompanionRiskConflict, CompanionRiskService


router = APIRouter(prefix="/robot/companion", tags=["robot-companion-risk"])


@router.post("/risk-events", response_model=CompanionRiskTransition)
async def receive_companion_risk_event(
    payload: CompanionRiskEvent,
    service: CompanionRiskService = Depends(get_companion_risk_service),
) -> CompanionRiskTransition:
    try:
        return service.handle_event(payload)
    except CompanionRiskConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/resume", response_model=CompanionRiskTransition)
async def resume_companion(
    payload: CompanionResumeRequest,
    service: CompanionRiskService = Depends(get_companion_risk_service),
) -> CompanionRiskTransition:
    try:
        return service.resume(payload.incident_id)
    except CompanionRiskConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/status", response_model=CompanionRiskStatus)
async def companion_risk_status(
    service: CompanionRiskService = Depends(get_companion_risk_service),
) -> CompanionRiskStatus:
    return service.status()
