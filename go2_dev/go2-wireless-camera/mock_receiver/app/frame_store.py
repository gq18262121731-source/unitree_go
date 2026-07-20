from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def latency_ms(captured_at: str) -> float | None:
    try:
        captured = datetime.fromisoformat(captured_at)
    except ValueError:
        return None
    return max(0.0, (datetime.now(timezone.utc).astimezone() - captured).total_seconds() * 1000.0)


@dataclass(frozen=True)
class ReceivedFrame:
    jpeg: bytes
    robot_id: str
    camera_id: str
    frame_seq: int
    captured_at: str
    received_at: str
    width: int
    height: int
    latency_ms: float | None


class ReceiverStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._latest: ReceivedFrame | None = None
        self._metadata: deque[dict[str, Any]] = deque(maxlen=100)
        self._last_heartbeat: dict[str, Any] | None = None
        self.total_frames = 0
        self.rejected_frames = 0
        self._latency_avg = 0.0

    def accept(self, frame: ReceivedFrame) -> None:
        with self._lock:
            self._latest = ReceivedFrame(bytes(frame.jpeg), frame.robot_id, frame.camera_id, frame.frame_seq, frame.captured_at, frame.received_at, frame.width, frame.height, frame.latency_ms)
            self.total_frames += 1
            if frame.latency_ms is not None:
                n = self.total_frames
                self._latency_avg = ((self._latency_avg * (n - 1)) + frame.latency_ms) / n
            self._metadata.append(
                {
                    "robotId": frame.robot_id,
                    "cameraId": frame.camera_id,
                    "frameSeq": frame.frame_seq,
                    "capturedAt": frame.captured_at,
                    "receivedAt": frame.received_at,
                    "width": frame.width,
                    "height": frame.height,
                    "latencyMs": frame.latency_ms,
                }
            )

    def reject(self) -> None:
        with self._lock:
            self.rejected_frames += 1

    def heartbeat(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._last_heartbeat = {"receivedAt": now_iso(), "payload": payload}

    def latest(self) -> ReceivedFrame | None:
        with self._lock:
            return self._latest

    def status(self) -> dict[str, Any]:
        with self._lock:
            latest = self._latest
            last_frame_at = latest.received_at if latest else None
            frame_age_ms = None
            if latest:
                frame_age_ms = latency_ms(latest.received_at)
            return {
                "collectorOnline": self._last_heartbeat is not None,
                "lastHeartbeatAt": self._last_heartbeat["receivedAt"] if self._last_heartbeat else None,
                "lastFrameAt": last_frame_at,
                "frameAgeMs": frame_age_ms,
                "receiveFps": None,
                "totalFrames": self.total_frames,
                "rejectedFrames": self.rejected_frames,
                "lastFrameSeq": latest.frame_seq if latest else None,
                "averageUploadLatencyMs": self._latency_avg,
                "recentFrames": list(self._metadata),
            }
