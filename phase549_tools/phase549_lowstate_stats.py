#!/usr/bin/env python3
import csv
import json
import math
import statistics
import sys
from pathlib import Path


def percentile(values, fraction):
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def stats(values):
    return {
        "min": min(values),
        "p05": percentile(values, 0.05),
        "median": statistics.median(values),
        "mean": statistics.fmean(values),
        "p95": percentile(values, 0.95),
        "max": max(values),
        "std": statistics.pstdev(values),
    }


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: phase549_lowstate_stats.py INPUT_CSV")
    source = Path(sys.argv[1])
    rows = list(csv.DictReader(source.open(newline="", encoding="utf-8")))
    if len(rows) < 10:
        raise SystemExit("not enough samples")

    lo = len(rows) // 10
    hi = len(rows) - lo
    middle = rows[lo:hi]
    acceleration = [
        [float(row["acc_x"]), float(row["acc_y"]), float(row["acc_z"])]
        for row in middle
    ]
    gyro = [
        [float(row["gyro_x"]), float(row["gyro_y"]), float(row["gyro_z"])]
        for row in middle
    ]
    acceleration_norm = [
        math.sqrt(sum(component * component for component in vector))
        for vector in acceleration
    ]
    gyro_norm = [
        math.sqrt(sum(component * component for component in vector))
        for vector in gyro
    ]
    system_times = [int(row["system_time_ns"]) for row in rows]
    intervals = [
        (second - first) * 1e-9
        for first, second in zip(system_times, system_times[1:])
    ]

    result = {
        "source": str(source),
        "samples": len(rows),
        "middle_80_percent_samples": len(middle),
        "frequency_hz_from_mean_interval": 1.0 / statistics.fmean(intervals),
        "timestamp_backward": sum(value < 0 for value in intervals),
        "acceleration_mean_xyz": [
            statistics.fmean(vector[index] for vector in acceleration)
            for index in range(3)
        ],
        "acceleration_norm": stats(acceleration_norm),
        "gyro_mean_xyz": [
            statistics.fmean(vector[index] for vector in gyro)
            for index in range(3)
        ],
        "gyro_norm": stats(gyro_norm),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
