from __future__ import annotations

from app.adapters.unitree_adapter import UnitreeGo2Adapter


class DummySportClient:
    def __init__(self) -> None:
        self.stop_count = 0

    def StopMove(self) -> int:
        self.stop_count += 1
        return 0


def test_unitree_adapter_close_clears_runtime_handles():
    adapter = UnitreeGo2Adapter("eth0", 1.0, "go2-edu-001")
    sport_client = DummySportClient()
    adapter._initialized = True
    adapter._sport_client = sport_client
    adapter._video_client = object()
    adapter._sport_state = object()
    adapter._low_state = object()
    adapter._subscribers = [object()]

    adapter.close()

    assert sport_client.stop_count == 1
    assert adapter.is_initialized() is False
    assert adapter._sport_client is None
    assert adapter._video_client is None
    assert adapter._sport_state is None
    assert adapter._low_state is None
    assert adapter._subscribers == []


def test_unitree_adapter_rewrites_dds_peer_to_robot_ip():
    config = '<Peer Address="192.168.123.161"/>'

    rewritten = UnitreeGo2Adapter._dds_config_with_peer(config, "192.168.43.147")

    assert rewritten == '<Peer Address="192.168.43.147"/>'


def test_unitree_adapter_diagnostics_exposes_topic_timeouts_after_subscribers_created():
    adapter = UnitreeGo2Adapter("WLAN", 1.0, "go2-edu-001", robot_ip="192.168.43.147", domain_id=0)
    adapter._initialized = True
    adapter._mark_topic_created("sportState", "rt/lf/sportmodestate")
    adapter._mark_topic_created("lowState", "rt/lf/lowstate")

    diagnostics = adapter.dds_diagnostics()

    assert diagnostics["ddsInitialized"] is True
    assert diagnostics["ddsStateAvailable"] is False
    assert diagnostics["sportState"]["timeout"] is True
    assert diagnostics["sportState"]["timeoutCode"] == "SPORT_STATE_TIMEOUT"
    assert diagnostics["lowState"]["timeout"] is True
    assert diagnostics["lowState"]["timeoutCode"] == "LOW_STATE_TIMEOUT"
