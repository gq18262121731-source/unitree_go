"""Advertise Robot Video Gateway on the local network with mDNS/DNS-SD."""

from __future__ import annotations

import argparse
import ipaddress
import signal
import socket
import threading
from collections.abc import Sequence

from zeroconf import IPVersion, ServiceInfo, Zeroconf


ROBOT_VIDEO_SERVICE_TYPE = "_robot-video._tcp.local."
HTTP_SERVICE_TYPE = "_http._tcp.local."
DEFAULT_INSTANCE_NAME = "Robot Video Gateway"
DEFAULT_SERVER_NAME = "robot-gateway.local."


def _packed_ipv4(address: str) -> bytes:
    parsed = ipaddress.ip_address(address)
    if parsed.version != 4 or parsed.is_loopback or parsed.is_unspecified:
        raise ValueError(f"A non-loopback IPv4 address is required: {address}")
    return socket.inet_aton(str(parsed))


def build_service_infos(
    address: str,
    port: int,
    *,
    instance_name: str = DEFAULT_INSTANCE_NAME,
    server_name: str = DEFAULT_SERVER_NAME,
) -> tuple[ServiceInfo, ServiceInfo]:
    if not 1 <= int(port) <= 65535:
        raise ValueError(f"Invalid TCP port: {port}")
    server = server_name.rstrip(".") + "."
    packed_address = _packed_ipv4(address)
    properties = {
        "api": "/api/v1/robot/video",
        "health": "/healthz",
        "status": "/api/v1/video/status",
        "stream": "/stream.mjpg",
        "protocol": "mjpeg",
        "version": "1",
    }
    robot_video = ServiceInfo(
        ROBOT_VIDEO_SERVICE_TYPE,
        f"{instance_name}.{ROBOT_VIDEO_SERVICE_TYPE}",
        addresses=[packed_address],
        port=int(port),
        properties=properties,
        server=server,
    )
    http = ServiceInfo(
        HTTP_SERVICE_TYPE,
        f"{instance_name}.{HTTP_SERVICE_TYPE}",
        addresses=[packed_address],
        port=int(port),
        properties={"path": "/", "role": "robot-video-gateway"},
        server=server,
    )
    return robot_video, http


def advertise(
    address: str,
    port: int,
    *,
    instance_name: str = DEFAULT_INSTANCE_NAME,
    server_name: str = DEFAULT_SERVER_NAME,
) -> int:
    services = build_service_infos(
        address,
        port,
        instance_name=instance_name,
        server_name=server_name,
    )
    stop_event = threading.Event()

    def request_stop(_signum=None, _frame=None) -> None:
        stop_event.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, request_stop)

    zeroconf = Zeroconf(interfaces=[address], ip_version=IPVersion.V4Only)
    registered: list[ServiceInfo] = []
    try:
        for service in services:
            zeroconf.register_service(service)
            registered.append(service)
        print(
            "MDNS_READY "
            f"service={ROBOT_VIDEO_SERVICE_TYPE} "
            f"host={server_name.rstrip('.')} address={address} port={port}",
            flush=True,
        )
        stop_event.wait()
        return 0
    finally:
        for service in reversed(registered):
            zeroconf.unregister_service(service)
        zeroconf.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", required=True, help="WLAN IPv4 address")
    parser.add_argument("--port", type=int, default=8093)
    parser.add_argument("--instance-name", default=DEFAULT_INSTANCE_NAME)
    parser.add_argument("--server-name", default=DEFAULT_SERVER_NAME)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return advertise(
        args.address,
        args.port,
        instance_name=args.instance_name,
        server_name=args.server_name,
    )


if __name__ == "__main__":
    raise SystemExit(main())
