"""Use case for computing unemployment insurance."""

from payroll.application.dto import (
    ComputeUnemploymentInsuranceCommandDTO,
    ComputeUnemploymentInsuranceResultDTO,
)
from payroll.application.services.contribution_computation import (
    _WithContributionCalculator,
)
from payroll.application.services.exchange_rates import (
    resolve_month_end_uf_exchange_rate,
)


class ComputeUnemploymentInsurance(_WithContributionCalculator):
    """Computes and persists unemployment insurance for a payroll period."""

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
