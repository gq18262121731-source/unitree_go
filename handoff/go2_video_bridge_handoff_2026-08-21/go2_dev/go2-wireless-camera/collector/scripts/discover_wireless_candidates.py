from __future__ import annotations

import argparse
import ipaddress
import platform
import re
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed


def run(command: list[str], timeout: float = 5.0) -> tuple[int, str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=timeout)
    return result.returncode, (result.stdout or result.stderr).strip()


def ping_command(ip: str) -> list[str]:
    if platform.system().lower().startswith("win"):
        return ["ping", "-n", "1", "-w", "500", ip]
    return ["ping", "-c", "1", "-W", "1", ip]


def arp_table() -> str:
    if platform.system().lower().startswith("win"):
        code, output = run(["arp", "-a"], timeout=5)
    else:
        code, output = run(["ip", "neigh"], timeout=5)
    return output if code == 0 else output


def scan_host(ip: str) -> tuple[str, bool, str | None]:
    code, _ = run(ping_command(ip), timeout=2)
    if code != 0:
        return ip, False, None
    name = None
    try:
        name = socket.gethostbyaddr(ip)[0]
    except Exception:
        pass
    return ip, True, name


def main() -> None:
    parser = argparse.ArgumentParser(description="Conservative Go2 wireless candidate discovery.")
    parser.add_argument("--cidr", help="Small CIDR to scan, for example 192.168.8.0/24")
    parser.add_argument("--max-hosts", type=int, default=256)
    parser.add_argument("--workers", type=int, default=32)
    args = parser.parse_args()

    print("== Current ARP table ==")
    arp = arp_table()
    print(arp)

    if not args.cidr:
        print("\nNo --cidr provided. ARP-only mode finished.")
        print("Provide a small CIDR such as --cidr 192.168.8.0/24 to ping-scan the current Wi-Fi subnet.")
        return

    network = ipaddress.ip_network(args.cidr, strict=False)
    hosts = [str(ip) for ip in network.hosts()]
    if len(hosts) > args.max_hosts:
        raise SystemExit(f"Refusing to scan {len(hosts)} hosts. Use a smaller CIDR or raise --max-hosts intentionally.")

    print(f"\n== Ping scan {network} ==")
    live: list[tuple[str, str | None]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(scan_host, ip): ip for ip in hosts}
        for future in as_completed(futures):
            ip, ok, name = future.result()
            if ok:
                live.append((ip, name))
                print(f"{ip}\t{name or ''}")

    print("\n== Refreshed ARP table ==");
    refreshed = arp_table()
    print(refreshed)
    print("\nCandidate hints:")
    print("- Look for unfamiliar hosts on the Wi-Fi subnet.")
    print("- Unitree MAC/vendor may not show a readable name.")
    print("- Do not assume the wired Go2 IP is the wireless IP.")


if __name__ == "__main__":
    main()
