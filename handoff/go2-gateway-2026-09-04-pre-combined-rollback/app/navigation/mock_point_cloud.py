from __future__ import annotations

import asyncio
import math
from contextlib import suppress
from dataclasses import dataclass
from threading import RLock
from typing import Callable, Union

from app.navigation.models import NavigationState
from app.navigation.point_cloud_models import (
    PointCloudErrorCode,
    PointCloudFrame,
    PointCloudPoint,
    PointCloudPose,
    PointCloudScenario,
    PointCloudScenarioResult,
    PointCloudStreamError,
    PointCloudStreamInfo,
)


@dataclass(frozen=True)
class PointCloudStreamConfig:
    target_fps: float = 5.0
    default_points: int = 2400
    max_points: int = 4000
    subscriber_queue_size: int = 2
    seed: int = 20260723

    def __post_init__(self) -> None:
        if not 0 < self.target_fps <= 5:
            raise ValueError("target_fps must be between 0 and 5")
        if not 0 < self.default_points <= self.max_points <= 5000:
            raise ValueError("point limits are invalid")
        if not 0 < self.subscriber_queue_size <= 2:
            raise ValueError("subscriber_queue_size must be 1 or 2")


class PointCloudDomainError(RuntimeError):
    def __init__(
        self,
        code: PointCloudErrorCode,
        message: str,
        *,
        http_status: int = 409,
    ) -> None:
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(f"{code.value}: {message}")


PointCloudMessage = Union[PointCloudFrame, PointCloudStreamError]


@dataclass
class PointCloudSubscription:
    subscription_id: int
    queue: asyncio.Queue[PointCloudMessage | None]
    dropped_frames: int = 0
    closed: bool = False


class MockPointCloudGenerator:
    _SCENARIO_OFFSETS = {
        PointCloudScenario.CLASSROOM_DEFAULT: 11,
        PointCloudScenario.CLASSROOM_SPARSE: 23,
        PointCloudScenario.CLASSROOM_OBSTACLE: 37,
        PointCloudScenario.EMPTY: 41,
    }

    def __init__(self, config: PointCloudStreamConfig | None = None) -> None:
        self.config = config or PointCloudStreamConfig()

    def point_count_for(self, scenario: PointCloudScenario) -> int:
        counts = {
            PointCloudScenario.CLASSROOM_DEFAULT: self.config.default_points,
            PointCloudScenario.CLASSROOM_SPARSE: 1200,
            PointCloudScenario.CLASSROOM_OBSTACLE: 3000,
            PointCloudScenario.EMPTY: 600,
        }
        return min(counts.get(scenario, 0), self.config.max_points)

    def generate(
        self,
        scenario: PointCloudScenario,
        frame_index: int,
        navigation_state: NavigationState,
    ) -> list[PointCloudPoint]:
        point_count = self.point_count_for(scenario)
        if point_count == 0:
            return []
        points = [
            self._point_for(scenario, frame_index, index, point_count)
            for index in range(point_count)
        ]
        self._overlay_pose(points, navigation_state.current_pose.x, navigation_state.current_pose.y, 0.9)
        if navigation_state.target_pose is not None:
            self._overlay_pose(
                points,
                navigation_state.target_pose.x,
                navigation_state.target_pose.y,
                1.0,
                offset=24,
            )
        return points

    def _point_for(
        self,
        scenario: PointCloudScenario,
        frame_index: int,
        index: int,
        point_count: int,
    ) -> PointCloudPoint:
        key = (
            self.config.seed
            + self._SCENARIO_OFFSETS[scenario] * 1009
            + frame_index * 9176
            + index * 7919
        )
        unit_a = (key % 1009) / 1008.0
        unit_b = ((key // 1009) % 1013) / 1012.0
        unit_c = ((key // (1009 * 1013)) % 997) / 996.0
        if scenario == PointCloudScenario.EMPTY:
            return self._ground(unit_a, unit_b, unit_c)

        ratio = index / point_count
        if scenario == PointCloudScenario.CLASSROOM_OBSTACLE and ratio >= 0.72:
            return self._obstacle(unit_a, unit_b, unit_c)
        if ratio < 0.32:
            return self._ground(unit_a, unit_b, unit_c)
        if ratio < 0.66:
            return self._wall(index, unit_a, unit_b)
        if ratio < 0.9:
            return self._furniture(index, unit_a, unit_b)
        return self._zone(index, unit_a, unit_b)

    @staticmethod
    def _ground(a: float, b: float, c: float) -> PointCloudPoint:
        return MockPointCloudGenerator._rounded(-5.0 + 10.0 * a, -4.0 + 8.0 * b, 0.0, 0.18 + 0.12 * c)

    @staticmethod
    def _wall(index: int, a: float, b: float) -> PointCloudPoint:
        side = index % 4
        z = 0.05 + 2.75 * b
        if side == 0:
            return MockPointCloudGenerator._rounded(-5.0, -4.0 + 8.0 * a, z, 0.55)
        if side == 1:
            return MockPointCloudGenerator._rounded(5.0, -4.0 + 8.0 * a, z, 0.58)
        if side == 2:
            return MockPointCloudGenerator._rounded(-5.0 + 10.0 * a, -4.0, z, 0.52)
        return MockPointCloudGenerator._rounded(-5.0 + 10.0 * a, 4.0, z, 0.56)

    @staticmethod
    def _furniture(index: int, a: float, b: float) -> PointCloudPoint:
        centers = ((-2.2, 1.6), (0.0, 1.6), (2.2, 1.6), (-1.8, -0.4), (1.2, -0.5))
        center_x, center_y = centers[index % len(centers)]
        angle = 2.0 * math.pi * a
        radius_x = 0.55 + 0.15 * (index % 2)
        radius_y = 0.35 + 0.1 * ((index // 2) % 2)
        z = 0.05 + 0.8 * b
        return MockPointCloudGenerator._rounded(
            center_x + radius_x * math.cos(angle),
            center_y + radius_y * math.sin(angle),
            z,
            0.62 + 0.15 * b,
        )

    @staticmethod
    def _zone(index: int, a: float, b: float) -> PointCloudPoint:
        center = (-3.3, -2.8) if index % 2 == 0 else (2.3, 1.1)
        radius = 0.7 if index % 2 == 0 else 1.35
        angle = 2.0 * math.pi * a
        return MockPointCloudGenerator._rounded(
            center[0] + radius * math.cos(angle),
            center[1] + radius * math.sin(angle),
            0.02 + 0.08 * b,
            0.35 if index % 2 == 0 else 0.48,
        )

    @staticmethod
    def _obstacle(a: float, b: float, c: float) -> PointCloudPoint:
        angle = 2.0 * math.pi * a
        radius = 0.75 + 0.1 * c
        return MockPointCloudGenerator._rounded(
            0.8 + radius * math.cos(angle),
            -1.1 + radius * math.sin(angle),
            0.05 + 1.45 * b,
            0.88,
        )

    @staticmethod
    def _overlay_pose(
        points: list[PointCloudPoint],
        x: float,
        y: float,
        intensity: float,
        *,
        offset: int = 0,
    ) -> None:
        marker_count = min(24, max(0, len(points) - offset))
        for marker_index in range(marker_count):
            angle = 2.0 * math.pi * marker_index / marker_count
            point_index = len(points) - 1 - offset - marker_index
            points[point_index] = MockPointCloudGenerator._rounded(
                x + 0.18 * math.cos(angle),
                y + 0.18 * math.sin(angle),
                0.05 + 0.55 * (marker_index % 4) / 3,
                intensity,
            )

    @staticmethod
    def _rounded(x: float, y: float, z: float, intensity: float) -> PointCloudPoint:
        return (round(x, 4), round(y, 4), round(z, 4), round(intensity, 4))


class MockPointCloudStream:
    def __init__(
        self,
        navigation_snapshot: Callable[[], NavigationState],
        *,
        config: PointCloudStreamConfig | None = None,
    ) -> None:
        self.config = config or PointCloudStreamConfig()
        self.generator = MockPointCloudGenerator(self.config)
        self._navigation_snapshot = navigation_snapshot
        self._lock = RLock()
        self._scenario = PointCloudScenario.CLASSROOM_DEFAULT
        self._subscribers: dict[int, PointCloudSubscription] = {}
        self._next_subscription_id = 0
        self._sequence = 0
        self._latest: PointCloudMessage | None = None
        self._producer_task: asyncio.Task | None = None
        self._closed = False

    async def subscribe(self) -> PointCloudSubscription:
        with self._lock:
            if self._closed:
                raise PointCloudDomainError(
                    PointCloudErrorCode.POINT_CLOUD_STREAM_UNAVAILABLE,
                    "The Mock point-cloud stream is closed.",
                    http_status=503,
                )
            self._next_subscription_id += 1
            subscription = PointCloudSubscription(
                subscription_id=self._next_subscription_id,
                queue=asyncio.Queue(maxsize=self.config.subscriber_queue_size),
            )
            self._subscribers[subscription.subscription_id] = subscription
            if self._latest is not None:
                subscription.queue.put_nowait(self._latest)
            if self._producer_task is None:
                self._producer_task = asyncio.create_task(self._produce())
            return subscription

    async def unsubscribe(self, subscription: PointCloudSubscription) -> None:
        task: asyncio.Task | None = None
        with self._lock:
            stored = self._subscribers.pop(subscription.subscription_id, None)
            if stored is not None:
                stored.closed = True
            if not self._subscribers and self._producer_task is not None:
                task = self._producer_task
                self._producer_task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            subscribers = list(self._subscribers.values())
            self._subscribers.clear()
            task = self._producer_task
            self._producer_task = None
            for subscription in subscribers:
                subscription.closed = True
                self._offer(subscription, None)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def set_scenario(self, scenario_value: str) -> PointCloudScenarioResult:
        try:
            scenario = PointCloudScenario(scenario_value)
        except ValueError as exc:
            raise PointCloudDomainError(
                PointCloudErrorCode.POINT_CLOUD_SCENARIO_INVALID,
                f"Unknown Mock point-cloud scenario: {scenario_value}",
                http_status=422,
            ) from exc
        with self._lock:
            if self._scenario != scenario:
                self._scenario = scenario
                self._latest = None
        return PointCloudScenarioResult(
            scenario=scenario,
            stream_status=self._status_for(scenario),
        )

    def stream_info(self) -> PointCloudStreamInfo:
        with self._lock:
            scenario = self._scenario
        return PointCloudStreamInfo(
            target_fps=self.config.target_fps,
            max_points=self.config.max_points,
            queue_size=self.config.subscriber_queue_size,
            scenario=scenario,
            stream_status=self._status_for(scenario),
        )

    def latest_message(self) -> PointCloudMessage | None:
        with self._lock:
            return self._latest.model_copy(deep=True) if self._latest is not None else None

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    @property
    def producer_task_active(self) -> bool:
        with self._lock:
            return self._producer_task is not None and not self._producer_task.done()

    @property
    def current_sequence(self) -> int:
        with self._lock:
            return self._sequence

    @property
    def scenario(self) -> PointCloudScenario:
        with self._lock:
            return self._scenario

    async def _produce(self) -> None:
        current_task = asyncio.current_task()
        with self._lock:
            emitted_unavailable_scenario = (
                self._scenario
                if isinstance(self._latest, PointCloudStreamError)
                and self._scenario
                in {PointCloudScenario.STREAM_STALE, PointCloudScenario.STREAM_ERROR}
                else None
            )
        try:
            while True:
                with self._lock:
                    scenario = self._scenario
                    has_subscribers = bool(self._subscribers)
                if not has_subscribers:
                    return
                if scenario in {
                    PointCloudScenario.STREAM_STALE,
                    PointCloudScenario.STREAM_ERROR,
                }:
                    if emitted_unavailable_scenario != scenario:
                        message = self._unavailable_message(scenario)
                        self._broadcast(message)
                        emitted_unavailable_scenario = scenario
                else:
                    emitted_unavailable_scenario = None
                    try:
                        state = self._navigation_snapshot()
                    except Exception:
                        self._broadcast(
                            PointCloudStreamError(
                                code=PointCloudErrorCode.NAVIGATION_STORE_UNAVAILABLE,
                                message="Navigation Store snapshot is unavailable.",
                            )
                        )
                    else:
                        try:
                            frame = self._build_frame(scenario, state)
                        except PointCloudDomainError as exc:
                            self._broadcast(
                                PointCloudStreamError(code=exc.code, message=exc.message)
                            )
                        else:
                            self._broadcast(frame)
                await asyncio.sleep(1.0 / self.config.target_fps)
        finally:
            with self._lock:
                if self._producer_task is current_task:
                    self._producer_task = None

    def _build_frame(
        self, scenario: PointCloudScenario, state: NavigationState
    ) -> PointCloudFrame:
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        try:
            points = self.generator.generate(scenario, sequence, state)
            target_pose = None
            if state.target_pose is not None:
                target_pose = PointCloudPose(
                    x=state.target_pose.x,
                    y=state.target_pose.y,
                    z=0.0,
                    yaw=state.target_pose.yaw,
                )
            return PointCloudFrame(
                sequence=sequence,
                scenario=scenario,
                point_count=len(points),
                points=points,
                robot_pose=PointCloudPose(
                    x=state.current_pose.x,
                    y=state.current_pose.y,
                    z=0.0,
                    yaw=state.current_pose.yaw,
                ),
                target_pose=target_pose,
                navigation_state=state.navigation_state,
                control_owner=state.control_owner,
            )
        except Exception as exc:
            raise PointCloudDomainError(
                PointCloudErrorCode.POINT_CLOUD_FRAME_INVALID,
                f"Mock point-cloud frame validation failed: {exc}",
                http_status=503,
            ) from exc

    def _broadcast(self, message: PointCloudMessage) -> None:
        with self._lock:
            self._latest = message
            subscribers = list(self._subscribers.values())
        for subscription in subscribers:
            self._offer(subscription, message)

    @staticmethod
    def _offer(
        subscription: PointCloudSubscription,
        message: PointCloudMessage | None,
    ) -> None:
        if subscription.closed and message is not None:
            return
        while subscription.queue.full():
            try:
                subscription.queue.get_nowait()
                subscription.dropped_frames += 1
            except asyncio.QueueEmpty:
                break
        try:
            subscription.queue.put_nowait(message)
        except asyncio.QueueFull:
            subscription.dropped_frames += 1

    @staticmethod
    def _status_for(scenario: PointCloudScenario) -> str:
        if scenario == PointCloudScenario.STREAM_STALE:
            return "stale"
        if scenario == PointCloudScenario.STREAM_ERROR:
            return "error"
        return "ready"

    @staticmethod
    def _unavailable_message(scenario: PointCloudScenario) -> PointCloudStreamError:
        if scenario == PointCloudScenario.STREAM_STALE:
            message = "The Mock point-cloud stream is explicitly stale."
        else:
            message = "The Mock point-cloud stream is in the configured error scenario."
        return PointCloudStreamError(
            code=PointCloudErrorCode.POINT_CLOUD_STREAM_UNAVAILABLE,
            message=message,
        )
