from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from app.config import Settings
from app.gateway.go2_gateway import Go2Gateway
from app.schemas.common import now_iso
from app.schemas.lidar import (
    LIDAR_CANDIDATE_TOPICS,
    LIDAR_MAX_PACKET_LOSS_RATE,
    LIDAR_MIN_FREQUENCY_HZ,
    LIDAR_STALE_AFTER_MS,
    LIDAR_STATE_TOPIC,
    LIDAR_TRANSPORT_DDS,
    empty_lidar_topic_status,
)


class NetworkDiagnosticsProvider(Protocol):
    def diagnostics(self) -> dict:
        ...


class LidarStatusService:
    """Read-only LiDAR observability service.

    This service deliberately does not publish to robot topics, switch LiDAR on/off,
    start mapping, send goals, or touch motion control.
    """

    def __init__(self, settings: Settings, gateway: Go2Gateway, network_diagnostics: NetworkDiagnosticsProvider) -> None:
        self.settings = settings
        self.gateway = gateway
        self.network_diagnostics = network_diagnostics

    def technical_status(self) -> dict:
        checked_at = now_iso()
        try:
            network = self.network_diagnostics.diagnostics()
        except Exception as exc:
            network = {
                "errorCode": "LIDAR_DIAGNOSTICS_ERROR",
                "error": str(exc),
                "networkReachable": None,
                "ddsInitialized": self.gateway.is_initialized(),
                "ddsStateAvailable": None,
                "networkInterfaceStatus": {
                    "enumerationStatus": "DIAGNOSTICS_ERROR",
                    "enumerationReliable": False,
                    "error": str(exc),
                },
                "dds": self._safe_dds_diagnostics(),
            }

        dds = network.get("dds") or self._safe_dds_diagnostics()
        lidar_topic = self._lidar_topic_status(dds)
        base_dds_initialized = bool(network.get("ddsInitialized", dds.get("ddsInitialized", self.gateway.is_initialized())))
        base_dds_state_available = bool(network.get("ddsStateAvailable", dds.get("ddsStateAvailable", False)))
        network_reachable = network.get("networkReachable")
        interface_status = network.get("networkInterfaceStatus") or {}
        enumeration_status = interface_status.get("enumerationStatus") or self._enumeration_status_from_network(network)
        enumeration_reliable = interface_status.get("enumerationReliable")
        blocked_by = self._blocked_by(network, base_dds_initialized, base_dds_state_available, lidar_topic)

        topic_discovered = bool(lidar_topic.get("discovered") or lidar_topic.get("created"))
        sample_received = bool(lidar_topic.get("received") or (lidar_topic.get("sampleCount") or 0) > 0)
        sample_age_ms = self._sample_age_ms(lidar_topic.get("lastSampleAt"), checked_at)
        data_fresh = sample_received and sample_age_ms is not None and sample_age_ms <= LIDAR_STALE_AFTER_MS
        frequency_hz = self._number(lidar_topic.get("frequencyHz") or lidar_topic.get("cloudFrequency"))
        packet_loss_rate = self._number(lidar_topic.get("packetLossRate") or lidar_topic.get("cloudPacketLossRate"))
        frequency_ok = frequency_hz is not None and frequency_hz >= LIDAR_MIN_FREQUENCY_HZ
        packet_loss_ok = packet_loss_rate is None or packet_loss_rate <= LIDAR_MAX_PACKET_LOSS_RATE

        if not base_dds_state_available:
            device_detected = None
            topic_discovered = False
        elif not topic_discovered:
            device_detected = None
        elif not sample_received:
            device_detected = None
        else:
            device_detected = True

        mapping_ready = bool(
            base_dds_initialized
            and base_dds_state_available
            and topic_discovered
            and sample_received
            and data_fresh
            and frequency_ok
            and packet_loss_ok
            and not lidar_topic.get("errorState")
        )
        error_code = self._error_code(
            network,
            base_dds_initialized,
            base_dds_state_available,
            topic_discovered,
            sample_received,
            data_fresh,
            frequency_hz,
            packet_loss_rate,
        )

        return {
            "deviceDetected": device_detected,
            "transportInitialized": base_dds_initialized,
            "topicDiscovered": topic_discovered,
            "sampleReceived": sample_received,
            "dataFresh": data_fresh,
            "mappingPrerequisitesReady": mapping_ready,
            "transport": LIDAR_TRANSPORT_DDS,
            "topic": lidar_topic.get("topic") if topic_discovered else None,
            "candidateTopics": list(LIDAR_CANDIDATE_TOPICS),
            "frequencyHz": frequency_hz,
            "minFrequencyHz": LIDAR_MIN_FREQUENCY_HZ,
            "packetLossRate": packet_loss_rate,
            "maxPacketLossRate": LIDAR_MAX_PACKET_LOSS_RATE,
            "sampleAgeMs": sample_age_ms,
            "staleAfterMs": LIDAR_STALE_AFTER_MS,
            "lastSampleAt": lidar_topic.get("lastSampleAt"),
            "enumerationStatus": enumeration_status,
            "enumerationReliable": enumeration_reliable,
            "errorCode": error_code,
            "message": self._message(error_code),
            "blockedBy": blocked_by,
            "checkedAt": checked_at,
            "robot": {
                "robotId": self.settings.robot_id,
                "robotIp": self.settings.robot_ip,
                "networkInterface": self.settings.network_interface,
                "domainId": self.settings.domain_id,
                "mode": self.settings.mode,
            },
            "topicStatus": lidar_topic,
            "rawDiagnostics": {
                "network": network,
                "dds": dds,
            },
        }

    def robot_status(self) -> dict:
        status = self.technical_status()
        error_code = status.get("errorCode")
        return {
            "available": bool(status["mappingPrerequisitesReady"]),
            "status": "ready" if status["mappingPrerequisitesReady"] else "unavailable",
            "mappingReady": bool(status["mappingPrerequisitesReady"]),
            "reason": error_code,
            "updatedAt": status["checkedAt"],
            "deviceDetected": status["deviceDetected"],
            "sampleReceived": status["sampleReceived"],
            "dataFresh": status["dataFresh"],
            "blockedBy": status["blockedBy"],
        }

    def _safe_dds_diagnostics(self) -> dict:
        try:
            return self.gateway.dds_diagnostics()
        except Exception as exc:
            return {
                "ddsInitialized": self.gateway.is_initialized(),
                "ddsStateAvailable": False,
                "errorCode": "DDS_DIAGNOSTICS_ERROR",
                "error": str(exc),
            }

    def _lidar_topic_status(self, dds: dict) -> dict:
        lidar = dds.get("lidarState") or dds.get("lidar") or dds.get("utlidar") or {}
        topic = empty_lidar_topic_status(LIDAR_STATE_TOPIC)
        topic.update(lidar)
        if not topic.get("topic"):
            topics = topic.get("topics") or []
            topic["topic"] = topics[0] if topics else LIDAR_STATE_TOPIC
        return topic

    def _blocked_by(self, network: dict, dds_initialized: bool, dds_state_available: bool, lidar_topic: dict) -> list[str]:
        blocked_by = []
        if network.get("networkReachable") is False:
            blocked_by.append("ROBOT_NETWORK_UNREACHABLE")
        if network.get("errorCode") == "LIDAR_DIAGNOSTICS_ERROR":
            blocked_by.append("LIDAR_DIAGNOSTICS_ERROR")
        if not dds_initialized:
            blocked_by.append("ROBOT_DDS_NOT_INITIALIZED")
        if dds_initialized and not dds_state_available:
            blocked_by.append("ROBOT_DDS_NO_STATE_SAMPLES")
        if network.get("errorCode") and network["errorCode"] not in {"UNITREE_DDS_NO_STATE_SAMPLES"}:
            blocked_by.append(str(network["errorCode"]))
        if lidar_topic.get("timeoutCode"):
            blocked_by.append(str(lidar_topic["timeoutCode"]))
        return blocked_by

    def _error_code(
        self,
        network: dict,
        dds_initialized: bool,
        dds_state_available: bool,
        topic_discovered: bool,
        sample_received: bool,
        data_fresh: bool,
        frequency_hz: float | None,
        packet_loss_rate: float | None,
    ) -> str | None:
        if network.get("errorCode") == "LIDAR_DIAGNOSTICS_ERROR":
            return "LIDAR_DIAGNOSTICS_ERROR"
        if network.get("networkReachable") is False:
            return "ROBOT_NETWORK_UNREACHABLE"
        if not dds_initialized:
            return "ROBOT_DDS_NOT_INITIALIZED"
        if dds_initialized and not dds_state_available:
            return "LIDAR_DATA_UNAVAILABLE"
        if network.get("networkInterfaceStatus", {}).get("enumerationReliable") is False:
            return "LIDAR_INTERFACE_ENUMERATION_UNRELIABLE"
        if not topic_discovered:
            return "LIDAR_TOPIC_NOT_DISCOVERED"
        if not sample_received:
            return "DDS_NO_LIDAR_SAMPLES"
        if not data_fresh:
            return "LIDAR_DATA_STALE"
        if frequency_hz is None or frequency_hz < LIDAR_MIN_FREQUENCY_HZ:
            return "LIDAR_FREQUENCY_TOO_LOW"
        if packet_loss_rate is not None and packet_loss_rate > LIDAR_MAX_PACKET_LOSS_RATE:
            return "LIDAR_PACKET_LOSS_HIGH"
        return None

    def _message(self, error_code: str | None) -> str:
        messages = {
            None: "雷达数据链路满足后续建图验证的最低技术条件。",
            "ROBOT_NETWORK_UNREACHABLE": "机器人网络不可达，无法判断雷达硬件状态。",
            "ROBOT_DDS_NOT_INITIALIZED": "DDS 通信尚未初始化，无法判断雷达硬件状态。",
            "LIDAR_DATA_UNAVAILABLE": "DDS 基础链路尚未收到机器人真实状态样本，雷达硬件状态未知。",
            "LIDAR_INTERFACE_ENUMERATION_UNRELIABLE": "网卡或接口枚举不可靠，雷达话题发现结果不可作为硬件不存在依据。",
            "LIDAR_TOPIC_NOT_DISCOVERED": "未发现可确认的雷达数据话题。",
            "DDS_NO_LIDAR_SAMPLES": "已进入 DDS 状态链路，但尚未收到雷达样本。",
            "LIDAR_DATA_STALE": "已收到雷达样本，但数据已过期。",
            "LIDAR_FREQUENCY_TOO_LOW": "雷达样本频率低于建图验证最低要求。",
            "LIDAR_PACKET_LOSS_HIGH": "雷达样本丢包率高于建图验证最低要求。",
            "LIDAR_DIAGNOSTICS_ERROR": "雷达状态底层探测异常，接口已返回结构化诊断结果。",
        }
        return messages.get(error_code, "雷达状态诊断异常。")

    def _enumeration_status_from_network(self, network: dict) -> str:
        if network.get("error"):
            return "DIAGNOSTICS_ERROR"
        if network.get("networkReachable") is False:
            return "NETWORK_UNREACHABLE"
        return "OK"

    def _sample_age_ms(self, last_sample_at: str | None, checked_at: str) -> int | None:
        if not last_sample_at:
            return None
        try:
            sample = self._parse_datetime(last_sample_at)
            checked = self._parse_datetime(checked_at)
        except ValueError:
            return None
        return max(0, int((checked - sample).total_seconds() * 1000))

    def _parse_datetime(self, value: str) -> datetime:
        if value.endswith("Z"):
            value = f"{value[:-1]}+00:00"
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _number(self, value) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
