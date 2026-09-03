from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import elder_companion_api
from backend.dependencies import get_elder_companion_control_service
from backend.models.user_model import UserRole
from backend.services.elder_companion_control_service import ElderCompanionControlError


class _Service:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.error: ElderCompanionControlError | None = None

    def start(self, elder_id: str):
        self.calls.append(("start", elder_id))
        if self.error is not None:
            raise self.error
        return _status("FOLLOWING", runtime_active=True)

    def stop(self, elder_id: str):
        self.calls.append(("stop", elder_id))
        return _status("IDLE", runtime_active=False)

    def status(self, elder_id: str):
        self.calls.append(("status", elder_id))
        return _status("IDLE", runtime_active=False)


def _status(state: str, *, runtime_active: bool):
    return {
        "elder_id": "elder-001",
        "elder_name": "张爷爷",
        "binding": {
            "configured": True,
            "matched": True,
            "elder_id": "elder-001",
            "robot_id": "go2_edu_01",
        },
        "robot": {
            "robot_id": "go2_edu_01",
            "name": "小康01",
            "model": "Go2 EDU",
            "online": True,
        },
        "gateway_available": True,
        "state": state,
        "reason": "test",
        "runtime_active": runtime_active,
        "resume_required": False,
        "uwb": {},
        "lidar": {},
        "risk": {},
        "motion": {},
        "configuration": {},
        "checks": [],
        "can_start": state == "IDLE",
        "can_stop": True,
    }


def _client(
    monkeypatch,
    service: _Service,
    *,
    role: UserRole = UserRole.COMMUNITY,
    user_id: str = "community-001",
) -> TestClient:
    monkeypatch.setattr(
        elder_companion_api,
        "require_write_session_user",
        lambda _authorization: SimpleNamespace(role=role, id=user_id),
    )
    app = FastAPI()
    app.include_router(elder_companion_api.router, prefix="/api/v1")
    app.dependency_overrides[get_elder_companion_control_service] = lambda: service
    return TestClient(app)


def test_start_route_uses_elder_scoped_health_new_contract(monkeypatch) -> None:
    service = _Service()
    client = _client(monkeypatch, service)

    response = client.post(
        "/api/v1/elders/elder-001/robot-companion/start",
        headers={"Authorization": "Bearer test"},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "FOLLOWING"
    assert service.calls == [("start", "elder-001")]


def test_gateway_error_code_and_status_are_preserved_by_route(monkeypatch) -> None:
    service = _Service()
    service.error = ElderCompanionControlError(
        code="CONTROL_BUSY",
        message="motion control is already owned",
        status_code=409,
    )
    client = _client(monkeypatch, service)

    response = client.post(
        "/api/v1/elders/elder-001/robot-companion/start",
        headers={"Authorization": "Bearer test"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "CONTROL_BUSY",
        "message": "motion control is already owned",
    }


def test_elder_can_control_only_own_companion(monkeypatch) -> None:
    service = _Service()
    client = _client(
        monkeypatch,
        service,
        role=UserRole.ELDER,
        user_id="elder-001",
    )

    response = client.post(
        "/api/v1/elders/elder-001/robot-companion/start",
        headers={"Authorization": "Bearer elder-session"},
    )

    assert response.status_code == 200
    assert service.calls == [("start", "elder-001")]


def test_elder_cannot_control_another_elder_companion(monkeypatch) -> None:
    service = _Service()
    client = _client(
        monkeypatch,
        service,
        role=UserRole.ELDER,
        user_id="elder-002",
    )

    response = client.post(
        "/api/v1/elders/elder-001/robot-companion/start",
        headers={"Authorization": "Bearer elder-session"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "ELDER_SELF_CONTROL_ONLY"
    assert service.calls == []


def test_family_account_cannot_control_companion(monkeypatch) -> None:
    service = _Service()
    client = _client(
        monkeypatch,
        service,
        role=UserRole.FAMILY,
        user_id="family-001",
    )

    response = client.post(
        "/api/v1/elders/elder-001/robot-companion/start",
        headers={"Authorization": "Bearer family-session"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "COMPANION_CONTROL_FORBIDDEN"
    assert service.calls == []
