"""Use case for computing unemployment insurance."""

from payroll.application.dto import (
    ComputeUnemploymentInsuranceCommandDTO,
    ComputeUnemploymentInsuranceResultDTO,
)
from payroll.application.ports.repositories import (
    MarketDataRepository,
    PayrollRepository,
)
from payroll.application.services.exchange_rates import (
    resolve_month_end_uf_exchange_rate,
)
from payroll.domain.contribution_calculator import ContributionCalculator


class ComputeUnemploymentInsurance:
    """Computes and persists unemployment insurance for a payroll period."""

    def __init__(
        self,
        repository: PayrollRepository,
        market_data_repository: MarketDataRepository,
        calculator: ContributionCalculator | None = None,
    ) -> None:
        """Initialize the instance."""
        self._repository = repository
        self._market_data_repository = market_data_repository
        self._calculator = calculator or ContributionCalculator()

    async def execute(
        self, command: ComputeUnemploymentInsuranceCommandDTO
    ) -> ComputeUnemploymentInsuranceResultDTO:
        """Handle execute."""
        context = await self._repository.get_unemployment_context(command)
        uf_value_clp = await resolve_month_end_uf_exchange_rate(
            provided_value=command.uf_value_clp,
            payment_date=context.payment_date,
            market_data_repository=self._market_data_repository,
        )
        unemployment = self._calculator.unemployment(
            context.taxable_income_clp,
            context.employment_contract_kind,
            context.unemployment_cap,
            uf_value_clp,
        )
        return await self._repository.save_computed_unemployment(
            ComputeUnemploymentInsuranceResultDTO(
                period_id=context.period_id,
                unemployment=unemployment,
            )
        )
