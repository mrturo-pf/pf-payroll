"""Tests for test reference data queries."""

from datetime import date
from decimal import Decimal

import pytest

from payroll.application.dto import (
    ContributionCapDTO,
    CurrencyDTO,
    HealthInstitutionDTO,
    HealthPlanDTO,
    IncomeTaxBracketDTO,
    PayrollConceptDTO,
    PensionInstitutionDTO,
    PensionPlanDTO,
)
from payroll.application.use_cases.reference_data import ReferenceDataQueries
from payroll.domain.contributions import HealthInstitutionKind


class StubReferenceDataRepository:
    """Test double for Reference Data Repository."""

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
                code="SALARY_BASE", name="Base Salary", kind="income", is_taxable=True
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


@pytest.mark.asyncio
async def test_reference_data_queries_delegate_to_repository() -> None:
    """Test reference data queries delegate to repository."""
    queries = ReferenceDataQueries(StubReferenceDataRepository())

    assert [item.code for item in await queries.list_currencies()] == ["CLP"]
    assert [item.code for item in await queries.list_pension_institutions()] == [
        "AFP_UNO"
    ]
    assert [item.code for item in await queries.list_health_institutions()] == [
        "FONASA"
    ]
    assert [item.id for item in await queries.list_pension_plans()] == [1]
    assert [item.id for item in await queries.list_health_plans()] == [2]
    assert [item.cap_type for item in await queries.list_contribution_caps()] == [
        "pension_health"
    ]
    assert [item.code for item in await queries.list_payroll_concepts()] == [
        "SALARY_BASE"
    ]
    assert [
        item.lower_bound_utm for item in await queries.list_income_tax_brackets()
    ] == [Decimal("0")]
