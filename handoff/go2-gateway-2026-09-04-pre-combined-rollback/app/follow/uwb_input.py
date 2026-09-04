from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class UwbBearingSource(str, Enum):
    ORIENTATION_EST = "orientation_est"


class UwbBearingUnit(str, Enum):
    RADIANS = "radians"


class UwbSampleOrderingError(ValueError):
    """A duplicate or out-of-order receive time from the UWB transport."""

    def __init__(
        self,
        kind: str,
        *,
        sample_monotonic: float,
        previous_monotonic: float,
    ) -> None:
        self.kind = kind
        self.sample_monotonic = sample_monotonic
        self.previous_monotonic = previous_monotonic
        super().__init__(
            "UWB sample time must be strictly increasing "
            f"({kind}: current={sample_monotonic:.9f}, "
            f"previous={previous_monotonic:.9f})"
        )


@dataclass(frozen=True)
class UwbInputConfig:
    bearing_source: UwbBearingSource
    bearing_unit: UwbBearingUnit
    bearing_sign: int
    bearing_zero_offset_rad: float
    calibration_confirmed: bool = False
    require_enabled_from_app: bool = True
    accepted_error_state: int = 0

    def __post_init__(self) -> None:
        if self.bearing_source is not UwbBearingSource.ORIENTATION_EST:
            raise ValueError("bearing_source must be orientation_est")
        if self.bearing_unit is not UwbBearingUnit.RADIANS:
            raise ValueError("bearing_unit must be radians")
        if self.bearing_sign not in {-1, 1}:
            raise ValueError("bearing_sign must be -1 or 1")
        if not math.isfinite(self.bearing_zero_offset_rad):
            raise ValueError("bearing_zero_offset_rad must be finite")


@dataclass(frozen=True)
class UwbObservation:
    distance_metres: float
    bearing_radians: float
    sample_monotonic: float
    enabled_from_app: int
    error_state: int


class UwbInputValidator:
    """Validate and normalize the real UWB fields before follow planning."""

    def __init__(self, config: UwbInputConfig) -> None:
        self.config = config
        self._last_sample_monotonic: float | None = None

    def normalize(
        self,
        *,
        distance_est: float,
        orientation_est: float,
        sample_monotonic: float,
        enabled_from_app: int,
        error_state: int,
    ) -> UwbObservation:
        if not self.config.calibration_confirmed:
            raise ValueError("UWB distance/bearing calibration has not been confirmed")
        values = (distance_est, orientation_est, sample_monotonic)
        if not all(math.isfinite(value) for value in values):
            raise ValueError(
                "UWB distance, orientation_est, and sample time must be finite"
            )
        if distance_est < 0.0:
            raise ValueError("UWB distance must not be negative")
        if (
            self._last_sample_monotonic is not None
            and sample_monotonic <= self._last_sample_monotonic
        ):
            raise UwbSampleOrderingError(
                "duplicate"
                if sample_monotonic == self._last_sample_monotonic
                else "out_of_order",
                sample_monotonic=float(sample_monotonic),
                previous_monotonic=self._last_sample_monotonic,
            )
        if self.config.require_enabled_from_app and int(enabled_from_app) != 1:
            raise ValueError("UWB is not enabled from the Unitree app")
        if int(error_state) != self.config.accepted_error_state:
            raise ValueError(f"UWB error_state is {int(error_state)}")

        # Phase 7.1 physical calibration established that target bearing comes
        # from orientation_est, not yaw_est.  Keep the device-specific zero
        # offset explicit and wrap the normalized bearing to [-pi, pi].
        bearing_radians = self.config.bearing_sign * (
            float(orientation_est) + self.config.bearing_zero_offset_rad
        )
        bearing_radians = math.atan2(
            math.sin(bearing_radians), math.cos(bearing_radians)
        )
        self._last_sample_monotonic = float(sample_monotonic)
        return UwbObservation(
            distance_metres=float(distance_est),
            bearing_radians=bearing_radians,
            sample_monotonic=float(sample_monotonic),
            enabled_from_app=int(enabled_from_app),
            error_state=int(error_state),
        )
