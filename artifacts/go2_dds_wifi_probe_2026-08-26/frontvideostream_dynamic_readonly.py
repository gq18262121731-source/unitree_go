#!/usr/bin/env python3
"""Discover the robot's actual DDS type and subscribe without application writes."""

import argparse
import dataclasses
import time
from typing import Optional

from cyclonedds.builtin import BuiltinDataReader, BuiltinTopicDcpsPublication
from cyclonedds.core import Listener, Policy
from cyclonedds.domain import DomainParticipant
from cyclonedds.dynamic import get_types_for_typeid
from cyclonedds.qos import Qos
from cyclonedds.sub import DataReader
from cyclonedds.topic import Topic
from cyclonedds.util import duration


def field_size(value) -> Optional[int]:
    try:
        return len(value)
    except TypeError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", type=int, required=True)
    parser.add_argument("--topic", default="rt/frontvideostream")
    parser.add_argument("--discover-seconds", type=float, default=10.0)
    parser.add_argument("--sample-seconds", type=float, default=15.0)
    args = parser.parse_args()

    participant = DomainParticipant(args.domain)
    publication_reader = BuiltinDataReader(participant, BuiltinTopicDcpsPublication)

    endpoint = None
    deadline = time.monotonic() + args.discover_seconds
    while time.monotonic() < deadline and endpoint is None:
        for sample in publication_reader.take(N=1000):
            if sample.participant_key != participant.guid and sample.topic_name == args.topic:
                endpoint = sample
                break
        time.sleep(0.02)

    if endpoint is None:
        print(f"NO_PUBLICATION domain={args.domain} topic={args.topic}")
        return

    print(f"PUBLICATION participant={endpoint.participant_key}")
    print(f"TYPE_NAME {endpoint.type_name}")
    print(f"TYPE_ID {endpoint.type_id!r}")
    dynamic_type, nested_types = get_types_for_typeid(
        participant, endpoint.type_id, duration(seconds=8)
    )
    print(f"DYNAMIC_TYPE {dynamic_type.__idl_typename__}")
    print(f"FIELDS {[field.name for field in dataclasses.fields(dynamic_type)]}")
    print(f"NESTED_TYPES {sorted(nested_types)}")

    topic = Topic(participant, args.topic, dynamic_type)
    qos = Qos(
        Policy.Reliability.Reliable(max_blocking_time=duration(milliseconds=100)),
        Policy.History.KeepLast(1),
    )
    matched = {"current": 0, "total": 0}

    def on_subscription_matched(_reader, status) -> None:
        matched["current"] = status.current_count
        matched["total"] = status.total_count

    reader = DataReader(
        participant,
        topic,
        qos=qos,
        listener=Listener(on_subscription_matched=on_subscription_matched),
    )

    samples = 0
    first = None
    deadline = time.monotonic() + args.sample_seconds
    while time.monotonic() < deadline:
        for sample in reader.take(N=16):
            samples += 1
            if first is None:
                first = {
                    field.name: field_size(getattr(sample, field.name))
                    for field in dataclasses.fields(sample)
                }
        time.sleep(0.01)

    print(f"MATCHED_CURRENT {matched['current']}")
    print(f"MATCHED_TOTAL {matched['total']}")
    print(f"SAMPLES {samples}")
    print(f"FIRST_FIELD_SIZES {first}")


if __name__ == "__main__":
    main()
