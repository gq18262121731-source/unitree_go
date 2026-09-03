from __future__ import annotations

import asyncio
import getpass
import json
import os
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(r"D:\health_new")
DEFAULT_WAV = Path(
    r"E:\笨笨狗\go2_dev\unitree_webrtc_connect"
    r"\examples\go2\audio\mp3_player\dog-barking.wav"
)
BUNDLE_POLICY = os.environ.get("GO2_BUNDLE_POLICY", "balanced").strip().lower()
ICE_HOST_ADDRESS = os.environ.get("GO2_ICE_HOST_ADDRESS", "").strip()


def constrain_ice_host_candidates() -> None:
    if not ICE_HOST_ADDRESS:
        return
    import aioice.ice

    def get_host_addresses(use_ipv4: bool, use_ipv6: bool) -> list[str]:
        return [ICE_HOST_ADDRESS] if use_ipv4 else []

    aioice.ice.get_host_addresses = get_host_addresses


def build_connection_factory(robot_ip: str, aes_key: str):
    if BUNDLE_POLICY == "balanced":
        return None
    if BUNDLE_POLICY != "max-bundle":
        raise ValueError("GO2_BUNDLE_POLICY must be balanced or max-bundle")

    from aiortc import RTCBundlePolicy, RTCConfiguration
    from unitree_webrtc_connect.webrtc_driver import (
        UnitreeWebRTCConnection,
        WebRTCConnectionMethod,
    )

    class MaxBundleConnection(UnitreeWebRTCConnection):
        def create_webrtc_configuration(
            self,
            turn_server_info,
            stunEnable=True,
            turnEnable=True,
        ):
            base = super().create_webrtc_configuration(
                turn_server_info,
                stunEnable=stunEnable,
                turnEnable=turnEnable,
            )
            return RTCConfiguration(
                iceServers=base.iceServers,
                bundlePolicy=RTCBundlePolicy.MAX_BUNDLE,
            )

    return lambda: MaxBundleConnection(
        WebRTCConnectionMethod.LocalSTA,
        ip=robot_ip,
        aes_128_key=aes_key,
    )


def main() -> None:
    constrain_ice_host_candidates()
    sys.path.insert(0, str(PROJECT_ROOT))
    from backend.services.robot_audio.webrtc_sink import Go2WebRTCAudioSink

    robot_ip = input("Go2 IP [192.168.123.161]: ").strip() or "192.168.123.161"
    aes_key = getpass.getpass("UNITREE AES-128 key: ").strip()
    if len(aes_key) != 32 or any(
        character not in "0123456789abcdefABCDEF" for character in aes_key
    ):
        raise SystemExit("AES key must be 32 hexadecimal characters")
    if not DEFAULT_WAV.is_file():
        raise SystemExit(f"Test WAV not found: {DEFAULT_WAV}")

    sink = Go2WebRTCAudioSink(
        robot_ip,
        aes_128_key=aes_key,
        play_timeout_seconds=20.0,
        connection_factory=build_connection_factory(robot_ip, aes_key),
    )
    started = time.perf_counter()
    result = asyncio.run(
        sink.play(DEFAULT_WAV, audio_id="historical-sink-replay")
    )
    print(
        "HISTORICAL_SINK_RESULT="
        + json.dumps(
            {
                "audio_id": result.audio_id,
                "bundle_policy": BUNDLE_POLICY,
                "ice_host_address": ICE_HOST_ADDRESS or "all",
                "task_state": result.status.value,
                "sink_state": sink.state.value,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "error": result.error,
                "wav": {
                    "name": DEFAULT_WAV.name,
                    "duration_seconds": 4.101,
                    "sample_rate_hz": 44100,
                    "channels": 2,
                    "sample_width_bits": 16,
                },
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
