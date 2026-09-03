from __future__ import annotations

import pytest

from tools.view_lidar_cloud_live import display_stride, parse_args


@pytest.mark.parametrize(
    "point_count,max_points,expected",
    [(0, 8000, 1), (100, 8000, 1), (8001, 8000, 2), (16001, 8000, 3)],
)
def test_display_stride_bounds_rendered_points(
    point_count: int, max_points: int, expected: int
) -> None:
    assert display_stride(point_count, max_points) == expected


def test_display_stride_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError, match="positive"):
        display_stride(10, 0)


def test_parse_args_keeps_viewer_read_only_topic_defaults() -> None:
    args = parse_args([])
    assert args.topic == "rt/utlidar/cloud_base"
    assert args.peer == "192.168.123.161"
    assert args.domain == 0
