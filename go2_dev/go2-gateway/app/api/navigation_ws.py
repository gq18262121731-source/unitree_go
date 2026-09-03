from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.navigation.event_bus import (
    NavigationEventBus,
    NavigationEventBusClosed,
    NavigationSubscription,
)
from app.navigation.events import NavigationWebSocketError, websocket_control_message
from app.navigation.service import NavigationService


router = APIRouter(tags=["mock-navigation"])

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
    code: str,
    message: str,
) -> None:
    await _send_json(
        websocket,
        send_lock,
        NavigationWebSocketError(code=code, message=message).model_dump(mode="json"),
    )


async def _send_events(
    websocket: WebSocket,
    subscription: NavigationSubscription,
    send_lock: asyncio.Lock,
) -> None:
    while True:
        event = await subscription.queue.get()
        if event is None:
            await _send_error(
                websocket,
                send_lock,
                "EVENT_QUEUE_CLOSED",
                "The Mock navigation event queue has closed.",
            )
            with suppress(RuntimeError):
                await websocket.close(code=1012)
            return
        await _send_json(websocket, send_lock, event.model_dump(mode="json"))


async def _receive_with_heartbeat(
    websocket: WebSocket, send_lock: asyncio.Lock
) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(websocket.receive_json(), timeout=HEARTBEAT_IDLE_SECONDS)
    except asyncio.TimeoutError:
        await _send_json(websocket, send_lock, websocket_control_message("ping"))
        try:
            return await asyncio.wait_for(websocket.receive_json(), timeout=HEARTBEAT_GRACE_SECONDS)
        except asyncio.TimeoutError as exc:
            raise TimeoutError("WebSocket heartbeat timed out.") from exc


@router.websocket("/ws/navigation/state")
async def navigation_state_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    send_lock = asyncio.Lock()
    service: NavigationService | None = getattr(
        websocket.app.state, "mock_navigation_service", None
    )
    event_bus: NavigationEventBus | None = getattr(
        websocket.app.state, "mock_navigation_event_bus", None
    )
    if service is None or event_bus is None:
        await _send_error(
            websocket,
            send_lock,
            "NAVIGATION_SERVICE_UNAVAILABLE",
            "Mock navigation service is not initialized.",
        )
        await websocket.close(code=1011)
        return
    capability = service.capabilities()
    if capability.provider != "mock" or capability.real_motion_enabled is not False:
        await _send_error(
            websocket,
            send_lock,
            "MOCK_PROVIDER_CONTRACT_VIOLATION",
            "The navigation provider does not satisfy the Mock-only contract.",
        )
        await websocket.close(code=1008)
        return
    try:
        subscription = event_bus.subscribe()
    except NavigationEventBusClosed:
        await _send_error(
            websocket,
            send_lock,
            "EVENT_QUEUE_CLOSED",
            "The Mock navigation event queue is closed.",
        )
        await websocket.close(code=1012)
        return

    sender: asyncio.Task | None = None
    try:
        snapshot = event_bus.snapshot_event(service.get_state().model_dump(mode="json"))
        await _send_json(websocket, send_lock, snapshot.model_dump(mode="json"))
        sender = asyncio.create_task(_send_events(websocket, subscription, send_lock))
        while True:
            try:
                message = await _receive_with_heartbeat(websocket, send_lock)
            except TimeoutError:
                await _send_error(
                    websocket,
                    send_lock,
                    "HEARTBEAT_TIMEOUT",
                    "The WebSocket client did not answer the heartbeat.",
                )
                await websocket.close(code=1001)
                break
            message_type = message.get("type") if isinstance(message, dict) else None
            if message_type == "pong":
                continue
            if message_type == "ping":
                await _send_json(websocket, send_lock, websocket_control_message("pong"))
                continue
            if message_type == "sync":
                snapshot = event_bus.snapshot_event(service.get_state().model_dump(mode="json"))
                await _send_json(websocket, send_lock, snapshot.model_dump(mode="json"))
                continue
            await _send_error(
                websocket,
                send_lock,
                "INVALID_WEBSOCKET_MESSAGE",
                "Supported message types are ping, pong, and sync.",
            )
    except WebSocketDisconnect:
        pass
    except (ValueError, TypeError):
        with suppress(WebSocketDisconnect, RuntimeError):
            await _send_error(
                websocket,
                send_lock,
                "INVALID_WEBSOCKET_MESSAGE",
                "WebSocket messages must be valid JSON objects.",
            )
    finally:
        event_bus.unsubscribe(subscription)
        if sender is not None:
            sender.cancel()
            with suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await sender
