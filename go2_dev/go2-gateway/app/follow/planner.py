from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable


LOGGER = logging.getLogger(__name__)


class FollowState(str, Enum):
    """Planning-only follow states.

    These states do not acquire the robot control lock and do not send motion
    commands.
    """

    FOLLOW_WAITING_FOR_TARGET = "FOLLOW_WAITING_FOR_TARGET"
    FOLLOW_TRACKING = "FOLLOW_TRACKING"
    FOLLOW_TOO_CLOSE = "FOLLOW_TOO_CLOSE"
    FOLLOW_TOO_FAR = "FOLLOW_TOO_FAR"
    FOLLOW_TARGET_LOST = "FOLLOW_TARGET_LOST"


@dataclass(frozen=True)
class FollowOffset:
    """Desired right-rear relationship and UWB distance safety limits.

    Robot frame:
      * +x points forward.
      * +y points left.

    Therefore, when the robot is behind and to the right of the person, the
    person should appear at ``(back_distance, right_offset)`` in the robot
    frame.
    """

    back_distance: float = 1.5
    right_offset: float = 0.5
    max_distance: float = 2.5
    min_distance: float = 1.0

    def __post_init__(self) -> None:
        values = {
            "back_distance": self.back_distance,
            "right_offset": self.right_offset,
            "max_distance": self.max_distance,
            "min_distance": self.min_distance,
        }
        for name, value in values.items():
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.back_distance <= 0.0:
            raise ValueError("back_distance must be greater than zero")
        if self.right_offset < 0.0:
            raise ValueError("right_offset must not be negative")
        if self.min_distance <= 0.0:
            raise ValueError("min_distance must be greater than zero")
        if self.max_distance <= self.min_distance:
            raise ValueError("max_distance must be greater than min_distance")

        desired_distance = math.hypot(self.back_distance, self.right_offset)
        if not self.min_distance <= desired_distance <= self.max_distance:
            raise ValueError(
                "the configured right-rear target distance must be within "
                "[min_distance, max_distance]"
            )


@dataclass(frozen=True)
class FollowPlan:
    """One planning result; it is never dispatched to the motion API."""

    timestamp: str
    uwb_distance: float | None
    uwb_yaw: float | None
    target_x: float
    target_y: float
    current_state: FollowState
    stop_required: bool
    cmd_velocity: dict[str, float] | None

    def to_log_record(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "uwb_distance": self.uwb_distance,
            "uwb_yaw": self.uwb_yaw,
            "target_x": self.target_x,
            "target_y": self.target_y,
            "current_state": self.current_state.value,
            "cmd_velocity": self.cmd_velocity,
        }


def calculate_follow_target(
    distance_est: float,
    yaw_est: float,
    offset: FollowOffset | None = None,
) -> tuple[float, float]:
    """Calculate the robot-frame displacement toward the right-rear target.

    ``distance_est`` is in metres and ``yaw_est`` is in radians. Both values
    are read-only inputs from UWB.

    The measured person position is converted from polar to robot-frame
    Cartesian coordinates. The desired person position
    ``(back_distance, right_offset)`` is then subtracted. The returned
    ``target_x`` and ``target_y`` are position errors for a later controller;
    they are not velocity commands.

    This definition uses the robot heading because the requested two-field
    input does not contain the person's heading.
    """

    follow_offset = offset or FollowOffset()
    _validate_measurement(distance_est, yaw_est)

    measured_x = distance_est * math.cos(yaw_est)
    measured_y = distance_est * math.sin(yaw_est)
    target_x = measured_x - follow_offset.back_distance
    target_y = measured_y - follow_offset.right_offset
    return target_x, target_y


class FollowTargetPlanner:
    """Stateful UWB liveness and right-rear target planner.

    The planner has no reference to DDS publishers, ``SportClient``, the Go2
    gateway, or any motion adapter. A caller may consume ``FollowPlan`` later,
    after a separate safety and control integration review.
    """

    def __init__(
        self,
        offset: FollowOffset | None = None,
        *,
        lost_timeout_seconds: float = 2.0,
        monotonic_clock: Callable[[], float] = time.monotonic,
        logger: logging.Logger = LOGGER,
    ) -> None:
        if not math.isfinite(lost_timeout_seconds) or lost_timeout_seconds <= 0.0:
            raise ValueError("lost_timeout_seconds must be finite and greater than zero")
        self.offset = offset or FollowOffset()
        self.lost_timeout_seconds = lost_timeout_seconds
        self._monotonic_clock = monotonic_clock
        self._logger = logger
        self._last_sample_at: float | None = None
        self._last_plan: FollowPlan | None = None

    def process_measurement(
        self,
        distance_est: float,
        yaw_est: float,
        *,
        sample_monotonic: float | None = None,
        timestamp: datetime | None = None,
    ) -> FollowPlan:
        """Process one real or simulated measurement without changing it."""

        _validate_measurement(distance_est, yaw_est)
        sample_at = self._monotonic_clock() if sample_monotonic is None else sample_monotonic
        if not math.isfinite(sample_at):
            raise ValueError("sample_monotonic must be finite")

        if distance_est < self.offset.min_distance:
            plan = self._stop_plan(
                state=FollowState.FOLLOW_TOO_CLOSE,
                timestamp=timestamp,
                uwb_distance=distance_est,
                uwb_yaw=yaw_est,
            )
        else:
            target_x, target_y = calculate_follow_target(
                distance_est,
                yaw_est,
                self.offset,
            )
            state = (
                FollowState.FOLLOW_TOO_FAR
                if distance_est > self.offset.max_distance
                else FollowState.FOLLOW_TRACKING
            )
            plan = FollowPlan(
                timestamp=_iso_timestamp(timestamp),
                uwb_distance=distance_est,
                uwb_yaw=yaw_est,
                target_x=target_x,
                target_y=target_y,
                current_state=state,
                stop_required=False,
                cmd_velocity=None,
            )

        self._last_sample_at = sample_at
        self._last_plan = plan
        self._emit(plan)
        return plan

    def check_target_liveness(
        self,
        *,
        now_monotonic: float | None = None,
        timestamp: datetime | None = None,
    ) -> FollowPlan:
        """Return the current plan, or a zero-velocity stop plan after timeout."""

        now = self._monotonic_clock() if now_monotonic is None else now_monotonic
        if not math.isfinite(now):
            raise ValueError("now_monotonic must be finite")

        if self._last_sample_at is None:
            plan = self._stop_plan(
                state=FollowState.FOLLOW_WAITING_FOR_TARGET,
                timestamp=timestamp,
            )
            self._emit(plan)
            return plan

        elapsed = now - self._last_sample_at
        if elapsed < 0.0:
            raise ValueError("now_monotonic must not precede the last UWB sample")
        if elapsed >= self.lost_timeout_seconds:
            plan = self._stop_plan(
                state=FollowState.FOLLOW_TARGET_LOST,
                timestamp=timestamp,
            )
            self._last_plan = plan
            self._emit(plan)
            return plan

        if self._last_plan is None:
            raise RuntimeError("planner state is inconsistent")
        return self._last_plan

    @staticmethod
    def _stop_plan(
        *,
        state: FollowState,
        timestamp: datetime | None,
        uwb_distance: float | None = None,
        uwb_yaw: float | None = None,
    ) -> FollowPlan:
        return FollowPlan(
            timestamp=_iso_timestamp(timestamp),
            uwb_distance=uwb_distance,
            uwb_yaw=uwb_yaw,
            target_x=0.0,
            target_y=0.0,
            current_state=state,
            stop_required=True,
            cmd_velocity={"vx": 0.0, "vy": 0.0, "wz": 0.0},
        )

    def _emit(self, plan: FollowPlan) -> None:
        self._logger.info(
            json.dumps(plan.to_log_record(), ensure_ascii=False, separators=(",", ":"))
        )


def _validate_measurement(distance_est: float, yaw_est: float) -> None:
    if not math.isfinite(distance_est) or distance_est < 0.0:
        raise ValueError("distance_est must be finite and not negative")
    if not math.isfinite(yaw_est):
        raise ValueError("yaw_est must be finite")


def _iso_timestamp(timestamp: datetime | None) -> str:
    value = timestamp or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()
