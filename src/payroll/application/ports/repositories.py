"""Port definitions for repositories."""

from datetime import date
from decimal import Decimal
from typing import Protocol

from payroll.application.dto import (
    ContractMaintenanceResultDTO,
    EmployerMaintenanceDTO,
    EmployerMaintenanceResultDTO,
    EmployerReadDTO,
    EmploymentContractMaintenanceDTO,
    EmploymentContractReadDTO,
    ComputeContributionsCommandDTO,
    ComputeContributionsResultDTO,
    ComputeIncomeTaxCommandDTO,
    ComputeIncomeTaxResultDTO,
    ComputeUnemploymentInsuranceCommandDTO,
    ComputeUnemploymentInsuranceResultDTO,
    ContributionCapDTO,
    ContributionComputationContextDTO,
    EmployerPaymentRuleDTO,
    EmploymentContractDTO,
    ExportPayrollFiltersDTO,
    HealthInstitutionDTO,
    HealthPlanDTO,
    ImportPayrollResultDTO,
    ImportPayrollRowDTO,
    IncomeTaxContextDTO,
    PayrollConceptDTO,
    PayrollPeriodDetailDTO,
    PayrollPeriodRangeContextDTO,
    PayrollPeriodRangeDTO,
    PayrollSummaryDTO,
    PensionInstitutionDTO,
    PensionPlanDTO,
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


class EmployerPaymentRuleReader(Protocol):
    """Narrow read-only port for resolving an employer's payment-date rule.

    Deliberately its own Protocol rather than a method tacked onto
    ReferenceDataRepository -- PreviewPdfImport (the only current consumer)
    only ever needs this one lookup, not the whole reference-catalogs
    surface, so depending on the narrower port keeps it honest about what it
    actually uses (interface segregation). SqlAlchemyReferenceDataRepository
    still implements this method too -- one concrete class can satisfy
    multiple Protocols, and reusing its existing session avoids standing up
    a whole separate repository class for a single query.
    """

    async def get_employer_payment_rule(
        self, employer_name: str
    ) -> EmployerPaymentRuleDTO | None:
        """Get an employer's configured payment-date rule by exact name.

        Returns None when no employer with that exact name is registered
        yet -- the caller (PreviewPdfImport) treats that the same as "no
        override available" and keeps its own generic default.
        """
        ...


class PayrollRepository(Protocol):
    """Persistence port for payroll operations."""

    async def maintain_employers(
        self, employers: list[EmployerMaintenanceDTO]
    ) -> EmployerMaintenanceResultDTO:
        """Create/update employers atomically."""
        ...

    async def maintain_contracts(
        self, contracts: list[EmploymentContractMaintenanceDTO]
    ) -> ContractMaintenanceResultDTO:
        """Create/update employment contracts atomically."""
        ...

    async def list_employers(self) -> list[EmployerReadDTO]:
        """List employers."""
        ...

    async def list_contracts(
        self, employer_id: int | None = None
    ) -> list[EmploymentContractReadDTO]:
        """List employment contracts."""
        ...

    async def delete_employers(self, employer_ids: list[int]) -> None:
        """Delete employers when no dependent data exists."""
        ...

    async def delete_contracts(self, contract_ids: list[int]) -> None:
        """Delete contracts when no payroll blocks the deletion."""
        ...

    async def get_effective_employment_contract(
        self, employer_id: int, payment_date: date
    ) -> EmploymentContractDTO:
        """Get the contract effective for an employer/payment date."""
        ...

    async def import_rows(
        self, rows: list[ImportPayrollRowDTO]
    ) -> ImportPayrollResultDTO:
        """Import rows."""
        ...

    async def delete_periods(self, period_ids: list[int]) -> None:
        """Delete payroll periods atomically, including owned data."""
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

    async def list_period_details(
        self, filters: ExportPayrollFiltersDTO
    ) -> list[PayrollPeriodDetailDTO]:
        """List full period detail (items included) for periods matching the filters.

        Backs the bulk spreadsheet export -- unlike list_period_summaries(),
        every returned entry carries its full items list, same shape as
        get_period_detail() for one period.
        """
        ...

    async def list_period_ranges(
        self,
        *,
        today: date | None = None,
        previous_months: int | None = None,
        future_months: int | None = None,
    ) -> list[PayrollPeriodRangeDTO]:
        """List payroll period date ranges around the current period."""
        ...

    async def get_period_range(
        self, period_id: int
    ) -> PayrollPeriodRangeContextDTO | None:
        """Get a single real period in the unified period-range shape.

        Unlike list_period_ranges(), not bounded by any window around
        "today" -- works for any period_id regardless of age. Returns None
        when no period with that id exists.
        """
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

    async def get_exchange_rate_values(
        self, pairs: list[tuple[str, date]]
    ) -> dict[tuple[str, date], Decimal | None]:
        """Get multiple exchange-rate values in one dependency call."""
        ...

    async def get_economic_index_value(
        self, code: str, period_year: int, period_month: int
    ) -> Decimal | None:
        """Get economic index value."""
        ...

    async def get_economic_index_values(
        self, pairs: list[tuple[str, int, int]]
    ) -> dict[tuple[str, int, int], Decimal | None]:
        """Get multiple economic-index values in one dependency call."""
        ...

    async def get_latest_economic_index(self, code: str) -> tuple[date, Decimal] | None:
        """Get the most recently published (period, value) for an index code."""
        ...
