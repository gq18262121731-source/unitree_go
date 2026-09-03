from __future__ import annotations

import ipaddress
import json
import platform
import subprocess
import sys
from typing import Callable

from app.config import Settings
from app.gateway.go2_gateway import Go2Gateway


CommandRunner = Callable[[list[str], float], tuple[bool, str]]


def run_command(command: list[str], timeout: float = 5.0) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except Exception as exc:
        return False, str(exc)
    output = (completed.stdout or completed.stderr or "").strip()
    return completed.returncode == 0, output


class NetworkDiagnosticsService:
    def __init__(self, settings: Settings, gateway: Go2Gateway, runner: CommandRunner = run_command) -> None:
        self.settings = settings
        self.gateway = gateway
        self.runner = runner

    def diagnostics(self) -> dict:
        interfaces = self.interfaces()
        route = self.route_to_robot()
        interface_status = self._interface_status(interfaces)
        selected = next((item for item in interfaces if item.get("name") == self.settings.network_interface), None)
        dds = self.gateway.dds_diagnostics()
        network_reachable = self.ping_robot()
        dds_initialized = bool(dds.get("ddsInitialized"))
        dds_state_available = bool(dds.get("ddsStateAvailable"))
        robot_online = bool(dds_state_available)
        motion_ready = bool(robot_online and dds_initialized)
        error_code = self._error_code(selected, route, dds_initialized, dds_state_available, interface_status["enumerationReliable"])

        return {
            "robotIp": self.settings.robot_ip,
            "networkInterface": self.settings.network_interface,
            "domainId": self.settings.domain_id,
            "networkReachable": network_reachable,
            "ddsInitialized": dds_initialized,
            "ddsStateAvailable": dds_state_available,
            "robotOnline": robot_online,
            "motionReady": motion_ready,
            "errorCode": error_code,
            "environment": {
                "os": platform.platform(),
                "python": sys.version,
                "pythonExecutable": sys.executable,
                "runtime": self._runtime_name(),
            },
            "interfaces": interfaces,
            "networkInterfaceStatus": interface_status,
            "routeToRobot": route,
            "configuredInterface": selected,
            "configuredInterfaceExists": selected is not None if interface_status["enumerationReliable"] else None,
            "configuredInterfaceSameSubnet": self._same_subnet(selected),
            "dds": dds,
            "cycloneDds": {
                "peer": self.settings.robot_ip,
                "domainId": self.settings.domain_id,
                "interface": self.settings.network_interface,
            },
            "warnings": self._warnings(selected, route, dds_state_available),
            "recommendations": self._recommendations(error_code),
        }

    def interfaces(self) -> list[dict]:
        if platform.system().lower() == "windows":
            return self._windows_interfaces()
        return self._linux_interfaces()

    def route_to_robot(self) -> dict:
        if platform.system().lower() == "windows":
            ok, output = self.runner(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"Find-NetRoute -RemoteIPAddress '{self.settings.robot_ip}' | Select-Object InterfaceAlias,InterfaceIndex,NextHop,RouteMetric | ConvertTo-Json -Depth 4",
                ],
                5.0,
            )
            if not ok:
                return {"ok": False, "error": output}
            try:
                return self._json_object(output)
            except json.JSONDecodeError as exc:
                return {"ok": False, "error": f"failed to parse Windows route diagnostics: {exc}", "raw": output[:500]}
        ok, output = self.runner(["ip", "route", "get", self.settings.robot_ip], 5.0)
        return {"ok": ok, "raw": output, "interface": self._linux_route_interface(output)}

    def ping_robot(self) -> bool:
        if platform.system().lower() == "windows":
            ok, _ = self.runner(["ping", "-n", "1", "-w", "1000", self.settings.robot_ip], 3.0)
            return ok
        ok, _ = self.runner(["ping", "-c", "1", "-W", "1", self.settings.robot_ip], 3.0)
        return ok

    def _windows_interfaces(self) -> list[dict]:
        ok, output = self.runner(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-NetIPConfiguration | ForEach-Object { "
                    "[pscustomobject]@{ "
                    "InterfaceAlias=$_.InterfaceAlias; "
                    "InterfaceIndex=$_.InterfaceIndex; "
                    "InterfaceDescription=$_.InterfaceDescription; "
                    "IPv4Address=@($_.IPv4Address | ForEach-Object { "
                    "[pscustomobject]@{ IPAddress=$_.IPAddress; PrefixLength=$_.PrefixLength } "
                    "}); "
                    "NetAdapter=[pscustomobject]@{ Status=$_.NetAdapter.Status } "
                    "} "
                    "} | ConvertTo-Json -Depth 6"
                ),
            ],
            5.0,
        )
        if not ok:
            return [
                {
                    "error": output,
                    "enumerationStatus": self._command_error_status(output),
                    "enumerationReliable": False,
                }
            ]
        try:
            payload = self._json_list(output)
        except json.JSONDecodeError as exc:
            return [
                {
                    "error": f"failed to parse Windows interface diagnostics: {exc}",
                    "raw": output[:500],
                    "enumerationStatus": "PARSE_ERROR",
                    "enumerationReliable": False,
                }
            ]
        interfaces = []
        for item in payload:
            addresses = item.get("IPv4Address") or []
            if isinstance(addresses, dict):
                addresses = [addresses]
            adapter = item.get("NetAdapter") or {}
            ipv4 = []
            for address in addresses:
                ip = address.get("IPAddress")
                prefix = address.get("PrefixLength")
                if ip:
                    ipv4.append({"address": ip, "prefixLength": prefix, "netmask": self._prefix_to_netmask(prefix)})
            status = str(adapter.get("Status") or "")
            interfaces.append(
                {
                    "name": item.get("InterfaceAlias"),
                    "index": item.get("InterfaceIndex"),
                    "description": item.get("InterfaceDescription"),
                    "enabled": status.lower() == "up",
                    "loopback": False,
                    "multicast": item.get("Multicast") if isinstance(item.get("Multicast"), bool) else None,
                    "ipv4": ipv4,
                }
            )
        return interfaces

    def _linux_interfaces(self) -> list[dict]:
        ok, output = self.runner(["ip", "-j", "addr"], 5.0)
        if not ok:
            return [{"error": output}]
        payload = self._json_list(output)
        interfaces = []
        for item in payload:
            flags = item.get("flags") or []
            ipv4 = [
                {
                    "address": info.get("local"),
                    "prefixLength": info.get("prefixlen"),
                    "netmask": self._prefix_to_netmask(info.get("prefixlen")),
                }
                for info in item.get("addr_info", [])
                if info.get("family") == "inet"
            ]
            interfaces.append(
                {
                    "name": item.get("ifname"),
                    "index": item.get("ifindex"),
                    "description": item.get("ifname"),
                    "enabled": "UP" in flags,
                    "loopback": "LOOPBACK" in flags,
                    "multicast": "MULTICAST" in flags,
                    "ipv4": ipv4,
                }
            )
        return interfaces

    def _error_code(
        self,
        selected: dict | None,
        route: dict,
        dds_initialized: bool,
        dds_state_available: bool,
        interface_enumeration_reliable: bool = True,
    ) -> str | None:
        if not interface_enumeration_reliable:
            return self._dds_error_code(dds_initialized, dds_state_available)
        if selected is None:
            return "UNITREE_INTERFACE_NOT_FOUND"
        if not selected.get("ipv4"):
            return "UNITREE_INTERFACE_NO_IPV4"
        if not self._same_subnet(selected):
            return "UNITREE_INTERFACE_SUBNET_MISMATCH"
        route_iface = route.get("InterfaceAlias") or route.get("interface")
        if route_iface and route_iface != self.settings.network_interface:
            return "UNITREE_ROUTE_INTERFACE_MISMATCH"
        if selected.get("multicast") is False:
            return "UNITREE_MULTICAST_UNAVAILABLE"
        return self._dds_error_code(dds_initialized, dds_state_available)

    def _dds_error_code(self, dds_initialized: bool, dds_state_available: bool) -> str | None:
        if not dds_initialized:
            return "UNITREE_DDS_NOT_INITIALIZED"
        if not dds_state_available and self.settings.domain_id != 0:
            return "UNITREE_DDS_WRONG_DOMAIN"
        if not dds_state_available:
            return "UNITREE_DDS_NO_STATE_SAMPLES"
        return None

    def _warnings(self, selected: dict | None, route: dict, dds_state_available: bool) -> list[str]:
        warnings = []
        if platform.system().lower() == "windows":
            warnings.append("Windows native CycloneDDS behavior can differ from Unitree's Ubuntu baseline.")
            warnings.append("Windows Firewall or hotspot multicast behavior may affect DDS discovery; no firewall changes were made.")
        if selected and selected.get("multicast") is False:
            warnings.append("Configured interface does not report multicast support.")
        if self.settings.domain_id != 0:
            warnings.append("Go2 real-mode baseline uses DDS domain 0; the configured domain is non-zero.")
        route_iface = route.get("InterfaceAlias") or route.get("interface")
        if route_iface and route_iface != self.settings.network_interface:
            warnings.append(f"Route to robot uses {route_iface}, but configured DDS interface is {self.settings.network_interface}.")
        if not dds_state_available:
            warnings.append("Network ping can succeed while DDS state samples are still unavailable.")
        return warnings

    def _recommendations(self, error_code: str | None) -> list[str]:
        recommendations = [
            "Confirm the DDS interface is the actual adapter connected to Go2.",
            "Confirm the real robot DDS domain is 0.",
            "Prefer the Go2 default wired 192.168.123.x development network for baseline validation.",
        ]
        if error_code == "UNITREE_DDS_NO_STATE_SAMPLES":
            recommendations.append("Run read-only DDS diagnostics on both the Wi-Fi path and the wired 192.168.123.x path.")
        if error_code == "UNITREE_DDS_WRONG_DOMAIN":
            recommendations.append("Set UNITREE_DOMAIN_ID=0 and start a fresh Python process before retesting DDS subscriptions.")
        if error_code == "UNITREE_MULTICAST_UNAVAILABLE":
            recommendations.append("Use an adapter/network path that supports DDS multicast or configure a reachable CycloneDDS peer.")
        return recommendations

    def _same_subnet(self, selected: dict | None) -> bool:
        if selected is None:
            return False
        robot_ip = ipaddress.ip_address(self.settings.robot_ip)
        for item in selected.get("ipv4", []):
            address = item.get("address")
            prefix = item.get("prefixLength")
            if address is None or prefix is None:
                continue
            try:
                network = ipaddress.ip_network(f"{address}/{prefix}", strict=False)
            except ValueError:
                continue
            if robot_ip in network:
                return True
        return False

    def _interface_status(self, interfaces: list[dict]) -> dict:
        selected = next((item for item in interfaces if item.get("name") == self.settings.network_interface), None)
        unreliable = next((item for item in interfaces if item.get("enumerationReliable") is False), None)
        if unreliable and selected is None:
            route = self.route_to_robot()
            route_iface = route.get("InterfaceAlias") or route.get("interface")
            route_matches = route_iface == self.settings.network_interface
            return {
                "name": self.settings.network_interface,
                "detected": True if route_matches else None,
                "enumerationStatus": unreliable.get("enumerationStatus") or "COMMAND_FAILED",
                "enumerationReliable": False,
                "error": unreliable.get("error"),
            }
        return {
            "name": self.settings.network_interface,
            "detected": selected is not None,
            "enumerationStatus": "OK",
            "enumerationReliable": True,
            "ipv4": selected.get("ipv4") if selected else [],
        }

    def _command_error_status(self, output: str) -> str:
        return "INTERFACE_ENUMERATION_TIMEOUT" if "timed out" in output.lower() else "COMMAND_FAILED"

    def _json_list(self, output: str) -> list[dict]:
        data = json.loads(output)
        return data if isinstance(data, list) else [data]

    def _json_object(self, output: str) -> dict:
        data = json.loads(output)
        if isinstance(data, list):
            return data[0] if data else {}
        return data if isinstance(data, dict) else {"raw": data}

    def _prefix_to_netmask(self, prefix: int | str | None) -> str | None:
        if prefix is None:
            return None
        try:
            return str(ipaddress.ip_network(f"0.0.0.0/{int(prefix)}").netmask)
        except ValueError:
            return None

    def _linux_route_interface(self, output: str) -> str | None:
        parts = output.split()
        return parts[parts.index("dev") + 1] if "dev" in parts and parts.index("dev") + 1 < len(parts) else None

    def _runtime_name(self) -> str:
        if "microsoft" in platform.release().lower() or "microsoft" in platform.version().lower():
            return "wsl"
        return platform.system().lower()
