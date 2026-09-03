from __future__ import annotations

import argparse
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAILED: {message}")


def read_json(url: str, timeout: float) -> dict:
    try:
        with urlopen(Request(url, method="GET"), timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise SystemExit(f"FAILED: cannot reach gateway: {exc}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a non-motion preflight check against a running Go2 gateway.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8090")
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--require-ready", action="store_true", help="Fail unless /api/preflight reports dispatch_ready=true.")
    parser.add_argument(
        "--allow-readonly",
        action="store_true",
        help="Allow CONTROL_DISABLED preflight state for read-only real-robot checks.",
    )
    parser.add_argument("--dump-json", action="store_true", help="Print the full preflight payload.")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    health = read_json(f"{base_url}/health", args.timeout_seconds)["data"]
    preflight = read_json(f"{base_url}/api/preflight", args.timeout_seconds)["data"]
    capabilities = read_json(f"{base_url}/api/capabilities", args.timeout_seconds)["data"]

    expect(health["service"] == "go2-gateway", f"unexpected service: {health}")
    expect(capabilities["status"]["preflight_url"] == "/api/preflight", f"missing preflight capability: {capabilities}")
    expect(preflight["camera"]["sampled"] is False, f"preflight sampled camera unexpectedly: {preflight}")
    expect(preflight["checks"]["sdk_initialized"]["ok"] is True, f"SDK is not initialized: {preflight}")
    expect(preflight["checks"]["robot_online"]["ok"] is True, f"robot is offline: {preflight}")
    expect(preflight["checks"]["state_fresh"]["ok"] is True, f"robot state is stale: {preflight}")
    expect(preflight["checks"]["dispatch_idle"]["ok"] is True, f"robot already has an active task: {preflight}")
    expect("voice" in preflight, f"preflight missing voice status: {preflight}")
    expect("voice_ready" in preflight["checks"], f"preflight missing voice readiness check: {preflight}")

    next_action = preflight.get("next_action")
    if args.require_ready:
        expect(preflight["dispatch_ready"] is True, f"gateway is not dispatch-ready: {preflight}")
    elif args.allow_readonly and next_action == "CONTROL_DISABLED":
        pass
    else:
        expect(preflight["checks"]["dispatch_accepting"]["ok"] is True, f"gateway is not accepting tasks: {preflight}")
        expect(
            preflight["dispatch_ready"] is True or next_action is None,
            f"preflight reported a dispatch blocker: {preflight}",
        )

    print(
        "preflight ok "
        f"mode={preflight.get('mode')} "
        f"dispatch_ready={preflight.get('dispatch_ready')} "
        f"dispatch_accepting={preflight.get('dispatch_accepting')} "
        f"next_action={next_action} "
        f"robot_online={preflight['connection'].get('online')} "
        f"camera={preflight['camera'].get('camera')} "
        f"voice_ready={preflight['checks']['voice_ready'].get('ok')} "
        f"voice_mode={preflight['voice'].get('mode')} "
        f"feedback_pending={preflight['feedback'].get('pending')}"
    )
    if args.dump_json:
        print(json.dumps(preflight, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
