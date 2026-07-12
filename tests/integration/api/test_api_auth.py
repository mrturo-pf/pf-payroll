"""Integration tests verifying that API key auth is wired to protected routes.

These tests exercise the router-level Depends(verify_api_key) wiring in main.py,
NOT just the verify_api_key function in isolation.  If the dependency were removed
from include_router(), the unit tests in test_security.py would still pass while
these tests would correctly fail.
"""

from fastapi.testclient import TestClient

from payroll.interfaces.api.main import app


def test_protected_route_returns_403_when_key_is_missing() -> None:
    """A request to a protected route with no X-API-Key header returns 403."""
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/payroll/summary")
    assert response.status_code == 403
    assert response.json() == {"detail": "Invalid or missing API key."}


def test_protected_route_returns_403_when_key_is_wrong() -> None:
    """A request to a protected route with an incorrect X-API-Key returns 403."""
    client = TestClient(
        app, headers={"X-API-Key": "wrong-key"}, raise_server_exceptions=False
    )
    response = client.get("/reference-data/pension-institutions")
    assert response.status_code == 403
    assert response.json() == {"detail": "Invalid or missing API key."}


def test_health_returns_200_without_any_key() -> None:
    """/health is public: no X-API-Key header required."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
