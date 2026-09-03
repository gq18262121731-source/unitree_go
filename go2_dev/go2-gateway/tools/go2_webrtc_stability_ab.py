"""Run the bounded, subscriber-only Go2 WebRTC stability matrix.

The probe never sends sport commands and intentionally disables AudioHub. Each
group owns a fresh PeerConnection so track/callback/subscription restoration is
tested instead of hidden by state carried from the previous group.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SDK_ROOT = ROOT.parent / "unitree_webrtc_connect"
if SDK_ROOT.is_dir() and str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.webrtc.go2_wireless_runtime import (
    ExpectedAioiceBindNoiseFilter,
    Go2WirelessRuntime,
    HighFrequencyUnitreeDataLogFilter,
)


@dataclass(frozen=True)
class StabilityProfile:
    name: str
    video: bool
    uwb: bool
    sport: bool
    low_state: bool
    multiple_state: bool
    audio_hub: bool = False


PROFILES: dict[str, StabilityProfile] = {
    "A": StabilityProfile("A", True, True, True, True, True),
    "B": StabilityProfile("B", True, True, True, False, True),
    "C": StabilityProfile("C", False, True, True, False, True),
    "D": StabilityProfile("D", True, False, True, False, True),
}
VIDEO_ONLY_PROFILE = StabilityProfile(
    "V", True, False, False, False, False
)
SELECTABLE_PROFILES = {**PROFILES, "V": VIDEO_ONLY_PROFILE}


class ConsentExpiryCounter(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}
        self._local_signaling_counts: dict[str, int] = {}
        self.active_group: str | None = None

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        with self._lock:
            if self.active_group is None:
                return
            if "Consent to send expired" in message:
                self._counts[self.active_group] = (
                    self._counts.get(self.active_group, 0) + 1
                )
            if "LocalSignalingPortError" in message:
                self._local_signaling_counts[self.active_group] = (
                    self._local_signaling_counts.get(self.active_group, 0) + 1
                )

    def count(self, group: str) -> int:
        with self._lock:
            return self._counts.get(group, 0)

    def local_signaling_count(self, group: str) -> int:
        with self._lock:
            return self._local_signaling_counts.get(group, 0)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _rising(previous: bool, current: bool) -> int:
    return int(current and not previous)


def run_group(
    profile: StabilityProfile,
    *,
    robot_ip: str,
    aes_key: str | None,
    duration_seconds: float,
    sample_interval_seconds: float,
    stale_seconds: float,
    connect_timeout_seconds: float,
    output_dir: Path,
    consent_counter: ConsentExpiryCounter,
) -> dict[str, Any]:
    started_at = _now_iso()
    started_monotonic = time.monotonic()
    runtime = Go2WirelessRuntime(
        robot_ip,
        aes_key=aes_key,
        connect_timeout_seconds=connect_timeout_seconds,
        state_timeout_seconds=connect_timeout_seconds,
        stale_timeout_seconds=stale_seconds,
        reconnect_delay_seconds=2.0,
        reconnect_backoff_step_seconds=2.0,
        reconnect_max_delay_seconds=10.0,
        reconnect_on_multi_signal_stale=False,
        multi_signal_stale_grace_seconds=10.0,
        enable_video=profile.video,
        enable_sport_state=profile.sport,
        enable_uwb=profile.uwb,
        enable_multiple_state=profile.multiple_state,
        enable_low_state=profile.low_state,
        enable_audio=False,
        diagnostic_mode=True,
    )
    samples_path = output_dir / f"group_{profile.name}_samples.jsonl"
    samples_file = samples_path.open("w", encoding="utf-8")
    consent_counter.active_group = profile.name
    online_segment_started: float | None = None
    longest_online = 0.0
    previous_video_stale = False
    previous_sport_stale = False
    previous_uwb_stale = False
    video_stale_episodes = 0
    sport_stale_episodes = 0
    uwb_stale_episodes = 0
    sample_count = 0
    error: str | None = None
    final_status: dict[str, Any] = {}
    try:
        runtime.start()
        deadline = started_monotonic + duration_seconds
        while time.monotonic() < deadline:
            observed_at = time.monotonic()
            status = runtime.status()
            final_status = status
            connected = bool(status.get("connected"))
            if connected and online_segment_started is None:
                online_segment_started = observed_at
            elif not connected and online_segment_started is not None:
                longest_online = max(
                    longest_online, observed_at - online_segment_started
                )
                online_segment_started = None

            video_stale = bool(
                profile.video and status.get("videoDegradedReason")
            )
            sport_stale = status.get("dataDegradedReason") == "sport_state_stale"
            uwb = status.get("uwb") or {}
            video_watchdog = status.get("videoWatchdog") or {}
            connection_age = 0.0
            if online_segment_started is not None:
                connection_age = observed_at - online_segment_started
            uwb_stale = bool(
                profile.uwb
                and connected
                and connection_age >= stale_seconds
                and not uwb.get("fresh", False)
            )
            video_stale_episodes += _rising(previous_video_stale, video_stale)
            sport_stale_episodes += _rising(previous_sport_stale, sport_stale)
            uwb_stale_episodes += _rising(previous_uwb_stale, uwb_stale)
            previous_video_stale = video_stale
            previous_sport_stale = sport_stale
            previous_uwb_stale = uwb_stale

            sample = {
                "observedAt": _now_iso(),
                "elapsedSeconds": observed_at - started_monotonic,
                "connected": connected,
                "connectionState": status.get("connectionState"),
                "peerConnectionState": status.get("peerConnectionState"),
                "iceConnectionState": status.get("iceConnectionState"),
                "reconnectCount": status.get("reconnectCount"),
                "sportStateAgeSeconds": status.get("sportStateAgeSeconds"),
                "rawFrameAgeSeconds": status.get("rawFrameAgeSeconds"),
                "encodedFrameAgeSeconds": status.get("encodedFrameAgeSeconds"),
                "uwbAgeMs": uwb.get("ageMs"),
                "videoStale": video_stale,
                "sportStateStale": sport_stale,
                "uwbStale": uwb_stale,
                "diagnosticReason": status.get("diagnosticReason"),
                "videoWatchdogState": video_watchdog.get("state"),
                "videoStaleCount": video_watchdog.get("video_stale_count"),
                "softRecoveryCount": video_watchdog.get(
                    "soft_recovery_count"
                ),
                "softRecoverySuccessCount": video_watchdog.get(
                    "soft_recovery_success_count"
                ),
                "fullReconnectCount": video_watchdog.get(
                    "full_reconnect_count"
                ),
                "maxRawFrameAgeMs": video_watchdog.get(
                    "max_raw_frame_age_ms"
                ),
                "maxRecoveryDurationMs": video_watchdog.get(
                    "max_recovery_duration_ms"
                ),
                "falseRecoveryCount": video_watchdog.get(
                    "false_recovery_count"
                ),
                "unrecoveredVideoStale": video_watchdog.get(
                    "unrecovered_video_stale"
                ),
            }
            samples_file.write(json.dumps(sample, ensure_ascii=False) + "\n")
            samples_file.flush()
            sample_count += 1
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(sample_interval_seconds, remaining))
    except KeyboardInterrupt:
        error = "KeyboardInterrupt"
        raise
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        observed_at = time.monotonic()
        if online_segment_started is not None:
            longest_online = max(
                longest_online, observed_at - online_segment_started
            )
        if not final_status:
            try:
                final_status = runtime.status()
            except Exception:
                final_status = {}
        runtime.close(send_stop=False)
        consent_counter.active_group = None
        samples_file.close()

    recent_disconnects = final_status.get("recentDisconnects") or []
    final_video_watchdog = final_status.get("videoWatchdog") or {}
    result = {
        "group": profile.name,
        "profile": asdict(profile),
        "startedAt": started_at,
        "finishedAt": _now_iso(),
        "requestedDurationSeconds": duration_seconds,
        "observedDurationSeconds": time.monotonic() - started_monotonic,
        "sampleCount": sample_count,
        "reconnectCount": int(final_status.get("reconnectCount") or 0),
        "disconnectCount": int(final_status.get("disconnectCount") or 0),
        "consentExpiredCount": consent_counter.count(profile.name),
        "localSignalingPortErrorCount": (
            consent_counter.local_signaling_count(profile.name)
        ),
        "sportStateStaleEpisodes": sport_stale_episodes,
        "videoStaleEpisodes": video_stale_episodes,
        "videoStaleCount": int(
            final_video_watchdog.get("video_stale_count") or 0
        ),
        "softRecoveryCount": int(
            final_video_watchdog.get("soft_recovery_count") or 0
        ),
        "softRecoverySuccessCount": int(
            final_video_watchdog.get("soft_recovery_success_count") or 0
        ),
        "fullReconnectCount": int(
            final_video_watchdog.get("full_reconnect_count") or 0
        ),
        "maxRawFrameAgeMs": float(
            final_video_watchdog.get("max_raw_frame_age_ms") or 0.0
        ),
        "maxRecoveryDurationMs": float(
            final_video_watchdog.get("max_recovery_duration_ms") or 0.0
        ),
        "falseRecoveryCount": int(
            final_video_watchdog.get("false_recovery_count") or 0
        ),
        "unrecoveredVideoStale": int(
            final_video_watchdog.get("unrecovered_video_stale") or 0
        ),
        "uwbStaleEpisodes": uwb_stale_episodes,
        "longestContinuousOnlineSeconds": longest_online,
        "lastDisconnectReason": final_status.get("lastDisconnectReason"),
        "lastDiagnosticReason": final_status.get("diagnosticReason"),
        "recentDisconnects": recent_disconnects,
        "subscribedTopics": (
            (final_status.get("subscriptionProfile") or {}).get("topics") or []
        ),
        "samplesPath": str(samples_path.resolve()),
        "error": error,
    }
    _write_json(output_dir / f"group_{profile.name}_result.json", result)
    return result


def _parse_groups(value: str) -> list[str]:
    groups = [item.strip().upper() for item in value.split(",") if item.strip()]
    unknown = [item for item in groups if item not in SELECTABLE_PROFILES]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown groups: {', '.join(unknown)}; expected A,B,C,D,V"
        )
    return list(dict.fromkeys(groups))


def _preflight_sdk() -> None:
    try:
        import aioice  # noqa: F401
        import aiortc  # noqa: F401
        import unitree_webrtc_connect  # noqa: F401
    except ModuleNotFoundError as exc:
        launcher = ROOT / "scripts" / "Start-Go2WebRTCStabilityAB.ps1"
        raise RuntimeError(
            f"missing WebRTC dependency {exc.name!r} in {sys.executable}. "
            f"Run: & '{launcher}'"
        ) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Subscriber-only Go2 WebRTC A/B probe with optional V video-only mode"
        )
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--robot-ip",
        default=os.environ.get("UNITREE_ROBOT_IP")
        or os.environ.get("GO2_ROBOT_IP")
        or "192.168.8.252",
    )
    parser.add_argument(
        "--aes-key", default=os.environ.get("GO2_AES_KEY", "").strip() or None
    )
    parser.add_argument("--groups", type=_parse_groups, default=["A", "B", "C", "D"])
    parser.add_argument("--duration-seconds", type=float, default=600.0)
    parser.add_argument("--sample-interval-seconds", type=float, default=0.5)
    parser.add_argument("--stale-seconds", type=float, default=3.0)
    parser.add_argument("--connect-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--cooldown-seconds", type=float, default=5.0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data" / "webrtc_stability",
    )
    args = parser.parse_args(argv)
    if not args.execute:
        parser.error("pass --execute to contact the robot")
    for name in (
        "duration_seconds",
        "sample_interval_seconds",
        "stale_seconds",
        "connect_timeout_seconds",
    ):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.cooldown_seconds < 0:
        parser.error("--cooldown-seconds must be non-negative")
    try:
        _preflight_sdk()
    except RuntimeError as exc:
        parser.error(str(exc))

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_dir.resolve() / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("aioice.ice").addFilter(ExpectedAioiceBindNoiseFilter())
    logging.getLogger("aiortc.codecs.h264").setLevel(logging.ERROR)
    protocol_filter = HighFrequencyUnitreeDataLogFilter()
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.addFilter(protocol_filter)
    run_log_path = output_dir / "run.log"
    run_log_handler = logging.FileHandler(run_log_path, encoding="utf-8")
    run_log_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    run_log_handler.addFilter(protocol_filter)
    root_logger.addHandler(run_log_handler)
    consent_counter = ConsentExpiryCounter()
    root_logger.addHandler(consent_counter)

    results: list[dict[str, Any]] = []
    interrupted = False
    try:
        for index, group in enumerate(args.groups):
            profile = SELECTABLE_PROFILES[group]
            print(
                f"WEBRTC_STABILITY_GROUP_START group={group} "
                f"duration_seconds={args.duration_seconds}",
                flush=True,
            )
            try:
                result = run_group(
                    profile,
                    robot_ip=args.robot_ip,
                    aes_key=args.aes_key,
                    duration_seconds=args.duration_seconds,
                    sample_interval_seconds=args.sample_interval_seconds,
                    stale_seconds=args.stale_seconds,
                    connect_timeout_seconds=args.connect_timeout_seconds,
                    output_dir=output_dir,
                    consent_counter=consent_counter,
                )
            except KeyboardInterrupt:
                interrupted = True
                break
            results.append(result)
            print(
                "WEBRTC_STABILITY_GROUP_DONE "
                + json.dumps(result, ensure_ascii=False),
                flush=True,
            )
            if result["sampleCount"] == 0 and result["error"]:
                print(
                    "WEBRTC_STABILITY_ABORTED reason=group_initialization_failed "
                    f"group={group} error={result['error']}",
                    file=sys.stderr,
                    flush=True,
                )
                break
            if index + 1 < len(args.groups) and args.cooldown_seconds:
                time.sleep(args.cooldown_seconds)
    finally:
        root_logger.removeHandler(consent_counter)
        root_logger.removeHandler(run_log_handler)
        run_log_handler.close()

    summary = {
        "runId": run_id,
        "robotIp": args.robot_ip,
        "startedGroups": args.groups[: len(results)],
        "interrupted": interrupted,
        "configuration": {
            "durationSecondsPerGroup": args.duration_seconds,
            "sampleIntervalSeconds": args.sample_interval_seconds,
            "staleSeconds": args.stale_seconds,
            "cooldownSeconds": args.cooldown_seconds,
            "audioHub": False,
            "followController": False,
            "reconnectPolicy": "hard_transport_only",
            "reconnectBackoffSeconds": [2, 4, 6, 8, 10],
        },
        "runLogPath": str(run_log_path.resolve()),
        "results": results,
    }
    summary_path = output_dir / "summary.json"
    _write_json(summary_path, summary)
    print(f"WEBRTC_STABILITY_SUMMARY path={summary_path.resolve()}", flush=True)
    return 130 if interrupted else int(any(item["error"] for item in results))


if __name__ == "__main__":
    raise SystemExit(main())
