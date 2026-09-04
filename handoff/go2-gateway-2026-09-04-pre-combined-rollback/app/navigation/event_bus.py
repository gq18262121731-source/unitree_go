from __future__ import annotations

import asyncio
from dataclasses import dataclass
from threading import RLock
from typing import Any

from app.navigation.events import NavigationEvent, NavigationEventType


class NavigationEventBusClosed(RuntimeError):
    pass


@dataclass
class NavigationSubscription:
    subscription_id: int
    queue: asyncio.Queue[NavigationEvent | None]
    loop: asyncio.AbstractEventLoop
    dropped_events: int = 0
    closed: bool = False


class NavigationEventBus:
    def __init__(self, *, subscriber_queue_size: int = 32) -> None:
        if subscriber_queue_size < 1:
            raise ValueError("subscriber_queue_size must be positive")
        self.subscriber_queue_size = subscriber_queue_size
        self._lock = RLock()
        self._subscribers: dict[int, NavigationSubscription] = {}
        self._next_subscription_id = 0
        self._sequence = 0
        self._closed = False

    def subscribe(self) -> NavigationSubscription:
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._closed:
                raise NavigationEventBusClosed("Navigation event bus is closed.")
            self._next_subscription_id += 1
            subscription = NavigationSubscription(
                subscription_id=self._next_subscription_id,
                queue=asyncio.Queue(maxsize=self.subscriber_queue_size),
                loop=loop,
            )
            self._subscribers[subscription.subscription_id] = subscription
            return subscription

    def unsubscribe(self, subscription: NavigationSubscription) -> None:
        with self._lock:
            stored = self._subscribers.pop(subscription.subscription_id, None)
            if stored is not None:
                stored.closed = True

    def publish(self, event_type: NavigationEventType, data: dict[str, Any]) -> NavigationEvent:
        event = self._new_event(event_type, data)
        with self._lock:
            subscribers = list(self._subscribers.values())
        for subscription in subscribers:
            if subscription.closed:
                continue
            try:
                subscription.loop.call_soon_threadsafe(self._offer, subscription, event)
            except RuntimeError:
                self.unsubscribe(subscription)
        return event

    def snapshot_event(self, data: dict[str, Any]) -> NavigationEvent:
        return self._new_event(NavigationEventType.NAVIGATION_SNAPSHOT, data)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            subscribers = list(self._subscribers.values())
            self._subscribers.clear()
            for subscription in subscribers:
                subscription.closed = True
        for subscription in subscribers:
            try:
                subscription.loop.call_soon_threadsafe(self._close_subscription, subscription)
            except RuntimeError:
                continue

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    @property
    def current_sequence(self) -> int:
        with self._lock:
            return self._sequence

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def _new_event(self, event_type: NavigationEventType, data: dict[str, Any]) -> NavigationEvent:
        with self._lock:
            if self._closed:
                raise NavigationEventBusClosed("Navigation event bus is closed.")
            self._sequence += 1
            sequence = self._sequence
        return NavigationEvent(type=event_type, sequence=sequence, data=data)

    @staticmethod
    def _offer(subscription: NavigationSubscription, event: NavigationEvent) -> None:
        if subscription.closed:
            return
        if subscription.queue.full():
            try:
                subscription.queue.get_nowait()
                subscription.dropped_events += 1
            except asyncio.QueueEmpty:
                pass
        try:
            subscription.queue.put_nowait(event)
        except asyncio.QueueFull:
            subscription.dropped_events += 1

    @staticmethod
    def _close_subscription(subscription: NavigationSubscription) -> None:
        while subscription.queue.full():
            try:
                subscription.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        try:
            subscription.queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
