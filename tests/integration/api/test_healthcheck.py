"""Tests for test healthcheck."""

from fastapi.testclient import TestClient

from payroll.interfaces.api.main import app


def test_healthcheck() -> None:
    """Test healthcheck."""
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "pf-payroll"
    assert isinstance(body["uptime_seconds"], int | float)
    assert body["uptime_seconds"] >= 0
