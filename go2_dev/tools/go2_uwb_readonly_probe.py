#!/usr/bin/env python3
"""Read-only Go2 UWB DDS probe.

The probe creates DDS readers only. It does not create a publisher, call the
UWB switch API, start Follow mode, or send any robot motion command.
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import sys
import time
from dataclasses import asdict
from typing import Any
from xml.sax.saxutils import escape

try:
    from cyclonedds.builtin import BuiltinDataReader, BuiltinTopicDcpsPublication
    from cyclonedds.domain import Domain, DomainParticipant
    from cyclonedds.sub import DataReader
    from cyclonedds.topic import Topic
    from unitree_sdk2py.idl.std_msgs.msg.dds_ import String_
    from unitree_sdk2py.idl.unitree_go.msg.dds_ import (
        LowState_,
        UwbState_,
        UwbSwitch_,
    )
except ImportError as exc:
    raise SystemExit(
        "Missing DDS dependencies. Activate the Unitree SDK2 Python environment "
        "and ensure unitree_sdk2py plus cyclonedds are importable. "
        f"Original error: {exc}"
    ) from exc


TOPIC_LOWSTATE = "rt/lowstate"
TOPIC_UWB_STATE = "rt/uwbstate"
TOPIC_UWB_SWITCH = "rt/uwbswitch"
TOPIC_MULTIPLE_STATE = "rt/multiplestate"


def emit(event: str, **fields: Any) -> None:
    print(
        json.dumps(
            {"event": event, "timestamp": time.time(), **fields},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ),
        flush=True,
    )


def finite_float(value: Any) -> float | None:
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    return converted if math.isfinite(converted) else None


def auto_local_address(peer: str) -> str:
    """Resolve the local IPv4 address selected by the OS route to the peer."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((peer, 7400))
        return str(probe.getsockname()[0])


def cyclone_config(
    *,
    peer: str,
    interface: str | None,
    local_address: str | None,
) -> str:
    if interface:
        selector = f'name="{escape(interface)}"'
    else:
        address = local_address or auto_local_address(peer)
        selector = f'address="{escape(address)}"'

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<CycloneDDS><Domain Id="any">'
        "<General><Interfaces>"
        f'<NetworkInterface {selector} priority="default" multicast="default"/>'
        "</Interfaces></General>"
        "<Discovery><Peers>"
        f'<Peer Address="{escape(peer)}"/>'
        "</Peers></Discovery>"
        "</Domain></CycloneDDS>"
    )


def compact_uwb(sample: UwbState_) -> dict[str, Any]:
    return {
        "distance_est": finite_float(sample.distance_est),
        "yaw_est": finite_float(sample.yaw_est),
        "pitch_est": finite_float(sample.pitch_est),
        "orientation_est": finite_float(sample.orientation_est),
        "tag_roll": finite_float(sample.tag_roll),
        "tag_pitch": finite_float(sample.tag_pitch),
        "tag_yaw": finite_float(sample.tag_yaw),
        "base_roll": finite_float(sample.base_roll),
        "base_pitch": finite_float(sample.base_pitch),
        "base_yaw": finite_float(sample.base_yaw),
        "error_state": int(sample.error_state),
        "enabled_from_app": int(sample.enabled_from_app),
        "channel": int(sample.channel),
        "joy_mode": int(sample.joy_mode),
        "buttons": int(sample.buttons),
    }


def parse_multiple_state(sample: String_) -> dict[str, Any] | str:
    try:
        value = json.loads(sample.data)
    except (TypeError, json.JSONDecodeError):
        return sample.data
    return value if isinstance(value, dict) else sample.data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only Go2 DDS probe for rt/uwbstate, with lowstate as the "
            "communication baseline."
        )
    )
    parser.add_argument(
        "--peer",
        default="192.168.123.161",
        help="Go2 IPv4 address (default: %(default)s)",
    )
    network = parser.add_mutually_exclusive_group()
    network.add_argument(
        "--interface",
        help="CycloneDDS interface name, for example enp0s8",
    )
    network.add_argument(
        "--local-address",
        help=(
            "Local IPv4 address of the Go2-facing NIC. If omitted, the OS route "
            "to --peer is used."
        ),
    )
    parser.add_argument("--domain", type=int, default=0, help="DDS domain ID")
    parser.add_argument(
        "--seconds",
        type=float,
        default=30.0,
        help="Capture duration in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--report-interval",
        type=float,
        default=1.0,
        help="Progress interval in seconds; 0 disables progress events",
    )
    parser.add_argument(
        "--quiet-samples",
        action="store_true",
        help="Do not print individual uwb_sample events",
    )
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be greater than zero")
    if args.report_interval < 0:
        parser.error("--report-interval cannot be negative")
    return args


def main() -> int:
    args = parse_args()
    local_address = args.local_address
    if not args.interface and not local_address:
        local_address = auto_local_address(args.peer)

    config = cyclone_config(
        peer=args.peer,
        interface=args.interface,
        local_address=local_address,
    )
    emit(
        "probe_start",
        peer=args.peer,
        interface=args.interface,
        local_address=local_address,
        domain=args.domain,
        seconds=args.seconds,
        read_only=True,
        topics=[
            TOPIC_LOWSTATE,
            TOPIC_UWB_STATE,
            TOPIC_UWB_SWITCH,
            TOPIC_MULTIPLE_STATE,
        ],
    )

    domain = Domain(args.domain, config)
    participant = DomainParticipant(args.domain)
    publication_reader = BuiltinDataReader(
        participant,
        BuiltinTopicDcpsPublication,
    )
    readers = {
        TOPIC_LOWSTATE: DataReader(
            participant,
            Topic(participant, TOPIC_LOWSTATE, LowState_),
        ),
        TOPIC_UWB_STATE: DataReader(
            participant,
            Topic(participant, TOPIC_UWB_STATE, UwbState_),
        ),
        TOPIC_UWB_SWITCH: DataReader(
            participant,
            Topic(participant, TOPIC_UWB_SWITCH, UwbSwitch_),
        ),
        TOPIC_MULTIPLE_STATE: DataReader(
            participant,
            Topic(participant, TOPIC_MULTIPLE_STATE, String_),
        ),
    }

    counts = {topic: 0 for topic in readers}
    publications: dict[str, str] = {}
    latest_uwb: dict[str, Any] | None = None
    latest_uwb_received_monotonic: float | None = None
    maximum_uwb_receive_gap_seconds = 0.0
    latest_uwb_switch: dict[str, Any] | None = None
    latest_multiple_state: dict[str, Any] | str | None = None
    started = time.monotonic()
    next_report = (
        started + args.report_interval if args.report_interval > 0 else math.inf
    )
    interrupted = False

    try:
        while time.monotonic() - started < args.seconds:
            for sample in publication_reader.take(1000):
                topic_name = getattr(sample, "topic_name", "")
                type_name = getattr(sample, "type_name", "")
                if topic_name:
                    publications[topic_name] = type_name

            counts[TOPIC_LOWSTATE] += len(readers[TOPIC_LOWSTATE].take(1000))

            for sample in readers[TOPIC_UWB_STATE].take(1000):
                counts[TOPIC_UWB_STATE] += 1
                latest_uwb = compact_uwb(sample)
                received_monotonic = time.monotonic()
                if latest_uwb_received_monotonic is not None:
                    maximum_uwb_receive_gap_seconds = max(
                        maximum_uwb_receive_gap_seconds,
                        received_monotonic - latest_uwb_received_monotonic,
                    )
                latest_uwb_received_monotonic = received_monotonic
                if not args.quiet_samples:
                    emit(
                        "uwb_sample",
                        sequence=counts[TOPIC_UWB_STATE],
                        topic=TOPIC_UWB_STATE,
                        receive_monotonic=received_monotonic,
                        elapsed_seconds=received_monotonic - started,
                        sample=latest_uwb,
                    )

            for sample in readers[TOPIC_UWB_SWITCH].take(1000):
                counts[TOPIC_UWB_SWITCH] += 1
                latest_uwb_switch = asdict(sample)

            for sample in readers[TOPIC_MULTIPLE_STATE].take(1000):
                counts[TOPIC_MULTIPLE_STATE] += 1
                latest_multiple_state = parse_multiple_state(sample)

            now = time.monotonic()
            if now >= next_report:
                elapsed = now - started
                emit(
                    "progress",
                    elapsed_seconds=round(elapsed, 3),
                    counts=dict(counts),
                    rates_hz={
                        topic: round(count / elapsed, 3)
                        for topic, count in counts.items()
                    },
                    uwb_writer_discovered=TOPIC_UWB_STATE in publications,
                    latest_uwb=latest_uwb,
                    latest_multiple_state=latest_multiple_state,
                )
                next_report = now + args.report_interval
            time.sleep(0.01)
    except KeyboardInterrupt:
        interrupted = True

    elapsed = max(time.monotonic() - started, 1e-9)
    baseline_ok = counts[TOPIC_LOWSTATE] > 0
    writer_discovered = TOPIC_UWB_STATE in publications
    samples_received = counts[TOPIC_UWB_STATE] > 0
    app_switch = (
        latest_multiple_state.get("uwbSwitch")
        if isinstance(latest_multiple_state, dict)
        else None
    )

    if samples_received:
        verdict = "UWB_SAMPLES_RECEIVED"
        next_action = "CALIBRATE_UWB_FIELDS"
    elif not baseline_ok:
        verdict = "DDS_BASELINE_FAILED"
        next_action = "CHECK_NETWORK_DOMAIN_AND_INTERFACE"
    elif not writer_discovered:
        verdict = "UWB_WRITER_NOT_DISCOVERED"
        next_action = "CHECK_GO2_FIRMWARE_AND_UTRACK_SERVICE"
    elif app_switch is False:
        verdict = "UWB_SWITCH_OFF_NO_SAMPLES"
        next_action = "ENABLE_FOLLOW_IN_APP_AND_RETRY"
    else:
        verdict = "UWB_WRITER_PRESENT_NO_SAMPLES"
        next_action = "CHECK_TRACKING_MODULE_POWER_PAIRING_AND_LINK"

    emit(
        "probe_result",
        elapsed_seconds=round(elapsed, 3),
        interrupted=interrupted,
        counts=counts,
        rates_hz={
            topic: round(count / elapsed, 3) for topic, count in counts.items()
        },
        dds_baseline_ok=baseline_ok,
        uwb_writer_discovered=writer_discovered,
        uwb_writer_type=publications.get(TOPIC_UWB_STATE),
        uwb_samples_received=samples_received,
        uwb_switch_from_multiple_state=app_switch,
        latest_uwb_switch=latest_uwb_switch,
        latest_uwb=latest_uwb,
        maximum_uwb_receive_gap_seconds=round(
            maximum_uwb_receive_gap_seconds, 6
        ),
        verdict=verdict,
        next_action=next_action,
    )
    return 0 if baseline_ok else 2


if __name__ == "__main__":
    sys.exit(main())
