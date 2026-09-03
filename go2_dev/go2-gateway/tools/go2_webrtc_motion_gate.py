"""Minimal staged Go2 WebRTC motion gate.

The default stage is subscriber-only. Motion stages are deliberately bounded
and require explicit operator confirmations. This tool does not integrate with
ScriptedMotionController or Companion; it only validates the transport.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
CONFIRM_WRITER = "WEBRTC_EXCLUSIVE_MOTION_WRITER"
CONFIRM_APP_CLOSED = "UNITREE_APP_CLOSED"
CONFIRM_AREA = "OPEN_AREA_REMOTE_READY"
CONFIRM_FORWARD = "WEBRTC_FORWARD_PULSE_APPROVED"
MAX_FORWARD_SPEED_MPS = 0.23
MAX_FORWARD_DURATION_S = 0.50
STATE_FRESH_SECONDS = 0.75


@dataclass(frozen=True)
class PulseConfig:
    speed_mps: float = MAX_FORWARD_SPEED_MPS
    duration_s: float = 0.40

    def validate(self) -> None:
        if not math.isfinite(self.speed_mps) or not 0.20 <= self.speed_mps <= MAX_FORWARD_SPEED_MPS:
            raise ValueError(
                f"forward speed must be within [0.20, {MAX_FORWARD_SPEED_MPS:.2f}] m/s"
            )
        if not math.isfinite(self.duration_s) or not 0.20 <= self.duration_s <= MAX_FORWARD_DURATION_S:
            raise ValueError(
                f"forward duration must be within [0.20, {MAX_FORWARD_DURATION_S:.2f}] seconds"
            )


def companion_is_idle(path: Path = ROOT / "data" / "companion_lifecycle_state.json") -> tuple[bool, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, f"missing lifecycle state: {path}"
    except (OSError, TypeError, ValueError) as exc:
        return False, f"cannot read lifecycle state: {exc}"
    state = str(payload.get("state") or "UNKNOWN").upper() if isinstance(payload, dict) else "UNKNOWN"
    return state == "IDLE", state


def confirm_motion_stage(stage: str, input_fn: Callable[[str], str] = input) -> None:
    idle, detail = companion_is_idle()
    if not idle:
        raise RuntimeError(f"COMPANION_NOT_CONFIRMED_IDLE: observed={detail}")
    confirmations = [
        CONFIRM_WRITER,
        CONFIRM_APP_CLOSED,
        CONFIRM_AREA,
    ]
    if stage == "forward-pulse":
        confirmations.append(CONFIRM_FORWARD)
    for expected in confirmations:
        if input_fn(f"Type {expected}: ").strip() != expected:
            raise RuntimeError(f"confirmation failed; expected exact text {expected}")


def api_status_code(response: Any) -> int | None:
    if not isinstance(response, dict):
        return None
    try:
        return int(response["data"]["header"]["status"]["code"])
    except (KeyError, TypeError, ValueError):
        return None


def compact_api_response(response: Any) -> dict[str, Any]:
    return {
        "statusCode": api_status_code(response),
        "acknowledged": api_status_code(response) == 0,
    }


def pose_from_state(state: Any) -> dict[str, float] | None:
    if not isinstance(state, dict):
        return None
    try:
        position = state["position"]
        rpy = state["imu_state"]["rpy"]
        pose = {
            "x": float(position[0]),
            "y": float(position[1]),
            "yaw": float(rpy[2]),
        }
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return pose if all(math.isfinite(value) for value in pose.values()) else None


def forward_progress(start: dict[str, float] | None, end: dict[str, float] | None) -> float | None:
    if start is None or end is None:
        return None
    dx = end["x"] - start["x"]
    dy = end["y"] - start["y"]
    return dx * math.cos(start["yaw"]) + dy * math.sin(start["yaw"])


class StateCollector:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.latest_state: dict[str, Any] | None = None
        self.latest_topic: str | None = None
        self.latest_monotonic: float | None = None
        self.first_sample = asyncio.Event()

    def callback(self, topic: str) -> Callable[[Any], None]:
        def receive(message: Any) -> None:
            self.counts[topic] = self.counts.get(topic, 0) + 1
            if isinstance(message, dict) and isinstance(message.get("data"), dict):
                self.latest_state = message["data"]
                self.latest_topic = topic
                self.latest_monotonic = time.monotonic()
                self.first_sample.set()

        return receive

    def is_fresh(self, now: float | None = None) -> bool:
        if self.latest_monotonic is None:
            return False
        return (time.monotonic() if now is None else now) - self.latest_monotonic <= STATE_FRESH_SECONDS


async def request_with_timeout(pub_sub: Any, topic: str, options: dict[str, Any], timeout: float = 2.0) -> Any:
    return await asyncio.wait_for(pub_sub.publish_request_new(topic, options), timeout=timeout)


async def stop_move(pub_sub: Any, sport_topic: str, stop_api_id: int) -> dict[str, Any]:
    response = await request_with_timeout(pub_sub, sport_topic, {"api_id": stop_api_id})
    result = compact_api_response(response)
    if not result["acknowledged"]:
        raise RuntimeError(f"StopMove was not acknowledged: {result}")
    return result


async def run_gate(args: argparse.Namespace) -> dict[str, Any]:
    from app.webrtc.go2_wireless_runtime import Go2WirelessRuntime

    runtime = Go2WirelessRuntime(
        args.robot_ip,
        aes_key=os.environ.get("GO2_AES_KEY", "").strip() or None,
        command_timeout_seconds=2.0,
        connect_timeout_seconds=args.connect_timeout,
        state_timeout_seconds=args.state_timeout,
        state_stale_seconds=STATE_FRESH_SECONDS,
        enable_video=False,
    )
    result: dict[str, Any] = {
        "transport": "WebRTCDataChannel",
        "connectionMode": "LocalSTA",
        "robotIp": args.robot_ip,
        "stage": args.stage,
        "videoEnabled": False,
        "audioEnabled": False,
        "connected": False,
        "sportStateReceived": False,
        "motionCommandsSent": 0,
        "completed": False,
        "reason": None,
    }
    motion_stage = args.stage in {"stop", "forward-pulse"}
    final_stop_attempted = False
    loop = asyncio.get_running_loop()

    async def call(function, *values):
        return await loop.run_in_executor(None, lambda: function(*values))

    try:
        await call(runtime.start)
        result["connected"] = True
        runtime_status = runtime.status()
        result["sportStateReceived"] = True
        result["stateTopic"] = runtime_status["stateTopic"]
        result["startPose"] = pose_from_state(runtime.get_sport_mode_state())

        if args.stage == "readonly":
            await asyncio.sleep(0.25)
            result["completed"] = True
            result["reason"] = "sport_state_received"
            return result

        await call(runtime.stop_motion)
        result["preStop"] = {"statusCode": 0, "acknowledged": True}
        result["motionCommandsSent"] += 1

        await call(runtime.send_move, 0.0, 0.0, 0.0)
        result["zeroVelocity"] = {"statusCode": 0, "acknowledged": True}
        result["motionCommandsSent"] += 1

        await call(runtime.stop_motion)
        result["postZeroStop"] = {"statusCode": 0, "acknowledged": True}
        result["motionCommandsSent"] += 1

        if args.stage == "stop":
            result["completed"] = True
            result["reason"] = "stop_and_zero_acknowledged"
            return result

        if not runtime.status()["sportStateReady"]:
            raise RuntimeError("SportModeState became stale before forward pulse")
        pulse = PulseConfig(args.speed, args.duration)
        pulse.validate()
        result["pulse"] = {
            "speedMps": pulse.speed_mps,
            "durationS": pulse.duration_s,
            "maximumCommandedDistanceM": pulse.speed_mps * pulse.duration_s,
        }
        result["startPose"] = pose_from_state(runtime.get_sport_mode_state())
        await call(runtime.send_move, pulse.speed_mps, 0.0, 0.0)
        result["forwardMove"] = {"statusCode": 0, "acknowledged": True}
        result["motionCommandsSent"] += 1
        await asyncio.sleep(pulse.duration_s)
        await call(runtime.stop_motion)
        result["pulseStop"] = {"statusCode": 0, "acknowledged": True}
        result["motionCommandsSent"] += 1
        final_stop_attempted = True
        await asyncio.sleep(0.50)
        result["endPose"] = pose_from_state(runtime.get_sport_mode_state())
        result["measuredForwardProgressM"] = forward_progress(result["startPose"], result["endPose"])
        result["completed"] = True
        result["reason"] = "forward_pulse_stopped"
        return result
    except asyncio.TimeoutError as exc:
        result["reason"] = "connect_or_state_timeout"
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    except Exception as exc:
        result["reason"] = "gate_failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    finally:
        runtime_status = runtime.status()
        result["stateSampleCounts"] = runtime_status["stateSampleCounts"]
        result["latestStateFresh"] = runtime_status["sportStateReady"]
        if motion_stage and result["connected"] and not final_stop_attempted:
            result["finalStopAttempted"] = True
            try:
                await call(runtime.stop_motion)
                result["finalStop"] = {"statusCode": 0, "acknowledged": True}
                result["motionCommandsSent"] += 1
            except Exception as exc:
                result["finalStop"] = {
                    "acknowledged": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
        try:
            await loop.run_in_executor(None, lambda: runtime.close(send_stop=False))
        except Exception as exc:
            result["disconnectError"] = f"{type(exc).__name__}: {exc}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Staged Go2 wireless WebRTC motion transport gate.")
    parser.add_argument("--robot-ip", default="192.168.8.252")
    parser.add_argument(
        "--stage",
        choices=("readonly", "stop", "forward-pulse"),
        default="readonly",
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--speed", type=float, default=MAX_FORWARD_SPEED_MPS)
    parser.add_argument("--duration", type=float, default=0.40)
    parser.add_argument("--connect-timeout", type=float, default=20.0)
    parser.add_argument("--state-timeout", type=float, default=10.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        PulseConfig(args.speed, args.duration).validate()
    except ValueError as exc:
        print(f"CONFIG_REJECTED: {exc}", file=sys.stderr)
        return 2
    if args.stage != "readonly" and not args.execute:
        print("WEBRTC_MOTION_REJECTED: pass --execute for stop or forward-pulse", file=sys.stderr)
        return 2
    if args.stage != "readonly":
        try:
            confirm_motion_stage(args.stage)
        except RuntimeError as exc:
            print(f"WEBRTC_MOTION_REJECTED: {exc}", file=sys.stderr)
            return 2

    try:
        result = asyncio.run(run_gate(args))
    except KeyboardInterrupt:
        print("WEBRTC_GATE_INTERRUPTED: StopMove cleanup requested", file=sys.stderr)
        return 130
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0 if result.get("completed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
