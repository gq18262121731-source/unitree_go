from __future__ import annotations

"""Subscriber-only Go2 UWB capture probe for Phase 7.1 evidence.

The script creates DDS readers for LowState, UWB state, UWB switch, and the
wireless controller state topic. It emits JSONL rows and never creates a
controller client or any DDS write surface.
"""

import argparse
import json
import math
import socket
import sys
import time
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape


GO2_DEV_ROOT = Path(__file__).resolve().parents[1]
GATEWAY_ROOT = GO2_DEV_ROOT / "go2-gateway"
SDK_ROOT = GO2_DEV_ROOT / "unitree_sdk2_python"
for root in (GATEWAY_ROOT, SDK_ROOT):
    if root.exists() and str(root) not in sys.path:
        sys.path.insert(0, str(root))


TOPIC_LOWSTATE = "rt/lowstate"
TOPIC_UWB_STATE = "rt/uwbstate"
TOPIC_UWB_SWITCH = "rt/uwbswitch"
TOPIC_MULTIPLE_STATE = "rt/multiplestate"


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, allow_nan=False), flush=True)


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


def _finite_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _sample_fields(sample: Any) -> dict[str, Any]:
    fields = (
        "distance_est",
        "yaw_est",
        "pitch_est",
        "orientation_est",
        "tag_roll",
        "tag_pitch",
        "tag_yaw",
        "base_roll",
        "base_pitch",
        "base_yaw",
        "error_state",
        "enabled_from_app",
        "channel",
        "joy_mode",
        "buttons",
    )
    payload: dict[str, Any] = {}
    for field in fields:
        value = getattr(sample, field, None)
        numeric = _finite_or_none(value)
        payload[field] = numeric if numeric is not None else value
    return payload


def _record_topic_sample(
    counters: dict[str, int],
    rates_started_at: float,
    topic: str,
) -> dict[str, Any]:
    counters[topic] = counters.get(topic, 0) + 1
    elapsed = max(time.monotonic() - rates_started_at, 0.0)
    return {
        "topic": topic,
        "sample_count": counters[topic],
        "frequency_hz": (
            round(counters[topic] / elapsed, 3) if elapsed > 0.0 else None
        ),
    }


def _emit_uwb_sample(
    *,
    sequence: int,
    topic: str,
    started_monotonic: float,
    received_monotonic: float,
    sample: Any,
) -> None:
    _emit(
        dict(
            event="uwb_sample",
            timestamp=time.time(),
            sequence=sequence,
            topic=topic,
            receive_monotonic=received_monotonic,
            elapsed_seconds=received_monotonic - started_monotonic,
            sample=_sample_fields(sample),
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--peer", default="192.168.123.161")
    network = parser.add_mutually_exclusive_group(required=True)
    network.add_argument("--interface")
    network.add_argument("--local-address")
    parser.add_argument("--domain", "--domain-id", dest="domain", type=int, default=0)
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--progress-interval", type=float, default=5.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.seconds <= 0.0:
        raise ValueError("--seconds must be greater than zero")

    from cyclonedds.domain import Domain, DomainParticipant
    from cyclonedds.sub import DataReader
    from cyclonedds.topic import Topic
    from unitree_sdk2py.idl.unitree_go.msg.dds_ import (
        LowState_,
        UwbState_,
        UwbSwitch_,
        WirelessController_,
    )

    address = args.local_address or (None if args.interface else _local_address(args.peer))
    configured_domain = Domain(
        args.domain,
        _cyclone_config(peer=args.peer, interface=args.interface, address=address),
    )
    participant = DomainParticipant(args.domain)
    readers = {
        TOPIC_LOWSTATE: DataReader(participant, Topic(participant, TOPIC_LOWSTATE, LowState_)),
        TOPIC_UWB_STATE: DataReader(participant, Topic(participant, TOPIC_UWB_STATE, UwbState_)),
        TOPIC_UWB_SWITCH: DataReader(participant, Topic(participant, TOPIC_UWB_SWITCH, UwbSwitch_)),
        TOPIC_MULTIPLE_STATE: DataReader(
            participant,
            Topic(participant, TOPIC_MULTIPLE_STATE, WirelessController_),
        ),
    }

    counters: dict[str, int] = {}
    uwb_sequence = 0
    last_uwb_receive_monotonic: float | None = None
    maximum_uwb_receive_gap_seconds = 0.0
    started_monotonic = time.monotonic()
    next_progress = started_monotonic + max(args.progress_interval, 0.5)
    _emit(
        {
            "event": "probe_started",
            "domain": args.domain,
            "peer": args.peer,
            "interface": args.interface,
            "local_address": address,
            "topics": list(readers),
            "subscriber_only": True,
            "dds_publishers": 0,
        }
    )

    try:
        deadline = started_monotonic + args.seconds
        while time.monotonic() < deadline:
            for topic, reader in readers.items():
                for sample in reader.take(1000):
                    received_monotonic = time.monotonic()
                    _record_topic_sample(counters, started_monotonic, topic)
                    if topic == TOPIC_UWB_STATE:
                        uwb_sequence += 1
                        if last_uwb_receive_monotonic is not None:
                            maximum_uwb_receive_gap_seconds = max(
                                maximum_uwb_receive_gap_seconds,
                                received_monotonic - last_uwb_receive_monotonic,
                            )
                        last_uwb_receive_monotonic = received_monotonic
                        _emit_uwb_sample(
                            sequence=uwb_sequence,
                            topic=topic,
                            started_monotonic=started_monotonic,
                            received_monotonic=received_monotonic,
                            sample=sample,
                        )
            now = time.monotonic()
            if now >= next_progress:
                _emit(
                    {
                        "event": "progress",
                        "elapsed_seconds": round(now - started_monotonic, 3),
                        "counts": dict(counters),
                        "maximum_uwb_receive_gap_seconds": round(
                            maximum_uwb_receive_gap_seconds, 3
                        ),
                    }
                )
                next_progress = now + max(args.progress_interval, 0.5)
            time.sleep(0.01)
    finally:
        del configured_domain

    result = {
        "event": "probe_result",
        "dds_baseline_ok": counters.get(TOPIC_LOWSTATE, 0) > 0,
        "uwb_writer_discovered": counters.get(TOPIC_UWB_STATE, 0) > 0,
        "uwb_samples_received": uwb_sequence > 0,
        "sample_counts": counters,
        "maximum_uwb_receive_gap_seconds": round(maximum_uwb_receive_gap_seconds, 3),
        "subscriber_only": True,
        "dds_publishers": 0,
    }
    _emit(result)
    return 0 if result["dds_baseline_ok"] and result["uwb_samples_received"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
