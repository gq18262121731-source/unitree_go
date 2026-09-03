from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.adapters.mock_adapter import MockGo2Adapter
from app.config import Settings
from app.gateway.go2_gateway import Go2Gateway
from app.schemas.lidar import LIDAR_STATE_TOPIC
from app.services.lidar_status_service import LidarStatusService


class FakeNetworkDiagnostics:
    def __init__(self, payload: dict | None = None, exc: Exception | None = None) -> None:
        self.payload = payload or {}
        self.exc = exc

    def diagnostics(self) -> dict:
        if self.exc:
            raise self.exc
        return self.payload


class FakeLidarStatusService:
    def __init__(self) -> None:
        self.technical_calls = 0
        self.robot_calls = 0

    def technical_status(self) -> dict:
        self.technical_calls += 1
        return {
            "deviceDetected": None,
            "transportInitialized": True,
            "topicDiscovered": False,
            "sampleReceived": False,
            "dataFresh": False,
            "mappingPrerequisitesReady": False,
            "errorCode": "LIDAR_DATA_UNAVAILABLE",
            "checkedAt": _now_iso(),
        }

    def robot_status(self) -> dict:
        self.robot_calls += 1
        status = self.technical_status()
        return {
            "available": False,
            "status": "unavailable",
            "mappingReady": False,
            "reason": status["errorCode"],
            "updatedAt": status["checkedAt"],
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _ago_iso(seconds: float) -> str:
    return (datetime.now(timezone.utc).astimezone() - timedelta(seconds=seconds)).isoformat()


def _lidar_topic(**overrides) -> dict:
    data = {
        "topic": LIDAR_STATE_TOPIC,
        "created": True,
        "discovered": True,
        "received": True,
        "sampleCount": 8,
        "firstSampleAt": _ago_iso(1.0),
        "lastSampleAt": _now_iso(),
        "frequencyHz": 5.0,
        "packetLossRate": 0.0,
        "cloudSize": 256,
        "errorState": 0,
    }
    data.update(overrides)
    return data


def _network_payload(
    *,
    reachable=True,
    dds_initialized=True,
    dds_state_available=True,
    enumeration_status="OK",
    enumeration_reliable=True,
    lidar: dict | None = None,
) -> dict:
    return {
        "networkReachable": reachable,
        "ddsInitialized": dds_initialized,
        "ddsStateAvailable": dds_state_available,
        "errorCode": None if dds_state_available else "UNITREE_DDS_NO_STATE_SAMPLES",
        "networkInterfaceStatus": {
            "enumerationStatus": enumeration_status,
            "enumerationReliable": enumeration_reliable,
        },
        "dds": {
            "ddsInitialized": dds_initialized,
            "ddsStateAvailable": dds_state_available,
            "lidarState": lidar or {},
        },
    }


def _service(payload: dict | None = None, exc: Exception | None = None) -> LidarStatusService:
    settings = Settings(mode="mock", task_audit_enabled=False)
    adapter = MockGo2Adapter(settings.robot_id)
    gateway = Go2Gateway(adapter)
    gateway.connect()
    return LidarStatusService(settings, gateway, FakeNetworkDiagnostics(payload, exc))


def test_lidar_status_reports_network_unreachable_without_device_false():
    status = _service(_network_payload(reachable=False, dds_state_available=False)).technical_status()

    assert status["errorCode"] == "ROBOT_NETWORK_UNREACHABLE"
    assert status["deviceDetected"] is None
    assert status["mappingPrerequisitesReady"] is False
    assert "ROBOT_NETWORK_UNREACHABLE" in status["blockedBy"]


def test_lidar_status_reports_dds_not_initialized():
    status = _service(_network_payload(dds_initialized=False, dds_state_available=False)).technical_status()

    assert status["transportInitialized"] is False
    assert status["errorCode"] == "ROBOT_DDS_NOT_INITIALIZED"
    assert "ROBOT_DDS_NOT_INITIALIZED" in status["blockedBy"]


def test_lidar_status_reports_interface_enumeration_timeout_as_unreliable():
    status = _service(
        _network_payload(
            enumeration_status="INTERFACE_ENUMERATION_TIMEOUT",
            enumeration_reliable=False,
            lidar={},
        )
    ).technical_status()

    assert status["enumerationStatus"] == "INTERFACE_ENUMERATION_TIMEOUT"
    assert status["enumerationReliable"] is False
    assert status["deviceDetected"] is None
    assert status["errorCode"] == "LIDAR_INTERFACE_ENUMERATION_UNRELIABLE"


def test_lidar_status_reports_topic_not_discovered():
    status = _service(_network_payload(lidar={})).technical_status()

    assert status["topicDiscovered"] is False
    assert status["deviceDetected"] is None
    assert status["errorCode"] == "LIDAR_TOPIC_NOT_DISCOVERED"


def test_lidar_status_reports_discovered_topic_with_zero_samples():
    status = _service(
        _network_payload(
            lidar=_lidar_topic(received=False, sampleCount=0, lastSampleAt=None, frequencyHz=None),
        )
    ).technical_status()

    assert status["topicDiscovered"] is True
    assert status["sampleReceived"] is False
    assert status["deviceDetected"] is None
    assert status["errorCode"] == "DDS_NO_LIDAR_SAMPLES"


def test_lidar_status_reports_stale_samples():
    status = _service(_network_payload(lidar=_lidar_topic(lastSampleAt=_ago_iso(10.0)))).technical_status()

    assert status["sampleReceived"] is True
    assert status["dataFresh"] is False
    assert status["errorCode"] == "LIDAR_DATA_STALE"


def test_lidar_status_reports_low_frequency():
    status = _service(_network_payload(lidar=_lidar_topic(frequencyHz=0.2))).technical_status()

    assert status["dataFresh"] is True
    assert status["errorCode"] == "LIDAR_FREQUENCY_TOO_LOW"
    assert status["mappingPrerequisitesReady"] is False


def test_lidar_status_reports_high_packet_loss():
    status = _service(_network_payload(lidar=_lidar_topic(packetLossRate=0.7))).technical_status()

    assert status["errorCode"] == "LIDAR_PACKET_LOSS_HIGH"
    assert status["mappingPrerequisitesReady"] is False


def test_lidar_status_reports_stable_samples_as_mapping_prerequisites_ready():
    status = _service(_network_payload(lidar=_lidar_topic())).technical_status()

    assert status["deviceDetected"] is True
    assert status["topicDiscovered"] is True
    assert status["sampleReceived"] is True
    assert status["dataFresh"] is True
    assert status["errorCode"] is None
    assert status["mappingPrerequisitesReady"] is True


def test_lidar_status_structures_bottom_probe_exception():
    status = _service(exc=RuntimeError("mock diagnostics exploded")).technical_status()

    assert status["errorCode"] == "LIDAR_DIAGNOSTICS_ERROR"
    assert status["mappingPrerequisitesReady"] is False
    assert "LIDAR_DIAGNOSTICS_ERROR" in status["blockedBy"]
    assert status["rawDiagnostics"]["network"]["error"]


def test_lidar_status_keeps_unknown_when_base_dds_has_no_robot_samples():
    status = _service(_network_payload(dds_state_available=False, lidar=_lidar_topic())).technical_status()

    assert status["deviceDetected"] is None
    assert status["topicDiscovered"] is False
    assert status["sampleReceived"] is True
    assert status["errorCode"] == "LIDAR_DATA_UNAVAILABLE"
    assert "ROBOT_DDS_NO_STATE_SAMPLES" in status["blockedBy"]


def test_lidar_http_endpoints_share_service_and_do_not_move_robot(client):
    fake = FakeLidarStatusService()
    client.app.state.lidar_status_service = fake
    adapter = client.app.state.adapter
    before_move_count = adapter.move_count
    before_stand_count = adapter.stand_count

    raw = client.get("/api/lidar/status")
    simplified = client.get("/api/robot/lidar/status")

    assert raw.status_code == 200
    assert raw.json()["data"]["errorCode"] == "LIDAR_DATA_UNAVAILABLE"
    assert simplified.status_code == 200
    assert simplified.json()["data"]["reason"] == "LIDAR_DATA_UNAVAILABLE"
    assert fake.technical_calls == 2
    assert fake.robot_calls == 1
    assert adapter.move_count == before_move_count
    assert adapter.stand_count == before_stand_count
