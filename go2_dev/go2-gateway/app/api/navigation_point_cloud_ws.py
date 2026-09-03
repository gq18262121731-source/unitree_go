from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.navigation.mock_point_cloud import (
    MockPointCloudStream,
    PointCloudDomainError,
    PointCloudSubscription,
)
from app.navigation.point_cloud_models import (
    PointCloudErrorCode,
    PointCloudStreamError,
)
from app.navigation.models import utc_now


router = APIRouter(tags=["mock-navigation-point-cloud"])

HEARTBEAT_IDLE_SECONDS = 30.0
HEARTBEAT_GRACE_SECONDS = 10.0


async def _send_json(
    websocket: WebSocket, send_lock: asyncio.Lock, payload: dict[str, Any]
) -> None:
    async with send_lock:
        await websocket.send_json(payload)


async def _send_error(
    websocket: WebSocket,
    send_lock: asyncio.Lock,
    code: PointCloudErrorCode,
    message: str,
) -> None:
    error = PointCloudStreamError(code=code, message=message)
    await _send_json(websocket, send_lock, error.model_dump(mode="json"))


async def _send_frames(
    websocket: WebSocket,
    subscription: PointCloudSubscription,
    send_lock: asyncio.Lock,
) -> None:
    while True:
        message = await subscription.queue.get()
        if message is None:
            await _send_error(
                websocket,
                send_lock,
                PointCloudErrorCode.POINT_CLOUD_STREAM_UNAVAILABLE,
                "The Mock point-cloud stream has closed.",
            )
            with suppress(RuntimeError):
                await websocket.close(code=1012)
            return
        await _send_json(websocket, send_lock, message.model_dump(mode="json"))


async def _receive_with_heartbeat(
    websocket: WebSocket, send_lock: asyncio.Lock
) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(websocket.receive_json(), timeout=HEARTBEAT_IDLE_SECONDS)
    except asyncio.TimeoutError:
        await _send_json(
            websocket,
            send_lock,
            {
                "type": "ping",
                "timestamp": utc_now().isoformat(),
                "provider": "mock",
                "real_motion_enabled": False,
            },
        )
        try:
            return await asyncio.wait_for(websocket.receive_json(), timeout=HEARTBEAT_GRACE_SECONDS)
        except asyncio.TimeoutError as exc:
            raise TimeoutError("Point-cloud WebSocket heartbeat timed out.") from exc


@router.websocket("/ws/navigation/point-cloud")
async def navigation_point_cloud_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    send_lock = asyncio.Lock()
    stream: MockPointCloudStream | None = getattr(
        websocket.app.state, "mock_point_cloud_stream", None
    )
    if stream is None:
        await _send_error(
            websocket,
            send_lock,
            PointCloudErrorCode.NAVIGATION_STORE_UNAVAILABLE,
            "Navigation Store is unavailable for the Mock point-cloud stream.",
        )
        await websocket.close(code=1011)
        return
    try:
        subscription = await stream.subscribe()
    except PointCloudDomainError as exc:
        await _send_error(websocket, send_lock, exc.code, exc.message)
        await websocket.close(code=1012)
        return

    sender: asyncio.Task | None = None
    try:
        info = stream.stream_info()
        if info.provider != "mock" or info.real_motion_enabled is not False:
            await _send_error(
                websocket,
                send_lock,
                PointCloudErrorCode.POINT_CLOUD_PROVIDER_CONTRACT_VIOLATION,
                "Point-cloud provider does not satisfy the Mock-only contract.",
            )
            await websocket.close(code=1008)
            return
        await _send_json(websocket, send_lock, info.model_dump(mode="json"))
        first_message = await subscription.queue.get()
        if first_message is None:
            await _send_error(
                websocket,
                send_lock,
                PointCloudErrorCode.POINT_CLOUD_STREAM_UNAVAILABLE,
                "The Mock point-cloud stream closed before its first frame.",
            )
            return
        await _send_json(websocket, send_lock, first_message.model_dump(mode="json"))
        sender = asyncio.create_task(_send_frames(websocket, subscription, send_lock))
        while True:
            try:
                message = await _receive_with_heartbeat(websocket, send_lock)
            except TimeoutError:
                await _send_error(
                    websocket,
                    send_lock,
                    PointCloudErrorCode.HEARTBEAT_TIMEOUT,
                    "The point-cloud client did not answer the heartbeat.",
                )
                await websocket.close(code=1001)
                break
            message_type = message.get("type") if isinstance(message, dict) else None
            if message_type == "pong":
                continue
            if message_type == "ping":
                await _send_json(
                    websocket,
                    send_lock,
                    {
                        "type": "pong",
                        "timestamp": utc_now().isoformat(),
                        "provider": "mock",
                        "real_motion_enabled": False,
                    },
                )
                continue
            if message_type == "sync":
                await _send_json(
                    websocket,
                    send_lock,
                    stream.stream_info().model_dump(mode="json"),
                )
                latest = stream.latest_message()
                if latest is not None:
                    await _send_json(websocket, send_lock, latest.model_dump(mode="json"))
                continue
            await _send_error(
                websocket,
                send_lock,
                PointCloudErrorCode.INVALID_WEBSOCKET_MESSAGE,
                "Supported message types are ping, pong, and sync.",
            )
    except WebSocketDisconnect:
        pass
    except (ValueError, TypeError):
        with suppress(WebSocketDisconnect, RuntimeError):
            await _send_error(
                websocket,
                send_lock,
                PointCloudErrorCode.INVALID_WEBSOCKET_MESSAGE,
                "WebSocket messages must be valid JSON objects.",
            )
    finally:
        await stream.unsubscribe(subscription)
        if sender is not None:
            sender.cancel()
            with suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await sender
