from __future__ import annotations

import struct
from dataclasses import dataclass

import pytest

from tools.probe_lidar_safety_phase7_1c import decode_xyz, expected_level
from app.motion import LidarSafetyConfig


@dataclass
class Field:
    name: str
    offset: int
    datatype: int = 7
    count: int = 1


@dataclass
class Cloud:
    fields: list[Field]
    width: int
    height: int
    point_step: int
    row_step: int
    data: bytes
    is_bigendian: bool = False


def cloud(points: list[tuple[float, float, float]]) -> Cloud:
    data = b"".join(struct.pack("<fff", *point) for point in points)
    return Cloud(
        fields=[Field("x", 0), Field("y", 4), Field("z", 8)],
        width=len(points),
        height=1,
        point_step=12,
        row_step=len(data),
        data=data,
    )


def test_decode_xyz_uses_pointcloud_layout() -> None:
    points = [(1.2, -0.3, 0.4), (2.0, 0.5, -0.1)]

    decoded = decode_xyz(cloud(points))
    assert len(decoded) == len(points)
    for actual, expected in zip(decoded, points):
        assert actual == pytest.approx(expected)


def test_decode_xyz_rejects_missing_axis() -> None:
    sample = cloud([(1.0, 0.0, 0.0)])
    sample.fields.pop()

    with pytest.raises(ValueError, match="missing fields: z"):
        decode_xyz(sample)


@pytest.mark.parametrize(
    "distance,level",
    [(2.0, "CLEAR"), (1.5, "CLEAR"), (1.2, "SLOW"), (0.8, "SLOW"), (0.65, "STOP"), (0.5, "STOP")],
)
def test_expected_level_matches_phase7_thresholds(distance: float, level: str) -> None:
    assert expected_level(distance, LidarSafetyConfig()) == level
