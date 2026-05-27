"""Service for computing complementary insurance costs."""

from decimal import Decimal

from payroll.application.dto import (
    ComplementaryInsuranceCostDTO,
    ComputeComplementaryInsuranceResultDTO,
)
from payroll.application.ports.repositories import (
    ComplementaryInsuranceRepository,
    PayrollRepository,
)
from payroll.domain.complementary_insurance import (
    calculate_complementary_insurance_cost,
)


class ComplementaryInsuranceCostComputationService:
    """Computes costs for complementary insurance plans assigned to a payroll period."""

    def __init__(
        self,
        payroll_repository: PayrollRepository,
        complementary_insurance_repository: ComplementaryInsuranceRepository,
    ) -> None:
        """Initialize the instance."""
        self._payroll_repository = payroll_repository
        self._complementary_insurance_repository = complementary_insurance_repository

    async def compute(self, period_id: int) -> ComputeComplementaryInsuranceResultDTO:
        """Compute total complementary insurance costs for a period.

        Retrieves assigned plans for the period, calculates cost for each plan
        based on plan configuration and salary, and returns aggregated result.
        """
        period_detail = await self._payroll_repository.get_period_detail(period_id)
        if period_detail is None or period_detail.summary is None:
            return ComputeComplementaryInsuranceResultDTO(
                period_id=period_id,
                costs=[],
                total_cost_clp=Decimal("0"),
            )

        # Get assigned plans
        plans = await self._complementary_insurance_repository.get_period_plans(
            period_id
        )
        if not plans:
            return ComputeComplementaryInsuranceResultDTO(
                period_id=period_id,
                costs=[],
                total_cost_clp=Decimal("0"),
            )

        # Calculate cost for each plan
        costs: list[ComplementaryInsuranceCostDTO] = []
        total_cost = Decimal("0")

        salary_base = period_detail.summary.taxable_income_clp
        for plan in plans:
            plan_cost = calculate_complementary_insurance_cost(plan, salary_base)
            costs.append(
                ComplementaryInsuranceCostDTO(
                    plan_id=plan.id,
                    plan_name=plan.name,
                    cost_clp=plan_cost,
                )
            )
            total_cost += plan_cost

        return ComputeComplementaryInsuranceResultDTO(
            period_id=period_id,
            costs=costs,
            total_cost_clp=total_cost,
        )
