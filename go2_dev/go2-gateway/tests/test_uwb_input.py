from __future__ import annotations

import math

import pytest

from app.follow import (
    UwbBearingSource,
    UwbBearingUnit,
    UwbInputConfig,
    UwbInputValidator,
    UwbSampleOrderingError,
)


def calibrated_config(**overrides) -> UwbInputConfig:
    values = {
        "bearing_source": UwbBearingSource.ORIENTATION_EST,
        "bearing_unit": UwbBearingUnit.RADIANS,
        "bearing_sign": 1,
        "bearing_zero_offset_rad": 0.55,
        "calibration_confirmed": True,
    }
    values.update(overrides)
    return UwbInputConfig(**values)


def normalize(validator: UwbInputValidator, **overrides):
    values = {
        "distance_est": 1.5,
        "orientation_est": -0.55,
        "sample_monotonic": 10.0,
        "enabled_from_app": 1,
        "error_state": 0,
    }
    values.update(overrides)
    return validator.normalize(**values)


def test_uwb_input_requires_explicit_calibration_confirmation() -> None:
    validator = UwbInputValidator(
        calibrated_config(calibration_confirmed=False)
    )

    with pytest.raises(ValueError, match="calibration"):
        normalize(validator)


@pytest.mark.parametrize(
    "orientation_est,expected_bearing",
    [
        (-0.55, 0.0),
        (0.17, 0.72),
        (-0.97, -0.42),
    ],
    ids=["front", "left", "right"],
)
def test_uwb_input_uses_calibrated_orientation_est_bearing(
    orientation_est: float, expected_bearing: float
) -> None:
    observation = normalize(
        UwbInputValidator(calibrated_config()),
        orientation_est=orientation_est,
    )

    assert observation.distance_metres == 1.5
    assert observation.bearing_radians == pytest.approx(expected_bearing)


@pytest.mark.parametrize(
    "orientation_est",
    [math.pi - 0.20, -math.pi - 0.90],
)
def test_uwb_input_wraps_bearing_to_pi(orientation_est: float) -> None:
    observation = normalize(
        UwbInputValidator(calibrated_config()),
        orientation_est=orientation_est,
    )

    assert -math.pi <= observation.bearing_radians <= math.pi


def test_legacy_yaw_est_keyword_cannot_drive_bearing() -> None:
    validator = UwbInputValidator(calibrated_config())

    with pytest.raises(TypeError, match="yaw_est"):
        validator.normalize(
            distance_est=1.5,
            orientation_est=-0.55,
            yaw_est=2.8,
            sample_monotonic=10.0,
            enabled_from_app=1,
            error_state=0,
        )


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"error_state": 1}, "error_state"),
        ({"enabled_from_app": 0}, "not enabled"),
        ({"distance_est": math.nan}, "finite"),
        ({"orientation_est": math.nan}, "orientation_est"),
        ({"orientation_est": math.inf}, "orientation_est"),
    ],
)
def test_uwb_input_fails_closed_on_invalid_device_state(overrides, match) -> None:
    with pytest.raises(ValueError, match=match):
        normalize(UwbInputValidator(calibrated_config()), **overrides)


def test_uwb_input_rejects_missing_orientation_est() -> None:
    validator = UwbInputValidator(calibrated_config())

    with pytest.raises(TypeError, match="orientation_est"):
        validator.normalize(
            distance_est=1.5,
            sample_monotonic=10.0,
            enabled_from_app=1,
            error_state=0,
        )


@pytest.mark.parametrize(
    "sample_time,kind",
    [(10.0, "duplicate"), (9.9, "out_of_order")],
)
def test_uwb_input_classifies_duplicate_or_backward_receive_time(
    sample_time: float, kind: str
) -> None:
    validator = UwbInputValidator(calibrated_config())
    normalize(validator)

    with pytest.raises(UwbSampleOrderingError, match="strictly increasing") as exc:
        normalize(validator, sample_monotonic=sample_time)

    assert exc.value.kind == kind
    assert exc.value.previous_monotonic == pytest.approx(10.0)
