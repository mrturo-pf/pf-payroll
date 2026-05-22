"""Use case for computing contributions."""

from payroll.application.dto import ComputeContributionsCommandDTO, ComputeContributionsResultDTO
from payroll.application.ports.repositories import PayrollRepository
from payroll.domain.contribution_calculator import ContributionCalculator


class ComputeContributions:
    """Computes pension and health contributions for an imported payroll period."""

    def __init__(self, repository: PayrollRepository, calculator: ContributionCalculator | None = None) -> None:
        self._repository = repository
        self._calculator = calculator or ContributionCalculator()

    async def execute(self, command: ComputeContributionsCommandDTO) -> ComputeContributionsResultDTO:
        context = await self._repository.get_contribution_context(command)

        pension = self._calculator.pension(
            context.taxable_income_clp,
            context.pension_plan,
            context.cap,
            command.uf_value_clp,
        )
        health = self._calculator.health(
            context.taxable_income_clp,
            context.health_plan,
            context.cap,
            command.uf_value_clp,
        )
        result = ComputeContributionsResultDTO(
            period_id=context.period_id,
            pension_plan_id=context.pension_plan.id,
            health_plan_id=context.health_plan.id,
            taxable_income_clp=context.taxable_income_clp,
            pension=pension,
            health=health,
            total_discount_clp=(
                pension.base_amount_clp
                + pension.additional_amount_clp
                + health.base_amount_clp
                + health.additional_amount_clp
            ),
        )
        return await self._repository.save_computed_contributions(result)
