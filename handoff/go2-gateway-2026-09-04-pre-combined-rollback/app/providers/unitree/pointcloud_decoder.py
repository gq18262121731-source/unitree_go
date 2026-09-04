from __future__ import annotations

import math
import struct
from typing import Any


_POINT_FORMATS = {
    7: "f",  # sensor_msgs/PointField.FLOAT32
    8: "d",  # sensor_msgs/PointField.FLOAT64
}


def _field_decoder(field: Any, *, big_endian: bool) -> tuple[int, str]:
    datatype = int(getattr(field, "datatype"))
    count = int(getattr(field, "count", 1))
    if datatype not in _POINT_FORMATS or count != 1:
        raise ValueError(
            f"field {getattr(field, 'name', '?')} must be scalar FLOAT32/FLOAT64"
        )
    prefix = ">" if big_endian else "<"
    return int(getattr(field, "offset")), prefix + _POINT_FORMATS[datatype]


def decode_xyz(sample: Any) -> list[tuple[float, float, float]]:
    """Decode finite XYZ triples from a Unitree PointCloud2 sample."""

    fields = {str(field.name): field for field in sample.fields}
    missing = sorted({"x", "y", "z"} - fields.keys())
    if missing:
        raise ValueError(f"PointCloud2 is missing fields: {', '.join(missing)}")

    point_step = int(sample.point_step)
    width = int(sample.width)
    height = int(sample.height)
    row_step = int(sample.row_step) or width * point_step
    if point_step <= 0 or width <= 0 or height <= 0:
        raise ValueError("PointCloud2 dimensions and point_step must be positive")

    decoders = [
        _field_decoder(fields[name], big_endian=bool(sample.is_bigendian))
        for name in ("x", "y", "z")
    ]
    data = bytes(sample.data)
    points: list[tuple[float, float, float]] = []
    for row in range(height):
        row_offset = row * row_step
        for column in range(width):
            point_offset = row_offset + column * point_step
            values: list[float] = []
            for field_offset, field_format in decoders:
                absolute_offset = point_offset + field_offset
                try:
                    values.append(
                        float(struct.unpack_from(field_format, data, absolute_offset)[0])
                    )
                except struct.error as exc:
                    raise ValueError(
                        "PointCloud2 data is shorter than its layout"
                    ) from exc
            xyz = (values[0], values[1], values[2])
            if all(math.isfinite(value) for value in xyz):
                points.append(xyz)
    return points
