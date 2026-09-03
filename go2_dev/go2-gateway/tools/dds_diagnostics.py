from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def new_topic_status(name: str, topic_names: list[str]) -> dict:
    return {
        "name": name,
        "topics": topic_names,
        "subscriberCreated": False,
        "receivedFirstSample": False,
        "firstSampleAt": None,
        "sampleCount": 0,
        "lastSampleAt": None,
        "frequencyHz": None,
        "timeoutCode": None,
    }


def record_sample(topic: dict, started_at: float) -> None:
    timestamp = now_iso()
    topic["sampleCount"] += 1
    if not topic["receivedFirstSample"]:
        topic["receivedFirstSample"] = True
        topic["firstSampleAt"] = timestamp
    topic["lastSampleAt"] = timestamp
    elapsed = max(time.monotonic() - started_at, 0.0)
    topic["frequencyHz"] = round(topic["sampleCount"] / elapsed, 3) if elapsed > 0 else None


def configure_discovery(sdk_channel, peer: str | None, discovery_mode: str) -> None:
    """Configure the SDK's in-process CycloneDDS XML before initialization.

    ``ChannelFactory.Init`` passes this string directly to ``Domain``. Merely
    exporting ``CYCLONEDDS_URI`` therefore does not control this SDK path.
    """

    config = sdk_channel.ChannelConfigHasInterface
    if peer:
        config = re.sub(
            r'<Peer\s+Address="[^"]+"\s*/>',
            f'<Peer Address="{peer}"/>',
            config,
            count=1,
        )

    if discovery_mode != "default":
        allow_multicast = "true" if discovery_mode == "multicast-peer" else "false"
        loopback = "true" if discovery_mode == "multicast-peer" else "false"
        config = config.replace(
            "</Interfaces>",
            "</Interfaces>\n"
            f"                <AllowMulticast>{allow_multicast}</AllowMulticast>\n"
            f"                <EnableMulticastLoopback>{loopback}</EnableMulticastLoopback>",
            1,
        )
        config = config.replace(
            "</Peers>",
            "</Peers>\n"
            "                <ParticipantIndex>auto</ParticipantIndex>\n"
            "                <MaxAutoParticipantIndex>100</MaxAutoParticipantIndex>",
            1,
        )

    sdk_channel.ChannelConfigHasInterface = config


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Unitree Go2 DDS status diagnostics.")
    parser.add_argument("--interface", required=True, help="Network adapter name, not robot IP. Example: WLAN or eth0.")
    parser.add_argument("--domain-id", type=int, default=int(os.getenv("UNITREE_DOMAIN_ID", "0")))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("UNITREE_DDS_TIMEOUT_SECONDS", "10")))
    parser.add_argument("--peer", default=os.getenv("UNITREE_ROBOT_IP") or os.getenv("GO2_ROBOT_IP"))
    parser.add_argument(
        "--discovery-mode",
        choices=("default", "multicast-peer", "unicast-peer"),
        default="default",
        help="CycloneDDS discovery policy. All modes remain subscriber-only.",
    )
    args = parser.parse_args()

    result = {
        "interface": args.interface,
        "domainId": args.domain_id,
        "peer": args.peer,
        "discoveryMode": args.discovery_mode,
        "ddsInitialized": False,
        "sportState": new_topic_status("SportModeState", ["rt/lf/sportmodestate", "rt/sportmodestate"]),
        "lowState": new_topic_status("LowState", ["rt/lf/lowstate", "rt/lowstate"]),
        "ddsStateAvailable": False,
        "errorCode": None,
    }

    try:
        from unitree_sdk2py.core import channel as sdk_channel
        from unitree_sdk2py.idl.unitree_go.msg.dds_ import LowState_, SportModeState_
    except Exception as exc:
        result["errorCode"] = "UNITREE_DDS_NOT_INITIALIZED"
        result["error"] = f"SDK import failed: {exc}"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(1) from exc

    configure_discovery(sdk_channel, args.peer, args.discovery_mode)

    started_at = time.monotonic()

    def on_sport(_msg) -> None:
        record_sample(result["sportState"], started_at)

    def on_low(_msg) -> None:
        record_sample(result["lowState"], started_at)

    try:
        sdk_channel.ChannelFactoryInitialize(args.domain_id, args.interface)
        result["ddsInitialized"] = True
        subscribers = []
        for topic in result["sportState"]["topics"]:
            subscriber = sdk_channel.ChannelSubscriber(topic, SportModeState_)
            subscriber.Init(on_sport, 10)
            subscribers.append(subscriber)
        result["sportState"]["subscriberCreated"] = True

        for topic in result["lowState"]["topics"]:
            subscriber = sdk_channel.ChannelSubscriber(topic, LowState_)
            subscriber.Init(on_low, 10)
            subscribers.append(subscriber)
        result["lowState"]["subscriberCreated"] = True
    except Exception as exc:
        result["errorCode"] = "UNITREE_DDS_NOT_INITIALIZED"
        result["error"] = f"DDS init failed: {exc}"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(1) from exc

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if result["sportState"]["receivedFirstSample"] and result["lowState"]["receivedFirstSample"]:
            break
        time.sleep(0.2)

    if not result["sportState"]["receivedFirstSample"]:
        result["sportState"]["timeoutCode"] = "SPORT_STATE_TIMEOUT"
    if not result["lowState"]["receivedFirstSample"]:
        result["lowState"]["timeoutCode"] = "LOW_STATE_TIMEOUT"
    result["ddsStateAvailable"] = bool(
        result["sportState"]["receivedFirstSample"] or result["lowState"]["receivedFirstSample"]
    )
    if not result["ddsStateAvailable"]:
        result["errorCode"] = "DDS_NO_ROBOT_SAMPLES"

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["errorCode"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
