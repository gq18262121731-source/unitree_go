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

    def dds_diagnostics(self) -> dict:
        return {}

    def lidar_diagnostics(self) -> dict:
        return {}

    def get_motion_state(self) -> dict | None:
        """Return the latest read-only planar pose sample when available."""

        return None

    def motion_transport_ready(self) -> bool:
        """Whether commands can be acknowledged without a fresh state sample.

        DDS adapters retain the default fail-closed behavior.  Transports such
        as WebRTC may override this when their command DataChannel has an
        independent, positively verified readiness signal.
        """

        return False

    @abstractmethod
    def stand_up(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def stand_down(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def sit(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def move(self, vx: float, vy: float, wz: float) -> int:
        raise NotImplementedError

    def switch_joystick(self, enabled: bool) -> int:
        raise NotImplementedError

    def apply_pose(
        self,
        *,
        roll_rad: float,
        pitch_rad: float,
        yaw_rad: float,
        body_height_m: float,
    ) -> int:
        raise NotImplementedError

    def reset_pose(self) -> int:
        raise NotImplementedError

    def play_audio_file(self, path: str) -> int:
        raise NotImplementedError

    def speak(self, text: str) -> int:
        raise NotImplementedError

    @abstractmethod
    def get_camera_jpeg(self) -> bytes:
        raise NotImplementedError


class Stoppable(Protocol):
    def stop(self) -> int:
        ...
