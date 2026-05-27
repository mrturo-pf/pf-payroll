"""Service for assigning complementary insurance plans to payroll periods."""

from payroll.application.ports.repositories import (
    ComplementaryInsuranceRepository,
    PayrollRepository,
)


class ComplementaryInsuranceService:
    """Assigns vigent complementary insurance plans to payroll periods post-import."""

    def __init__(
        self,
        payroll_repository: PayrollRepository,
        complementary_insurance_repository: ComplementaryInsuranceRepository,
    ) -> None:
        """Initialize the instance."""
        self._payroll_repository = payroll_repository
        self._complementary_insurance_repository = complementary_insurance_repository

    async def assign_plans_for_period(self, period_id: int) -> None:
        """Assign vigent complementary insurance plans to a payroll period.

        Retrieves the payroll period, obtains vigent plans for the payment date,
        and stores the plan-period relationships in the database.
        """
        period_detail = await self._payroll_repository.get_period_detail(period_id)
        if period_detail is None or period_detail.summary is None:
            return

        payment_date = period_detail.summary.payment_date
        vigent_plans = await self._complementary_insurance_repository.get_vigent_plans(
            payment_date
        )

        if vigent_plans:
            plan_ids = [plan.id for plan in vigent_plans]
            await self._complementary_insurance_repository.assign_plans_to_period(
                period_id, plan_ids
            )
