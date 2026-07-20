from __future__ import annotations

from pydantic import BaseModel, Field


class MoveRequest(BaseModel):
    vx: float = Field(default=0.0)
    vy: float = Field(default=0.0)
    wz: float = Field(default=0.0)
    duration: float = Field(default=0.3)
    control_source: str = Field(default="api", alias="controlSource")

    model_config = {"populate_by_name": True}

