from __future__ import annotations

import logging
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from typing import Callable, Protocol

import cv2
import numpy as np

from .config import Settings
from .frame_store import Frame, LatestFrameStore, now_iso


class CameraClient(Protocol):
    def get_image_sample(self) -> tuple[int, bytes]:
        ...


class UnitreeVideoClient:
    def __init__(self, network_interface: str, timeout_seconds: float) -> None:
        self.network_interface = network_interface
        self.timeout_seconds = timeout_seconds
        self._client = None

    def initialize(self) -> None:
        from unitree_sdk2py.core.channel import ChannelFactoryInitialize
        from unitree_sdk2py.go2.video.video_client import VideoClient

        ChannelFactoryInitialize(0, self.network_interface)
        client = VideoClient()
        client.SetTimeout(self.timeout_seconds)
        client.Init()
        self._client = client

    def get_image_sample(self) -> tuple[int, bytes]:
        if self._client is None:
            raise RuntimeError("VideoClient is not initialized")
        code, data = self._client.GetImageSample()
        return int(code), bytes(data)


@dataclass
class CollectorStats:
    initialized: bool = False
    running: bool = False
    frame_count: int = 0
    sdk_error_count: int = 0
    reconnect_count: int = 0
    last_error: str | None = None
    last_frame_at: str | None = None
    longest_frame_gap_ms: float = 0.0
    capture_fps: float = 0.0


class CameraCollector:
    def __init__(
        self,
        settings: Settings,
        store: LatestFrameStore,
        client_factory: Callable[[], CameraClient] | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.client_factory = client_factory or self._unitree_factory
        self.stats = CollectorStats()
        self.error_codes: Counter[int] = Counter()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._client: CameraClient | None = None
        self._sample_times: deque[float] = deque(maxlen=30)
        self.logger = logging.getLogger("go2_wireless_camera.collector")

    def _unitree_factory(self) -> CameraClient:
        if not self.settings.network_interface:
            raise ValueError("GO2_NETWORK_INTERFACE is required for real Unitree camera capture")
        client = UnitreeVideoClient(self.settings.network_interface, self.settings.sdk_timeout_seconds)
        client.initialize()
        return client

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self.stats.running = True
            self._thread = threading.Thread(target=self._run, name="go2-wireless-camera", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread:
            thread.join(timeout=2.0)
        with self._lock:
            self.stats.running = False

    def snapshot_stats(self) -> dict:
        with self._lock:
            return {
                "initialized": self.stats.initialized,
                "running": self.stats.running,
                "frameCount": self.stats.frame_count,
                "sdkErrorCount": self.stats.sdk_error_count,
                "sdkErrorCodes": dict(self.error_codes),
                "reconnectCount": self.stats.reconnect_count,
                "lastError": self.stats.last_error,
                "lastFrameAt": self.stats.last_frame_at,
                "longestFrameGapMs": self.stats.longest_frame_gap_ms,
                "captureFps": self.stats.capture_fps,
            }

    def _run(self) -> None:
        delay = self.settings.reconnect_initial_seconds
        consecutive_errors = 0
        while not self._stop.is_set():
            try:
                if self._client is None:
                    self._client = self.client_factory()
                    with self._lock:
                        self.stats.initialized = True
                        self.stats.last_error = None
                    delay = self.settings.reconnect_initial_seconds
                    consecutive_errors = 0

                started = time.monotonic()
                code, jpeg = self._client.get_image_sample()
                latency_ms = (time.monotonic() - started) * 1000.0
                if code != 0:
                    self._record_sdk_error(code, f"SDK returned code {code}")
                    consecutive_errors += 1
                    if consecutive_errors >= 3:
                        self._reconnect(delay)
                        delay = min(self.settings.reconnect_max_seconds, delay * 2)
                        consecutive_errors = 0
                    continue

                width, height = decode_jpeg_size(jpeg)
                now = time.monotonic()
                self._sample_times.append(now)
                capture_fps = self._calculate_fps()
                with self._lock:
                    previous_at = self._sample_times[-2] if len(self._sample_times) > 1 else None
                    if previous_at is not None:
                        self.stats.longest_frame_gap_ms = max(self.stats.longest_frame_gap_ms, (now - previous_at) * 1000.0)
                    self.stats.frame_count += 1
                    frame_seq = self.stats.frame_count
                    self.stats.capture_fps = capture_fps
                    self.stats.last_frame_at = now_iso()
                    self.stats.last_error = None
                frame = Frame(
                    jpeg=jpeg,
                    frame_seq=frame_seq,
                    captured_at=self.stats.last_frame_at or now_iso(),
                    width=width,
                    height=height,
                    frame_size=len(jpeg),
                    sdk_code=code,
                    capture_latency_ms=latency_ms,
                    capture_fps=capture_fps,
                )
                self.store.update(frame, now)
                consecutive_errors = 0
                self._pace_capture(started)
            except Exception as exc:
                self._record_sdk_error(-1, str(exc))
                self._reconnect(delay)
                delay = min(self.settings.reconnect_max_seconds, delay * 2)

    def _record_sdk_error(self, code: int, message: str) -> None:
        with self._lock:
            self.stats.sdk_error_count += 1
            self.error_codes[int(code)] += 1
            self.stats.last_error = message
        self.logger.warning("camera capture error code=%s message=%s", code, message)

    def _reconnect(self, delay: float) -> None:
        with self._lock:
            self.stats.initialized = False
            self.stats.reconnect_count += 1
        self._client = None
        self._stop.wait(delay)

    def _calculate_fps(self) -> float:
        if len(self._sample_times) < 2:
            return 0.0
        elapsed = self._sample_times[-1] - self._sample_times[0]
        return 0.0 if elapsed <= 0 else (len(self._sample_times) - 1) / elapsed

    def _pace_capture(self, started: float) -> None:
        if self.settings.capture_fps <= 0:
            return
        interval = 1.0 / self.settings.capture_fps
        elapsed = time.monotonic() - started
        remaining = interval - elapsed
        if remaining > 0:
            self._stop.wait(remaining)


def decode_jpeg_size(jpeg: bytes) -> tuple[int, int]:
    if len(jpeg) < 4 or not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
        raise ValueError("not a JPEG image")
    array = np.frombuffer(jpeg, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("failed to decode JPEG")
    height, width = image.shape[:2]
    return width, height
