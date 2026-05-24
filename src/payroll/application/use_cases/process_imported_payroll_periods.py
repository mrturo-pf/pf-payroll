"""Use case for post-processing imported payroll periods."""

from payroll.application.dto import (
    ComputeIncomeTaxCommandDTO,
    ComputeUnemploymentInsuranceCommandDTO,
    ImportPayrollResultDTO,
    ImportedPayrollPeriodDTO,
)
from payroll.application.ports.repositories import (
    MarketDataRepository,
    PayrollRepository,
)
from payroll.application.use_cases.compute_income_tax import ComputeIncomeTax
from payroll.application.use_cases.compute_unemployment_insurance import (
    ComputeUnemploymentInsurance,
)

_REQUIRED_IMPORTED_CONTRIBUTION_CODES = frozenset(
    {
        "PENSION_BASE",
        "PENSION_ADDITIONAL",
        "HEALTH_BASE",
        "HEALTH_ADDITIONAL_UF",
    }
)


class ProcessImportedPayrollPeriods:
    """Post-process imported payroll periods when safe to do so."""

    def __init__(
        self,
        repository: PayrollRepository,
        market_data_repository: MarketDataRepository,
    ) -> None:
        """Initialize the instance."""
        self._repository = repository
        self._compute_unemployment = ComputeUnemploymentInsurance(
            repository, market_data_repository
        )
        self._compute_income_tax = ComputeIncomeTax(repository, market_data_repository)

    async def execute(self, result: ImportPayrollResultDTO) -> ImportPayrollResultDTO:
        """Auto-compute derived payroll items for eligible imported periods."""
        for period in result.periods:
            await self._process_period(period.id)

        refreshed_periods = [
            await self._refresh_imported_period(period) for period in result.periods
        ]
        return ImportPayrollResultDTO(
            imported_periods=result.imported_periods,
            imported_items=result.imported_items,
            periods=refreshed_periods,
            market_data_sync_request=result.market_data_sync_request,
        )

    async def _process_period(self, period_id: int) -> None:
        """Process a single imported period when eligible."""
        detail = await self._repository.get_period_detail(period_id)
        summary = None if detail is None else detail.summary
        if detail is None or summary is None or summary.declared_net_pay_clp is None:
            return

        item_codes = {item.concept_code for item in detail.items}
        if not _REQUIRED_IMPORTED_CONTRIBUTION_CODES.issubset(item_codes):
            return

        unemployment_was_computed = False
        if "UNEMPLOYMENT_INSURANCE" not in item_codes:
            await self._compute_unemployment.execute(
                ComputeUnemploymentInsuranceCommandDTO(period_id=period_id)
            )
            unemployment_was_computed = True

        if unemployment_was_computed or "INCOME_TAX" not in item_codes:
            await self._compute_income_tax.execute(
                ComputeIncomeTaxCommandDTO(period_id=period_id)
            )

    async def _refresh_imported_period(
        self, period: ImportedPayrollPeriodDTO
    ) -> ImportedPayrollPeriodDTO:
        """Refresh an imported period from persisted state."""
        detail = await self._repository.get_period_detail(period.id)
        if detail is None:
            return period
        summary = detail.summary
        if summary is None:
            return period

        return ImportedPayrollPeriodDTO(
            id=detail.id,
            employer=detail.employer_name,
            period_year=detail.period_year,
            period_month=detail.period_month,
            payment_date=detail.payment_date,
            status=detail.status,
            employment_contract_kind=detail.employment_contract_kind,
            item_count=len(detail.items),
            declared_net_pay_clp=summary.declared_net_pay_clp,
            expected_net_pay_clp=summary.expected_net_pay_clp,
            net_pay_difference_clp=summary.net_pay_difference_clp,
            net_pay_warning=summary.net_pay_warning,
        )
