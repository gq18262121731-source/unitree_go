from __future__ import annotations

from app.adapters.base import RobotAdapter


class Go2Gateway:
    """Stable capability facade over the concrete Unitree adapter."""

    def __init__(self, adapter: RobotAdapter) -> None:
        self.adapter = adapter
        self.sdk_version = adapter.sdk_version

    def connect(self) -> None:
        self.adapter.initialize()

    def close(self) -> None:
        self.adapter.close()

    def is_initialized(self) -> bool:
        return self.adapter.is_initialized()

    def get_status(self) -> dict:
        return self.adapter.get_status()

    def dds_diagnostics(self) -> dict:
        return self.adapter.dds_diagnostics()

    def lidar_diagnostics(self) -> dict:
        return self.adapter.lidar_diagnostics()

    def get_motion_state(self) -> dict | None:
        return self.adapter.get_motion_state()

    def motion_transport_ready(self) -> bool:
        checker = getattr(self.adapter, "motion_transport_ready", None)
        return bool(checker()) if callable(checker) else False

    def stand(self) -> int:
        return self.adapter.stand_up()

    def lie_down(self) -> int:
        return self.adapter.stand_down()

    def sit(self) -> int:
        return self.adapter.sit()

    def stop(self) -> int:
        return self.adapter.stop()

    def move(self, vx: float, vy: float, wz: float) -> int:
        return self.adapter.move(vx, vy, wz)

    def switch_joystick(self, enabled: bool) -> int:
        return self.adapter.switch_joystick(enabled)

    def apply_pose(
        self,
        *,
        roll_rad: float,
        pitch_rad: float,
        yaw_rad: float,
        body_height_m: float,
    ) -> int:
        return self.adapter.apply_pose(
            roll_rad=roll_rad,
            pitch_rad=pitch_rad,
            yaw_rad=yaw_rad,
            body_height_m=body_height_m,
        )

    def reset_pose(self) -> int:
        return self.adapter.reset_pose()

    def play_audio_file(self, path: str) -> int:
        return self.adapter.play_audio_file(path)

    def speak(self, text: str) -> int:
        return self.adapter.speak(text)

    def get_camera(self) -> bytes:
        return self.adapter.get_camera_jpeg()
