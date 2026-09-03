from __future__ import annotations

import asyncio
import getpass
import json
import logging
import re
import time

from unitree_webrtc_connect.webrtc_driver import (
    UnitreeWebRTCConnection,
    WebRTCConnectionMethod,
)


ROBOT_IP = "192.168.123.161"
OBSERVE_SECONDS = 12.0


class AnswerCaptureConnection(UnitreeWebRTCConnection):
    answer_json: str | None = None

    async def get_answer_from_local_peer(self, pc, ip):
        answer = await super().get_answer_from_local_peer(pc, ip)
        self.answer_json = answer
        return answer


def configure_ice_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("ICE_TRACE %(message)s"))
    logger = logging.getLogger("aioice.ice")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False


def summarize_sdp(sdp: str | None) -> dict[str, object]:
    if not sdp:
        return {"bundle": [], "media": [], "candidates": []}

    bundle: list[str] = []
    media: list[str] = []
    candidates: list[dict[str, object]] = []
    current_media = "session"
    for raw_line in sdp.splitlines():
        line = raw_line.strip()
        if line.startswith("a=group:BUNDLE "):
            bundle = line.removeprefix("a=group:BUNDLE ").split()
        elif line.startswith("m="):
            parts = line[2:].split()
            current_media = parts[0] if parts else "unknown"
            media.append(current_media)
        elif line.startswith("a=candidate:"):
            parts = re.split(r"\s+", line.removeprefix("a=candidate:"))
            if len(parts) < 8:
                continue
            candidate: dict[str, object] = {
                "media": current_media,
                "component": parts[1],
                "protocol": parts[2].lower(),
                "address": parts[4],
                "port": int(parts[5]),
                "type": parts[7],
            }
            if "raddr" in parts:
                index = parts.index("raddr")
                if index + 1 < len(parts):
                    candidate["related_address"] = parts[index + 1]
            if "rport" in parts:
                index = parts.index("rport")
                if index + 1 < len(parts):
                    candidate["related_port"] = int(parts[index + 1])
            candidates.append(candidate)
    return {"bundle": bundle, "media": media, "candidates": candidates}


async def diagnose(aes_key: str) -> dict[str, object]:
    connection = AnswerCaptureConnection(
        WebRTCConnectionMethod.LocalSTA,
        ip=ROBOT_IP,
        aes_128_key=aes_key,
    )
    started = time.perf_counter()
    connect_task = asyncio.create_task(connection.connect())
    result: dict[str, object] = {
        "robot_ip": ROBOT_IP,
        "observe_seconds": OBSERVE_SECONDS,
    }
    try:
        deadline = asyncio.get_running_loop().time() + OBSERVE_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            if connection.answer_json is not None or connect_task.done():
                break
            await asyncio.sleep(0.05)

        answer_sdp = None
        if connection.answer_json:
            answer_sdp = json.loads(connection.answer_json).get("sdp")
        pc = connection.pc
        local_sdp = None
        if pc is not None and pc.localDescription is not None:
            local_sdp = pc.localDescription.sdp

        result["local_offer"] = summarize_sdp(local_sdp)
        result["robot_answer"] = summarize_sdp(answer_sdp)

        remaining = max(
            0.0,
            deadline - asyncio.get_running_loop().time(),
        )
        if remaining:
            try:
                await asyncio.wait_for(
                    asyncio.shield(connect_task),
                    timeout=remaining,
                )
            except TimeoutError:
                pass

        pc = connection.pc
        result.update(
            {
                "connect_task_done": connect_task.done(),
                "peer_state": getattr(pc, "connectionState", None),
                "ice_state": getattr(pc, "iceConnectionState", None),
                "signaling_state": getattr(pc, "signalingState", None),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }
        )
        if connect_task.done():
            error = connect_task.exception()
            result["connect_error"] = (
                None
                if error is None
                else f"{error.__class__.__name__}: {error}"
            )
        else:
            result["connect_error"] = "observation window elapsed"
        return result
    finally:
        if not connect_task.done():
            connect_task.cancel()
            try:
                await connect_task
            except BaseException:
                pass
        try:
            await connection.disconnect()
        except Exception:
            pass


def main() -> None:
    configure_ice_logging()
    aes_key = getpass.getpass("UNITREE AES-128 key: ").strip()
    if len(aes_key) != 32 or any(
        character not in "0123456789abcdefABCDEF" for character in aes_key
    ):
        raise SystemExit("AES key must be 32 hexadecimal characters")
    result = asyncio.run(diagnose(aes_key))
    print("ICE_DIAGNOSTIC_RESULT=" + json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
