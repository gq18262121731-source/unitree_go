from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FallEventRequest(BaseModel):
    event: Literal["fall_detected"] = Field(default="fall_detected")
    elder_id: str = Field(alias="elderId")
    location: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_event_id: str | None = Field(default=None, alias="sourceEventId")
    camera_id: str | None = Field(default=None, alias="cameraId")

    model_config = {"populate_by_name": True}


class TargetMoveRequest(BaseModel):
    location: str
    task: Literal["move_to_target"] = Field(default="move_to_target")
    priority: Literal["normal", "high"] = Field(default="normal")

    model_config = {"populate_by_name": True}
