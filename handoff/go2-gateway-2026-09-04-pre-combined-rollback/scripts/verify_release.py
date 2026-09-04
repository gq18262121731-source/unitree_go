from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIRS = ["app", "scripts", "demo", "tests"]


def run_step(name: str, command: list[str]) -> None:
    print(f"== {name} ==", flush=True)
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode != 0:
        raise SystemExit(f"FAILED: {name} exited with {result.returncode}")


def python_files() -> list[str]:
    files: list[str] = []
    for directory in PYTHON_DIRS:
        for path in (ROOT / directory).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            files.append(str(path.relative_to(ROOT)))
    return sorted(files)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run safe release checks for the Go2 gateway.")
    parser.add_argument("--skip-pytest", action="store_true")
    parser.add_argument("--skip-contract", action="store_true")
    parser.add_argument("--skip-compile", action="store_true")
    parser.add_argument(
        "--running-base-url",
        default="",
        help="Optionally run non-motion HTTP preflight against an already running gateway.",
    )
    parser.add_argument("--allow-readonly", action="store_true", help="Allow CONTROL_DISABLED for running preflight.")
    args = parser.parse_args()

    if not args.skip_compile:
        run_step("python compile", [sys.executable, "-m", "py_compile", *python_files()])
    if not args.skip_contract:
        run_step("health_new contract", [sys.executable, "scripts/verify_health_new_contract.py"])
    if not args.skip_pytest:
        run_step("pytest", [sys.executable, "-m", "pytest", "-q"])
    if args.running_base_url:
        preflight_command = [
            sys.executable,
            "scripts/verify_preflight.py",
            "--base-url",
            args.running_base_url,
        ]
        preflight_command.append("--allow-readonly" if args.allow_readonly else "--require-ready")
        run_step("running gateway preflight", preflight_command)

    print("release verification passed")


if __name__ == "__main__":
    main()
