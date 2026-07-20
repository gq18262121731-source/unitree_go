from __future__ import annotations

import importlib.metadata
import logging
import threading
import time

from app.adapters.base import RobotAdapter
from app.core.errors import ErrorCode, GatewayError


class UnitreeGo2Adapter(RobotAdapter):
    def __init__(self, network_interface: str, timeout_seconds: float, robot_id: str) -> None:
        self.network_interface = network_interface
        self.timeout_seconds = timeout_seconds
        self.robot_id = robot_id
        self._initialized = False
        self._lock = threading.RLock()
        self._sport_client = None
        self._video_client = None
        self._sport_state = None
        self._low_state = None
        self._subscribers = []
        self.logger = logging.getLogger("go2_gateway.unitree")
        try:
            self.sdk_version = importlib.metadata.version("unitree_sdk2py")
        except importlib.metadata.PackageNotFoundError:
            self.sdk_version = "source"

    def initialize(self) -> None:
        with self._lock:
            if self._initialized:
                return
            try:
                from unitree_sdk2py.core.channel import ChannelFactoryInitialize, ChannelSubscriber
                from unitree_sdk2py.go2.sport.sport_client import SportClient
                from unitree_sdk2py.go2.video.video_client import VideoClient
                from unitree_sdk2py.idl.unitree_go.msg.dds_ import LowState_, SportModeState_
            except Exception as exc:
                raise GatewayError(ErrorCode.SDK_NOT_INITIALIZED, f"Unitree SDK import failed: {exc}", 503) from exc

            try:
                ChannelFactoryInitialize(0, self.network_interface)

                sport_client = SportClient()
                sport_client.SetTimeout(self.timeout_seconds)
                sport_client.Init()

                video_client = VideoClient()
                video_client.SetTimeout(self.timeout_seconds)
                video_client.Init()

                for topic in ("rt/lf/sportmodestate", "rt/sportmodestate"):
                    subscriber = ChannelSubscriber(topic, SportModeState_)
                    subscriber.Init(self._on_sport_state, 10)
                    self._subscribers.append(subscriber)

                for topic in ("rt/lf/lowstate", "rt/lowstate"):
                    subscriber = ChannelSubscriber(topic, LowState_)
                    subscriber.Init(self._on_low_state, 10)
                    self._subscribers.append(subscriber)

                self._sport_client = sport_client
                self._video_client = video_client
                self._initialized = True
            except Exception as exc:
                raise GatewayError(ErrorCode.SDK_NOT_INITIALIZED, f"Unitree SDK initialization failed: {exc}", 503) from exc

    def close(self) -> None:
        try:
            self.stop()
        finally:
            self._initialized = False

    def is_initialized(self) -> bool:
        return self._initialized

    def get_status(self) -> dict:
        from app.core.state_store import iso, utc_now

        sport = self._sport_state
        low = self._low_state
        online = sport is not None or low is not None
        status = {
            "robotId": self.robot_id,
            "online": online,
            "lastSeen": iso(utc_now()) if online else None,
            "motion": {},
            "attitude": {},
            "battery": {"percentage": None, "voltage": None, "current": None, "raw": {}},
        }
        if sport is not None:
            velocity = list(getattr(sport, "velocity", []) or [])
            imu = getattr(sport, "imu_state", None)
            rpy = list(getattr(imu, "rpy", []) or []) if imu is not None else []
            status["motion"] = {
                "mode": getattr(sport, "mode", None),
                "modeName": None,
                "gaitType": getattr(sport, "gait_type", None),
                "velocityX": velocity[0] if len(velocity) > 0 else None,
                "velocityY": velocity[1] if len(velocity) > 1 else None,
                "yawSpeed": getattr(sport, "yaw_speed", None),
                "bodyHeight": getattr(sport, "body_height", None),
            }
            status["attitude"] = {
                "roll": rpy[0] if len(rpy) > 0 else None,
                "pitch": rpy[1] if len(rpy) > 1 else None,
                "yaw": rpy[2] if len(rpy) > 2 else None,
            }
        if low is not None:
            bms = getattr(low, "bms_state", None)
            status["battery"] = {
                "percentage": getattr(bms, "soc", None),
                "voltage": getattr(low, "power_v", None),
                "current": getattr(low, "power_a", None),
                "raw": {
                    "version": list(getattr(low, "version", []) or []),
                    "sn": list(getattr(low, "sn", []) or []),
                },
            }
        return status

    def wait_for_status(self, timeout_seconds: float = 10.0) -> dict:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            status = self.get_status()
            if status.get("online"):
                return status
            time.sleep(0.2)
        return self.get_status()

    def stand_up(self) -> int:
        return self._sport().StandUp()

    def stand_down(self) -> int:
        self.stop()
        return self._sport().StandDown()

    def stop(self) -> int:
        if not self._initialized or self._sport_client is None:
            return 0
        return self._sport_client.StopMove()

    def move(self, vx: float, vy: float, wz: float) -> int:
        return self._sport().Move(vx, vy, wz)

    def get_camera_jpeg(self) -> bytes:
        if not self._initialized or self._video_client is None:
            raise GatewayError(ErrorCode.SDK_NOT_INITIALIZED, "Video client is not initialized.", 503)
        code, data = self._video_client.GetImageSample()
        if code != 0:
            raise GatewayError(ErrorCode.CAMERA_UNAVAILABLE, f"Failed to obtain Go2 image, code={code}", 503)
        return bytes(data)

    def _sport(self):
        if not self._initialized or self._sport_client is None:
            raise GatewayError(ErrorCode.SDK_NOT_INITIALIZED, "Sport client is not initialized.", 503)
        return self._sport_client

    def _on_sport_state(self, msg) -> None:
        self._sport_state = msg

    def _on_low_state(self, msg) -> None:
        self._low_state = msg
