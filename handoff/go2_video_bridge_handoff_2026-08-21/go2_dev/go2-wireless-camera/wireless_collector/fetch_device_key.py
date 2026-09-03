from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import certifi


def configure_ca_bundle() -> None:
    target_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Go2Wireless"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "cacert.pem"
    shutil.copyfile(certifi.where(), target)
    os.environ["REQUESTS_CA_BUNDLE"] = str(target)


def main() -> int:
    configure_ca_bundle()
    from unitree_webrtc_connect import fetch_aes_key

    email = os.environ.get("UNITREE_ACCOUNT_EMAIL", "").strip()
    password = os.environ.get("UNITREE_ACCOUNT_PASSWORD", "")
    if not email or not password:
        print("Unitree email and password are required.", file=sys.stderr)
        return 2

    try:
        key = fetch_aes_key(
            email=email,
            password=password,
            sn="B42N6000Q3PABHGC",
            region="cn",
            device_type="Go2",
        )
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        os.environ.pop("UNITREE_ACCOUNT_PASSWORD", None)

    print(key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
