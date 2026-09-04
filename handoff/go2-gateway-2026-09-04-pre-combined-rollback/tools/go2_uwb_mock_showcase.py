from __future__ import annotations

import sys

from go2_uwb_telemetry import main


if __name__ == "__main__":
    raise SystemExit(main(["--mock", *sys.argv[1:]]))
