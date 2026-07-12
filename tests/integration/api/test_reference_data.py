"""Tests for test reference data."""

from fastapi.testclient import TestClient

from payroll.interfaces.api.dependencies import get_reference_data_queries
from payroll.interfaces.api.main import app
from helpers.reference_data import ReferenceDataStubMixin


class FakeReferenceDataQueries(ReferenceDataStubMixin):
    """Test double for Reference Data Queries."""


def test_reference_data_endpoints() -> None:
    """Test reference data endpoints."""
    fake_queries = FakeReferenceDataQueries()
    app.dependency_overrides[get_reference_data_queries] = lambda: fake_queries
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        assert client.get("/reference-data/pension-institutions").json() == [
            {
                "code": "AFP_UNO",
                "name": "AFP Uno",
                "mandatory_rate": "0.10",
                "is_active": True,
            }
        ]
        assert client.get("/reference-data/health-institutions").json() == [
            {
                "code": "FONASA",
                "name": "Fonasa",
                "kind": "fonasa",
                "mandatory_rate": "0.07",
                "is_active": True,
            }
        ]
        assert client.get("/reference-data/pension-plans").json() == [
            {
                "id": 1,
                "institution_code": "AFP_UNO",
                "institution_name": "AFP Uno",
                "valid_from": "2024-01-01",
                "valid_to": None,
                "additional_rate": "0",
            }
        ]
        assert client.get("/reference-data/health-plans").json() == [
            {
                "id": 2,
                "institution_code": "FONASA",
                "institution_name": "Fonasa",
                "institution_kind": "fonasa",
                "valid_from": "2024-01-01",
                "valid_to": None,
                "plan_name": "Base",
                "contracted_uf": "0",
            }
        ]
        assert client.get("/reference-data/contribution-caps").json() == [
            {
                "cap_type": "pension_health",
                "valid_from": "2026-01-01",
                "valid_to": None,
                "value_uf": "90.0600",
            }
        ]
        assert client.get("/reference-data/payroll-concepts").json() == [
            {
                "code": "SALARY_BASE",
                "name": "Base Salary",
                "kind": "income",
                "is_taxable": True,
            }
        ]
        assert fake_queries.include_inactive_health_institutions is False
        assert fake_queries.include_inactive_health_plans is False
    finally:
        app.dependency_overrides.clear()


def test_reference_data_endpoints_forward_include_inactive_query_param() -> None:
    """Test reference data endpoints forward include_inactive query param."""
    fake_queries = FakeReferenceDataQueries()
    app.dependency_overrides[get_reference_data_queries] = lambda: fake_queries
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        health_institutions_response = client.get(
            "/reference-data/health-institutions?include_inactive=true"
        )
        health_plans_response = client.get(
            "/reference-data/health-plans?include_inactive=true"
        )
    finally:
        app.dependency_overrides.clear()

    assert health_institutions_response.status_code == 200
    assert health_plans_response.status_code == 200
    assert fake_queries.include_inactive_health_institutions is True
    assert fake_queries.include_inactive_health_plans is True


def test_currencies_and_income_tax_brackets_routes_no_longer_exist() -> None:
    """Verify that routes removed in pf-rates migration return 404."""
    client = TestClient(app, headers={"X-API-Key": "test-key"})
    assert client.get("/reference-data/currencies").status_code == 404
    assert client.get("/reference-data/income-tax-brackets").status_code == 404
    assert (
        client.post(
            "/reference-data/income-tax-brackets/refresh", json={"year": 2026}
        ).status_code
        == 404
    )
