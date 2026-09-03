#!/usr/bin/env python3
"""Read-only CycloneDDS discovery inventory.

This creates only DDS built-in discovery readers.  It does not create any
application topic writer or publish robot commands.
"""

import argparse
import time

from cyclonedds.builtin import (
    BuiltinDataReader,
    BuiltinTopicDcpsParticipant,
    BuiltinTopicDcpsPublication,
    BuiltinTopicDcpsSubscription,
)
from cyclonedds.domain import DomainParticipant


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", type=int, required=True)
    parser.add_argument("--seconds", type=float, default=8.0)
    args = parser.parse_args()

    participant = DomainParticipant(args.domain)
    participant_reader = BuiltinDataReader(participant, BuiltinTopicDcpsParticipant)
    publication_reader = BuiltinDataReader(participant, BuiltinTopicDcpsPublication)
    subscription_reader = BuiltinDataReader(participant, BuiltinTopicDcpsSubscription)

    participants = {}
    publications = set()
    subscriptions = set()
    deadline = time.monotonic() + args.seconds

    while time.monotonic() < deadline:
        for sample in participant_reader.take(N=1000):
            if sample.key != participant.guid:
                participants[str(sample.key)] = repr(sample.qos)

        for sample in publication_reader.take(N=1000):
            if sample.participant_key != participant.guid:
                publications.add(
                    (str(sample.participant_key), str(sample.topic_name), str(sample.type_name))
                )

        for sample in subscription_reader.take(N=1000):
            if sample.participant_key != participant.guid:
                subscriptions.add(
                    (str(sample.participant_key), str(sample.topic_name), str(sample.type_name))
                )

        time.sleep(0.02)

    print(f"DOMAIN {args.domain}")
    print(f"REMOTE_PARTICIPANTS {len(participants)}")
    for key, qos in sorted(participants.items()):
        print(f"PARTICIPANT {key} {qos}")

    print(f"UNIQUE_PUBLICATIONS {len(publications)}")
    for key, topic, type_name in sorted(publications):
        print(f"PUB {key} {topic} {type_name}")

    print(f"UNIQUE_SUBSCRIPTIONS {len(subscriptions)}")
    for key, topic, type_name in sorted(subscriptions):
        print(f"SUB {key} {topic} {type_name}")


if __name__ == "__main__":
    main()
