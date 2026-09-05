from __future__ import annotations

import asyncio
import threading

from unitree_webrtc_connect.constants import WebRTCConnectionMethod
from unitree_webrtc_connect import unitree_auth
from unitree_webrtc_connect import webrtc_driver


class _Response:
    status_code = 200
    text = "ok"

    def raise_for_status(self) -> None:
        return None


def test_local_signaling_http_uses_bounded_connect_and_read_timeouts(
    monkeypatch,
) -> None:
    recorded: dict[str, object] = {}

    def post(**kwargs):
        recorded.update(kwargs)
        return _Response()

    monkeypatch.setattr(unitree_auth.requests, "post", post)

    response = unitree_auth.make_local_request("http://192.168.8.245:9991/test")

    assert response is not None
    assert recorded["timeout"] == (2.0, 3.0)


def test_local_signaling_request_does_not_block_aiortc_event_loop(
    monkeypatch,
) -> None:
    started = threading.Event()
    release = threading.Event()

    def blocking_signaling(ip, payload, *, aes_128_key=None):
        started.set()
        assert release.wait(timeout=1.0)
        return '{"sdp":"answer","type":"answer"}'

    monkeypatch.setattr(
        webrtc_driver,
        "send_sdp_to_local_peer",
        blocking_signaling,
    )
    connection = webrtc_driver.UnitreeWebRTCConnection(
        WebRTCConnectionMethod.LocalSTA,
        ip="192.168.8.245",
    )
    connection.pc = type(
        "Peer",
        (),
        {
            "localDescription": type(
                "Description",
                (),
                {"sdp": "offer", "type": "offer"},
            )()
        },
    )()

    async def exercise() -> None:
        task = asyncio.create_task(
            connection.get_answer_from_local_peer(
                connection.pc,
                "192.168.8.245",
            )
        )
        for _ in range(50):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        assert started.is_set()
        # This line can execute only if the synchronous requests call was
        # moved away from the event-loop thread.
        release.set()
        assert await asyncio.wait_for(task, timeout=1.0) == (
            '{"sdp":"answer","type":"answer"}'
        )

    asyncio.run(exercise())
