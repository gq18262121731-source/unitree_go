from __future__ import annotations

import re
import time
from typing import Any, Iterable

from ..events import SensorEvent


def _stamp_ns(message: Any) -> int | None:
    header = getattr(message, "header", None)
    stamp = getattr(header, "stamp", None)
    if stamp is None:
        return None
    return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)


class Sdk2DdsReadonlySource:
    """Direct SDK2 DDS reader using only public Python IDL types.

    The public SDK2 Python checkout used by this project has no sensor_msgs Imu
    IDL, so this source deliberately does not pretend it can decode
    rt/utlidar/imu. The ROS2 source covers that already-bridged observation.
    """

    source_name = "dds"

    def __init__(
        self,
        network_interface: str,
        robot_ip: str = "192.168.123.161",
        domain_id: int = 0,
    ) -> None:
        self.network_interface = network_interface
        self.robot_ip = robot_ip
        self.domain_id = domain_id

    def events(self, duration_seconds: float) -> Iterable[SensorEvent]:
        try:
            from cyclonedds.domain import Domain, DomainParticipant
            from cyclonedds.sub import DataReader
            from cyclonedds.topic import Topic
            from unitree_sdk2py.core.channel_config import ChannelConfigHasInterface
            from unitree_sdk2py.idl.nav_msgs.msg.dds_ import Odometry_
            from unitree_sdk2py.idl.sensor_msgs.msg.dds_ import PointCloud2_
            from unitree_sdk2py.idl.unitree_go.msg.dds_ import (
                LowState_,
                SportModeState_,
            )
        except ImportError as exc:
            raise RuntimeError(
                "DDS source requires cyclonedds and the official unitree_sdk2py checkout"
            ) from exc

        config = ChannelConfigHasInterface.replace(
            "$__IF_NAME__$", self.network_interface
        )
        if self.robot_ip:
            config = re.sub(
                r'<Peer\s+Address="[^"]+"\s*/>',
                f'<Peer Address="{self.robot_ip}"/>',
                config,
                count=1,
            )
        configured_domain = Domain(self.domain_id, config)
        participant = DomainParticipant(self.domain_id)

        readers = [
            (
                "robot",
                "rt/lowstate",
                DataReader(
                    participant,
                    Topic(participant, "rt/lowstate", LowState_),
                ),
                self._low_state,
            ),
            (
                "robot",
                "rt/sportmodestate",
                DataReader(
                    participant,
                    Topic(participant, "rt/sportmodestate", SportModeState_),
                ),
                self._sport_mode_state,
            ),
            (
                "lidar",
                "rt/utlidar/cloud",
                DataReader(
                    participant,
                    Topic(participant, "rt/utlidar/cloud", PointCloud2_),
                ),
                self._lidar,
            ),
            (
                "odometry",
                "rt/utlidar/robot_odom",
                DataReader(
                    participant,
                    Topic(participant, "rt/utlidar/robot_odom", Odometry_),
                ),
                self._odometry,
            ),
        ]

        deadline = time.monotonic() + max(duration_seconds, 0.0)
        while time.monotonic() < deadline:
            had_sample = False
            for kind, topic_name, reader, parser in readers:
                for sample in list(reader.take(128) or []):
                    had_sample = True
                    yield SensorEvent(
                        kind=kind,
                        topic=topic_name,
                        received_timestamp_ns=time.time_ns(),
                        source_timestamp_ns=_stamp_ns(sample),
                        frame_id=getattr(
                            getattr(sample, "header", None), "frame_id", None
                        ),
                        payload=parser(sample),
                    )
            if not had_sample:
                time.sleep(0.01)
        # Keep the configured domain alive for the complete reader lifetime.
        del configured_domain

    @staticmethod
    def _low_state(sample: Any) -> dict[str, Any]:
        bms = getattr(sample, "bms_state", None)
        return {
            "source": "lowstate",
            "battery_percentage": getattr(bms, "soc", None),
            "tick": getattr(sample, "tick", None),
        }

    @staticmethod
    def _sport_mode_state(sample: Any) -> dict[str, Any]:
        return {
            "source": "sportmodestate",
            "mode": getattr(sample, "mode", None),
            "position": list(getattr(sample, "position", None) or []),
        }

    @staticmethod
    def _lidar(sample: Any) -> dict[str, Any]:
        return {
            "points": int(getattr(sample, "width", 0) or 0)
            * int(getattr(sample, "height", 0) or 0),
            "point_step": int(getattr(sample, "point_step", 0) or 0),
        }

    @staticmethod
    def _odometry(sample: Any) -> dict[str, Any]:
        pose = getattr(getattr(sample, "pose", None), "pose", None)
        position = getattr(pose, "position", None)
        return {
            "child_frame_id": getattr(sample, "child_frame_id", None),
            "position": [
                getattr(position, "x", None),
                getattr(position, "y", None),
                getattr(position, "z", None),
            ],
        }
