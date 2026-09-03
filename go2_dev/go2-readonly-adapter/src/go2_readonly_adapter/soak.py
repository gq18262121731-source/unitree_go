from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .provider import UnitreeReadonlyProvider
from .sources.ros2 import Ros2ReadonlySource


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(int(math.ceil(fraction * len(ordered))) - 1, len(ordered) - 1)
    return ordered[max(index, 0)]


def _process_memory() -> dict[str, float | None]:
    values: dict[str, float | None] = {"rss_mb": None, "high_water_mb": None}
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                values["rss_mb"] = float(line.split()[1]) / 1024.0
            elif line.startswith("VmHWM:"):
                values["high_water_mb"] = float(line.split()[1]) / 1024.0
    except OSError:
        pass
    return values


def _within(value: float | None, minimum: float, maximum: float) -> bool:
    return value is not None and minimum <= value <= maximum


@dataclass
class IntervalTracker:
    last_source_ns: int | None = None
    intervals_ms: deque[float] = field(default_factory=lambda: deque(maxlen=10_000))
    samples: int = 0
    rollback_count: int = 0
    duplicate_count: int = 0
    sum_ms: float = 0.0
    sum_square_ms: float = 0.0
    minimum_ms: float | None = None
    maximum_ms: float | None = None

    def consume(self, source_ns: int | None) -> None:
        self.samples += 1
        if source_ns is None:
            return
        if self.last_source_ns is not None:
            interval_ms = (source_ns - self.last_source_ns) / 1_000_000.0
            if interval_ms < 0:
                self.rollback_count += 1
            elif interval_ms == 0:
                self.duplicate_count += 1
            else:
                self.intervals_ms.append(interval_ms)
                self.sum_ms += interval_ms
                self.sum_square_ms += interval_ms * interval_ms
                self.minimum_ms = (
                    interval_ms
                    if self.minimum_ms is None
                    else min(self.minimum_ms, interval_ms)
                )
                self.maximum_ms = (
                    interval_ms
                    if self.maximum_ms is None
                    else max(self.maximum_ms, interval_ms)
                )
        self.last_source_ns = source_ns

    def report(self) -> dict[str, Any]:
        interval_count = max(
            self.samples - 1 - self.rollback_count - self.duplicate_count, 0
        )
        mean_ms = self.sum_ms / interval_count if interval_count else None
        variance = None
        if interval_count and mean_ms is not None:
            variance = max(self.sum_square_ms / interval_count - mean_ms**2, 0.0)
        window = list(self.intervals_ms)
        return {
            "samples": self.samples,
            "positive_intervals": interval_count,
            "rollback_count": self.rollback_count,
            "duplicate_count": self.duplicate_count,
            "minimum_ms": self.minimum_ms,
            "mean_ms": mean_ms,
            "standard_deviation_ms": math.sqrt(variance) if variance is not None else None,
            "p50_ms_recent_window": statistics.median(window) if window else None,
            "p95_ms_recent_window": _percentile(window, 0.95),
            "maximum_ms": self.maximum_ms,
            "recent_window_size": len(window),
        }


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run_soak(duration_seconds: float, output: Path) -> dict[str, Any]:
    provider = UnitreeReadonlyProvider()
    source = Ros2ReadonlySource()
    provider.set_transport_source(source.source_name)
    started_monotonic = time.monotonic()
    started_at = _iso_now()
    last_checkpoint = started_monotonic
    checkpoint_seconds = 5.0
    intervals = {
        kind: IntervalTracker() for kind in ("lidar", "imu", "odometry")
    }
    event_counts: Counter[str] = Counter()
    checkpoints: list[dict[str, Any]] = []
    completed = False
    error: str | None = None

    def payload() -> dict[str, Any]:
        elapsed = time.monotonic() - started_monotonic
        snapshot = provider.snapshot()
        memory = _process_memory()
        rss_values = [
            item["rss_mb"]
            for item in checkpoints
            if item.get("rss_mb") is not None
        ]
        rss_growth = None
        rss_slope = None
        if rss_values:
            rss_growth = memory["rss_mb"] - rss_values[0] if memory["rss_mb"] else None
            if rss_growth is not None and elapsed > 0:
                rss_slope = rss_growth / (elapsed / 60.0)
        stale_counts = {
            kind: sum(not item[f"{kind}_fresh"] for item in checkpoints)
            for kind in ("lidar", "imu", "odometry")
        }
        lidar = snapshot["sensors"]["lidar"]
        imu = snapshot["sensors"]["imu"]
        odometry = snapshot["sensors"]["odometry"]
        checks = {
            "duration_reached": elapsed >= duration_seconds - 1.0,
            "lidar_samples": event_counts["lidar"] > 0,
            "imu_samples": event_counts["imu"] > 0,
            "odometry_samples": event_counts["odometry"] > 0,
            "lidar_frequency_10_to_20_hz": _within(
                lidar["frequency_hz"], 10.0, 20.0
            ),
            "imu_frequency_200_to_300_hz": _within(
                imu["frequency_hz"], 200.0, 300.0
            ),
            "odometry_frequency_100_to_200_hz": _within(
                odometry["frequency_hz"], 100.0, 200.0
            ),
            "topics_fresh": all(
                item["fresh"] for item in (lidar, imu, odometry)
            ),
            "stale_checkpoints_zero": all(
                count == 0 for count in stale_counts.values()
            ),
            "timestamp_rollback_zero": all(
                tracker.rollback_count == 0 for tracker in intervals.values()
            ),
            "motion_disabled": snapshot["motion"]["enabled"] is False,
            "localization_disabled": snapshot["localization"]["available"] is False,
            "navigation_disabled": snapshot["navigation"]["available"] is False,
            "rss_growth_under_50_mb": rss_growth is None or rss_growth < 50.0,
            "unexpected_error_absent": error is None,
        }
        return {
            "phase": "6.1-B",
            "started_at": started_at,
            "updated_at": _iso_now(),
            "target_duration_seconds": duration_seconds,
            "elapsed_seconds": elapsed,
            "completed": completed,
            "error": error,
            "safety": {
                "publishers_created": 0,
                "motion_control": "NOT_USED",
                "slam_started": False,
                "tf_published": False,
            },
            "event_counts": dict(event_counts),
            "intervals": {
                kind: tracker.report() for kind, tracker in intervals.items()
            },
            "resource": {
                **memory,
                "rss_growth_mb": rss_growth,
                "rss_slope_mb_per_minute": rss_slope,
                "process_cpu_seconds": time.process_time(),
            },
            "checkpoint_count": len(checkpoints),
            "stale_checkpoint_counts": stale_counts,
            "recent_checkpoints": checkpoints[-24:],
            "provider": snapshot,
            "checks": checks,
            "passed": completed and all(checks.values()),
        }

    try:
        for event in source.events(duration_seconds):
            provider.ingest(event)
            event_counts[event.kind] += 1
            if event.kind in intervals:
                intervals[event.kind].consume(event.source_timestamp_ns)
            now = time.monotonic()
            if now - last_checkpoint >= checkpoint_seconds:
                snapshot = provider.snapshot()
                memory = _process_memory()
                checkpoints.append(
                    {
                        "elapsed_seconds": now - started_monotonic,
                        "lidar_fresh": snapshot["sensors"]["lidar"]["fresh"],
                        "imu_fresh": snapshot["sensors"]["imu"]["fresh"],
                        "odometry_fresh": snapshot["sensors"]["odometry"]["fresh"],
                        "lidar_samples": event_counts["lidar"],
                        "imu_samples": event_counts["imu"],
                        "odometry_samples": event_counts["odometry"],
                        "rss_mb": memory["rss_mb"],
                        "process_cpu_seconds": time.process_time(),
                    }
                )
                last_checkpoint = now
                _write_atomic(output, payload())
        completed = True
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    result = payload()
    _write_atomic(output, result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 6.1-B read-only soak")
    parser.add_argument("--duration", type=float, default=1800.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.home()
        / "go2_validation"
        / "phase61b_readonly_soak.json",
    )
    args = parser.parse_args(argv)
    result = run_soak(args.duration, args.output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
