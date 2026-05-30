"""Service for computing complementary insurance costs."""

from decimal import Decimal

from payroll.application.dto import (
    ComplementaryInsuranceCostDTO,
    ComplementaryInsuranceValidationAuditDTO,
    ComputeComplementaryInsuranceResultDTO,
)
from payroll.application.errors import EconomicIndexNotFoundError
from payroll.application.ports.repositories import (
    ComplementaryInsuranceRepository,
    MarketDataRepository,
    PayrollRepository,
)
from payroll.domain.complementary_insurance import (
    calculate_complementary_insurance_cost,
)
from payroll.domain.contributions import ComplementaryInsuranceCostType


class ComplementaryInsuranceCostComputationService:
    """Computes costs for complementary insurance plans assigned to a payroll period.

    Calculates total complementary insurance costs based on assigned plans,
    including generating audit trails for cost computation traceability.
    """

    def __init__(
        self,
        payroll_repository: PayrollRepository,
        complementary_insurance_repository: ComplementaryInsuranceRepository,
        market_data_repository: MarketDataRepository,
    ) -> None:
        """Initialize the instance."""
        self._payroll_repository = payroll_repository
        self._complementary_insurance_repository = complementary_insurance_repository
        self._market_data_repository = market_data_repository

    async def compute(self, period_id: int) -> ComputeComplementaryInsuranceResultDTO:
        """Compute total complementary insurance costs for a period.

        Retrieves assigned plans for the period, calculates cost for each plan
        based on plan configuration and salary, and returns aggregated result
        with audit trail.

        Raises:
            EconomicIndexNotFoundError: If any plan requires a UF rate that is
                not available in the market data repository.
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

        # Fetch UF rate once if any plan requires it
        uf_rate_clp: Decimal | None = None
        if any(p.cost_type == ComplementaryInsuranceCostType.FIXED_UF for p in plans):
            uf_rate_clp = await self._market_data_repository.get_exchange_rate_value(
                "UF",
                period_detail.payment_date,
            )
            if uf_rate_clp is None:
                raise EconomicIndexNotFoundError(
                    f"UF rate not found for period "
                    f"{period_detail.period_year}-{period_detail.period_month:02d}. "
                    "Please load UF data before importing payroll."
                )

        # Calculate cost for each plan
        costs: list[ComplementaryInsuranceCostDTO] = []
        total_cost = Decimal("0")

        salary_base = period_detail.summary.taxable_income_clp
        for plan in plans:
            plan_cost = calculate_complementary_insurance_cost(
                plan, salary_base, uf_rate_clp
            )
            costs.append(
                ComplementaryInsuranceCostDTO(
                    plan_id=plan.id,
                    plan_name=plan.name,
                    cost_clp=plan_cost,
                )
            )
            total_cost += plan_cost

        # Build audit trail for traceability
        audit = ComplementaryInsuranceValidationAuditDTO(
            period_id=period_id,
            gross_income_clp=period_detail.summary.gross_income_clp,
            taxable_income_clp=salary_base,
            total_legal_deductions_clp=(
                period_detail.summary.gross_income_clp - salary_base
            ),
            declared_employer_contribution_clp=None,
            calculated_total_cost_clp=total_cost,
            individual_plan_costs=costs,
        )

        return ComputeComplementaryInsuranceResultDTO(
            period_id=period_id,
            costs=costs,
            total_cost_clp=total_cost,
            audit=audit,
        )
