from __future__ import annotations

import threading
import time
from typing import Callable

from app.motion.supervised_loop import RawUwbSample, SupervisedMotionLoop
from app.providers.unitree.pointcloud_decoder import decode_xyz


class Phase7ReadonlyInputStream:
    """SDK2 reader-only UWB and cloud_base input on the existing ChannelFactory.

    The caller must initialize RobotService first. This class deliberately does
    not call ChannelFactoryInitialize and never creates a DDS publisher.
    """

    def __init__(
        self,
        loop: SupervisedMotionLoop,
        *,
        uwb_topic: str = "rt/uwbstate",
        lidar_topic: str = "rt/utlidar/cloud_base",
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.loop = loop
        self.uwb_topic = uwb_topic
        self.lidar_topic = lidar_topic
        self._monotonic_clock = monotonic_clock
        self._lock = threading.RLock()
        self._subscribers: list[object] = []
        self._started = False
        self.uwb_samples = 0
        self.lidar_samples = 0
        self.uwb_errors = 0
        self.lidar_errors = 0

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            from unitree_sdk2py.core.channel import ChannelSubscriber
            from unitree_sdk2py.idl.sensor_msgs.msg.dds_ import PointCloud2_
            from unitree_sdk2py.idl.unitree_go.msg.dds_ import UwbState_

            uwb = ChannelSubscriber(self.uwb_topic, UwbState_)
            lidar = ChannelSubscriber(self.lidar_topic, PointCloud2_)
            uwb.Init(self._on_uwb, 10)
            lidar.Init(self._on_lidar, 2)
            self._subscribers = [uwb, lidar]
            self._started = True

    def close(self) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
            self._subscribers = []
            self._started = False
        for subscriber in subscribers:
            try:
                subscriber.Close()
            except Exception:
                pass

    def diagnostics(self) -> dict[str, object]:
        with self._lock:
            return {
                "started": self._started,
                "dds_publishers": 0,
                "uwb_topic": self.uwb_topic,
                "lidar_topic": self.lidar_topic,
                "uwb_samples": self.uwb_samples,
                "lidar_samples": self.lidar_samples,
                "uwb_errors": self.uwb_errors,
                "lidar_errors": self.lidar_errors,
            }

    def _on_uwb(self, sample: object) -> None:
        now = self._monotonic_clock()
        try:
            self.loop.ingest_uwb(
                RawUwbSample(
                    distance_est=float(getattr(sample, "distance_est")),
                    orientation_est=float(getattr(sample, "orientation_est")),
                    enabled_from_app=int(getattr(sample, "enabled_from_app")),
                    error_state=int(getattr(sample, "error_state")),
                    sample_monotonic=now,
                )
            )
            with self._lock:
                self.uwb_samples += 1
        except Exception as exc:
            self.loop.report_uwb_error(exc)
            with self._lock:
                self.uwb_errors += 1

    def _on_lidar(self, sample: object) -> None:
        now = self._monotonic_clock()
        try:
            points = decode_xyz(sample)
            frame_id = str(
                getattr(getattr(sample, "header", None), "frame_id", "")
            )
            self.loop.ingest_lidar(
                points,
                frame_id=frame_id,
                sample_monotonic=now,
            )
            with self._lock:
                self.lidar_samples += 1
        except Exception as exc:
            self.loop.report_lidar_error(exc)
            with self._lock:
                self.lidar_errors += 1
