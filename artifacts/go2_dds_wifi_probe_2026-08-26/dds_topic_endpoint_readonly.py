#!/usr/bin/env python3
"""Print discovered DDS endpoint QoS for one exact topic; no application writes."""

import argparse
import time

from cyclonedds.builtin import (
    BuiltinDataReader,
    BuiltinTopicDcpsPublication,
    BuiltinTopicDcpsSubscription,
)
from cyclonedds.domain import DomainParticipant


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", type=int, required=True)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--seconds", type=float, default=8.0)
    args = parser.parse_args()

    participant = DomainParticipant(args.domain)
    publication_reader = BuiltinDataReader(participant, BuiltinTopicDcpsPublication)
    subscription_reader = BuiltinDataReader(participant, BuiltinTopicDcpsSubscription)
    publications = {}
    subscriptions = {}
    deadline = time.monotonic() + args.seconds

    while time.monotonic() < deadline:
        for sample in publication_reader.take(N=1000):
            if sample.participant_key != participant.guid and sample.topic_name == args.topic:
                publications[str(sample.key)] = sample
        for sample in subscription_reader.take(N=1000):
            if sample.participant_key != participant.guid and sample.topic_name == args.topic:
                subscriptions[str(sample.key)] = sample
        time.sleep(0.02)

    print(f"DOMAIN {args.domain}")
    print(f"TOPIC {args.topic}")
    print(f"PUBLICATIONS {len(publications)}")
    for sample in publications.values():
        print(
            f"PUB participant={sample.participant_key} type={sample.type_name} "
            f"type_id={sample.type_id!r} qos={sample.qos!r}"
        )
    print(f"SUBSCRIPTIONS {len(subscriptions)}")
    for sample in subscriptions.values():
        print(
            f"SUB participant={sample.participant_key} type={sample.type_name} "
            f"type_id={sample.type_id!r} qos={sample.qos!r}"
        )


if __name__ == "__main__":
    main()
