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
    ContributionComputationContextDTO,
    ContributionCapDTO,
    CurrencyDTO,
    EconomicIndexDTO,
    ExchangeRateDTO,
    HealthInstitutionDTO,
    HealthPlanDTO,
    IncomeTaxBracketDTO,
    IncomeTaxBracketWriteDTO,
    IncomeTaxContextDTO,
    ImportPayrollResultDTO,
    ImportPayrollRowDTO,
    PayrollPeriodDetailDTO,
    PayrollConceptDTO,
    PayrollSummaryDTO,
    PensionInstitutionDTO,
    PensionPlanDTO,
    RefreshRatesCommandDTO,
    RefreshRatesResultDTO,
    ReviewPayrollPeriodCommandDTO,
    ReviewPayrollPeriodResultDTO,
)
from payroll.domain.taxes import IncomeTaxBracket


class ReferenceDataRepository(Protocol):
    """Access to reference catalogs and official synchronization flows."""

    async def list_currencies(self) -> list[CurrencyDTO]: ...

    async def list_pension_institutions(self) -> list[PensionInstitutionDTO]: ...

    async def list_health_institutions(self) -> list[HealthInstitutionDTO]: ...

    async def list_pension_plans(self) -> list[PensionPlanDTO]: ...

    async def list_health_plans(self) -> list[HealthPlanDTO]: ...

    async def list_contribution_caps(self) -> list[ContributionCapDTO]: ...

    async def list_payroll_concepts(self) -> list[PayrollConceptDTO]: ...

    async def list_income_tax_brackets(self) -> list[IncomeTaxBracketDTO]: ...

    async def upsert_income_tax_brackets(self, brackets: list[IncomeTaxBracketWriteDTO]) -> int: ...


class PayrollRepository(Protocol):
    """Persistence port for payroll operations."""

    async def import_rows(self, rows: list[ImportPayrollRowDTO]) -> ImportPayrollResultDTO: ...

    async def assign_plans(self, command: AssignPlansCommandDTO) -> AssignPlansResultDTO: ...

    async def review_period(self, command: ReviewPayrollPeriodCommandDTO) -> ReviewPayrollPeriodResultDTO: ...

    async def get_contribution_context(
        self,
        command: ComputeContributionsCommandDTO,
    ) -> ContributionComputationContextDTO: ...

    async def save_computed_contributions(
        self,
        result: ComputeContributionsResultDTO,
    ) -> ComputeContributionsResultDTO: ...

    async def get_period_detail(self, period_id: int) -> PayrollPeriodDetailDTO | None: ...

    async def list_period_summaries(self) -> list[PayrollSummaryDTO]: ...

    async def get_income_tax_context(self, command: ComputeIncomeTaxCommandDTO) -> IncomeTaxContextDTO: ...

    async def get_income_tax_bracket(self, payment_date: date, taxable_base_utm: Decimal) -> IncomeTaxBracket | None: ...

    async def save_computed_income_tax(self, result: ComputeIncomeTaxResultDTO) -> ComputeIncomeTaxResultDTO: ...


class MarketDataRepository(Protocol):
    """Persistence port for historical rates and indices."""

    async def list_exchange_rates(self, currency_code: str | None = None) -> list[ExchangeRateDTO]: ...

    async def list_economic_indices(self, code: str | None = None) -> list[EconomicIndexDTO]: ...

    async def get_exchange_rate_value(self, currency_code: str, rate_date: date) -> Decimal | None: ...

    async def refresh_rates(self, command: RefreshRatesCommandDTO) -> RefreshRatesResultDTO: ...

    async def get_economic_index_value(self, code: str, period_year: int, period_month: int) -> Decimal | None: ...
