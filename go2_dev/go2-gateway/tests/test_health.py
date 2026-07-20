from __future__ import annotations


def test_health_endpoint(client):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["service"] == "go2-gateway"
    assert body["data"]["mode"] == "mock"
    assert body["data"]["initialized"] is True

