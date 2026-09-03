from __future__ import annotations

from pathlib import Path

from tools import dds_diagnostics, wireless_dds_peer_gate


class FakeSdkChannel:
    ChannelConfigHasInterface = """<CycloneDDS><Domain Id="any"><General><Interfaces>
    <NetworkInterface name="$__IF_NAME__$"/></Interfaces></General><Discovery><Peers>
    <Peer Address="192.168.123.161"/></Peers></Discovery></Domain></CycloneDDS>"""


def test_configure_discovery_builds_pure_unicast_peer_config() -> None:
    channel = FakeSdkChannel()

    dds_diagnostics.configure_discovery(channel, "192.168.8.252", "unicast-peer")

    assert '<Peer Address="192.168.8.252"/>' in channel.ChannelConfigHasInterface
    assert "<AllowMulticast>false</AllowMulticast>" in channel.ChannelConfigHasInterface
    assert "<EnableMulticastLoopback>false</EnableMulticastLoopback>" in channel.ChannelConfigHasInterface
    assert "<ParticipantIndex>auto</ParticipantIndex>" in channel.ChannelConfigHasInterface
    assert "<MaxAutoParticipantIndex>100</MaxAutoParticipantIndex>" in channel.ChannelConfigHasInterface


def test_configure_discovery_builds_multicast_peer_config() -> None:
    channel = FakeSdkChannel()

    dds_diagnostics.configure_discovery(channel, "192.168.8.252", "multicast-peer")

    assert "<AllowMulticast>true</AllowMulticast>" in channel.ChannelConfigHasInterface
    assert "<EnableMulticastLoopback>true</EnableMulticastLoopback>" in channel.ChannelConfigHasInterface


def test_probe_command_is_subscriber_probe_only() -> None:
    command = wireless_dds_peer_gate.probe_command(
        "python3",
        Path("tools/dds_diagnostics.py"),
        "eth2",
        "192.168.8.252",
        10,
        "unicast-peer",
        12.0,
    )

    assert command[1].endswith("dds_diagnostics.py")
    assert "--domain-id" in command
    assert "10" in command
    assert "--discovery-mode" in command
    assert "unicast-peer" in command


def test_parse_and_compact_probe_json() -> None:
    payload = wireless_dds_peer_gate.parse_probe_json(
        'prefix\n{"domainId": 0, "discoveryMode": "multicast-peer", '
        '"ddsInitialized": true, "sportState": {"sampleCount": 3}, '
        '"lowState": {"sampleCount": 5}, "errorCode": null}'
    )

    result = wireless_dds_peer_gate.compact_result(payload, 0)

    assert result["sportSampleCount"] == 3
    assert result["lowSampleCount"] == 5
    assert result["probeReturnCode"] == 0
