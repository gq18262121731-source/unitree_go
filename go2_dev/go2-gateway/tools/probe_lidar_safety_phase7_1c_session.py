#!/usr/bin/env python3
"""Continuous read-only LiDAR SafetyGuard calibration session.

Unlike the one-position probe, this process keeps one SafetyGuard instance
alive across CLEAR, SLOW, STOP, and recovery captures.  It creates DDS readers
only and never imports a Unitree motion client or creates a DDS writer.

Commands on stdin:
  status
  capture LABEL EXPECTED_DISTANCE [SECONDS]
  quit
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.motion import LidarSafetyConfig, LidarSafetyGuard
from tools.probe_lidar_safety_phase7_1c import (
    TOPIC_CLOUD_BASE,
    auto_local_address,
    cyclone_config,
    decode_xyz,
    expected_level,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--peer", default="192.168.123.161")
    parser.add_argument("--local-address")
    parser.add_argument("--interface")
    parser.add_argument("--domain", type=int, default=0)
    parser.add_argument("--topic", default=TOPIC_CLOUD_BASE)
    parser.add_argument("--stop-distance", type=float, default=0.65)
    parser.add_argument("--slow-distance", type=float, default=1.20)
    parser.add_argument("--roi-min-z", type=float, default=-0.30)
    parser.add_argument("--roi-max-z", type=float, default=0.65)
    parser.add_argument("--minimum-samples", type=int, default=30)
    parser.add_argument("--capture-seconds", type=float, default=5.0)
    parser.add_argument("--distance-tolerance", type=float, default=0.15)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.interface and args.local_address:
        parser.error("--interface and --local-address are mutually exclusive")
    if args.minimum_samples < 1:
        parser.error("--minimum-samples must be positive")
    if args.capture_seconds <= 0.0:
        parser.error("--capture-seconds must be greater than zero")
    if args.distance_tolerance <= 0.0:
        parser.error("--distance-tolerance must be greater than zero")
    if not math.isfinite(args.stop_distance) or args.stop_distance <= 0.0:
        parser.error("--stop-distance must be finite and greater than zero")
    if not math.isfinite(args.slow_distance) or args.slow_distance <= 0.0:
        parser.error("--slow-distance must be finite and greater than zero")
    if args.stop_distance >= args.slow_distance:
        parser.error("--stop-distance must be less than --slow-distance")
    if args.roi_min_z >= args.roi_max_z:
        parser.error("--roi-min-z must be less than --roi-max-z")
    return args


def summarize_capture(
    *,
    label: str,
    expected_distance: float,
    decisions: list[dict[str, Any]],
    minimum_samples: int,
    config: LidarSafetyConfig,
    distance_tolerance: float,
) -> dict[str, Any]:
    stable = decisions[-minimum_samples:]
    stable_levels = {str(item["level"]) for item in stable}
    stable_level = (
        str(stable[0]["level"])
        if len(stable) >= minimum_samples and len(stable_levels) == 1
        else None
    )
    nearest = [
        float(item["nearest_distance"])
        for item in decisions
        if item.get("nearest_distance") is not None
    ]
    observed = statistics.median(nearest) if nearest else None
    expected = expected_level(expected_distance, config)
    error = observed - expected_distance if observed is not None else None
    level_pass = stable_level == expected
    distance_pass = error is not None and abs(error) <= distance_tolerance
    timestamps = [float(item["received_at"]) for item in decisions]
    frequency = None
    if len(timestamps) > 1 and timestamps[-1] > timestamps[0]:
        frequency = (len(timestamps) - 1) / (timestamps[-1] - timestamps[0])
    return {
        "label": label,
        "expected_distance": expected_distance,
        "expected_level": expected,
        "sample_count": len(decisions),
        "frequency_hz": frequency,
        "stable_window_size": len(stable),
        "stable_level": stable_level,
        "level_counts": dict(Counter(str(item["level"]) for item in decisions)),
        "reason_counts": dict(Counter(str(item["reason"]) for item in decisions)),
        "nearest_distance": {
            "min": min(nearest) if nearest else None,
            "median": observed,
            "max": max(nearest) if nearest else None,
        },
        "distance_error": error,
        "distance_tolerance": distance_tolerance,
        "level_pass": level_pass,
        "distance_pass": distance_pass,
        "verdict": "PASS_LEVEL" if level_pass else "HOLD_LEVEL",
        "motion_calls": 0,
    }


class CalibrationSession:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.config = LidarSafetyConfig(
            stop_distance=args.stop_distance,
            slow_distance=args.slow_distance,
            roi_min_z=args.roi_min_z,
            roi_max_z=args.roi_max_z,
        )
        self.guard = LidarSafetyGuard(self.config)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.started_event = threading.Event()
        self.capture: dict[str, Any] | None = None
        self.latest: dict[str, Any] | None = None
        self.total_frames = 0
        self.decode_errors: list[str] = []
        self.ready_announced = False
        self.startup_error: str | None = None
        self.reports: list[dict[str, Any]] = []
        self.thread = threading.Thread(target=self._reader_loop, daemon=True)

    def start(self) -> None:
        self.thread.start()
        if not self.started_event.wait(timeout=10.0):
            raise RuntimeError("DDS reader startup timed out")
        if self.startup_error:
            raise RuntimeError(self.startup_error)

    def close(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=3.0)

    def begin_capture(self, label: str, expected_distance: float, seconds: float) -> None:
        if not math.isfinite(expected_distance) or expected_distance <= 0.0:
            raise ValueError("expected distance must be finite and positive")
        if not math.isfinite(seconds) or seconds <= 0.0:
            raise ValueError("capture seconds must be finite and positive")
        with self.lock:
            if self.capture is not None:
                raise RuntimeError("a capture is already active")
            self.capture = {
                "label": label,
                "expected_distance": expected_distance,
                "deadline": time.monotonic() + seconds,
                "decisions": [],
            }
        print(
            f"CAPTURE_STARTED label={label} expected={expected_distance:.3f}m seconds={seconds:.1f}",
            flush=True,
        )

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "total_frames": self.total_frames,
                "latest": self.latest,
                "capture_active": None if self.capture is None else self.capture["label"],
                "decode_errors": list(self.decode_errors),
                "motion_calls": 0,
            }

    def _save_reports(self) -> None:
        if self.args.output is None:
            return
        payload = {
            "phase": "7.1-C",
            "mode": "continuous_read_only_static_calibration",
            "topic": self.args.topic,
            "peer": self.args.peer,
            "local_address": self.args.local_address,
            "interface": self.args.interface,
            "guard_config": {
                "stop_distance": self.config.stop_distance,
                "slow_distance": self.config.slow_distance,
                "roi_min_z": self.config.roi_min_z,
                "roi_max_z": self.config.roi_max_z,
                "clear_samples_required": self.config.clear_samples_required,
            },
            "reports": self.reports,
            "read_only": True,
            "motion_calls": 0,
        }
        self.args.output.parent.mkdir(parents=True, exist_ok=True)
        self.args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )

    def _finish_capture_if_due(self, now: float) -> None:
        report = None
        with self.lock:
            if self.capture is None or now < float(self.capture["deadline"]):
                return
            active = self.capture
            self.capture = None
            report = summarize_capture(
                label=str(active["label"]),
                expected_distance=float(active["expected_distance"]),
                decisions=list(active["decisions"]),
                minimum_samples=self.args.minimum_samples,
                config=self.config,
                distance_tolerance=self.args.distance_tolerance,
            )
            self.reports.append(report)
            self._save_reports()
        print("CAPTURE_RESULT " + json.dumps(report, ensure_ascii=False, allow_nan=False), flush=True)

    def _reader_loop(self) -> None:
        try:
            from cyclonedds.domain import Domain, DomainParticipant
            from cyclonedds.sub import DataReader
            from cyclonedds.topic import Topic
            from unitree_sdk2py.idl.sensor_msgs.msg.dds_ import PointCloud2_

            local_address = self.args.local_address
            if not self.args.interface and not local_address:
                local_address = auto_local_address(self.args.peer)
            config_xml = cyclone_config(
                peer=self.args.peer,
                interface=self.args.interface,
                local_address=local_address,
            )
            configured_domain = Domain(self.args.domain, config_xml)
            participant = DomainParticipant(self.args.domain)
            reader = DataReader(
                participant,
                Topic(participant, self.args.topic, PointCloud2_),
            )
            self.started_event.set()
            print(
                f"SESSION_DDS_READY topic={self.args.topic} local={local_address or self.args.interface} read_only=true motion_calls=0",
                flush=True,
            )
            while not self.stop_event.is_set():
                samples = list(reader.take(64) or [])
                now = time.monotonic()
                for sample in samples:
                    received_at = time.monotonic()
                    frame = str(getattr(getattr(sample, "header", None), "frame_id", ""))
                    try:
                        points = decode_xyz(sample)
                        decision = self.guard.update(
                            points,
                            frame_id=frame,
                            sample_monotonic=received_at,
                        )
                    except ValueError as exc:
                        with self.lock:
                            self.decode_errors.append(str(exc))
                        continue
                    item = {
                        "received_at": received_at,
                        "level": decision.level.value,
                        "reason": decision.reason,
                        "nearest_distance": decision.nearest_distance,
                        "roi_point_count": decision.roi_point_count,
                        "frame_id": decision.frame_id,
                    }
                    announce_ready = False
                    with self.lock:
                        self.total_frames += 1
                        self.latest = item
                        if self.capture is not None:
                            self.capture["decisions"].append(item)
                        if not self.ready_announced and decision.level.value == "CLEAR":
                            self.ready_announced = True
                            announce_ready = True
                    if announce_ready:
                        print(
                            "SESSION_CLEAR_READY consecutive_clear_requirement_satisfied=true",
                            flush=True,
                        )
                self._finish_capture_if_due(now)
                if not samples:
                    time.sleep(0.005)
            del configured_domain
        except Exception as exc:
            self.startup_error = str(exc)
            self.started_event.set()
            print(f"SESSION_ERROR {exc}", flush=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    session = CalibrationSession(args)
    try:
        session.start()
        print("COMMANDS: status | capture LABEL EXPECTED_DISTANCE [SECONDS] | quit", flush=True)
        while True:
            try:
                line = input().strip()
            except EOFError:
                break
            if not line:
                continue
            parts = line.split()
            command = parts[0].lower()
            try:
                if command == "status" and len(parts) == 1:
                    print(
                        "SESSION_STATUS "
                        + json.dumps(session.status(), ensure_ascii=False, allow_nan=False),
                        flush=True,
                    )
                elif command == "capture" and len(parts) in (3, 4):
                    seconds = float(parts[3]) if len(parts) == 4 else args.capture_seconds
                    session.begin_capture(parts[1], float(parts[2]), seconds)
                elif command == "quit" and len(parts) == 1:
                    break
                else:
                    print("COMMAND_ERROR invalid command", flush=True)
            except (RuntimeError, ValueError) as exc:
                print(f"COMMAND_ERROR {exc}", flush=True)
    except RuntimeError as exc:
        print(f"SESSION_ERROR {exc}", flush=True)
        return 2
    finally:
        session.close()
    print("SESSION_STOPPED read_only=true motion_calls=0", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
