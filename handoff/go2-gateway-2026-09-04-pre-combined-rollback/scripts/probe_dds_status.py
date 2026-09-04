from __future__ import annotations

import argparse
import re
import time


def main() -> None:
    parser = argparse.ArgumentParser(description="Subscribe to Go2 DDS status topics without sending motion commands.")
    parser.add_argument("--interface", default=None, help="DDS network interface, for example WLAN or 192.168.43.86.")
    parser.add_argument("--peer", default=None, help="Robot DDS peer IP. Use GO2_ROBOT_IP when the robot is not 192.168.123.161.")
    parser.add_argument("--seconds", type=float, default=6.0)
    args = parser.parse_args()

    try:
        from unitree_sdk2py.core import channel as sdk_channel
        from unitree_sdk2py.idl.unitree_go.msg.dds_ import LowState_, SportModeState_
    except Exception as exc:
        raise SystemExit(f"import failed: {exc}") from exc

    if args.peer:
        sdk_channel.ChannelConfigHasInterface = re.sub(
            r'<Peer\s+Address="[^"]+"\s*/>',
            f'<Peer Address="{args.peer}"/>',
            sdk_channel.ChannelConfigHasInterface,
            count=1,
        )

    got = {"sport": False, "low": False}

    def on_sport(_msg) -> None:
        got["sport"] = True

    def on_low(_msg) -> None:
        got["low"] = True

    try:
        sdk_channel.ChannelFactoryInitialize(0, args.interface)
        subscribers = [
            sdk_channel.ChannelSubscriber("rt/lf/sportmodestate", SportModeState_),
            sdk_channel.ChannelSubscriber("rt/sportmodestate", SportModeState_),
            sdk_channel.ChannelSubscriber("rt/lf/lowstate", LowState_),
            sdk_channel.ChannelSubscriber("rt/lowstate", LowState_),
        ]
        subscribers[0].Init(on_sport, 10)
        subscribers[1].Init(on_sport, 10)
        subscribers[2].Init(on_low, 10)
        subscribers[3].Init(on_low, 10)
    except Exception as exc:
        raise SystemExit(f"dds init failed: {exc}") from exc

    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline and not (got["sport"] or got["low"]):
        time.sleep(0.2)

    print(
        "dds probe "
        f"interface={args.interface or '<auto>'} "
        f"peer={args.peer or '<sdk-default>'} "
        f"sport={got['sport']} "
        f"low={got['low']}"
    )
    if not (got["sport"] or got["low"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
