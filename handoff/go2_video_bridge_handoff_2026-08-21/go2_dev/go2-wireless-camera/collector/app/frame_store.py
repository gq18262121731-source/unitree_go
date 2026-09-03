from __future__ import annotations

import threading
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class Frame:
    jpeg: bytes
    frame_seq: int
    captured_at: str
    width: int
    height: int
    frame_size: int
    sdk_code: int
    capture_latency_ms: float
    capture_fps: float

    def metadata(self) -> dict[str, Any]:
        return {
            "frameSeq": self.frame_seq,
            "capturedAt": self.captured_at,
            "width": self.width,
            "height": self.height,
            "frameSize": self.frame_size,
            "sdkCode": self.sdk_code,
            "captureLatencyMs": self.capture_latency_ms,
            "captureFps": self.capture_fps,
        }


class LatestFrameStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._frame: Frame | None = None
        self._last_update_monotonic: float | None = None

    def update(self, frame: Frame, monotonic_time: float) -> None:
        with self._lock:
            self._frame = Frame(bytes(frame.jpeg), frame.frame_seq, frame.captured_at, frame.width, frame.height, frame.frame_size, frame.sdk_code, frame.capture_latency_ms, frame.capture_fps)
            self._last_update_monotonic = monotonic_time

    def latest(self) -> Frame | None:
        with self._lock:
            if self._frame is None:
                return None
            frame = self._frame
            return Frame(bytes(frame.jpeg), frame.frame_seq, frame.captured_at, frame.width, frame.height, frame.frame_size, frame.sdk_code, frame.capture_latency_ms, frame.capture_fps)

    def status(self, stale_seconds: float, monotonic_time: float) -> dict[str, Any]:
        with self._lock:
            frame = self._frame
            age_ms = None
            stale = True
            if frame is not None and self._last_update_monotonic is not None:
                age_ms = max(0.0, (monotonic_time - self._last_update_monotonic) * 1000.0)
                stale = age_ms > stale_seconds * 1000.0
            return {
                "hasFrame": frame is not None and not stale,
                "frameAgeMs": age_ms,
                "latestFrame": deepcopy(frame.metadata()) if frame else None,
            }
