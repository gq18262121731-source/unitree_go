"""Go2 EDU companion supervision and its explicitly gated lifecycle."""

from typing import TYPE_CHECKING

from app.companion.config import (
    CompanionConfig,
    CompanionSafetyConfig,
    FollowProfile,
    ViewAdjustConfig,
)
from app.companion.config_loader import (
    CompanionConfigError,
    CompanionDemoConfig,
    load_companion_demo_config,
)
from app.companion.events import CompanionEvent, CompanionEventType
from app.companion.models import (
    CompanionDirective,
    CompanionMotionMode,
    CompanionSnapshot,
    CompanionState,
)
from app.companion.state_machine import CompanionStateMachine
from app.companion.supervisor import CompanionSupervisor
from app.companion.exceptions import CompanionLifecycleError

if TYPE_CHECKING:
    from app.companion.lifecycle_service import CompanionLifecycleService
    from app.companion.runtime import CompanionRuntime

__all__ = [
    "CompanionConfig",
    "CompanionConfigError",
    "CompanionDemoConfig",
    "CompanionDirective",
    "CompanionEvent",
    "CompanionEventType",
    "CompanionMotionMode",
    "CompanionSnapshot",
    "CompanionState",
    "CompanionStateMachine",
    "CompanionSupervisor",
    "CompanionLifecycleError",
    "CompanionLifecycleService",
    "CompanionRuntime",
    "CompanionSafetyConfig",
    "FollowProfile",
    "ViewAdjustConfig",
    "load_companion_demo_config",
]


def __getattr__(name: str):
    """Load lifecycle types lazily to keep sensor/tool imports acyclic."""

    if name == "CompanionLifecycleService":
        from app.companion.lifecycle_service import CompanionLifecycleService

        return CompanionLifecycleService
    if name == "CompanionRuntime":
        from app.companion.runtime import CompanionRuntime

        return CompanionRuntime
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
from app.companion.competition_lifecycle import (
    CompetitionLifecycle,
    LifecycleAction,
    LifecycleReadiness,
    LifecycleResult,
)

__all__ = [
    "CompetitionLifecycle",
    "LifecycleAction",
    "LifecycleReadiness",
    "LifecycleResult",
]
