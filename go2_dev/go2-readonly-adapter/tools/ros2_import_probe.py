"""Report exactly which Phase 6.1-B ROS2 Python import fails."""

from __future__ import annotations

import importlib
import sys
import traceback


MODULES = (
    "rclpy",
    "rclpy.node",
    "rclpy.qos",
    "sensor_msgs.msg",
    "nav_msgs.msg",
)


def main() -> int:
    print(f"python={sys.executable}")
    print("sys.path:")
    for path in sys.path:
        print(f"  {path}")
    for module_name in MODULES:
        try:
            module = importlib.import_module(module_name)
            print(f"PASS {module_name} {getattr(module, '__file__', None)}")
        except Exception:
            print(f"FAIL {module_name}")
            traceback.print_exc()
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
