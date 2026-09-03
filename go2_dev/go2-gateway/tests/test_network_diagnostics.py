from __future__ import annotations

import json

from app.config import Settings
from app.services.network_diagnostics import NetworkDiagnosticsService


class FakeGateway:
    def dds_diagnostics(self) -> dict:
        return {
            "ddsInitialized": True,
            "ddsStateAvailable": False,
            "sportState": {"received": False, "sampleCount": 0, "lastSampleAt": None},
            "lowState": {"received": False, "sampleCount": 0, "lastSampleAt": None},
        }


def fake_runner(command: list[str], _timeout: float) -> tuple[bool, str]:
    joined = " ".join(command)
    if "Get-NetIPConfiguration" in joined:
        return True, json.dumps(
            [
                {
                    "InterfaceAlias": "WLAN",
                    "InterfaceIndex": 15,
                    "InterfaceDescription": "Wi-Fi",
                    "IPv4Address": {"IPAddress": "192.168.43.86", "PrefixLength": 24},
                    "NetAdapter": {"Status": "Up"},
                }
            ]
        )
    if "Find-NetRoute" in joined:
        return True, json.dumps({"InterfaceAlias": "WLAN", "InterfaceIndex": 15, "NextHop": "", "RouteMetric": 0})
    if command and command[0] == "ping":
        return True, "Reply"
    return False, "unexpected command"


def test_network_diagnostics_reports_missing_configured_interface():
    service = NetworkDiagnosticsService(
        Settings(mode="real", network_interface="missing0", robot_ip="192.168.43.147"),
        FakeGateway(),
        fake_runner,
    )

    diagnostics = service.diagnostics()

    assert diagnostics["networkReachable"] is True
    assert diagnostics["ddsInitialized"] is True
    assert diagnostics["ddsStateAvailable"] is False
    assert diagnostics["robotOnline"] is False
    assert diagnostics["motionReady"] is False
    assert diagnostics["errorCode"] == "UNITREE_INTERFACE_NOT_FOUND"


def test_network_diagnostics_reports_dds_no_state_samples_when_interface_matches():
    service = NetworkDiagnosticsService(
        Settings(mode="real", network_interface="WLAN", robot_ip="192.168.43.147"),
        FakeGateway(),
        fake_runner,
    )

    diagnostics = service.diagnostics()

    assert diagnostics["networkReachable"] is True
    assert diagnostics["configuredInterfaceSameSubnet"] is True
    assert diagnostics["ddsInitialized"] is True
    assert diagnostics["ddsStateAvailable"] is False
    assert diagnostics["robotOnline"] is False
    assert diagnostics["motionReady"] is False
    assert diagnostics["errorCode"] == "UNITREE_DDS_NO_STATE_SAMPLES"


def test_network_diagnostics_interface_timeout_does_not_mask_dds_error():
    def runner(command: list[str], _timeout: float) -> tuple[bool, str]:
        joined = " ".join(command)
        if "Get-NetIPConfiguration" in joined:
            return False, "Command timed out after 5.0 seconds"
        if "Find-NetRoute" in joined:
            return True, json.dumps({"InterfaceAlias": "WLAN", "InterfaceIndex": 15, "NextHop": "", "RouteMetric": 0})
        if command and command[0] == "ping":
            return True, "Reply"
        return False, "unexpected command"

    service = NetworkDiagnosticsService(
        Settings(mode="real", network_interface="WLAN", robot_ip="192.168.8.235"),
        FakeGateway(),
        runner,
    )

    diagnostics = service.diagnostics()

    assert diagnostics["networkReachable"] is True
    assert diagnostics["networkInterfaceStatus"]["enumerationStatus"] == "INTERFACE_ENUMERATION_TIMEOUT"
    assert diagnostics["networkInterfaceStatus"]["enumerationReliable"] is False
    assert diagnostics["networkInterfaceStatus"]["detected"] is True
    assert diagnostics["configuredInterfaceExists"] is None
    assert diagnostics["ddsInitialized"] is True
    assert diagnostics["ddsStateAvailable"] is False
    assert diagnostics["errorCode"] == "UNITREE_DDS_NO_STATE_SAMPLES"


def test_network_diagnostics_reports_nonzero_domain_as_wrong_domain_candidate():
    service = NetworkDiagnosticsService(
        Settings(mode="real", network_interface="WLAN", robot_ip="192.168.43.147", domain_id=7),
        FakeGateway(),
        fake_runner,
    )

    diagnostics = service.diagnostics()

    assert diagnostics["ddsInitialized"] is True
    assert diagnostics["ddsStateAvailable"] is False
    assert diagnostics["errorCode"] == "UNITREE_DDS_WRONG_DOMAIN"
    assert any("UNITREE_DOMAIN_ID=0" in item for item in diagnostics["recommendations"])


def test_network_diagnostics_reports_multicast_unavailable_before_dds_samples():
    def runner(command: list[str], timeout: float) -> tuple[bool, str]:
        ok, output = fake_runner(command, timeout)
        if "Get-NetIPConfiguration" in " ".join(command):
            payload = json.loads(output)
            payload[0]["Multicast"] = False
            return True, json.dumps(payload)
        return ok, output

    service = NetworkDiagnosticsService(
        Settings(mode="real", network_interface="WLAN", robot_ip="192.168.43.147"),
        FakeGateway(),
        runner,
    )

    diagnostics = service.diagnostics()

    assert diagnostics["configuredInterface"]["multicast"] is False
    assert diagnostics["errorCode"] == "UNITREE_MULTICAST_UNAVAILABLE"
