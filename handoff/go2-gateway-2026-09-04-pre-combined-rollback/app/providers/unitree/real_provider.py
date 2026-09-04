from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.providers.unitree.dds_reader import DdsReader, DiscoveredTopic
from app.providers.unitree.lidar_reader import LidarReader
from app.providers.unitree.network_diagnostics import NetworkProbe, probe_network
from app.providers.unitree.state_reader import StateReader


class SafetyConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class RealProviderConfig:
    robot_ip: str
    network_interface: str
    domain_id: int = 0
    provider: str = "unitree_real"
    real_motion_enabled: bool = False

    def validate(self) -> None:
        if self.provider != "unitree_real":
            raise SafetyConfigurationError(
                "Phase 5.1 hardware probe requires ROBOT_PROVIDER=unitree_real"
            )
        if self.real_motion_enabled:
            raise SafetyConfigurationError(
                "Phase 5.1 is read-only; REAL_MOTION_ENABLED must remain false"
            )


class RealGo2Provider:
    """Phase 5.1 read-only state and LiDAR provider.

    It intentionally does not implement the navigation provider protocol and has
    no motion, command, publisher, Nav2, or SLAM surface.
    """

    provider_name = "unitree_real"
    real_motion_enabled = False

    def __init__(
        self,
        config: RealProviderConfig,
        *,
        dds_reader_factory: Callable[[str, str, int], DdsReader] = DdsReader,
        network_probe: Callable[[str, str, int], NetworkProbe] = probe_network,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        config.validate()
        self.config = config
        self._dds_reader_factory = dds_reader_factory
        self._network_probe = network_probe
        self._clock = clock
        self._monotonic = monotonic
        self._sleep = sleep

    def collect_report(
        self,
        *,
        sample_seconds: float = 10.0,
        discovery_seconds: float = 3.0,
        ping_count: int = 4,
    ) -> dict[str, Any]:
        network = self._network_probe(
            self.config.robot_ip, self.config.network_interface, ping_count
        )
        report: dict[str, Any] = {
            "phase": "5.1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "provider": self.provider_name,
            "real_motion_enabled": False,
            "network": network.to_dict(),
            "dds": {
                "initialized": False,
                "domain_id": self.config.domain_id,
                "topics": [],
                "error": None,
            },
            "state": StateReader().report(),
            "lidar": LidarReader().report(),
        }
        if not network.reachable:
            report["dds"]["error"] = "NETWORK_UNREACHABLE"
            return self._finalize(report)

        dds = self._dds_reader_factory(
            self.config.network_interface,
            self.config.robot_ip,
            self.config.domain_id,
        )
        state_reader = StateReader()
        lidar_reader = LidarReader()
        try:
            dds.initialize()
            report["dds"]["initialized"] = True
            topics = dds.discover_topics(discovery_seconds)
            report["dds"]["topics"] = [item.to_dict() for item in topics]
            readers = self._create_discovered_readers(dds, topics)
            deadline = self._monotonic() + max(sample_seconds, 0.0)
            first_pass = True
            while first_pass or self._monotonic() < deadline:
                first_pass = False
                for kind, topic_name, reader in readers:
                    for sample in dds.take(reader):
                        received_epoch = self._clock()
                        if kind == "low_state":
                            state_reader.consume_low_state(topic_name, sample, received_epoch)
                        elif kind == "sport_mode_state":
                            state_reader.consume_sport_mode_state(
                                topic_name, sample, received_epoch
                            )
                        else:
                            lidar_reader.consume(topic_name, sample, received_epoch)
                if self._monotonic() < deadline:
                    self._sleep(min(0.02, max(deadline - self._monotonic(), 0.0)))
        except Exception as exc:
            report["dds"]["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            dds.close()

        report["state"] = state_reader.report()
        report["lidar"] = lidar_reader.report()
        return self._finalize(report)

    @staticmethod
    def _finalize(report: dict[str, Any]) -> dict[str, Any]:
        checks = {
            "network_reachable": bool(report["network"]["reachable"]),
            "dds_initialized": bool(report["dds"]["initialized"]),
            "low_state_received": report["state"]["low_state"]["sample_count"] > 0,
            "sport_mode_state_received": (
                report["state"]["sport_mode_state"]["sample_count"] > 0
            ),
            "lidar_point_cloud_received": report["lidar"]["sample_count"] > 0,
        }
        if not checks["network_reachable"]:
            status = "BLOCKED_NETWORK"
        elif not checks["dds_initialized"]:
            status = "BLOCKED_DDS"
        elif not checks["low_state_received"] or not checks["sport_mode_state_received"]:
            status = "INCOMPLETE_STATE"
        elif not checks["lidar_point_cloud_received"]:
            status = "INCOMPLETE_LIDAR"
        else:
            status = "PASSED"
        report["checks"] = checks
        report["status"] = status
        report["passed"] = all(checks.values())
        return report

    @staticmethod
    def _create_discovered_readers(
        dds: DdsReader, topics: list[DiscoveredTopic]
    ) -> list[tuple[str, str, Any]]:
        from unitree_sdk2py.idl.sensor_msgs.msg.dds_ import PointCloud2_
        from unitree_sdk2py.idl.unitree_go.msg.dds_ import LowState_, SportModeState_

        readers: list[tuple[str, str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for topic in topics:
            normalized_name = topic.name.lower().rstrip("/")
            normalized_type = topic.type_name.lower()
            kind: str | None = None
            message_type: Any = None
            if normalized_name.endswith("/lowstate") and "lowstate" in normalized_type:
                kind, message_type = "low_state", LowState_
            elif normalized_name.endswith("/sportmodestate") and "sportmodestate" in normalized_type:
                kind, message_type = "sport_mode_state", SportModeState_
            elif "utlidar" in normalized_name and "pointcloud2" in normalized_type:
                kind, message_type = "lidar", PointCloud2_
            if kind is None or (kind, topic.name) in seen:
                continue
            seen.add((kind, topic.name))
            readers.append((kind, topic.name, dds.create_reader(topic.name, message_type)))
        return readers
