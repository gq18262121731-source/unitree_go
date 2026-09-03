from __future__ import annotations

import argparse
import importlib.util
import os
import platform
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    required: bool = False


def run_check(command: list[str], timeout: float = 5.0) -> tuple[bool, str]:
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        return False, str(exc)
    output = "\n".join(part.strip() for part in [result.stdout, result.stderr] if part and part.strip())
    return result.returncode == 0, output.strip()


def has_module(name: str) -> tuple[bool, str]:
    spec = importlib.util.find_spec(name)
    return spec is not None, "" if spec is None else str(spec.origin)


def network_interface_check(iface: str) -> Check:
    system = platform.system().lower()
    if system == "windows":
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            return Check("network_interface", False, "PowerShell not found; cannot query Windows network adapters.")
        command = [
            powershell,
            "-NoProfile",
            "-Command",
            (
                "$adapter = Get-NetAdapter -Name '%s' -ErrorAction SilentlyContinue; "
                "if ($adapter) { $adapter | Select-Object -First 1 -ExpandProperty Status } else { exit 1 }"
            )
            % iface.replace("'", "''"),
        ]
        ok, output = run_check(command)
        return Check("network_interface", ok, output or f"adapter not found: {iface}")

    if shutil.which("ip"):
        ok, output = run_check(["ip", "-br", "addr", "show", iface])
        return Check("network_interface", ok, output or f"interface not found: {iface}")

    if shutil.which("ifconfig"):
        ok, output = run_check(["ifconfig", iface])
        return Check("network_interface", ok, output.splitlines()[0] if output else f"interface not found: {iface}")

    return Check("network_interface", False, "neither ip nor ifconfig is available")


def route_check(robot_ip: str) -> Check:
    system = platform.system().lower()
    if system == "windows":
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            return Check("route_to_robot", False, "PowerShell not found; cannot query Windows route.")
        command = [
            powershell,
            "-NoProfile",
            "-Command",
            (
                "$route = Find-NetRoute -RemoteIPAddress '%s' -ErrorAction SilentlyContinue; "
                "if ($route) { $route | Select-Object -First 1 InterfaceAlias,NextHop,RouteMetric | Format-List | Out-String } else { exit 1 }"
            )
            % robot_ip.replace("'", "''"),
        ]
        ok, output = run_check(command)
        return Check("route_to_robot", ok, output or f"no route to {robot_ip}")

    if shutil.which("ip"):
        ok, output = run_check(["ip", "route", "get", robot_ip])
        return Check("route_to_robot", ok, output or f"no route to {robot_ip}")

    return Check("route_to_robot", False, "ip command is not available")


def ping_check(robot_ip: str, iface: str) -> Check:
    system = platform.system().lower()
    if system == "windows":
        ok, output = run_check(["ping", "-n", "1", "-w", "1000", robot_ip], timeout=3.0)
        return Check("ping_robot", ok, output.splitlines()[-1] if output else f"ping failed: {robot_ip}")

    if shutil.which("ping"):
        ok, output = run_check(["ping", "-I", iface, "-c", "1", "-W", "1", robot_ip], timeout=3.0)
        return Check("ping_robot", ok, output.splitlines()[-1] if output else f"ping failed: {robot_ip}")

    return Check("ping_robot", False, "ping command is not available")


def print_check(check: Check) -> None:
    status = "OK" if check.ok else ("FAIL" if check.required else "WARN")
    required = " required" if check.required else ""
    print(f"[{status}] {check.name}{required}")
    if check.detail:
        for line in check.detail.splitlines():
            if line.strip():
                print(f"  {line.strip()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the local environment for the Go2 gateway.")
    parser.add_argument("--strict-real", action="store_true", help="Fail if real-mode P0 checks are not satisfied.")
    parser.add_argument("--require-ping", action="store_true", help="Treat robot ping failure as fatal.")
    args = parser.parse_args()

    mode = os.getenv("GO2_MODE", "mock").lower()
    iface = os.getenv("GO2_NETWORK_INTERFACE", "enp3s0")
    robot_ip = os.getenv("GO2_ROBOT_IP", "192.168.123.161")
    control_enabled = os.getenv("GO2_CONTROL_ENABLED", "true")
    strict_real = args.strict_real or mode == "real"

    print(f"Python: {sys.version.split()[0]}")
    print(f"Platform: {platform.platform()}")
    print(f"Host: {socket.gethostname()}")
    print(f"GO2_MODE: {mode}")
    print(f"GO2_ROBOT_IP: {robot_ip}")
    print(f"GO2_NETWORK_INTERFACE: {iface}")
    print(f"GO2_CONTROL_ENABLED: {control_enabled}")

    sdk_ok, sdk_detail = has_module("unitree_sdk2py")
    cv2_ok, cv2_detail = has_module("cv2")
    numpy_ok, numpy_detail = has_module("numpy")

    checks = [
        Check("unitree_sdk2py", sdk_ok, sdk_detail or "not found", required=strict_real),
        Check("opencv_cv2", cv2_ok, cv2_detail or "not found", required=False),
        Check("numpy", numpy_ok, numpy_detail or "not found", required=False),
        network_interface_check(iface),
        route_check(robot_ip),
        ping_check(robot_ip, iface),
    ]
    checks[3].required = strict_real
    checks[4].required = strict_real
    checks[5].required = args.require_ping

    for check in checks:
        print_check(check)

    failed_required = [check.name for check in checks if check.required and not check.ok]
    if failed_required:
        raise SystemExit("FAILED required checks: " + ", ".join(failed_required))

    if strict_real:
        print("environment check finished: real-mode required checks passed")
    else:
        print("environment check finished: warnings are allowed outside strict real mode")


if __name__ == "__main__":
    main()
