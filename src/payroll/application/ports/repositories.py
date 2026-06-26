"""Port definitions for repositories."""

from datetime import date
from decimal import Decimal
from typing import Protocol

from payroll.application.dto import (
    AssignPlansCommandDTO,
    AssignPlansResultDTO,
    ComputeContributionsCommandDTO,
    ComputeContributionsResultDTO,
    ComputeIncomeTaxResultDTO,
    ComputeIncomeTaxCommandDTO,
    ComputeUnemploymentInsuranceCommandDTO,
    ComputeUnemploymentInsuranceResultDTO,
    ContributionComputationContextDTO,
    ContributionCapDTO,
    HealthInstitutionDTO,
    HealthPlanDTO,
    IncomeTaxContextDTO,
    ImportPayrollResultDTO,
    ImportPayrollRowDTO,
    PayrollPeriodDetailDTO,
    PayrollPeriodRangeDTO,
    PayrollConceptDTO,
    PayrollSummaryDTO,
    PensionInstitutionDTO,
    PensionPlanDTO,
    ReviewPayrollPeriodCommandDTO,
    ReviewPayrollPeriodResultDTO,
    UnemploymentComputationContextDTO,
)
from payroll.domain.contributions import ComplementaryInsurancePlan


class ReferenceDataRepository(Protocol):
    """Access to reference catalogs and official synchronization flows."""

    async def list_pension_institutions(self) -> list[PensionInstitutionDTO]:
        """List pension institutions."""
        ...

    async def list_health_institutions(
        self, *, include_inactive: bool = False
    ) -> list[HealthInstitutionDTO]:
        """List health institutions."""
        ...

    async def list_pension_plans(self) -> list[PensionPlanDTO]:
        """List pension plans."""
        ...

    async def list_health_plans(
        self, *, include_inactive: bool = False
    ) -> list[HealthPlanDTO]:
        """List health plans."""
        ...

    async def list_contribution_caps(self) -> list[ContributionCapDTO]:
        """List contribution caps."""
        ...

    async def list_payroll_concepts(self) -> list[PayrollConceptDTO]:
        """List payroll concepts."""
        ...


class PayrollRepository(Protocol):
    """Persistence port for payroll operations."""

    async def import_rows(
        self, rows: list[ImportPayrollRowDTO]
    ) -> ImportPayrollResultDTO:
        """Import rows."""
        ...

    async def assign_plans(
        self, command: AssignPlansCommandDTO
    ) -> AssignPlansResultDTO:
        """Assign plans."""
        ...

    async def review_period(
        self, command: ReviewPayrollPeriodCommandDTO
    ) -> ReviewPayrollPeriodResultDTO:
        """Review period."""
        ...

    async def get_contribution_context(
        self,
        command: ComputeContributionsCommandDTO,
    ) -> ContributionComputationContextDTO:
        """Get contribution context."""
        ...

    async def get_unemployment_context(
        self,
        command: ComputeUnemploymentInsuranceCommandDTO,
    ) -> UnemploymentComputationContextDTO:
        """Get unemployment computation context."""
        ...

    async def save_computed_contributions(
        self,
        result: ComputeContributionsResultDTO,
    ) -> ComputeContributionsResultDTO:
        """Save computed contributions."""
        ...

    async def save_computed_unemployment(
        self,
        result: ComputeUnemploymentInsuranceResultDTO,
    ) -> ComputeUnemploymentInsuranceResultDTO:
        """Save computed unemployment insurance."""
        ...

    async def get_period_detail(self, period_id: int) -> PayrollPeriodDetailDTO | None:
        """Get period detail."""
        ...

    async def list_period_summaries(self) -> list[PayrollSummaryDTO]:
        """List period summaries."""
        ...

    async def list_period_ranges(
        self, *, today: date | None = None
    ) -> list[PayrollPeriodRangeDTO]:
        """List payroll period date ranges around the current period."""
        ...

    async def get_income_tax_context(
        self, command: ComputeIncomeTaxCommandDTO
    ) -> IncomeTaxContextDTO:
        """Get income tax context."""
        ...

    async def save_computed_income_tax(
        self, result: ComputeIncomeTaxResultDTO
    ) -> ComputeIncomeTaxResultDTO:
        """Save computed income tax."""
        ...


class ComplementaryInsuranceRepository(Protocol):
    """Persistence port for complementary insurance operations."""

    async def get_vigent_plans(
        self, reference_date: date
    ) -> list[ComplementaryInsurancePlan]:
        """Get complementary insurance plans vigent on the given date."""
        ...

    async def assign_plans_to_period(self, period_id: int, plan_ids: list[int]) -> None:
        """Assign complementary insurance plans to a payroll period."""
        ...

    async def get_period_plans(
        self, period_id: int
    ) -> list[ComplementaryInsurancePlan]:
        """Get complementary insurance plans assigned to a payroll period."""
        ...


class MarketDataRepository(Protocol):
    """Read port for financial market data served by pf-rates."""

    async def get_exchange_rate_value(
        self, currency_code: str, rate_date: date
    ) -> Decimal | None:
        """Get exchange rate value."""
        ...

    async def get_economic_index_value(
        self, code: str, period_year: int, period_month: int
    ) -> Decimal | None:
        """Get economic index value."""
        ...
