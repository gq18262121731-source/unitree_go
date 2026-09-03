from __future__ import annotations

from typing import Any

from app.adapters.base import RobotAdapter
from app.core.errors import ErrorCode, GatewayError
from app.webrtc.go2_wireless_runtime import Go2WirelessRuntime


class WebRTCMotionBackend(RobotAdapter):
    """RobotAdapter view over an injected shared Go2WirelessRuntime.

    This class never constructs a PeerConnection. The runtime is the sole
    connection owner and can be shared with the local video bridge.
    """

    def __init__(
        self,
        runtime: Go2WirelessRuntime,
        robot_id: str,
        *,
        close_runtime: bool = False,
    ) -> None:
        self.runtime = runtime
        self.robot_id = robot_id
        self.close_runtime = close_runtime
        self.sdk_version = runtime.sdk_version
        self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return
        self.runtime.start()
        self._initialized = True

    def close(self) -> None:
        if self._initialized:
            try:
                self.stop()
            except Exception:
                pass
        self._initialized = False
        if self.close_runtime:
            self.runtime.close()

    def is_initialized(self) -> bool:
        return self._initialized and self.runtime.is_started()

    def get_status(self) -> dict[str, Any]:
        runtime_status = self.runtime.status()
        state = self.runtime.get_sport_mode_state()
        fresh = bool(self.is_initialized() and runtime_status["sportStateReady"])
        motion: dict[str, Any] = {}
        attitude: dict[str, Any] = {}
        if state is not None:
            velocity = list(state.get("velocity") or [])
            imu = state.get("imu_state") or {}
            rpy = list(imu.get("rpy") or []) if isinstance(imu, dict) else []
            motion = {
                "mode": state.get("mode"),
                "modeName": None,
                "gaitType": state.get("gait_type"),
                "velocityX": velocity[0] if len(velocity) > 0 else None,
                "velocityY": velocity[1] if len(velocity) > 1 else None,
                "yawSpeed": state.get("yaw_speed"),
                "bodyHeight": state.get("body_height"),
            }
            attitude = {
                "roll": rpy[0] if len(rpy) > 0 else None,
                "pitch": rpy[1] if len(rpy) > 1 else None,
                "yaw": rpy[2] if len(rpy) > 2 else None,
            }
        return {
            "robotId": self.robot_id,
            "online": fresh,
            "lastSeen": runtime_status["lastStateAt"],
            "stateStale": not fresh,
            "motion": motion,
            "attitude": attitude,
            "battery": {
                "percentage": None,
                "voltage": None,
                "current": None,
                "raw": {},
            },
            "webrtc": runtime_status,
        }

    def get_motion_state(self) -> dict[str, Any] | None:
        return self.runtime.get_motion_state()

    def motion_transport_ready(self) -> bool:
        """Use the verified WebRTC command channel as the motion readiness gate.

        SportModeState remains required by closed-loop scripted/follow motion,
        but keyboard control only needs the connected, acknowledged command
        channel.  This must never fall back to network reachability alone.
        """

        if not self.is_initialized():
            return False
        status = self.runtime.status()
        return bool(
            status.get("connected")
            and status.get("connectionCount") == 1
            and status.get("dataChannelReady")
            and status.get("motionReady")
        )

    def stop(self) -> int:
        if not self._initialized:
            return 0
        return self.runtime.stop_motion()

    def move(self, vx: float, vy: float, wz: float) -> int:
        if not self._initialized:
            raise GatewayError(
                ErrorCode.SDK_NOT_INITIALIZED,
                "WebRTC movement backend is not initialized.",
                503,
            )
        return self.runtime.send_move(vx, vy, wz)

    def stand_up(self) -> int:
        raise self._unsupported("StandUp")

    def stand_down(self) -> int:
        raise self._unsupported("StandDown")

    def sit(self) -> int:
        raise self._unsupported("Sit")

    def switch_joystick(self, enabled: bool) -> int:
        raise self._unsupported("SwitchJoystick")

    def apply_pose(
        self,
        *,
        roll_rad: float,
        pitch_rad: float,
        yaw_rad: float,
        body_height_m: float,
    ) -> int:
        self._ensure_initialized("pose")
        return self.runtime.apply_pose(
            roll_rad=roll_rad,
            pitch_rad=pitch_rad,
            yaw_rad=yaw_rad,
            body_height_m=body_height_m,
        )

    def reset_pose(self) -> int:
        self._ensure_initialized("pose reset")
        return self.runtime.reset_pose()

    def play_audio_file(self, path: str) -> int:
        self._ensure_initialized("audio playback")
        return self.runtime.play_audio_file(path)

    def speak(self, text: str) -> int:
        self._ensure_initialized("speech playback")
        return self.runtime.speak(text)

    def get_camera_jpeg(self) -> bytes:
        frame = self.runtime.latest_frame()
        if frame is None:
            raise GatewayError(
                ErrorCode.CAMERA_UNAVAILABLE,
                "The shared Wireless Runtime has not received a video frame.",
                503,
            )
        return frame.jpeg

    def _ensure_initialized(self, capability: str) -> None:
        if not self._initialized:
            raise GatewayError(
                ErrorCode.SDK_NOT_INITIALIZED,
                f"WebRTC {capability} backend is not initialized.",
                503,
            )

    @staticmethod
    def _unsupported(command: str) -> GatewayError:
        return GatewayError(
            ErrorCode.TASK_NOT_SUPPORTED,
            f"{command} is outside the WebRTC movement/runtime scope.",
            422,
        )
