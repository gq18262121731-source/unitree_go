from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path

from werkzeug.serving import make_server


SOURCE_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", SOURCE_ROOT))
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from app.companion.config_loader import load_companion_demo_config
from app.telemetry.uwb_dashboard import (
    CompanionStatusSource,
    MockTelemetrySource,
    create_dashboard,
    quiet_dashboard_logs,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Go2 UWB伴随实时监测（只读比赛界面）"
    )
    parser.add_argument("--mock", action="store_true", help="使用内置模拟遥测")
    parser.add_argument(
        "--wireless",
        action="store_true",
        help="读取本机8093端口的WebRTC无线Companion Runtime",
    )
    parser.add_argument("--debug", action="store_true", help="显示开发诊断信息")
    parser.add_argument(
        "--interface",
        help="记录 Gateway 使用的网络接口；Dashboard 本身不初始化 DDS",
    )
    parser.add_argument(
        "--status-url",
        default=None,
        help="现有 Companion Runtime 的只读状态接口",
    )
    parser.add_argument(
        "--config",
        default=os.getenv(
            "GO2_COMPANION_CONFIG", "configs/companion_follow_demo.yaml"
        ),
        help="用于首帧占位和 Mock 的伴随配置",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Web监听地址")
    parser.add_argument("--port", type=int, default=8050, help="Web监听端口")
    parser.add_argument(
        "--no-open-browser", action="store_true", help="启动后不自动打开浏览器"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_companion_demo_config(_resolve_config_path(args.config))
    profile = config.follow
    if args.mock:
        source = MockTelemetrySource(
            target_distance_m=profile.target_distance,
            target_bearing_rad=profile.target_bearing_radians,
        )
    else:
        status_url = args.status_url or (
            "http://127.0.0.1:8093/api/v1/robot/companion/status"
            if args.wireless
            else "http://127.0.0.1:8090/api/v1/robot/companion/status"
        )
        source = CompanionStatusSource(
            status_url,
            target_distance_m=profile.target_distance,
            target_bearing_rad=profile.target_bearing_radians,
            interface=args.interface,
        )

    quiet_dashboard_logs(args.debug)
    app = create_dashboard(
        source,
        assets_folder=str(BUNDLE_ROOT / "assets"),
        debug_mode=args.debug,
    )
    browser_url = f"http://127.0.0.1:{args.port}"
    if not args.no_open_browser:
        browser_timer = threading.Timer(1.0, lambda: webbrowser.open(browser_url))
        browser_timer.daemon = True
        browser_timer.start()

    mode = "模拟数据" if args.mock else "真实 Runtime 只读状态"
    print(f"Go2 UWB伴随实时监测：{browser_url}（{mode}）", flush=True)
    server = make_server(args.host, args.port, app.server, threaded=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _resolve_config_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else BUNDLE_ROOT / path


if __name__ == "__main__":
    raise SystemExit(main())
