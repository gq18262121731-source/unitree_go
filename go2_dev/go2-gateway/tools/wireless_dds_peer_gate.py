"""Run the bounded, subscriber-only Go2 Wi-Fi DDS discovery matrix.

This tool never creates an application publisher or SportClient. Each matrix
entry runs in a fresh Python process because Unitree's ChannelFactory is a
process singleton and cannot safely switch DDS domains after initialization.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


MATRIX = (
    (0, "multicast-peer"),
    (0, "unicast-peer"),
    (10, "multicast-peer"),
    (10, "unicast-peer"),
)


def parse_probe_json(output: str) -> dict:
    for index, character in enumerate(output):
        if character != "{":
            continue
        try:
            return json.loads(output[index:])
        except json.JSONDecodeError:
            continue
    raise ValueError("probe did not emit a JSON object")


def probe_command(
    python: str,
    probe: Path,
    interface: str,
    peer: str,
    domain_id: int,
    discovery_mode: str,
    timeout: float,
) -> list[str]:
    return [
        python,
        str(probe),
        "--interface",
        interface,
        "--peer",
        peer,
        "--domain-id",
        str(domain_id),
        "--discovery-mode",
        discovery_mode,
        "--timeout",
        str(timeout),
    ]


def compact_result(payload: dict, return_code: int) -> dict:
    sport = payload.get("sportState") or {}
    low = payload.get("lowState") or {}
    return {
        "domainId": payload.get("domainId"),
        "discoveryMode": payload.get("discoveryMode"),
        "ddsInitialized": payload.get("ddsInitialized", False),
        "sportSampleCount": sport.get("sampleCount", 0),
        "lowSampleCount": low.get("sampleCount", 0),
        "sportTimeoutCode": sport.get("timeoutCode"),
        "lowTimeoutCode": low.get("timeoutCode"),
        "errorCode": payload.get("errorCode"),
        "probeReturnCode": return_code,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Subscriber-only Domain 0/10 multicast/unicast Go2 Wi-Fi DDS gate."
    )
    parser.add_argument("--interface", required=True)
    parser.add_argument("--peer", required=True)
    parser.add_argument("--timeout", type=float, default=12.0)
    args = parser.parse_args()
    if args.timeout <= 0 or args.timeout > 30:
        parser.error("--timeout must be within (0, 30] seconds")

    probe = Path(__file__).with_name("dds_diagnostics.py")
    summary = {
        "interface": args.interface,
        "peer": args.peer,
        "subscriberOnly": True,
        "publisherCreated": False,
        "sportClientCreated": False,
        "motionCommandsSent": False,
        "stoppedOnFirstSamples": False,
        "results": [],
        "status": "RUNNING",
    }

    for domain_id, discovery_mode in MATRIX:
        command = probe_command(
            sys.executable,
            probe,
            args.interface,
            args.peer,
            domain_id,
            discovery_mode,
            args.timeout,
        )
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=args.timeout + 10.0,
                check=False,
            )
        except subprocess.TimeoutExpired:
            summary["results"].append(
                {
                    "domainId": domain_id,
                    "discoveryMode": discovery_mode,
                    "errorCode": "PROBE_PROCESS_TIMEOUT",
                }
            )
            continue
        try:
            payload = parse_probe_json(completed.stdout)
            result = compact_result(payload, completed.returncode)
        except ValueError as exc:
            result = {
                "domainId": domain_id,
                "discoveryMode": discovery_mode,
                "errorCode": "PROBE_OUTPUT_INVALID",
                "error": str(exc),
                "probeReturnCode": completed.returncode,
                "stderr": completed.stderr.strip()[-1000:],
            }
        summary["results"].append(result)

        if result.get("sportSampleCount", 0) > 0 or result.get("lowSampleCount", 0) > 0:
            summary["stoppedOnFirstSamples"] = True
            summary["status"] = "WIRELESS_DDS_SAMPLES_DETECTED"
            break

    if summary["status"] == "RUNNING":
        summary["status"] = "WIRELESS_DDS_NO_SAMPLES_IN_MATRIX"

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["status"] != "WIRELESS_DDS_SAMPLES_DETECTED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
