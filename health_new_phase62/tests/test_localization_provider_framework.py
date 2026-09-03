from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from backend.localization import (
    LocalizationAdmissionController,
    LocalizationCandidate,
    LocalizationPose,
    LocalizationSource,
    LocalizationStatus,
    Quaternion,
    UnavailableLocalizationProvider,
    Vector3,
)


def candidate(
    timestamp: datetime,
    *,
    frame: str = "map",
    map_id: str = "validated-map-id",
    source: LocalizationSource = LocalizationSource.GO2_USLAM,
    position_x: float = 0.0,
    orientation_w: float = 1.0,
) -> LocalizationCandidate:
    return LocalizationCandidate(
        source=source,
        pose=LocalizationPose(
            position=Vector3(x=position_x, y=0.0, z=0.0),
            orientation=Quaternion(x=0.0, y=0.0, z=0.0, w=orientation_w),
        ),
        frame=frame,
        map_id=map_id,
        confidence=0.8,
        timestamp=timestamp,
    )


def test_default_provider_is_unavailable_and_exposes_no_pose() -> None:
    provider = UnavailableLocalizationProvider()

    status = provider.get_status()

    assert status.state is LocalizationStatus.UNAVAILABLE
    assert status.available is False
    assert status.source is None
    assert provider.get_pose() is None
    assert provider.health_check().healthy is False


def test_invalid_pose_is_rejected() -> None:
    now = datetime.now(timezone.utc)
    controller = LocalizationAdmissionController(
        validated_sources={LocalizationSource.GO2_USLAM}
    )

    status = controller.observe(candidate(now, position_x=float("nan")), now=now)

    assert status.state is LocalizationStatus.UNAVAILABLE
    assert status.reason == "POSE_INVALID"
    assert controller.get_pose() is None


def test_stale_timestamp_is_rejected() -> None:
    now = datetime.now(timezone.utc)
    controller = LocalizationAdmissionController(
        validated_sources={LocalizationSource.GO2_USLAM},
        max_sample_age_ms=500.0,
    )

    status = controller.observe(candidate(now - timedelta(seconds=1)), now=now)

    assert status.state is LocalizationStatus.UNAVAILABLE
    assert status.reason == "TIMESTAMP_STALE"


def test_missing_frame_is_rejected() -> None:
    now = datetime.now(timezone.utc)
    controller = LocalizationAdmissionController(
        validated_sources={LocalizationSource.GO2_USLAM}
    )

    status = controller.observe(candidate(now, frame="  "), now=now)

    assert status.state is LocalizationStatus.UNAVAILABLE
    assert status.reason == "FRAME_MISSING"


def test_invalid_confidence_is_rejected_by_contract() -> None:
    now = datetime.now(timezone.utc)

    with pytest.raises(ValidationError):
        LocalizationCandidate(
            source=LocalizationSource.GO2_USLAM,
            pose=LocalizationPose(
                position=Vector3(x=0.0, y=0.0, z=0.0),
                orientation=Quaternion(x=0.0, y=0.0, z=0.0, w=1.0),
            ),
            frame="map",
            map_id="validated-map-id",
            confidence=1.1,
            timestamp=now,
        )


def test_controller_has_no_validated_source_by_default() -> None:
    now = datetime.now(timezone.utc)
    controller = LocalizationAdmissionController()

    status = controller.observe(candidate(now), now=now)

    assert status.state is LocalizationStatus.UNAVAILABLE
    assert status.reason == "SOURCE_NOT_VALIDATED"


def test_valid_samples_pass_through_observing_before_ready() -> None:
    now = datetime.now(timezone.utc)
    controller = LocalizationAdmissionController(
        validated_sources={LocalizationSource.GO2_USLAM},
        minimum_ready_samples=3,
    )

    first = controller.observe(candidate(now), now=now)
    second_time = now + timedelta(milliseconds=10)
    second = controller.observe(candidate(second_time), now=second_time)
    third_time = now + timedelta(milliseconds=20)
    third = controller.observe(candidate(third_time), now=third_time)

    assert first.state is LocalizationStatus.OBSERVING
    assert first.available is False
    assert first.pose is None
    assert second.state is LocalizationStatus.OBSERVING
    assert third.state is LocalizationStatus.READY
    assert third.available is True
    assert controller.get_pose() == third.pose
    assert controller.health_check().healthy is True


def test_ready_provider_fails_closed_on_bad_sample() -> None:
    now = datetime.now(timezone.utc)
    controller = LocalizationAdmissionController(
        validated_sources={LocalizationSource.GO2_USLAM},
        minimum_ready_samples=2,
    )

    controller.observe(candidate(now), now=now)
    ready_time = now + timedelta(milliseconds=10)
    assert (
        controller.observe(candidate(ready_time), now=ready_time).state
        is LocalizationStatus.READY
    )

    failed = controller.observe(
        candidate(
            ready_time + timedelta(milliseconds=10),
            source=LocalizationSource.POINT_LIO,
        ),
        now=ready_time + timedelta(milliseconds=10),
    )

    assert failed.state is LocalizationStatus.UNAVAILABLE
    assert failed.available is False
    assert failed.source is None
    assert failed.pose is None
    assert failed.reason == "SOURCE_NOT_VALIDATED"
    assert controller.get_pose() is None


def test_timestamp_rollback_resets_observation_window() -> None:
    now = datetime.now(timezone.utc)
    controller = LocalizationAdmissionController(
        validated_sources={LocalizationSource.GO2_USLAM},
        minimum_ready_samples=2,
    )

    controller.observe(candidate(now), now=now)
    status = controller.observe(
        candidate(now - timedelta(milliseconds=1)),
        now=now,
    )

    assert status.state is LocalizationStatus.UNAVAILABLE
    assert status.reason == "TIMESTAMP_ROLLBACK"
