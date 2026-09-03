from __future__ import annotations

import importlib.metadata
import logging
import math
import re
import threading
import time
from copy import deepcopy

from app.adapters.base import RobotAdapter
from app.core.errors import ErrorCode, GatewayError


class UnitreeGo2Adapter(RobotAdapter):
    def __init__(
        self,
        network_interface: str,
        timeout_seconds: float,
        robot_id: str,
        robot_ip: str = "192.168.123.161",
        domain_id: int = 0,
    ) -> None:
        self.network_interface = network_interface
        self.timeout_seconds = timeout_seconds
        self.robot_id = robot_id
        self.robot_ip = robot_ip
        self.domain_id = domain_id
        self._initialized = False
        self._lock = threading.RLock()
        self._sport_client = None
        self._video_client = None
        self._sport_state = None
        self._sport_state_received_monotonic = None
        self._low_state = None
        self._lidar_state = None
        self._subscribers = []
        self._dds_topics = {
            "sportState": self._new_topic_status(),
            "lowState": self._new_topic_status(),
            "lidarState": self._new_lidar_topic_status(),
        }
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
                from unitree_sdk2py.core import channel as sdk_channel
                from unitree_sdk2py.go2.sport.sport_client import SportClient
                from unitree_sdk2py.go2.video.video_client import VideoClient
                from unitree_sdk2py.idl.unitree_go.msg.dds_ import LidarState_, LowState_, SportModeState_
            except Exception as exc:
                raise GatewayError(ErrorCode.SDK_NOT_INITIALIZED, f"Unitree SDK import failed: {exc}", 503) from exc

            try:
                self._configure_dds_peer(sdk_channel)
                sdk_channel.ChannelFactoryInitialize(self.domain_id, self.network_interface)

                sport_client = SportClient()
                sport_client.SetTimeout(self.timeout_seconds)
                sport_client.Init()

                video_client = VideoClient()
                video_client.SetTimeout(self.timeout_seconds)
                video_client.Init()

                for topic in ("rt/lf/sportmodestate", "rt/sportmodestate"):
                    subscriber = sdk_channel.ChannelSubscriber(topic, SportModeState_)
                    subscriber.Init(self._on_sport_state, 10)
                    self._subscribers.append(subscriber)
                    self._mark_topic_created("sportState", topic)

                for topic in ("rt/lf/lowstate", "rt/lowstate"):
                    subscriber = sdk_channel.ChannelSubscriber(topic, LowState_)
                    subscriber.Init(self._on_low_state, 10)
                    self._subscribers.append(subscriber)
                    self._mark_topic_created("lowState", topic)

                for topic in ("rt/utlidar/lidar_state",):
                    subscriber = sdk_channel.ChannelSubscriber(topic, LidarState_)
                    subscriber.Init(self._on_lidar_state, 10)
                    self._subscribers.append(subscriber)
                    self._mark_topic_created("lidarState", topic)

                self._sport_client = sport_client
                self._video_client = video_client
                self._initialized = True
            except Exception as exc:
                raise GatewayError(ErrorCode.SDK_NOT_INITIALIZED, f"Unitree SDK initialization failed: {exc}", 503) from exc

    def close(self) -> None:
        try:
            self.stop()
        finally:
            with self._lock:
                self._initialized = False
                self._sport_client = None
                self._video_client = None
                self._sport_state = None
                self._sport_state_received_monotonic = None
                self._low_state = None
                self._lidar_state = None
                self._subscribers = []
                self._dds_topics = {
                    "sportState": self._new_topic_status(),
                    "lowState": self._new_topic_status(),
                    "lidarState": self._new_lidar_topic_status(),
                }

    def is_initialized(self) -> bool:
        return self._initialized

    def get_status(self) -> dict:
        from app.core.state_store import iso, utc_now

        sport = self._sport_state
        low = self._low_state
        diagnostics = self.dds_diagnostics()
        online = bool(diagnostics["ddsStateAvailable"])
        last_seen = diagnostics.get("lastSampleAt")
        status = {
            "robotId": self.robot_id,
            "online": online,
            "lastSeen": last_seen or (iso(utc_now()) if online else None),
            "motion": {},
            "attitude": {},
            "battery": {"percentage": None, "voltage": None, "current": None, "raw": {}},
            "dds": diagnostics,
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

    def get_motion_state(self) -> dict | None:
        """Return position/yaw from the already-subscribed SportModeState.

        This is deliberately a snapshot of the adapter's existing DDS reader;
        scripted motion must not create a second DDS participant or state
        subscriber. ``received_monotonic`` is local receive time and is used for
        strict stale-state rejection.
        """

        with self._lock:
            sport = self._sport_state
            received = self._sport_state_received_monotonic
            if sport is None or received is None:
                return None
            position = list(getattr(sport, "position", []) or [])
            imu = getattr(sport, "imu_state", None)
            rpy = list(getattr(imu, "rpy", []) or []) if imu is not None else []
            quaternion = (
                list(getattr(imu, "quaternion", []) or [])
                if imu is not None
                else []
            )
            stamp = getattr(sport, "stamp", None)

        yaw = rpy[2] if len(rpy) >= 3 else _yaw_from_unitree_quaternion(quaternion)
        if len(position) < 2 or yaw is None:
            return None
        return {
            "x": position[0],
            "y": position[1],
            "yaw": yaw,
            "received_monotonic": received,
            "source": "SportModeState.position+imu_state.rpy",
            "source_timestamp": {
                "sec": getattr(stamp, "sec", None),
                "nanosec": getattr(stamp, "nanosec", None),
            },
        }

    def stand_up(self) -> int:
        return self._sport().StandUp()

    def stand_down(self) -> int:
        self.stop()
        return self._sport().StandDown()

    def sit(self) -> int:
        self.stop()
        return self._sport().Sit()

    def stop(self) -> int:
        if not self._initialized or self._sport_client is None:
            return 0
        return self._sport_client.StopMove()

    def move(self, vx: float, vy: float, wz: float) -> int:
        return self._sport().Move(vx, vy, wz)

    def switch_joystick(self, enabled: bool) -> int:
        return self._sport().SwitchJoystick(enabled)

    def get_camera_jpeg(self) -> bytes:
        if not self._initialized or self._video_client is None:
            raise GatewayError(ErrorCode.SDK_NOT_INITIALIZED, "Video client is not initialized.", 503)
        code, data = self._video_client.GetImageSample()
        if code != 0:
            raise GatewayError(ErrorCode.CAMERA_UNAVAILABLE, f"Failed to obtain Go2 image, code={code}", 503)
        return bytes(data)

    def dds_diagnostics(self) -> dict:
        with self._lock:
            sport = deepcopy(self._dds_topics["sportState"])
            low = deepcopy(self._dds_topics["lowState"])
            lidar = deepcopy(self._dds_topics["lidarState"])
        self._fill_timeout(sport, "SPORT_STATE_TIMEOUT")
        self._fill_timeout(low, "LOW_STATE_TIMEOUT")
        self._fill_timeout(lidar, "LIDAR_STATE_TIMEOUT")
        dds_state_available = bool(sport["received"] or low["received"])
        last_sample_at = max(
            [value for value in (sport["lastSampleAt"], low["lastSampleAt"]) if value],
            default=None,
        )
        return {
            "robotIp": self.robot_ip,
            "networkInterface": self.network_interface,
            "domainId": self.domain_id,
            "ddsInitialized": self._initialized,
            "sportState": sport,
            "lowState": low,
            "lidarState": lidar,
            "ddsStateAvailable": dds_state_available,
            "lastSampleAt": last_sample_at,
            "errorCode": None if dds_state_available else "UNITREE_DDS_NO_STATE_SAMPLES",
        }

    def lidar_diagnostics(self) -> dict:
        with self._lock:
            lidar = deepcopy(self._dds_topics["lidarState"])
        self._fill_timeout(lidar, "LIDAR_STATE_TIMEOUT")
        return lidar

    def _fill_timeout(self, topic: dict, timeout_code: str) -> None:
        if self._initialized and topic.get("created") and not topic.get("received"):
            topic["timeout"] = True
            topic["timeoutCode"] = timeout_code

    def _configure_dds_peer(self, channel_config_module) -> None:
        if not self.robot_ip:
            return
        channel_config_module.ChannelConfigHasInterface = self._dds_config_with_peer(
            channel_config_module.ChannelConfigHasInterface,
            self.robot_ip,
        )

    @staticmethod
    def _dds_config_with_peer(config: str, robot_ip: str) -> str:
        peer_pattern = r'<Peer\s+Address="[^"]+"\s*/>'
        peer = f'<Peer Address="{robot_ip}"/>'
        if re.search(peer_pattern, config):
            return re.sub(peer_pattern, peer, config, count=1)
        return config

    def _sport(self):
        if not self._initialized or self._sport_client is None:
            raise GatewayError(ErrorCode.SDK_NOT_INITIALIZED, "Sport client is not initialized.", 503)
        return self._sport_client

    def _on_sport_state(self, msg) -> None:
        with self._lock:
            self._sport_state = msg
            self._sport_state_received_monotonic = time.monotonic()
        self._record_topic_sample("sportState")

    def _on_low_state(self, msg) -> None:
        self._low_state = msg
        self._record_topic_sample("lowState")

    def _on_lidar_state(self, msg) -> None:
        self._lidar_state = msg
        self._record_lidar_sample(msg)

    def _new_topic_status(self) -> dict:
        return {
            "topics": [],
            "created": False,
            "received": False,
            "sampleCount": 0,
            "firstSampleAt": None,
            "lastSampleAt": None,
            "frequencyHz": None,
            "timeout": None,
            "timeoutCode": None,
        }

    def _new_lidar_topic_status(self) -> dict:
        status = self._new_topic_status()
        status.update(
            {
                "topic": None,
                "discovered": False,
                "packetLossRate": None,
                "cloudFrequency": None,
                "cloudSize": None,
                "cloudScanNum": None,
                "errorState": None,
                "firmwareVersion": None,
                "softwareVersion": None,
                "sdkVersion": None,
                "systemRotationSpeed": None,
                "commandedRotationSpeed": None,
                "imuFrequency": None,
                "imuPacketLossRate": None,
                "imuRpy": None,
                "serialBufferSize": None,
                "serialBufferRead": None,
            }
        )
        return status

    def _mark_topic_created(self, key: str, topic: str) -> None:
        with self._lock:
            state = self._dds_topics[key]
            state["created"] = True
            state["topics"].append(topic)
            if key == "lidarState":
                state["topic"] = topic
                state["discovered"] = True

    def _record_topic_sample(self, key: str) -> None:
        from app.core.state_store import iso, utc_now

        now = iso(utc_now())
        with self._lock:
            state = self._dds_topics[key]
            state["received"] = True
            state["sampleCount"] = int(state["sampleCount"]) + 1
            if state["firstSampleAt"] is None:
                state["firstSampleAt"] = now
            state["lastSampleAt"] = now
            state["timeout"] = False
            state["timeoutCode"] = None
            first = state["firstSampleAt"]
            try:
                first_ts = time.mktime(time.strptime(first[:19], "%Y-%m-%dT%H:%M:%S"))
                last_ts = time.mktime(time.strptime(now[:19], "%Y-%m-%dT%H:%M:%S"))
                elapsed = max(last_ts - first_ts, 0.0)
                if elapsed > 0:
                    state["frequencyHz"] = round((state["sampleCount"] - 1) / elapsed, 3)
            except Exception:
                state["frequencyHz"] = None

    def _record_lidar_sample(self, msg) -> None:
        self._record_topic_sample("lidarState")
        with self._lock:
            state = self._dds_topics["lidarState"]
            state["discovered"] = True
            state["cloudFrequency"] = getattr(msg, "cloud_frequency", None)
            state["frequencyHz"] = getattr(msg, "cloud_frequency", None) or state.get("frequencyHz")
            state["packetLossRate"] = getattr(msg, "cloud_packet_loss_rate", None)
            state["cloudSize"] = getattr(msg, "cloud_size", None)
            state["cloudScanNum"] = getattr(msg, "cloud_scan_num", None)
            state["errorState"] = getattr(msg, "error_state", None)
            state["firmwareVersion"] = getattr(msg, "firmware_version", None)
            state["softwareVersion"] = getattr(msg, "software_version", None)
            state["sdkVersion"] = getattr(msg, "sdk_version", None)
            state["systemRotationSpeed"] = getattr(msg, "sys_rotation_speed", None)
            state["commandedRotationSpeed"] = getattr(msg, "com_rotation_speed", None)
            state["imuFrequency"] = getattr(msg, "imu_frequency", None)
            state["imuPacketLossRate"] = getattr(msg, "imu_packet_loss_rate", None)
            imu_rpy = getattr(msg, "imu_rpy", None)
            state["imuRpy"] = list(imu_rpy) if imu_rpy is not None else None
            state["serialBufferSize"] = getattr(msg, "serial_buffer_size", None)
            state["serialBufferRead"] = getattr(msg, "serial_buffer_read", None)


def _yaw_from_unitree_quaternion(values: list[object]) -> float | None:
    """Convert Unitree's documented [w, x, y, z] quaternion to yaw."""

    if len(values) < 4:
        return None
    try:
        w, x, y, z = (float(values[index]) for index in range(4))
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (w, x, y, z)):
        return None
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
