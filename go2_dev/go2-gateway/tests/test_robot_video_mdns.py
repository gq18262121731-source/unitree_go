from __future__ import annotations

import socket

import pytest

from tools.robot_video_mdns import (
    HTTP_SERVICE_TYPE,
    ROBOT_VIDEO_SERVICE_TYPE,
    build_parser,
    build_service_infos,
)


def test_service_infos_advertise_stable_gateway_contract() -> None:
    robot_video, http = build_service_infos("192.168.8.254", 8093)

    assert robot_video.type == ROBOT_VIDEO_SERVICE_TYPE
    assert robot_video.name == "Robot Video Gateway._robot-video._tcp.local."
    assert robot_video.server == "robot-gateway.local."
    assert robot_video.port == 8093
    assert robot_video.addresses == [socket.inet_aton("192.168.8.254")]
    assert robot_video.properties[b"stream"] == b"/stream.mjpg"
    assert robot_video.properties[b"status"] == b"/api/v1/video/status"
    assert http.type == HTTP_SERVICE_TYPE
    assert http.properties[b"path"] == b"/"


@pytest.mark.parametrize("address", ["127.0.0.1", "0.0.0.0", "::1"])
def test_service_info_rejects_non_lan_addresses(address: str) -> None:
    with pytest.raises(ValueError):
        build_service_infos(address, 8093)


def test_cli_defaults_match_gateway_port_and_name() -> None:
    args = build_parser().parse_args(["--address", "10.0.0.20"])

    assert args.port == 8093
    assert args.server_name == "robot-gateway.local."
    assert args.instance_name == "Robot Video Gateway"
