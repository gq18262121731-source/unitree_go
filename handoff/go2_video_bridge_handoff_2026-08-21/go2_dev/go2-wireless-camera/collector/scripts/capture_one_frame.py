from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.unitree_camera import UnitreeVideoClient, decode_jpeg_size


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture one Go2 wireless camera JPEG without robot motion.")
    parser.add_argument("--interface", required=True)
    parser.add_argument("--output", type=Path, default=Path.home() / "go2-wireless-test" / "frame.jpg")
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args()

    client = UnitreeVideoClient(args.interface, args.timeout)
    started = time.monotonic()
    client.initialize()
    code, jpeg = client.get_image_sample()
    elapsed_ms = (time.monotonic() - started) * 1000.0
    if code != 0:
        raise SystemExit(f"SDK returned code={code}")
    width, height = decode_jpeg_size(jpeg)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(jpeg)
    print(f"code={code}")
    print(f"size={width}x{height}")
    print(f"bytes={len(jpeg)}")
    print(f"elapsedMs={elapsed_ms:.1f}")
    print(f"saved={args.output}")


if __name__ == "__main__":
    main()
