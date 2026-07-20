from __future__ import annotations

import importlib.util
import os
import platform
import socket
import subprocess
import sys


def run_check(command: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=5)
    except Exception as exc:
        return False, str(exc)
    output = (result.stdout or result.stderr).strip()
    return result.returncode == 0, output


def main() -> None:
    iface = os.getenv("GO2_NETWORK_INTERFACE", "enp3s0")
    robot_ip = os.getenv("GO2_ROBOT_IP", "192.168.123.161")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Platform: {platform.platform()}")
    print(f"GO2_MODE: {os.getenv('GO2_MODE', 'mock')}")
    print(f"GO2_NETWORK_INTERFACE: {iface}")
    print(f"GO2_CONTROL_ENABLED: {os.getenv('GO2_CONTROL_ENABLED', 'true')}")
    print(f"Host: {socket.gethostname()}")
    sdk = importlib.util.find_spec("unitree_sdk2py")
    print(f"unitree_sdk2py: {'installed' if sdk else 'not found'}")
    if sdk:
        print(f"unitree_sdk2py path: {sdk.origin}")
    cv2 = importlib.util.find_spec("cv2")
    print(f"OpenCV: {'installed' if cv2 else 'not found'}")
    numpy = importlib.util.find_spec("numpy")
    print(f"NumPy: {'installed' if numpy else 'not found'}")
    ok, output = run_check(["ip", "-br", "addr", "show", iface])
    print(f"{iface}: {'available' if ok else 'unavailable'}")
    if output:
        print(output)
    ok, output = run_check(["ip", "route", "get", robot_ip])
    print(f"Route to {robot_ip}: {'ok' if ok else 'failed'}")
    if output:
        print(output)
    ok, _ = run_check(["ping", "-I", iface, "-c", "1", "-W", "1", robot_ip])
    print(f"Ping {robot_ip} via {iface}: {'ok' if ok else 'failed'}")


if __name__ == "__main__":
    main()
