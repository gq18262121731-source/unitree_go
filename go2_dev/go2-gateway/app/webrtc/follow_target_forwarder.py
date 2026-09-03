from __future__ import annotations

import json
import logging
import math
import socket
import threading
import time
from dataclasses import asdict, dataclass, replace
from typing import Any, Callable, Protocol


LOGGER = logging.getLogger(__name__)
SCHEMA_VERSION = "go2_follow_target.v1"


@dataclass(frozen=True)
class FollowTargetState:
    schema_version: str = SCHEMA_VERSION
    sequence: int = 0
    target_valid: bool = False
    source_connected: bool = False
    follow_active: bool = False
    monitoring_active: bool = False
    bearing_deg: float | None = None
    distance_m: float | None = None
    relative_x_m: float | None = None
    relative_y_m: float | None = None
    source_timestamp_ms: int = 0
    sent_timestamp_ms: int = 0

    def to_payload(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class FollowTargetForwardConfig:
    enabled: bool = False
    host: str = ""
    port: int = 8766
    hz: float = 20.0
    stale_seconds: float = 1.0
    stats_interval_seconds: float = 10.0
    verbose: bool = False

    def __post_init__(self) -> None:
        if self.enabled and not self.host.strip():
            raise ValueError(
                "FOLLOW_TARGET_FORWARD_HOST is required when forwarding is enabled"
            )
        if not 1 <= int(self.port) <= 65535:
            raise ValueError("FOLLOW_TARGET_FORWARD_PORT must be in [1, 65535]")
        if not math.isfinite(self.hz) or not 1.0 <= self.hz <= 100.0:
            raise ValueError("FOLLOW_TARGET_FORWARD_HZ must be in [1, 100]")
        if not math.isfinite(self.stale_seconds) or self.stale_seconds <= 0.0:
            raise ValueError("FOLLOW_TARGET_FORWARD_STALE_SECONDS must be positive")
        if (
            not math.isfinite(self.stats_interval_seconds)
            or self.stats_interval_seconds < 1.0
        ):
            raise ValueError(
                "FOLLOW_TARGET_FORWARD_STATS_INTERVAL_SECONDS must be at least 1"
            )


class WirelessRuntime(Protocol):
    def get_uwb_snapshot(self) -> dict[str, Any]: ...

    def status(self) -> dict[str, Any]: ...


class FollowTargetStateSource(Protocol):
    def current_state(self) -> FollowTargetState: ...


class Go2UwbFollowTargetSource:
    """Map the existing WebRTC UWB subscription into the forwarding schema.

    This adapter never opens a Go2 connection. It reads only the coherent latest
    snapshot already maintained by ``Go2WirelessRuntime``.
    """

    def __init__(
        self,
        runtime: WirelessRuntime,
        *,
        bearing_sign: int,
        bearing_zero_offset_rad: float,
        stale_seconds: float,
        allow_missing_error_state: bool,
        monitoring_active: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if bearing_sign not in {-1, 1}:
            raise ValueError("bearing_sign must be -1 or 1")
        if not math.isfinite(bearing_zero_offset_rad):
            raise ValueError("bearing_zero_offset_rad must be finite")
        self.runtime = runtime
        self.bearing_sign = bearing_sign
        self.bearing_zero_offset_rad = float(bearing_zero_offset_rad)
        self.stale_seconds = float(stale_seconds)
        self.allow_missing_error_state = bool(allow_missing_error_state)
        self._clock = clock
        self._lock = threading.Lock()
        self._follow_active = False
        self._monitoring_active = bool(monitoring_active)

    def set_follow_active(self, active: bool) -> None:
        with self._lock:
            self._follow_active = bool(active)

    def is_follow_active(self) -> bool:
        with self._lock:
            return self._follow_active

    def set_monitoring_active(self, active: bool) -> None:
        with self._lock:
            self._monitoring_active = bool(active)

    def is_monitoring_active(self) -> bool:
        with self._lock:
            return self._monitoring_active

    def current_state(self) -> FollowTargetState:
        return self._state_from_snapshot(
            self.runtime.get_uwb_snapshot(),
            self.runtime.status(),
        )

    def current_state_from_runtime_status(
        self, status: dict[str, Any]
    ) -> FollowTargetState:
        """Validate an already-coherent lightweight Runtime status read."""

        uwb = status.get("uwb")
        if not isinstance(uwb, dict):
            uwb = {}
        return self._state_from_snapshot(
            {
                "fields": uwb.get("fields"),
                "received_monotonic": uwb.get("receivedMonotonic"),
                "received_timestamp_ms": uwb.get("receivedTimestampMs"),
                "sample_count": uwb.get("sampleCount"),
                "source_keys": uwb.get("sourceKeys"),
                "topic": uwb.get("topic"),
            },
            status,
        )

    def _state_from_snapshot(
        self,
        snapshot: dict[str, Any],
        status: dict[str, Any],
    ) -> FollowTargetState:
        with self._lock:
            follow_active = self._follow_active
            monitoring_active = self._monitoring_active
        timestamp_ms = self._source_timestamp_ms(snapshot)
        source_connected = bool(
            status.get("connected") and status.get("connectionCount") == 1
        )
        invalid = FollowTargetState(
            source_connected=source_connected,
            follow_active=follow_active,
            monitoring_active=monitoring_active,
            source_timestamp_ms=timestamp_ms,
        )

        if not monitoring_active:
            return invalid
        if not status.get("connected") or status.get("connectionCount") != 1:
            return invalid

        fields = snapshot.get("fields")
        received = snapshot.get("received_monotonic")
        if not isinstance(fields, dict) or received is None:
            return invalid
        try:
            age = self._clock() - float(received)
            distance = float(fields["distance_est"])
            raw_orientation = float(fields["orientation_est"])
            enabled = int(fields["enabled_from_app"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return invalid
        if (
            not math.isfinite(age)
            or age < 0.0
            or age >= self.stale_seconds
            or not math.isfinite(distance)
            or distance < 0.0
            or not math.isfinite(raw_orientation)
            or enabled != 1
        ):
            return invalid

        error_state = fields.get("error_state")
        source_keys = {str(key) for key in snapshot.get("source_keys") or ()}
        if error_state is None:
            if (
                not self.allow_missing_error_state
                or "error_state" in source_keys
                or "errorState" in source_keys
            ):
                return invalid
        else:
            try:
                if int(error_state) != 0:
                    return invalid
            except (TypeError, ValueError, OverflowError):
                return invalid

        # Existing Phase 7.1 calibration produces left-positive/right-negative
        # robot bearing. The forwarding contract is the opposite: left is
        # negative and right is positive, hence the explicit final negation.
        calibrated = self.bearing_sign * (
            raw_orientation + self.bearing_zero_offset_rad
        )
        calibrated = math.atan2(math.sin(calibrated), math.cos(calibrated))
        bearing_deg = -math.degrees(calibrated)
        if not math.isfinite(bearing_deg):
            return invalid

        return FollowTargetState(
            target_valid=True,
            source_connected=True,
            follow_active=follow_active,
            monitoring_active=True,
            bearing_deg=bearing_deg,
            distance_m=distance,
            # rt/uwbstate on this robot does not expose real relative X/Y.
            relative_x_m=None,
            relative_y_m=None,
            source_timestamp_ms=timestamp_ms,
        )

    @staticmethod
    def _source_timestamp_ms(snapshot: dict[str, Any]) -> int:
        try:
            value = int(snapshot.get("received_timestamp_ms") or 0)
        except (TypeError, ValueError, OverflowError):
            return 0
        return max(0, value)


SocketFactory = Callable[..., socket.socket]


class UdpFollowTargetForwarder:
    """Latest-only, non-blocking UDP sender with no history queue."""

    def __init__(
        self,
        config: FollowTargetForwardConfig,
        source: FollowTargetStateSource,
        *,
        socket_factory: SocketFactory = socket.socket,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.source = source
        self._socket_factory = socket_factory
        self._clock = clock
        self._wall_clock = wall_clock
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._sequence = 0
        self._send_count = 0
        self._send_error_count = 0
        self._last_state: FollowTargetState | None = None
        self._last_valid: bool | None = None
        self._network_failed = False
        self._source_failed = False
        self._started_monotonic: float | None = None

    def start(self) -> None:
        if not self.config.enabled:
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._started_monotonic = self._clock()
            self._thread = threading.Thread(
                target=self._run,
                name="go2-follow-target-udp",
                daemon=True,
            )
            self._thread.start()
        LOGGER.info(
            "Follow target UDP forwarding started: %s:%d at %.1f Hz",
            self.config.host,
            self.config.port,
            self.config.hz,
        )

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._close_socket()
        with self._lock:
            self._thread = None
        if self.config.enabled:
            LOGGER.info("Follow target UDP forwarding stopped")

    def send_once(self) -> FollowTargetState:
        try:
            state = self.source.current_state()
        except Exception as exc:
            with self._lock:
                first_source_failure = not self._source_failed
                self._source_failed = True
            if first_source_failure:
                LOGGER.warning("Follow target Go2 data source error: %s", exc)
            state = FollowTargetState(target_valid=False, follow_active=False)
        else:
            with self._lock:
                source_recovered = self._source_failed
                self._source_failed = False
            if source_recovered:
                LOGGER.info("Follow target Go2 data source recovered")
        sent_timestamp_ms = int(self._wall_clock() * 1000.0)
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        state = replace(
            state,
            schema_version=SCHEMA_VERSION,
            sequence=sequence,
            sent_timestamp_ms=sent_timestamp_ms,
        )
        payload = json.dumps(
            state.to_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        try:
            udp_socket = self._ensure_socket()
            udp_socket.sendto(payload, (self.config.host, self.config.port))
        except (BlockingIOError, OSError) as exc:
            with self._lock:
                self._send_error_count += 1
                first_failure = not self._network_failed
                self._network_failed = True
            self._close_socket()
            if first_failure:
                LOGGER.warning("Follow target UDP network error: %s", exc)
        else:
            with self._lock:
                recovered = self._network_failed
                self._network_failed = False
                self._send_count += 1
            if recovered:
                LOGGER.info("Follow target UDP network recovered")
        with self._lock:
            previous_valid = self._last_valid
            self._last_valid = state.target_valid
            self._last_state = state
        if previous_valid is not state.target_valid:
            if state.target_valid:
                LOGGER.info("UWB_STATE: STALE -> FRESH target_valid=true")
            elif previous_valid is True:
                LOGGER.info("UWB_STATE: FRESH -> STALE target_valid=false")
        return state

    def debug_status(self) -> dict[str, object]:
        with self._lock:
            state = self._last_state
            started = self._started_monotonic
            thread = self._thread
            sends = self._send_count
            errors = self._send_error_count
            sequence = self._sequence
        if state is None:
            try:
                state = replace(self.source.current_state(), sequence=sequence)
            except Exception:
                state = FollowTargetState(sequence=sequence)
        now_ms = int(self._wall_clock() * 1000.0)
        age_ms = (
            None
            if state is None or state.source_timestamp_ms <= 0
            else max(0, now_ms - state.source_timestamp_ms)
        )
        elapsed = 0.0 if started is None else max(0.0, self._clock() - started)
        return {
            "enabled": self.config.enabled,
            "running": bool(thread is not None and thread.is_alive()),
            "destination": (
                None
                if not self.config.enabled
                else {"host": self.config.host, "port": self.config.port}
            ),
            "configured_hz": self.config.hz,
            "actual_send_hz": 0.0 if elapsed <= 0.0 else sends / elapsed,
            "latest_sequence": sequence,
            "send_count": sends,
            "send_error_count": errors,
            "state_age_ms": age_ms,
            "state": None if state is None else state.to_payload(),
        }

    def _run(self) -> None:
        period = 1.0 / self.config.hz
        next_tick = self._clock()
        next_stats = next_tick + self.config.stats_interval_seconds
        while not self._stop.is_set():
            self.send_once()
            now = self._clock()
            if self.config.verbose and now >= next_stats:
                status = self.debug_status()
                state = status.get("state") or {}
                LOGGER.info(
                    "Follow target UDP stats: send_hz=%.1f sequence=%s "
                    "valid=%s bearing=%s distance=%s errors=%s",
                    status["actual_send_hz"],
                    status["latest_sequence"],
                    state.get("target_valid"),
                    state.get("bearing_deg"),
                    state.get("distance_m"),
                    status["send_error_count"],
                )
                next_stats = now + self.config.stats_interval_seconds
            next_tick += period
            delay = max(0.0, next_tick - self._clock())
            if delay == 0.0:
                next_tick = self._clock()
            self._stop.wait(delay)

    def _ensure_socket(self) -> socket.socket:
        with self._lock:
            if self._socket is None:
                udp_socket = self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
                udp_socket.setblocking(False)
                self._socket = udp_socket
            return self._socket

    def _close_socket(self) -> None:
        with self._lock:
            udp_socket = self._socket
            self._socket = None
        if udp_socket is not None:
            try:
                udp_socket.close()
            except OSError:
                pass
