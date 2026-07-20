from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class Heartbeat(BaseModel):
    robotId: str
    cameraId: str
    online: bool
    lastFrameAt: Optional[str] = None
    captureFps: Optional[float] = None
    frameAgeMs: Optional[float] = None
    networkInterface: Optional[str] = None
