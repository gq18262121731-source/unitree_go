from __future__ import annotations

import json
import platform
import re
import subprocess
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NetworkProbe:
    robot_ip: str
    network_interface: str
    source_ip: str | None
    reachable: bool
    packets_sent: int | None
    packets_received: int | None
    packet_loss_percent: float | None
    average_latency_ms: float | None
    route: str | None
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "robot_ip": self.robot_ip,
            "network_interface": self.network_interface,
            "source_ip": self.source_ip,
            "reachable": self.reachable,
            "packets_sent": self.packets_sent,
            "packets_received": self.packets_received,
            "packet_loss_percent": self.packet_loss_percent,
            "average_latency_ms": self.average_latency_ms,
            "route": self.route,
            "error": self.error,
        }


def probe_network(robot_ip: str, network_interface: str, count: int = 4) -> NetworkProbe:
    if platform.system().lower() == "windows":
        return _probe_windows(robot_ip, network_interface, count)
    return _probe_linux(robot_ip, network_interface, count)


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=15)


def _probe_linux(robot_ip: str, network_interface: str, count: int) -> NetworkProbe:
    source_ip = None
    route_text = None
    errors: list[str] = []
    try:
        address = _run(["ip", "-j", "address", "show", "dev", network_interface])
        if address.returncode == 0:
            payload = json.loads(address.stdout or "[]")
            for entry in payload:
                for addr in entry.get("addr_info", []):
                    if addr.get("family") == "inet":
                        source_ip = addr.get("local")
                        break
        else:
            errors.append(address.stderr.strip() or "interface lookup failed")

        route = _run(["ip", "route", "get", robot_ip])
        if route.returncode == 0:
            route_text = route.stdout.strip()
        else:
            errors.append(route.stderr.strip() or "route lookup failed")

        ping = _run(
            ["ping", "-I", network_interface, "-c", str(count), "-W", "1", robot_ip]
        )
        text = f"{ping.stdout}\n{ping.stderr}"
        sent, received, loss = _parse_linux_packets(text)
        latency = _parse_linux_latency(text)
        if ping.returncode != 0:
            errors.append(ping.stderr.strip() or "ping failed")
        return NetworkProbe(
            robot_ip=robot_ip,
            network_interface=network_interface,
            source_ip=source_ip,
            reachable=bool(received and received > 0),
            packets_sent=sent,
            packets_received=received,
            packet_loss_percent=loss,
            average_latency_ms=latency,
            route=route_text,
            error="; ".join(item for item in errors if item) or None,
        )
    except Exception as exc:
        return NetworkProbe(
            robot_ip, network_interface, source_ip, False, None, None, None, None, route_text, str(exc)
        )


def _probe_windows(robot_ip: str, network_interface: str, count: int) -> NetworkProbe:
    source_ip = None
    errors: list[str] = []
    try:
        interface = _run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-NetIPAddress -AddressFamily IPv4 "
                    f"-InterfaceAlias '{network_interface.replace(chr(39), chr(39) * 2)}' "
                    "| Select-Object -First 1 -ExpandProperty IPAddress"
                ),
            ]
        )
        if interface.returncode == 0:
            source_ip = interface.stdout.strip() or None
        else:
            errors.append(interface.stderr.strip() or "interface lookup failed")
        command = ["ping", "-n", str(count), "-w", "1000"]
        if source_ip:
            command.extend(["-S", source_ip])
        command.append(robot_ip)
        ping = _run(command)
        text = f"{ping.stdout}\n{ping.stderr}"
        sent, received, loss = _parse_windows_packets(text)
        latency = _parse_windows_latency(text)
        if ping.returncode != 0:
            errors.append(ping.stderr.strip() or "ping failed")
        return NetworkProbe(
            robot_ip,
            network_interface,
            source_ip,
            bool(received and received > 0),
            sent,
            received,
            loss,
            latency,
            None,
            "; ".join(item for item in errors if item) or None,
        )
    except Exception as exc:
        return NetworkProbe(
            robot_ip, network_interface, source_ip, False, None, None, None, None, None, str(exc)
        )


def _parse_linux_packets(text: str) -> tuple[int | None, int | None, float | None]:
    match = re.search(
        r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets )?received,.*?([\d.]+)%\s+packet loss",
        text,
    )
    if not match:
        return None, None, None
    return int(match.group(1)), int(match.group(2)), float(match.group(3))


def _parse_linux_latency(text: str) -> float | None:
    match = re.search(r"=\s*[\d.]+/([\d.]+)/[\d.]+/[\d.]+\s*ms", text)
    return float(match.group(1)) if match else None


def _parse_windows_packets(text: str) -> tuple[int | None, int | None, float | None]:
    match = re.search(
        r"(?:Sent|已发送)\s*=\s*(\d+).*?(?:Received|已接收)\s*=\s*(\d+).*?\((\d+)%\s*(?:loss|丢失)\)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return None, None, None
    return int(match.group(1)), int(match.group(2)), float(match.group(3))


def _parse_windows_latency(text: str) -> float | None:
    match = re.search(r"(?:Average|平均)\s*=\s*(\d+)ms", text, re.IGNORECASE)
    return float(match.group(1)) if match else None
