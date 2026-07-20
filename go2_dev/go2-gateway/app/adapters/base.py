from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol


class RobotAdapter(ABC):
    sdk_version: str = "unknown"

    @abstractmethod
    def initialize(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def is_initialized(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_status(self) -> dict:
        raise NotImplementedError

    @abstractmethod
    def stand_up(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def stand_down(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def move(self, vx: float, vy: float, wz: float) -> int:
        raise NotImplementedError

    @abstractmethod
    def get_camera_jpeg(self) -> bytes:
        raise NotImplementedError


class Stoppable(Protocol):
    def stop(self) -> int:
        ...

