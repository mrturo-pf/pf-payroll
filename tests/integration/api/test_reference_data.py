from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from payroll.application.dto import (
    ContributionCapDTO,
    CurrencyDTO,
    HealthInstitutionDTO,
    HealthPlanDTO,
    PayrollConceptDTO,
    PensionInstitutionDTO,
    PensionPlanDTO,
)
from payroll.domain.contributions import HealthInstitutionKind
from payroll.interfaces.api.dependencies import get_reference_data_queries
from payroll.interfaces.api.main import app


class FakeReferenceDataQueries:
    async def list_currencies(self) -> list[CurrencyDTO]:
        return [CurrencyDTO(code="CLP", name="Peso chileno", is_fiat=True, unit_kind="currency")]

    async def list_pension_institutions(self) -> list[PensionInstitutionDTO]:
        return [
            PensionInstitutionDTO(
                code="AFP_UNO",
                name="AFP Uno",
                mandatory_rate=Decimal("0.10"),
                is_active=True,
            )
        ]

    async def list_health_institutions(self) -> list[HealthInstitutionDTO]:
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
        return [
            ContributionCapDTO(
                cap_type="pension_health",
                valid_from=date(2026, 1, 1),
                valid_to=None,
                value_uf=Decimal("90.0600"),
            )
        ]

    async def list_payroll_concepts(self) -> list[PayrollConceptDTO]:
        return [
            PayrollConceptDTO(
                code="SALARY_BASE",
                name="Base Salary",
                kind="income",
                is_taxable=True,
            )
        ]


def test_reference_data_endpoints() -> None:
    app.dependency_overrides[get_reference_data_queries] = lambda: FakeReferenceDataQueries()
    client = TestClient(app)

    try:
        assert client.get("/reference-data/currencies").json() == [
            {"code": "CLP", "name": "Peso chileno", "is_fiat": True, "unit_kind": "currency"}
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
    finally:
        app.dependency_overrides.clear()
