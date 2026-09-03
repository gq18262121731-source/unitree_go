from __future__ import annotations

import asyncio
import getpass
import json
import os
import tempfile
import time
import wave
from datetime import datetime
from pathlib import Path

from aiortc import RTCBundlePolicy, RTCConfiguration
from unitree_webrtc_connect.webrtc_driver import (
    UnitreeWebRTCConnection,
    WebRTCConnectionMethod,
)


CAPTURE_SECONDS = 5.0
CONNECT_TIMEOUT_SECONDS = float(
    os.environ.get("GO2_CONNECT_TIMEOUT_SECONDS", "75")
)
CAPTURE_TIMEOUT_SECONDS = 12.0
BUNDLE_POLICY = os.environ.get(
    "GO2_BUNDLE_POLICY",
    "max-bundle",
).strip().lower()
CONNECTION_MODE = os.environ.get(
    "GO2_CONNECTION_MODE",
    "remote",
).strip().lower()


class BundlePolicyConnection(UnitreeWebRTCConnection):
    def create_webrtc_configuration(
        self,
        turn_server_info,
        stunEnable=True,
        turnEnable=True,
    ) -> RTCConfiguration:
        base = super().create_webrtc_configuration(
            turn_server_info,
            stunEnable=stunEnable,
            turnEnable=turnEnable,
        )
        if BUNDLE_POLICY == "balanced":
            return base
        if BUNDLE_POLICY != "max-bundle":
            raise ValueError(
                "GO2_BUNDLE_POLICY must be 'balanced' or 'max-bundle'"
            )
        return RTCConfiguration(
            iceServers=base.iceServers,
            bundlePolicy=RTCBundlePolicy.MAX_BUNDLE,
        )


def prompt_remote_credentials() -> tuple[str, str, str, str]:
    email = input("Unitree account email: ").strip()
    password = getpass.getpass("Unitree account password: ")
    serial_number = input("Go2 serial number: ").strip()
    region = input("Region [cn/global] (default cn): ").strip().lower() or "cn"

    if not email or not password or not serial_number:
        raise ValueError("email, password, and serial number are required")
    if region not in {"cn", "global"}:
        raise ValueError("region must be cn or global")
    return email, password, serial_number, region


def create_connection() -> BundlePolicyConnection:
    if CONNECTION_MODE == "remote":
        email, password, serial_number, region = prompt_remote_credentials()
        return BundlePolicyConnection(
            WebRTCConnectionMethod.Remote,
            serialNumber=serial_number,
            username=email,
            password=password,
            region=region,
            device_type="Go2",
        )
    if CONNECTION_MODE == "local":
        robot_ip = (
            input("Go2 IP address (default 192.168.123.161): ").strip()
            or "192.168.123.161"
        )
        aes_key = getpass.getpass("UNITREE AES-128 key: ").strip()
        if len(aes_key) != 32 or any(
            character not in "0123456789abcdefABCDEF"
            for character in aes_key
        ):
            raise ValueError(
                "UNITREE AES-128 key must be 32 hexadecimal characters"
            )
        return BundlePolicyConnection(
            WebRTCConnectionMethod.LocalSTA,
            ip=robot_ip,
            aes_128_key=aes_key,
        )
    raise ValueError("GO2_CONNECTION_MODE must be 'local' or 'remote'")


async def capture_audio(
    connection: BundlePolicyConnection,
) -> dict[str, object]:
    started = time.perf_counter()
    connected_at: float | None = None
    first_frame_at: float | None = None
    audio_enabled = False
    capture_complete = asyncio.Event()
    chunks: list[bytes] = []
    captured_frames = 0
    metadata: dict[str, object] = {}
    callback_error: BaseException | None = None

    async def receive_audio_frame(frame) -> None:
        nonlocal callback_error, captured_frames, first_frame_at
        if capture_complete.is_set():
            return
        try:
            frame_format = str(frame.format.name).lower()
            sample_rate = int(frame.sample_rate)
            samples = int(frame.samples)
            channels = len(frame.layout.channels)
            array = frame.to_ndarray()

            if frame_format != "s16":
                raise RuntimeError(
                    f"unsupported audio format {frame_format!r}; expected 's16'"
                )
            if sample_rate <= 0 or samples <= 0 or channels <= 0:
                raise RuntimeError("received incomplete audio frame metadata")
            if array.dtype.itemsize != 2:
                raise RuntimeError(
                    f"unsupported sample width {array.dtype.itemsize}; expected 2"
                )

            current = {
                "sample_rate": sample_rate,
                "channels": channels,
                "sample_width": int(array.dtype.itemsize),
                "format": frame_format,
                "layout": str(frame.layout.name),
            }
            if not metadata:
                metadata.update(current)
            elif current != metadata:
                raise RuntimeError(
                    f"audio format changed: expected {metadata}, received {current}"
                )

            if first_frame_at is None:
                first_frame_at = time.perf_counter()
            chunks.append(array.tobytes())
            captured_frames += samples
            if captured_frames >= int(CAPTURE_SECONDS * sample_rate):
                capture_complete.set()
        except BaseException as exc:
            callback_error = exc
            capture_complete.set()

    try:
        await asyncio.wait_for(
            connection.connect(),
            timeout=CONNECT_TIMEOUT_SECONDS,
        )
        connected_at = time.perf_counter()

        connection.audio.add_track_callback(receive_audio_frame)
        await connection.datachannel.disableTrafficSaving(True)
        await asyncio.sleep(0.3)
        connection.audio.switchAudioChannel(True)
        audio_enabled = True

        print(
            f"Speak clearly near the Go2 microphone for {CAPTURE_SECONDS:g} seconds."
        )
        await asyncio.wait_for(
            capture_complete.wait(),
            timeout=CAPTURE_TIMEOUT_SECONDS,
        )
        if callback_error is not None:
            raise callback_error
        if not chunks or not metadata:
            raise RuntimeError("capture completed without audio frames")

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        policy_label = BUNDLE_POLICY.replace("-", "_")
        output_path = (
            Path(tempfile.gettempdir())
            / (
                f"go2-upstream-{CONNECTION_MODE}-"
                f"{policy_label}-{timestamp}.wav"
            )
        )
        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setnchannels(int(metadata["channels"]))
            wav_file.setsampwidth(int(metadata["sample_width"]))
            wav_file.setframerate(int(metadata["sample_rate"]))
            wav_file.writeframes(b"".join(chunks))

        finished = time.perf_counter()
        return {
            "status": "CAPTURED",
            "connection_mode": CONNECTION_MODE,
            "bundle_policy": BUNDLE_POLICY,
            "path": str(output_path),
            "frames": captured_frames,
            "duration_seconds": round(
                captured_frames / int(metadata["sample_rate"]),
                3,
            ),
            "connect_latency_seconds": round(connected_at - started, 3),
            "first_frame_latency_seconds": (
                round(first_frame_at - connected_at, 3)
                if first_frame_at is not None
                else None
            ),
            "total_seconds": round(finished - started, 3),
            "frame": metadata,
        }
    finally:
        if audio_enabled:
            try:
                connection.audio.switchAudioChannel(False)
            except Exception:
                pass
        try:
            await connection.disconnect()
        except Exception:
            pass


def main() -> None:
    try:
        connection = create_connection()
        result = asyncio.run(
            capture_audio(connection)
        )
    except Exception as exc:
        error_message = str(exc)
        if isinstance(exc, TimeoutError) and not error_message:
            error_message = (
                f"WebRTC connection did not complete within "
                f"{CONNECT_TIMEOUT_SECONDS:g} seconds"
            )
        result = {
            "status": "ERROR",
            "connection_mode": CONNECTION_MODE,
            "bundle_policy": BUNDLE_POLICY,
            "error_type": exc.__class__.__name__,
            "error": error_message,
        }
        print("P0B_REMOTE_RESULT=" + json.dumps(result, ensure_ascii=False))
        raise SystemExit(1) from None
    print("P0B_REMOTE_RESULT=" + json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
