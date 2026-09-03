from __future__ import annotations

import argparse
import subprocess
import sys


def run(command: list[str]) -> tuple[int, str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=10)
    return result.returncode, (result.stdout or result.stderr).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Check WSL wireless route to Go2 without sending robot commands.")
    parser.add_argument("--interface", required=True)
    parser.add_argument("--ip", required=True, help="Go2 wireless IP")
    args = parser.parse_args()

    checks = [
        ("interface", ["ip", "-br", "addr", "show", args.interface]),
        ("route", ["ip", "route", "get", args.ip]),
        ("ping", ["ping", "-I", args.interface, "-c", "4", "-W", "1", args.ip]),
    ]
    failed = False
    for name, command in checks:
        code, output = run(command)
        print(f"== {name} ==")
        print(output)
        if code != 0:
            failed = True
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
