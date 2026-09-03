from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.config import get_settings
from backend.services.elder_companion_control_service import (
    ElderCompanionControlError,
    ElderCompanionControlService,
)


class _CareService:
    def get_directory(self):
        return SimpleNamespace(
            elders=[SimpleNamespace(id="elder-001", name="张爷爷")],
        )


class _RiskService:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed

    def status(self):
        return SimpleNamespace(motion_allowed=self.allowed)

    def motion_conflict_code(self):
        return None if self.allowed else "COMPANION_RISK_LOCK_ACTIVE"


class _Gateway:
    def __init__(self) -> None:
        self.state = "IDLE"
        self.calls: list[str] = []
        self.start_error: dict | None = None

    def companion_status(self):
        self.calls.append("status")
        return self._companion_result()

    def start_companion(self):
        self.calls.append("start")
        if self.start_error is not None:
            return self.start_error
        self.state = "FOLLOWING"
        return self._companion_result(runtime_active=True)

    def stop_companion(self):
        self.calls.append("stop")
        self.state = "IDLE"
        return self._companion_result()

    def status(self):
        self.calls.append("robot_status")
        return {
            "ok": True,
            "status": "ok",
            "data": {
                "success": True,
                "data": {
                    "robotId": "go2_edu_01",
                    "online": True,
                    "dds": {"ddsInitialized": True, "ddsStateAvailable": True},
                    "control": {"busy": self.state != "IDLE"},
                },
            },
        }

    def _companion_result(self, *, runtime_active: bool = False):
        active = runtime_active or self.state == "FOLLOWING"
        return {
            "ok": True,
            "status": "ok",
            "status_code": 200,
            "data": {
                "success": True,
                "data": {
                    "state": self.state,
                    "reason": "companion_started" if active else "already_idle",
                    "runtime_active": active,
                    "robot_online": True,
                    "resume_required": False,
                    "incident_id": None,
                    "uwb": {"valid": active, "distance_m": 1.82},
                    "lidar": {"valid": active, "state": "CLEAR" if active else "STOP"},
                    "risk": {
                        "state": "NORMAL",
                        "incident_id": None,
                        "manual_takeover": False,
                        "emergency_active": False,
                    },
                    "motion": {"vx": 0.0, "vy": 0.0, "wz": 0.0, "authority": "IDLE"},
                    "configuration": {
                        "motion_limits_aligned": True,
                        "vx_max_mps": 0.3,
                        "gateway_max_vx_mps": 0.3,
                        "walk_min_mps": 0.2,
                        "wz_max_radps": 0.3,
                        "gateway_max_wz_radps": 0.3,
                        "vy_mps": 0.0,
                    },
                },
            },
        }


def _service(*, gateway: _Gateway | None = None, bound_elder_id: str = "elder-001", risk_allowed: bool = True):
    settings = get_settings().model_copy(
        update={
            "companion_bound_elder_id": bound_elder_id,
            "companion_robot_id": "go2_edu_01",
            "companion_robot_name": "小康01",
            "companion_robot_model": "Go2 EDU",
        }
    )
    resolved_gateway = gateway or _Gateway()
    return ElderCompanionControlService(
        settings=settings,
        gateway=resolved_gateway,
        care_service=_CareService(),
        risk_service=_RiskService(allowed=risk_allowed),
    ), resolved_gateway


def test_idle_status_exposes_binding_checks_and_pending_live_inputs() -> None:
    service, _ = _service()

    status = service.status("elder-001")

    assert status.state == "IDLE"
    assert status.binding.matched is True
    assert status.robot.online is True
    assert status.can_start is True
    assert {item.key: item.state for item in status.checks}["uwb"] == "pending"
    assert {item.key: item.state for item in status.checks}["lidar"] == "pending"
    assert {item.key: item.code for item in status.checks}["uwb"] == "UWB_NOT_READY"
    assert {item.key: item.state for item in status.checks}["speed_contract"] == "passed"


def test_start_requires_matching_binding_and_confirms_following() -> None:
    service, gateway = _service()
    started = service.start("elder-001")
    assert started.state == "FOLLOWING"
    assert started.runtime_active is True
    assert {item.key: item.state for item in started.checks}["control_idle"] == "passed"
    assert gateway.calls[0] == "start"

    unbound, unbound_gateway = _service(bound_elder_id="")
    with pytest.raises(ElderCompanionControlError) as exc_info:
        unbound.start("elder-001")
    assert exc_info.value.code == "COMPANION_BINDING_NOT_CONFIGURED"
    assert "start" not in unbound_gateway.calls


def test_gateway_start_error_code_is_preserved() -> None:
    gateway = _Gateway()
    gateway.start_error = {
        "ok": False,
        "status": "degraded",
        "status_code": 503,
        "data": {
            "success": False,
            "code": "UWB_NOT_READY",
            "message": "UWB input is stale",
        },
        "error": "HTTP 503",
    }
    service, _ = _service(gateway=gateway)

    with pytest.raises(ElderCompanionControlError) as exc_info:
        service.start("elder-001")

    assert exc_info.value.code == "UWB_NOT_READY"
    assert exc_info.value.status_code == 503


def test_gateway_control_busy_error_code_is_preserved() -> None:
    gateway = _Gateway()
    gateway.start_error = {
        "ok": False,
        "status": "degraded",
        "status_code": 409,
        "data": {
            "success": False,
            "code": "CONTROL_BUSY",
            "message": "motion control is already owned",
        },
        "error": "HTTP 409",
    }
    service, _ = _service(gateway=gateway)

    with pytest.raises(ElderCompanionControlError) as exc_info:
        service.start("elder-001")

    assert exc_info.value.code == "CONTROL_BUSY"
    assert exc_info.value.status_code == 409


def test_risk_lock_blocks_start_before_gateway_call() -> None:
    service, gateway = _service(risk_allowed=False)
    with pytest.raises(ElderCompanionControlError) as exc_info:
        service.start("elder-001")
    assert exc_info.value.code == "RISK_LOCK_ACTIVE"
    assert "start" not in gateway.calls


def test_stop_is_idempotent_and_always_confirms_idle() -> None:
    service, gateway = _service()
    first = service.stop("elder-001")
    second = service.stop("elder-001")
    assert first.state == "IDLE"
    assert second.state == "IDLE"
    assert gateway.calls.count("stop") == 2
