#!/usr/bin/env python3
"""Subscribe to Go2 front video without publishing any DDS samples."""

import argparse
import time

from cyclonedds.core import Listener, Policy
from cyclonedds.domain import DomainParticipant
from cyclonedds.qos import Qos
from cyclonedds.sub import DataReader
from cyclonedds.topic import Topic
from cyclonedds.util import duration

from unitree_sdk2py.idl.unitree_go.msg.dds_ import Go2FrontVideoData_


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", type=int, required=True)
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--topic", default="rt/frontvideostream")
    parser.add_argument("--reliability", choices=("best-effort", "reliable"), default="reliable")
    args = parser.parse_args()

    participant = DomainParticipant(args.domain)
    topic = Topic(participant, args.topic, Go2FrontVideoData_)
    reliability = (
        Policy.Reliability.BestEffort
        if args.reliability == "best-effort"
        else Policy.Reliability.Reliable(max_blocking_time=duration(milliseconds=100))
    )
    qos = Qos(reliability, Policy.History.KeepLast(1))
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
    nonempty_720p = 0
    first_sizes = None
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        for sample in reader.take(N=16):
            samples += 1
            sizes = (
                len(sample.video720p),
                len(sample.video360p),
                len(sample.video180p),
            )
            if sizes[0] > 0:
                nonempty_720p += 1
            if first_sizes is None:
                first_sizes = sizes
        time.sleep(0.01)

    print(f"DOMAIN {args.domain}")
    print(f"TOPIC {args.topic}")
    print(f"RELIABILITY {args.reliability}")
    print(f"MATCHED_CURRENT {matched['current']}")
    print(f"MATCHED_TOTAL {matched['total']}")
    print(f"SAMPLES {samples}")
    print(f"NONEMPTY_720P {nonempty_720p}")
    print(f"FIRST_SIZES {first_sizes}")


if __name__ == "__main__":
    main()
