from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

import certifi


SERIAL_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


def configure_ca_bundle() -> None:
    target_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Go2Wireless"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "cacert.pem"
    shutil.copyfile(certifi.where(), target)
    os.environ["REQUESTS_CA_BUNDLE"] = str(target)


def main() -> int:
    email = os.environ.get("UNITREE_ACCOUNT_EMAIL", "").strip()
    password = os.environ.get("UNITREE_ACCOUNT_PASSWORD", "")
    serial_number = os.environ.get("GO2_DEVICE_SN", "").strip()
    region = os.environ.get("GO2_CLOUD_REGION", "cn").strip().lower()

    if not email or not password or not serial_number:
        print("Required local provisioning inputs are missing.", file=sys.stderr)
        return 2
    if not SERIAL_PATTERN.fullmatch(serial_number):
        print("The device serial number format is invalid.", file=sys.stderr)
        return 2
    if region not in {"cn", "global"}:
        print("The cloud region must be cn or global.", file=sys.stderr)
        return 2

    configure_ca_bundle()
    from unitree_webrtc_connect import fetch_aes_key

    try:
        key = fetch_aes_key(
            email=email,
            password=password,
            sn=serial_number,
            region=region,
            device_type="Go2",
        )
    except Exception as exc:
        print(
            f"{type(exc).__name__}: device key lookup failed; verify the App account, "
            "binding, cloud region, device serial number, Internet access, and firmware.",
            file=sys.stderr,
        )
        return 1
    finally:
        os.environ.pop("UNITREE_ACCOUNT_PASSWORD", None)

    if not re.fullmatch(r"[0-9A-Fa-f]{32}", key or ""):
        print("The cloud did not return a valid 32-hex device key.", file=sys.stderr)
        return 1

    # stdout is intentionally key-only. The parent PowerShell process captures it
    # in memory and immediately protects it with Windows DPAPI.
    print(key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
