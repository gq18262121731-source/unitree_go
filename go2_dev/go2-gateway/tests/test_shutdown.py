from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_shutdown_calls_stop():
    app = create_app(Settings(mode="mock"))
    with TestClient(app) as client:
        adapter = client.app.state.adapter
        before = adapter.stop_count

    assert adapter.stop_count > before

