#!/usr/bin/env python3
"""Read-only Go2 status subscriber.

This script intentionally creates no publishers and no service clients. It only
subscribes to Go2 DDS status topics and prints a compact status heartbeat.
"""

import argparse
import time

from unitree_sdk2py.core.channel import ChannelFactoryInitialize, ChannelSubscriber
from unitree_sdk2py.idl.unitree_go.msg.dds_ import LowState_, SportModeState_


def vec(values, count=3):
    return ", ".join(f"{float(values[i]):.3f}" for i in range(min(count, len(values))))


class ReadOnlyStatus:
    def __init__(self):
        self.low_counts = {}
        self.sport_counts = {}
        self.last_print = 0.0

    def on_low_state(self, topic, msg: LowState_):
        self.low_counts[topic] = self.low_counts.get(topic, 0) + 1
        now = time.time()
        if now - self.last_print >= 1.0:
            imu = msg.imu_state
            print(
                "[lowstate] "
                f"topic={topic} "
                f"count={self.low_counts[topic]} "
                f"battery_v={getattr(msg, 'power_v', 'n/a')} "
                f"battery_a={getattr(msg, 'power_a', 'n/a')} "
                f"imu_rpy=({vec(imu.rpy)})"
            )
            self.last_print = now

    def on_sport_state(self, topic, msg: SportModeState_):
        self.sport_counts[topic] = self.sport_counts.get(topic, 0) + 1
        if self.sport_counts[topic] % 20 == 1:
            print(
                "[sportstate] "
                f"topic={topic} "
                f"count={self.sport_counts[topic]} "
                f"mode={getattr(msg, 'mode', 'n/a')} "
                f"progress={getattr(msg, 'progress', 'n/a')} "
                f"position=({vec(msg.position)}) "
                f"velocity=({vec(msg.velocity)})"
            )


def main():
    parser = argparse.ArgumentParser(description="Subscribe to Go2 status only.")
    parser.add_argument(
        "interface",
        nargs="?",
        default=None,
        help="Robot Ethernet interface, for example eth0. Omit to autodetect.",
    )
    parser.add_argument("--domain", type=int, default=0, help="DDS domain id")
    parser.add_argument("--seconds", type=float, default=15.0, help="Run duration")
    args = parser.parse_args()

    if args.interface:
        ChannelFactoryInitialize(args.domain, args.interface)
    else:
        ChannelFactoryInitialize(args.domain)
    reader = ReadOnlyStatus()

    subscribers = []
    for topic in ("rt/lowstate", "rt/lf/lowstate", "lowstate", "lf/lowstate"):
        sub = ChannelSubscriber(topic, LowState_)
        sub.Init(lambda msg, topic=topic: reader.on_low_state(topic, msg), 10)
        subscribers.append(sub)

    for topic in (
        "rt/sportmodestate",
        "rt/lf/sportmodestate",
        "sportmodestate",
        "lf/sportmodestate",
    ):
        sub = ChannelSubscriber(topic, SportModeState_)
        sub.Init(lambda msg, topic=topic: reader.on_sport_state(topic, msg), 10)
        subscribers.append(sub)

    print("Read-only subscribers started. No publishers or robot commands are used.")
    deadline = time.time() + args.seconds
    while time.time() < deadline:
        time.sleep(0.2)

    print(
        "Finished. "
        f"lowstate_messages={sum(reader.low_counts.values())}, "
        f"sportstate_messages={sum(reader.sport_counts.values())}"
    )


if __name__ == "__main__":
    main()
