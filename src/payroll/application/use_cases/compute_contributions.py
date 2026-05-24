"""Use case for computing contributions."""

from payroll.application.dto import (
    ComputeContributionsCommandDTO,
    ComputeContributionsResultDTO,
)
from payroll.application.ports.repositories import (
    MarketDataRepository,
    PayrollRepository,
)
from payroll.application.services.exchange_rates import resolve_required_exchange_rate
from payroll.domain.contribution_calculator import ContributionCalculator
from payroll.shared.dates import last_day_of_month


class ComputeContributions:
    """Computes pension and health contributions for an imported payroll period."""

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
        self, command: ComputeContributionsCommandDTO
    ) -> ComputeContributionsResultDTO:
        """Handle execute."""
        context = await self._repository.get_contribution_context(command)
        month_end_rate_date = last_day_of_month(context.payment_date)
        month_end_uf_value_clp = await resolve_required_exchange_rate(
            provided_value=command.uf_value_clp,
            currency_code="UF",
            rate_date=month_end_rate_date,
            market_data_repository=self._market_data_repository,
        )

        pension = self._calculator.pension(
            context.taxable_income_clp,
            context.pension_plan,
            context.cap,
            month_end_uf_value_clp,
        )
        health = self._calculator.health(
            context.taxable_income_clp,
            context.health_plan,
            context.cap,
            month_end_uf_value_clp,
            month_end_uf_value_clp,
        )
        unemployment = self._calculator.unemployment(
            context.taxable_income_clp,
            context.employment_contract_kind,
            context.unemployment_cap,
            month_end_uf_value_clp,
        )
        result = ComputeContributionsResultDTO(
            period_id=context.period_id,
            pension_plan_id=context.pension_plan.id,
            health_plan_id=context.health_plan.id,
            taxable_income_clp=context.taxable_income_clp,
            pension=pension,
            health=health,
            unemployment=unemployment,
            total_discount_clp=(
                pension.base_amount_clp
                + pension.additional_amount_clp
                + health.base_amount_clp
                + health.additional_amount_clp
                + unemployment.employee_amount_clp
            ),
        )
        return await self._repository.save_computed_contributions(result)
