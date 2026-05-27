"""SQLAlchemy repository for complementary insurance data."""

from datetime import date

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from payroll.domain.complementary_insurance import (
    ComplementaryInsurancePlan,
)
from payroll.infrastructure.db.models.reference_data import (
    ComplementaryInsurancePlanModel,
    ComplementaryInsuranceProviderModel,
)
from payroll.infrastructure.db.models.payroll import (
    PayrollComplementaryInsuranceModel,
)


def _map_plan_model_to_domain(
    model: ComplementaryInsurancePlanModel,
) -> ComplementaryInsurancePlan:
    """Map a complementary insurance plan model to domain entity."""
    return ComplementaryInsurancePlan(
        id=model.id,
        provider_id=model.provider_id,
        name=model.name,
        cost_type=model.cost_type,
        cost_value=model.cost_value,
        cost_currency=model.cost_currency,
        valid_from=model.valid_from,
        valid_to=model.valid_to,
    )


class SqlAlchemyComplementaryInsuranceRepository:
    """Provide SQLAlchemy complementary insurance repository."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the instance."""
        self._session = session

    async def get_vigent_plans(
        self, reference_date: date
    ) -> list[ComplementaryInsurancePlan]:
        """Get complementary insurance plans vigent on the reference date.

        A plan is vigent if valid_from <= reference_date and
        (valid_to IS NULL or valid_to >= reference_date).

        Args:
            reference_date: The date to check plan validity.

        Returns:
            List of vigent complementary insurance plans.
        """
        result = await self._session.execute(
            select(ComplementaryInsurancePlanModel)
            .join(ComplementaryInsuranceProviderModel)
            .where(
                and_(
                    ComplementaryInsurancePlanModel.valid_from <= reference_date,
                    (ComplementaryInsurancePlanModel.valid_to.is_(None))
                    | (ComplementaryInsurancePlanModel.valid_to >= reference_date),
                )
            )
            .order_by(ComplementaryInsurancePlanModel.id)
        )
        return [_map_plan_model_to_domain(row) for row in result.scalars().all()]

    async def get_plan_by_id(self, plan_id: int) -> ComplementaryInsurancePlan | None:
        """Get a complementary insurance plan by ID.

        Args:
            plan_id: The plan ID.

        Returns:
            The complementary insurance plan or None if not found.
        """
        result = await self._session.execute(
            select(ComplementaryInsurancePlanModel).where(
                ComplementaryInsurancePlanModel.id == plan_id
            )
        )
        row = result.scalar_one_or_none()
        return _map_plan_model_to_domain(row) if row else None

    async def assign_plans_to_period(self, period_id: int, plan_ids: list[int]) -> None:
        """Assign complementary insurance plans to a payroll period.

        Args:
            period_id: The payroll period ID.
            plan_ids: List of complementary insurance plan IDs to assign.
        """
        if not plan_ids:
            return

        for plan_id in plan_ids:
            existing = await self._session.execute(
                select(PayrollComplementaryInsuranceModel).where(
                    and_(
                        PayrollComplementaryInsuranceModel.period_id == period_id,
                        PayrollComplementaryInsuranceModel.complementary_insurance_plan_id
                        == plan_id,
                    )
                )
            )
            if existing.scalar_one_or_none() is None:
                self._session.add(
                    PayrollComplementaryInsuranceModel(
                        period_id=period_id,
                        complementary_insurance_plan_id=plan_id,
                    )
                )

    async def get_period_plans(
        self, period_id: int
    ) -> list[ComplementaryInsurancePlan]:
        """Get all complementary insurance plans assigned to a payroll period.

        Args:
            period_id: The payroll period ID.

        Returns:
            List of complementary insurance plans assigned to the period.
        """
        result = await self._session.execute(
            select(ComplementaryInsurancePlanModel)
            .join(
                PayrollComplementaryInsuranceModel,
                ComplementaryInsurancePlanModel.id
                == PayrollComplementaryInsuranceModel.complementary_insurance_plan_id,
            )
            .where(PayrollComplementaryInsuranceModel.period_id == period_id)
            .order_by(ComplementaryInsurancePlanModel.id)
        )
        return [_map_plan_model_to_domain(row) for row in result.scalars().all()]
