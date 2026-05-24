"""Tests for test reference data."""

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from payroll.application.errors import PayrollDependencyError
from payroll.application.dto import (
    ContributionCapDTO,
    CurrencyDTO,
    HealthInstitutionDTO,
    HealthPlanDTO,
    IncomeTaxBracketDTO,
    PayrollConceptDTO,
    PensionInstitutionDTO,
    PensionPlanDTO,
    RefreshIncomeTaxBracketsResultDTO,
)
from payroll.domain.contributions import HealthInstitutionKind
from payroll.interfaces.api.dependencies import (
    get_reference_data_queries,
    get_refresh_income_tax_brackets_use_case,
)
from payroll.interfaces.api.main import app


class FakeReferenceDataQueries:
    """Test double for Reference Data Queries."""

    async def list_currencies(self) -> list[CurrencyDTO]:
        """List currencies."""
        return [
            CurrencyDTO(
                code="CLP", name="Peso chileno", is_fiat=True, unit_kind="currency"
            )
        ]

    async def list_pension_institutions(self) -> list[PensionInstitutionDTO]:
        """List pension institutions."""
        return [
            PensionInstitutionDTO(
                code="AFP_UNO",
                name="AFP Uno",
                mandatory_rate=Decimal("0.10"),
                is_active=True,
            )
        ]

    async def list_health_institutions(self) -> list[HealthInstitutionDTO]:
        """List health institutions."""
        return [
            HealthInstitutionDTO(
                code="FONASA",
                name="Fonasa",
                kind=HealthInstitutionKind.FONASA,
                mandatory_rate=Decimal("0.07"),
                is_active=True,
            )
        ]

    async def list_pension_plans(self) -> list[PensionPlanDTO]:
        """List pension plans."""
        return [
            PensionPlanDTO(
                id=1,
                institution_code="AFP_UNO",
                institution_name="AFP Uno",
                valid_from=date(2024, 1, 1),
                valid_to=None,
                additional_rate=Decimal("0"),
            )
        ]

    async def list_health_plans(self) -> list[HealthPlanDTO]:
        """List health plans."""
        return [
            HealthPlanDTO(
                id=2,
                institution_code="FONASA",
                institution_name="Fonasa",
                institution_kind=HealthInstitutionKind.FONASA,
                valid_from=date(2024, 1, 1),
                valid_to=None,
                plan_name="Base",
                contracted_uf=Decimal("0"),
            )
        ]

    async def list_contribution_caps(self) -> list[ContributionCapDTO]:
        """List contribution caps."""
        return [
            ContributionCapDTO(
                cap_type="pension_health",
                valid_from=date(2026, 1, 1),
                valid_to=None,
                value_uf=Decimal("90.0600"),
            )
        ]

    async def list_payroll_concepts(self) -> list[PayrollConceptDTO]:
        """List payroll concepts."""
        return [
            PayrollConceptDTO(
                code="SALARY_BASE",
                name="Base Salary",
                kind="income",
                is_taxable=True,
            )
        ]

    async def list_income_tax_brackets(self) -> list[IncomeTaxBracketDTO]:
        """List income tax brackets."""
        return [
            IncomeTaxBracketDTO(
                valid_from=date(2026, 1, 1),
                valid_to=None,
                lower_bound_utm=Decimal("0"),
                upper_bound_utm=Decimal("13.5"),
                marginal_rate=Decimal("0"),
                rebate_utm=Decimal("0"),
            )
        ]


class FakeRefreshIncomeTaxBrackets:
    """Test double for Refresh Income Tax Brackets."""

    async def execute(self, command: object) -> RefreshIncomeTaxBracketsResultDTO:
        """Handle execute."""
        assert getattr(command, "year") == 2026
        return RefreshIncomeTaxBracketsResultDTO(
            year=2026, refreshed_months=6, upserted_brackets=48
        )


def test_reference_data_endpoints() -> None:
    """Test reference data endpoints."""
    app.dependency_overrides[get_reference_data_queries] = lambda: (
        FakeReferenceDataQueries()
    )
    client = TestClient(app)

    try:
        assert client.get("/reference-data/currencies").json() == [
            {
                "code": "CLP",
                "name": "Peso chileno",
                "is_fiat": True,
                "unit_kind": "currency",
            }
        ]
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
        assert client.get("/reference-data/income-tax-brackets").json() == [
            {
                "valid_from": "2026-01-01",
                "valid_to": None,
                "lower_bound_utm": "0",
                "upper_bound_utm": "13.5",
                "marginal_rate": "0",
                "rebate_utm": "0",
            }
        ]
    finally:
        app.dependency_overrides.clear()


def test_reference_data_refresh_income_tax_brackets_endpoint() -> None:
    """Test reference data refresh income tax brackets endpoint."""
    app.dependency_overrides[get_refresh_income_tax_brackets_use_case] = lambda: (
        FakeRefreshIncomeTaxBrackets()
    )
    client = TestClient(app)

    try:
        response = client.post(
            "/reference-data/income-tax-brackets/refresh", json={"year": 2026}
        )

        assert response.status_code == 200
        assert response.json() == {
            "year": 2026,
            "refreshed_months": 6,
            "upserted_brackets": 48,
        }
    finally:
        app.dependency_overrides.clear()


def test_reference_data_refresh_income_tax_brackets_endpoint_returns_bad_request() -> (
    None
):
    """Test reference data refresh income tax brackets endpoint returns bad request."""

    class ErrorRefreshIncomeTaxBrackets:
        """Represent the error refresh income tax brackets."""

        async def execute(self, command: object) -> RefreshIncomeTaxBracketsResultDTO:
            """Handle execute."""
            raise PayrollDependencyError(
                "No official income tax brackets were found for 2026."
            )

    app.dependency_overrides[get_refresh_income_tax_brackets_use_case] = lambda: (
        ErrorRefreshIncomeTaxBrackets()
    )
    client = TestClient(app)

    try:
        response = client.post(
            "/reference-data/income-tax-brackets/refresh", json={"year": 2026}
        )

        assert response.status_code == 502
        assert response.json() == {
            "detail": "No official income tax brackets were found for 2026."
        }
    finally:
        app.dependency_overrides.clear()
