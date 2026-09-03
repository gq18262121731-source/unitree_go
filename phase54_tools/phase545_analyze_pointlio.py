#!/usr/bin/env python3
"""Analyze Phase 5.4.5 Point-LIO trajectory and binary PCD map."""

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


ROOT = Path(r"E:\笨笨狗")
RESULT = ROOT / "phase545_pointlio_result"
ODOM_JSON = RESULT / "pointlio_odom.json"
PCD_PATH = RESULT / "scans.pcd"
OUTPUT_JSON = ROOT / "phase545_pointlio_analysis.json"
TOPDOWN_PNG = RESULT / "pointlio_map_topdown.png"
VIEWS_PNG = RESULT / "pointlio_map_views.png"
LOCAL_PNG = RESULT / "pointlio_map_local_10m.png"


def describe(values):
    data = np.asarray(values, dtype=np.float64)
    if data.size == 0:
        return {"samples": 0}
    return {
        "samples": int(data.size),
        "min": float(np.min(data)),
        "median": float(np.median(data)),
        "mean": float(np.mean(data)),
        "p95": float(np.percentile(data, 95)),
        "p99": float(np.percentile(data, 99)),
        "max": float(np.max(data)),
    }


def quaternion_yaw(q):
    x, y, z, w = q
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def load_trajectory():
    payload = json.loads(ODOM_JSON.read_text(encoding="utf-8"))
    raw = payload["samples"]
    raw_sample_count = len(raw)
    stamps = []
    positions = []
    yaw = []
    duplicate_stamps = 0
    backward_jumps = 0
    previous = None
    for item in raw:
        stamp = int(item["stamp_ns"])
        if previous is not None:
            if stamp == previous:
                duplicate_stamps += 1
                continue
            if stamp < previous:
                backward_jumps += 1
        previous = stamp
        stamps.append(stamp)
        positions.append(item["position"])
        yaw.append(quaternion_yaw(item["orientation_xyzw"]))

    stamps = np.asarray(stamps, dtype=np.int64)
    positions = np.asarray(positions, dtype=np.float64)
    yaw = np.unwrap(np.asarray(yaw, dtype=np.float64))
    del payload
    del raw

    sample_indices = [0]
    next_stamp = stamps[0] + 100_000_000
    for index, stamp in enumerate(stamps[1:], start=1):
        if stamp >= next_stamp:
            sample_indices.append(index)
            next_stamp = stamp + 100_000_000
    if sample_indices[-1] != len(stamps) - 1:
        sample_indices.append(len(stamps) - 1)
    indices = np.asarray(sample_indices)
    sampled_positions = positions[indices]
    sampled_yaw = yaw[indices]
    steps = np.linalg.norm(np.diff(sampled_positions, axis=0), axis=1)
    dt = np.diff(stamps[indices]) / 1e9
    speed = np.divide(steps, dt, out=np.zeros_like(steps), where=dt > 0)
    unique_delta = np.diff(stamps) / 1e6
    relative_time = (stamps[indices] - stamps[indices][0]) / 1e9
    radius = np.linalg.norm(sampled_positions - sampled_positions[0], axis=1)
    ten_second_bins = []
    for start in np.arange(0.0, relative_time[-1] + 1e-6, 10.0):
        mask = (relative_time >= start) & (relative_time < start + 10.0)
        selected = np.flatnonzero(mask)
        if not len(selected):
            continue
        ten_second_bins.append(
            {
                "start_s": float(start),
                "end_s": float(min(start + 10.0, relative_time[-1])),
                "end_position_m": sampled_positions[selected[-1]].tolist(),
                "end_radius_m": float(radius[selected[-1]]),
                "max_radius_m": float(np.max(radius[selected])),
            }
        )

    first_threshold_crossing = {}
    for threshold in [1.0, 5.0, 10.0, 100.0, 1000.0, 10000.0]:
        crossed = np.flatnonzero(radius > threshold)
        first_threshold_crossing[str(threshold)] = (
            float(relative_time[crossed[0]]) if len(crossed) else None
        )

    return {
        "raw_samples": raw_sample_count,
        "unique_samples": int(len(stamps)),
        "duplicate_stamps": duplicate_stamps,
        "backward_jumps": backward_jumps,
        "duration_seconds": float((stamps[-1] - stamps[0]) / 1e9),
        "unique_interval_ms": describe(unique_delta),
        "path_length_10hz_m": float(np.sum(steps)),
        "net_displacement_m": float(
            np.linalg.norm(sampled_positions[-1] - sampled_positions[0])
        ),
        "position_span_m": (
            np.max(sampled_positions, axis=0) - np.min(sampled_positions, axis=0)
        ).tolist(),
        "net_yaw_change_deg": float(np.degrees(sampled_yaw[-1] - sampled_yaw[0])),
        "absolute_yaw_travel_10hz_deg": float(
            np.degrees(np.sum(np.abs(np.diff(sampled_yaw))))
        ),
        "step_10hz_m": describe(steps),
        "speed_10hz_mps": describe(speed),
        "finite_positions": bool(np.isfinite(sampled_positions).all()),
        "max_radius_from_start_m": float(
            np.max(np.linalg.norm(sampled_positions - sampled_positions[0], axis=1))
        ),
        "first_radius_threshold_crossing_s": first_threshold_crossing,
        "ten_second_bins": ten_second_bins,
        "_sampled_positions": sampled_positions,
    }


def read_pcd():
    with PCD_PATH.open("rb") as handle:
        header_lines = []
        while True:
            line = handle.readline()
            if not line:
                raise RuntimeError("PCD header ended before DATA")
            decoded = line.decode("ascii").strip()
            header_lines.append(decoded)
            if decoded.startswith("DATA "):
                data_offset = handle.tell()
                break
    header = {}
    for line in header_lines:
        if not line or line.startswith("#"):
            continue
        key, *values = line.split()
        header[key] = values
    if header["DATA"] != ["binary"]:
        raise RuntimeError(f"Expected binary PCD, found {header['DATA']}")
    fields = header["FIELDS"]
    points = int(header["POINTS"][0])
    raw = np.memmap(
        PCD_PATH, dtype="<f4", mode="r", offset=data_offset, shape=(points, len(fields))
    )
    xyz = np.asarray(raw[:, :3])
    finite = np.isfinite(xyz).all(axis=1)
    xyz_finite = xyz[finite]
    robust_low = np.percentile(xyz_finite, 0.1, axis=0)
    robust_high = np.percentile(xyz_finite, 99.9, axis=0)
    robust = xyz_finite[
        np.all((xyz_finite >= robust_low) & (xyz_finite <= robust_high), axis=1)
    ]

    voxel = np.floor(robust / 0.05).astype(np.int32)
    occupied_voxels = np.unique(voxel, axis=0).shape[0]
    radius = np.linalg.norm(xyz_finite, axis=1)
    return {
        "header": {key: values for key, values in header.items()},
        "points": points,
        "finite_points": int(np.sum(finite)),
        "finite_ratio": float(np.mean(finite)),
        "xyz_min_m": np.min(xyz_finite, axis=0).tolist(),
        "xyz_max_m": np.max(xyz_finite, axis=0).tolist(),
        "xyz_robust_p0_1_m": robust_low.tolist(),
        "xyz_robust_p99_9_m": robust_high.tolist(),
        "xyz_robust_span_m": (robust_high - robust_low).tolist(),
        "z_percentiles_m": {
            str(value): float(np.percentile(xyz_finite[:, 2], value))
            for value in [0.1, 1, 5, 25, 50, 75, 95, 99, 99.9]
        },
        "occupied_5cm_voxels": int(occupied_voxels),
        "points_per_occupied_5cm_voxel": float(len(robust) / occupied_voxels),
        "fraction_within_radius_m": {
            str(value): float(np.mean(radius <= value))
            for value in [5.0, 10.0, 20.0, 100.0, 1000.0]
        },
        "_xyz": xyz_finite,
        "_robust": robust,
    }


def project(points, horizontal, vertical, width, height, margin=40):
    low = np.percentile(points[:, [horizontal, vertical]], 0.1, axis=0)
    high = np.percentile(points[:, [horizontal, vertical]], 99.9, axis=0)
    span = np.maximum(high - low, 1e-6)
    scale = min((width - 2 * margin) / span[0], (height - 2 * margin) / span[1])
    offset = np.asarray(
        [
            (width - span[0] * scale) / 2.0,
            (height - span[1] * scale) / 2.0,
        ]
    )
    uv = (points[:, [horizontal, vertical]] - low) * scale + offset
    uv[:, 1] = height - uv[:, 1]
    return np.rint(uv).astype(np.int32), low, high, scale, offset


def render_map(map_data, trajectory):
    xyz = map_data["_robust"]
    positions = trajectory["_sampled_positions"]
    if len(xyz) > 900_000:
        indices = np.linspace(0, len(xyz) - 1, 900_000).astype(np.int64)
        shown = xyz[indices]
    else:
        shown = xyz

    width = height = 1500
    pixels = np.full((height, width, 3), 255, dtype=np.uint8)
    uv, low, high, scale, offset = project(shown, 0, 1, width, height)
    valid = (
        (uv[:, 0] >= 0)
        & (uv[:, 0] < width)
        & (uv[:, 1] >= 0)
        & (uv[:, 1] < height)
    )
    uv = uv[valid]
    z = shown[valid, 2]
    z_low, z_high = np.percentile(z, [1, 99])
    normalized = np.clip((z - z_low) / max(z_high - z_low, 1e-6), 0.0, 1.0)
    colors = np.column_stack(
        [
            (30 + 220 * normalized).astype(np.uint8),
            (80 + 150 * (1.0 - np.abs(normalized - 0.5) * 2.0)).astype(np.uint8),
            (220 - 180 * normalized).astype(np.uint8),
        ]
    )
    pixels[uv[:, 1], uv[:, 0]] = colors
    image = Image.fromarray(pixels)
    draw = ImageDraw.Draw(image)
    trajectory_uv = (positions[:, :2] - low) * scale + offset
    trajectory_uv[:, 1] = height - trajectory_uv[:, 1]
    trajectory_points = [tuple(value) for value in trajectory_uv]
    draw.line(trajectory_points, fill=(220, 20, 60), width=4)
    for point, color in [
        (trajectory_points[0], (0, 180, 80)),
        (trajectory_points[-1], (255, 110, 0)),
    ]:
        x, y = point
        draw.ellipse((x - 8, y - 8, x + 8, y + 8), fill=color)
    draw.rectangle((1, 1, width - 2, height - 2), outline=(30, 30, 30), width=2)
    draw.text((18, 15), "Phase 5.4.5 Point-LIO map: top view (X-Y)", fill=(0, 0, 0))
    draw.text((18, height - 28), "red=trajectory  green=start  orange=end", fill=(0, 0, 0))
    image.save(TOPDOWN_PNG)

    panel_width, panel_height = 700, 650
    canvas = Image.new("RGB", (panel_width * 3, panel_height), "white")
    views = [(0, 1, "Top X-Y"), (0, 2, "Side X-Z"), (1, 2, "Side Y-Z")]
    for panel, (horizontal, vertical, title) in enumerate(views):
        panel_pixels = np.full(
            (panel_height, panel_width, 3), 255, dtype=np.uint8
        )
        panel_uv, _, _, _, _ = project(
            shown, horizontal, vertical, panel_width, panel_height
        )
        valid = (
            (panel_uv[:, 0] >= 0)
            & (panel_uv[:, 0] < panel_width)
            & (panel_uv[:, 1] >= 0)
            & (panel_uv[:, 1] < panel_height)
        )
        panel_uv = panel_uv[valid]
        panel_pixels[panel_uv[:, 1], panel_uv[:, 0]] = (25, 95, 175)
        panel_image = Image.fromarray(panel_pixels)
        panel_draw = ImageDraw.Draw(panel_image)
        panel_draw.rectangle(
            (1, 1, panel_width - 2, panel_height - 2),
            outline=(30, 30, 30),
            width=2,
        )
        panel_draw.text((15, 12), title, fill=(0, 0, 0))
        canvas.paste(panel_image, (panel * panel_width, 0))
    canvas.save(VIEWS_PNG)

    local = xyz[
        (np.linalg.norm(xyz, axis=1) <= 10.0) & (np.abs(xyz[:, 2]) <= 5.0)
    ]
    trajectory_radius = np.linalg.norm(positions - positions[0], axis=1)
    crossing = np.flatnonzero(trajectory_radius > 10.0)
    trajectory_end = int(crossing[0]) if len(crossing) else len(positions)
    local_trajectory = positions[: max(trajectory_end, 2)]
    local_pixels = np.full((height, width, 3), 255, dtype=np.uint8)
    local_uv, local_low, _, local_scale, local_offset = project(
        local, 0, 1, width, height
    )
    local_valid = (
        (local_uv[:, 0] >= 0)
        & (local_uv[:, 0] < width)
        & (local_uv[:, 1] >= 0)
        & (local_uv[:, 1] < height)
    )
    local_uv = local_uv[local_valid]
    local_pixels[local_uv[:, 1], local_uv[:, 0]] = (25, 95, 175)
    local_image = Image.fromarray(local_pixels)
    local_draw = ImageDraw.Draw(local_image)
    local_traj_uv = (local_trajectory[:, :2] - local_low) * local_scale + local_offset
    local_traj_uv[:, 1] = height - local_traj_uv[:, 1]
    local_traj_points = [tuple(value) for value in local_traj_uv]
    local_draw.line(local_traj_points, fill=(220, 20, 60), width=4)
    local_draw.text(
        (18, 15),
        "Point-LIO local evidence before trajectory exceeds 10 m",
        fill=(0, 0, 0),
    )
    local_image.save(LOCAL_PNG)


def main():
    trajectory = load_trajectory()
    map_data = read_pcd()
    render_map(map_data, trajectory)
    trajectory.pop("_sampled_positions")
    map_data.pop("_xyz")
    map_data.pop("_robust")
    result = {
        "trajectory": trajectory,
        "map": map_data,
        "artifacts": {
            "topdown_png": str(TOPDOWN_PNG),
            "views_png": str(VIEWS_PNG),
            "local_10m_png": str(LOCAL_PNG),
            "pcd": str(PCD_PATH),
        },
    }
    OUTPUT_JSON.write_text(
        json.dumps(result, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
