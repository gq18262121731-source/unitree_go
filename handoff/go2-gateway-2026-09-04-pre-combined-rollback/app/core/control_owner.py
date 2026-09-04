from __future__ import annotations

from enum import Enum


class ControlOwner(str, Enum):
    NONE = "NONE"
    MANUAL = "MANUAL"
    NAVIGATION = "NAVIGATION"
    FOLLOW = "FOLLOW"
    EMERGENCY_STOP = "EMERGENCY_STOP"
