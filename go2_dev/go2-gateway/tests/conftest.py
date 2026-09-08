from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SDK_ROOT = ROOT.parent / "unitree_sdk2_python"
if SDK_ROOT.exists() and str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(mode="mock", state_stale_seconds=2.0, task_audit_enabled=False)


@pytest.fixture
def client(settings: Settings):
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
