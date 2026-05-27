"""Shared stub mixin for reference-data test doubles."""

from datetime import date
from decimal import Decimal

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
from payroll.domain.contributions import HealthInstitutionKind


class ReferenceDataStubMixin:
    """Mixin that implements all eight reference-data query methods with canned data.

    Inherit from this class in test doubles to avoid duplicating the stub
    implementations across integration and unit test modules.
    """

    def __init__(self) -> None:
        """Initialize the instance."""
        self.include_inactive_health_institutions = False
        self.include_inactive_health_plans = False

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

    async def list_health_institutions(
        self, *, include_inactive: bool = False
    ) -> list[HealthInstitutionDTO]:
        """List health institutions."""
        self.include_inactive_health_institutions = include_inactive
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

    async def list_health_plans(
        self, *, include_inactive: bool = False
    ) -> list[HealthPlanDTO]:
        """List health plans."""
        self.include_inactive_health_plans = include_inactive
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
