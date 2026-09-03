#!/usr/bin/env python3
"""Read-only Phase 7.1-C LiDAR safety calibration probe.

This tool creates DDS readers only. It decodes XYZ fields from the firmware
``cloud_base`` PointCloud2 stream and feeds the existing LidarSafetyGuard. It
does not import RobotService, create a DDS publisher, or call motion APIs.
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import statistics
import struct
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.motion import LidarSafetyConfig, LidarSafetyGuard


TOPIC_LOWSTATE = "rt/lowstate"
TOPIC_CLOUD_BASE = "rt/utlidar/cloud_base"
POINT_FORMATS = {
    7: "f",  # sensor_msgs/PointField.FLOAT32
    8: "d",  # sensor_msgs/PointField.FLOAT64
}


def auto_local_address(peer: str) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((peer, 7400))
        return str(probe.getsockname()[0])


def cyclone_config(
    *, peer: str, interface: str | None, local_address: str | None
) -> str:
    if interface:
        selector = f'name="{escape(interface)}"'
    else:
        address = local_address or auto_local_address(peer)
        selector = f'address="{escape(address)}"'
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<CycloneDDS><Domain Id="any">'
        "<General><Interfaces>"
        f'<NetworkInterface {selector} priority="default" multicast="default"/>'
        "</Interfaces></General>"
        "<Discovery><Peers>"
        f'<Peer Address="{escape(peer)}"/>'
        "</Peers></Discovery>"
        "</Domain></CycloneDDS>"
    )


def _field_decoder(field: Any, *, big_endian: bool) -> tuple[int, str]:
    datatype = int(getattr(field, "datatype"))
    count = int(getattr(field, "count", 1))
    if datatype not in POINT_FORMATS or count != 1:
        raise ValueError(
            f"field {getattr(field, 'name', '?')} must be scalar FLOAT32/FLOAT64"
        )
    prefix = ">" if big_endian else "<"
    return int(getattr(field, "offset")), prefix + POINT_FORMATS[datatype]


def decode_xyz(sample: Any) -> list[tuple[float, float, float]]:
    """Decode finite XYZ triples from a sensor_msgs PointCloud2 sample."""

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
            values = []
            for field_offset, field_format in decoders:
                absolute_offset = point_offset + field_offset
                try:
                    values.append(struct.unpack_from(field_format, data, absolute_offset)[0])
                except struct.error as exc:
                    raise ValueError("PointCloud2 data is shorter than its layout") from exc
            xyz = tuple(float(value) for value in values)
            if all(math.isfinite(value) for value in xyz):
                points.append(xyz)  # type: ignore[arg-type]
    return points


def expected_level(distance: float, config: LidarSafetyConfig) -> str:
    if distance <= config.stop_distance:
        return "STOP"
    if distance <= config.slow_distance:
        return "SLOW"
    return "CLEAR"


def _summary(values: Iterable[float]) -> dict[str, float | None]:
    items = list(values)
    if not items:
        return {"min": None, "median": None, "max": None}
    return {
        "min": min(items),
        "median": statistics.median(items),
        "max": max(items),
    }


def _xyz_summary(points: Iterable[tuple[float, float, float]]) -> dict[str, Any]:
    items = list(points)
    return {
        "count": len(items),
        "x": _summary(point[0] for point in items),
        "y": _summary(point[1] for point in items),
        "z": _summary(point[2] for point in items),
        "radial_distance": _summary(math.hypot(point[0], point[1]) for point in items),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--peer", default="192.168.123.161")
    network = parser.add_mutually_exclusive_group()
    network.add_argument("--interface")
    network.add_argument("--local-address")
    parser.add_argument("--domain", type=int, default=0)
    parser.add_argument("--topic", default=TOPIC_CLOUD_BASE)
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--minimum-samples", type=int, default=30)
    parser.add_argument("--expected-distance", type=float)
    parser.add_argument("--position-label", default="baseline")
    parser.add_argument("--distance-tolerance", type=float, default=0.15)
    parser.add_argument("--stop-distance", type=float, default=0.65)
    parser.add_argument("--slow-distance", type=float, default=1.20)
    parser.add_argument("--roi-min-z", type=float, default=-0.35)
    parser.add_argument("--roi-max-z", type=float, default=0.65)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.seconds <= 0.0:
        parser.error("--seconds must be greater than zero")
    if args.minimum_samples < 1:
        parser.error("--minimum-samples must be positive")
    if args.expected_distance is not None and args.expected_distance <= 0.0:
        parser.error("--expected-distance must be greater than zero")
    if args.distance_tolerance <= 0.0:
        parser.error("--distance-tolerance must be greater than zero")
    if not math.isfinite(args.stop_distance) or args.stop_distance <= 0.0:
        parser.error("--stop-distance must be finite and greater than zero")
    if not math.isfinite(args.slow_distance) or args.slow_distance <= 0.0:
        parser.error("--slow-distance must be finite and greater than zero")
    if args.stop_distance >= args.slow_distance:
        parser.error("--stop-distance must be less than --slow-distance")
    if not math.isfinite(args.roi_min_z) or not math.isfinite(args.roi_max_z):
        parser.error("ROI z bounds must be finite")
    if args.roi_min_z >= args.roi_max_z:
        parser.error("--roi-min-z must be less than --roi-max-z")
    return args


def run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    try:
        from cyclonedds.domain import Domain, DomainParticipant
        from cyclonedds.sub import DataReader
        from cyclonedds.topic import Topic
        from unitree_sdk2py.idl.sensor_msgs.msg.dds_ import PointCloud2_
        from unitree_sdk2py.idl.unitree_go.msg.dds_ import LowState_
    except ImportError as exc:
        raise RuntimeError("CycloneDDS and unitree_sdk2py are required") from exc

    local_address = args.local_address
    if not args.interface and not local_address:
        local_address = auto_local_address(args.peer)
    config_xml = cyclone_config(
        peer=args.peer, interface=args.interface, local_address=local_address
    )
    configured_domain = Domain(args.domain, config_xml)
    participant = DomainParticipant(args.domain)
    low_reader = DataReader(
        participant, Topic(participant, TOPIC_LOWSTATE, LowState_)
    )
    cloud_reader = DataReader(
        participant, Topic(participant, args.topic, PointCloud2_)
    )

    guard_config = LidarSafetyConfig(
        stop_distance=args.stop_distance,
        slow_distance=args.slow_distance,
        roi_min_z=args.roi_min_z,
        roi_max_z=args.roi_max_z,
    )
    guard = LidarSafetyGuard(guard_config)
    lowstate_count = 0
    cloud_count = 0
    decode_errors: list[str] = []
    frames: Counter[str] = Counter()
    levels: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    cloud_point_counts: list[int] = []
    roi_point_counts: list[int] = []
    nearest_distances: list[float] = []
    diagnostic_roi_points: list[tuple[float, float, float]] = []
    diagnostic_stop_points: list[tuple[float, float, float]] = []
    nearest_xyz_per_cloud: list[tuple[float, float, float]] = []
    decisions: list[dict[str, Any]] = []
    started = time.monotonic()
    deadline = started + args.seconds
    first_cloud_at = None
    last_cloud_at = None

    while time.monotonic() < deadline:
        lowstate_count += len(low_reader.take(1000))
        samples = list(cloud_reader.take(64) or [])
        for sample in samples:
            received_at = time.monotonic()
            frame = str(getattr(getattr(sample, "header", None), "frame_id", ""))
            frames[frame] += 1
            try:
                points = decode_xyz(sample)
            except ValueError as exc:
                decode_errors.append(str(exc))
                continue
            decision = guard.update(
                points, frame_id=frame, sample_monotonic=received_at
            )
            roi_xyz = [
                point
                for point in points
                if guard_config.roi_min_x <= point[0] <= guard_config.roi_max_x
                and abs(point[1]) <= guard_config.roi_half_width
                and guard_config.roi_min_z <= point[2] <= guard_config.roi_max_z
            ]
            diagnostic_roi_points.extend(roi_xyz)
            stop_xyz = [
                point
                for point in roi_xyz
                if math.hypot(point[0], point[1]) <= guard_config.stop_distance
            ]
            diagnostic_stop_points.extend(stop_xyz)
            if roi_xyz:
                nearest_xyz_per_cloud.append(
                    min(roi_xyz, key=lambda point: math.hypot(point[0], point[1]))
                )
            cloud_count += 1
            first_cloud_at = received_at if first_cloud_at is None else first_cloud_at
            last_cloud_at = received_at
            cloud_point_counts.append(len(points))
            roi_point_counts.append(decision.roi_point_count)
            levels[decision.level.value] += 1
            reasons[decision.reason] += 1
            if decision.nearest_distance is not None:
                nearest_distances.append(decision.nearest_distance)
            decisions.append(
                {
                    "level": decision.level.value,
                    "reason": decision.reason,
                    "nearest_distance": decision.nearest_distance,
                    "roi_point_count": decision.roi_point_count,
                    "frame_id": decision.frame_id,
                }
            )
        if not samples:
            time.sleep(0.005)

    del configured_domain
    stable_window = decisions[-args.minimum_samples :]
    stable_level = None
    if len(stable_window) >= args.minimum_samples:
        window_levels = {item["level"] for item in stable_window}
        if len(window_levels) == 1:
            stable_level = str(stable_window[0]["level"])

    observed_distance = (
        statistics.median(nearest_distances) if nearest_distances else None
    )
    expected = (
        expected_level(args.expected_distance, guard_config)
        if args.expected_distance is not None
        else "CLEAR"
    )
    distance_error = (
        observed_distance - args.expected_distance
        if observed_distance is not None and args.expected_distance is not None
        else None
    )
    accepted_frames = set(guard_config.accepted_frames)
    frames_ok = bool(frames) and set(frames).issubset(accepted_frames)
    position_pass = (
        lowstate_count > 0
        and cloud_count >= args.minimum_samples
        and not decode_errors
        and frames_ok
        and stable_level is not None
        and (expected is None or stable_level == expected)
        and (
            args.expected_distance is None
            or observed_distance is not None
        )
        and (
            distance_error is None
            if args.expected_distance is None
            else (
                distance_error is not None
                and abs(distance_error) <= args.distance_tolerance
            )
        )
    )
    frequency = None
    if cloud_count > 1 and first_cloud_at is not None and last_cloud_at is not None:
        elapsed = last_cloud_at - first_cloud_at
        if elapsed > 0.0:
            frequency = (cloud_count - 1) / elapsed

    report = {
        "phase": "7.1-C",
        "mode": "read_only_static_calibration",
        "verdict": "PASS_POSITION" if position_pass else "HOLD_POSITION",
        "read_only": True,
        "motion_calls": 0,
        "network": {
            "peer": args.peer,
            "interface": args.interface,
            "local_address": local_address,
            "domain": args.domain,
        },
        "topic": args.topic,
        "position_label": args.position_label,
        "expected_distance": args.expected_distance,
        "expected_level": expected,
        "distance_tolerance": args.distance_tolerance,
        "lowstate_count": lowstate_count,
        "cloud_count": cloud_count,
        "frequency_hz": frequency,
        "frames": dict(frames),
        "decode_errors": decode_errors,
        "cloud_points": _summary(float(value) for value in cloud_point_counts),
        "roi_points": _summary(float(value) for value in roi_point_counts),
        "nearest_distance": _summary(nearest_distances),
        "roi_xyz_diagnostics": _xyz_summary(diagnostic_roi_points),
        "stop_zone_xyz_diagnostics": _xyz_summary(diagnostic_stop_points),
        "nearest_xyz_per_cloud": _xyz_summary(nearest_xyz_per_cloud),
        "distance_error": distance_error,
        "level_counts": dict(levels),
        "reason_counts": dict(reasons),
        "stable_window_size": len(stable_window),
        "stable_level": stable_level,
        "guard_config": {
            "stop_distance": guard_config.stop_distance,
            "slow_distance": guard_config.slow_distance,
            "roi_min_x": guard_config.roi_min_x,
            "roi_max_x": guard_config.roi_max_x,
            "roi_half_width": guard_config.roi_half_width,
            "roi_min_z": guard_config.roi_min_z,
            "roi_max_z": guard_config.roi_max_z,
            "minimum_obstacle_points": guard_config.minimum_obstacle_points,
            "clear_samples_required": guard_config.clear_samples_required,
            "accepted_frames": list(guard_config.accepted_frames),
        },
    }
    return report, 0 if position_pass else 3


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report, exit_code = run(args)
    except (OSError, RuntimeError, ValueError) as exc:
        report = {
            "phase": "7.1-C",
            "mode": "read_only_static_calibration",
            "verdict": "FAIL_PROBE",
            "error": str(exc),
            "read_only": True,
            "motion_calls": 0,
        }
        exit_code = 2
    rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    print(rendered)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
