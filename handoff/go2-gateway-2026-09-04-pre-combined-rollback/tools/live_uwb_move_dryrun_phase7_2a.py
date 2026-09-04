from __future__ import annotations

"""Future DDS-reader-only UWB-to-command preview for Phase 7.2-A.

The tool is intentionally gated and is not part of the current offline run.
It computes planner/controller/arbiter output and always reports executed
velocity as zero.
"""

import argparse
import json
import math
import os
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.control_owner import ControlOwner
from app.follow import (
    FollowController,
    FollowControllerConfig,
    FollowOffset,
    FollowTargetPlanner,
    UwbBearingSource,
    UwbBearingUnit,
    UwbInputConfig,
    UwbInputValidator,
)
from app.motion.arbiter import MotionArbiter, MotionArbiterConfig
from app.motion.lidar_safety import LidarSafetyConfig, LidarSafetyGuard, LidarSafetyLevel


TOPIC_LOWSTATE = "rt/lowstate"
TOPIC_UWB_STATE = "rt/uwbstate"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--peer", default="192.168.123.161")
    network = parser.add_mutually_exclusive_group()
    network.add_argument("--interface")
    network.add_argument("--local-address")
    parser.add_argument("--domain", type=int, default=0)
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument(
        "--simulated-lidar-state",
        choices=[item.value for item in LidarSafetyLevel],
        default=LidarSafetyLevel.CLEAR.value,
    )
    parser.add_argument(
        "--confirm-readonly-live",
        action="store_true",
        help="explicitly allow future DDS-reader startup after Go2 is powered",
    )
    return parser


def _local_address(peer: str) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((peer, 7400))
        return str(probe.getsockname()[0])


def _cyclone_config(*, peer: str, interface: str | None, address: str | None) -> str:
    selector = (
        f'name="{escape(interface)}"'
        if interface
        else f'address="{escape(address or _local_address(peer))}"'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<CycloneDDS><Domain Id="any"><General><Interfaces>'
        f'<NetworkInterface {selector} priority="default" multicast="default"/>'
        '</Interfaces></General><Discovery><Peers>'
        f'<Peer Address="{escape(peer)}"/>'
        '</Peers></Discovery></Domain></CycloneDDS>'
    )


def _finite(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("UWB value must be finite")
    return result


def _clear_cloud() -> list[tuple[float, float, float]]:
    return [(2.5, -1.0 + index * 0.1, 0.0) for index in range(21)]


def _lidar_cloud(level: LidarSafetyLevel) -> list[tuple[float, float, float]]:
    points = _clear_cloud()
    distance = 1.0 if level is LidarSafetyLevel.SLOW else 0.6
    if level is not LidarSafetyLevel.CLEAR:
        points.extend(
            [(distance, -0.05, 0.0), (distance, 0.0, 0.0), (distance, 0.05, 0.0)]
        )
    return points


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, allow_nan=False), flush=True)


def main(argv: list[str] | None = None) -> int:
    print("PHASE 7.2-A LIVE DRY-RUN", flush=True)
    print("REAL MOTION DISABLED", flush=True)
    print("DDS READERS ONLY", flush=True)
    args = build_parser().parse_args(argv)
    if os.getenv("PHASE7_MOTION_EXECUTION_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        print("REFUSED: PHASE7_MOTION_EXECUTION_ENABLED must be false", flush=True)
        return 3
    if not args.confirm_readonly_live:
        print("WAITING_FOR_ROBOT: pass --confirm-readonly-live only after Go2 is powered", flush=True)
        return 4
    if args.seconds <= 0.0:
        raise ValueError("--seconds must be greater than zero")

    from cyclonedds.domain import Domain, DomainParticipant
    from cyclonedds.sub import DataReader
    from cyclonedds.topic import Topic
    from unitree_sdk2py.idl.unitree_go.msg.dds_ import LowState_, UwbState_

    address = args.local_address or (None if args.interface else _local_address(args.peer))
    configured_domain = Domain(
        args.domain,
        _cyclone_config(peer=args.peer, interface=args.interface, address=address),
    )
    participant = DomainParticipant(args.domain)
    low_reader = DataReader(participant, Topic(participant, TOPIC_LOWSTATE, LowState_))
    uwb_reader = DataReader(participant, Topic(participant, TOPIC_UWB_STATE, UwbState_))

    validator = UwbInputValidator(
        UwbInputConfig(
            bearing_source=UwbBearingSource.ORIENTATION_EST,
            bearing_unit=UwbBearingUnit.RADIANS,
            bearing_sign=1,
            bearing_zero_offset_rad=0.55,
            calibration_confirmed=True,
        )
    )
    planner = FollowTargetPlanner(FollowOffset(back_distance=1.5, right_offset=0.5))
    controller = FollowController(
        FollowControllerConfig(simulation_mode=False, velocity_feedforward_enabled=False)
    )
    arbiter = MotionArbiter(
        MotionArbiterConfig(require_external_risk_feed=True)
    )
    lidar_guard = LidarSafetyGuard(
        LidarSafetyConfig(
            slow_distance=1.40,
            stop_distance=0.80,
            roi_min_z=-0.25,
            clear_samples_required=1,
        )
    )
    lidar_level = LidarSafetyLevel(args.simulated_lidar_state)
    risk_timestamp = datetime(2026, 8, 23, tzinfo=timezone.utc)
    started = time.monotonic()
    sequence = 0
    lowstate_count = 0
    try:
        while time.monotonic() - started < args.seconds:
            lowstate_count += len(low_reader.take(1000))
            for sample in uwb_reader.take(1000):
                sequence += 1
                now = time.monotonic()
                observation = validator.normalize(
                    distance_est=_finite(sample.distance_est),
                    orientation_est=_finite(sample.orientation_est),
                    sample_monotonic=now,
                    enabled_from_app=int(sample.enabled_from_app),
                    error_state=int(sample.error_state),
                )
                plan = planner.process_measurement(
                    observation.distance_metres,
                    observation.bearing_radians,
                    sample_monotonic=now,
                )
                candidate = controller.calculate_velocity(
                    plan,
                    control_owner=ControlOwner.FOLLOW,
                    measurement_age_seconds=0.0,
                    sample_monotonic=now,
                )
                arbiter.ingest_risk_event(
                    {
                        "event_type": "NON_FALL",
                        "timestamp": (risk_timestamp + timedelta(microseconds=sequence)).isoformat(),
                    },
                    received_monotonic=now,
                )
                lidar = lidar_guard.update(
                    _lidar_cloud(lidar_level),
                    frame_id="base_link",
                    sample_monotonic=now,
                )
                decision = arbiter.decide(
                    follow_command=candidate,
                    uwb_age_seconds=0.0,
                    lidar=lidar,
                    now_monotonic=now,
                )
                _emit(
                    {
                        "sequence": sequence,
                        "timestamp": time.time(),
                        "uwb_distance": observation.distance_metres,
                        "uwb_bearing": observation.bearing_radians,
                        "follow_target_x": plan.target_x,
                        "follow_target_y": plan.target_y,
                        "candidate_vx": candidate.vx,
                        "candidate_vy": candidate.vy,
                        "candidate_wz": candidate.wz,
                        "lidar_state": lidar.level.value,
                        "arbiter_authority": decision.authority.value,
                        "final_vx": decision.vx,
                        "final_vy": decision.vy,
                        "final_wz": decision.wz,
                        "execution_enabled": False,
                        "executed_vx": 0.0,
                        "executed_vy": 0.0,
                        "executed_wz": 0.0,
                        "reason": decision.reason,
                    }
                )
            time.sleep(0.01)
    finally:
        del configured_domain
    _emit(
        {
            "event": "live_dryrun_complete",
            "lowstate_count": lowstate_count,
            "uwb_sample_count": sequence,
            "execution_enabled": False,
            "motion_calls": 0,
            "dds_publishers": 0,
        }
    )
    return 0 if lowstate_count > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
