"""Shared helpers for SQLAlchemy payroll repositories."""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from payroll.application.errors import (
    HealthPlanNotFoundError,
    PayrollConflictError,
    PayrollNotFoundError,
    PayrollPeriodNotFoundError,
    PensionPlanNotFoundError,
)
from payroll.application.dto import PayrollSummaryDTO
from payroll.infrastructure.db.models import (
    ContributionCapModel,
    EmployerModel,
    HealthInstitutionModel,
    HealthPlanModel,
    PensionInstitutionModel,
    PensionPlanModel,
    PayrollSummaryModel,
)
from payroll.infrastructure.db.models.payroll import PayrollPeriodModel
from payroll.infrastructure.db.models.reference_data import ContributionCapType


def build_net_pay_warning(net_pay_difference_clp: Decimal | None) -> str | None:
    """Build net pay warning."""
    if net_pay_difference_clp is None or net_pay_difference_clp == 0:
        return None
    return (
        "Declared net_pay does not match the imported concept totals. "
        f"Difference: {net_pay_difference_clp} CLP."
    )


def build_payroll_summary_dto(
    summary: PayrollSummaryModel,
    *,
    employer_name: str,
    period: PayrollPeriodModel,
) -> PayrollSummaryDTO:
    """Build payroll summary dto."""
    return PayrollSummaryDTO(
        period_id=summary.period_id,
        employer_id=summary.employer_id,
        employer_name=employer_name,
        period_year=summary.period_year,
        period_month=summary.period_month,
        payment_date=summary.payment_date,
        taxable_income_clp=summary.taxable_income_clp,
        gross_income_clp=summary.gross_income_clp,
        total_discounts_clp=summary.total_discounts_clp,
        net_pay_clp=summary.net_pay_clp,
        declared_net_pay_clp=period.declared_net_pay_clp,
        expected_net_pay_clp=period.expected_net_pay_clp,
        net_pay_difference_clp=period.net_pay_difference_clp,
        net_pay_warning=build_net_pay_warning(period.net_pay_difference_clp),
    )


class SqlAlchemyPayrollRepositoryBase:
    """Common helpers shared across payroll repository concerns."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the instance."""
        self._session = session

    async def _refresh_summary_view(self) -> None:
        """Handle refresh summary view."""
        await self._session.commit()
        await self._session.execute(
            text("REFRESH MATERIALIZED VIEW mv_payroll_summary")
        )
        await self._session.commit()

    async def _get_latest_contribution_cap(
        self,
        *,
        cap_type: ContributionCapType,
        payment_date: date,
        missing_message: str,
    ) -> ContributionCapModel:
        """Handle get latest contribution cap."""
        result = await self._session.execute(
            select(ContributionCapModel)
            .where(ContributionCapModel.cap_type == cap_type)
            .where(ContributionCapModel.valid_from <= payment_date)
            .where(
                or_(
                    ContributionCapModel.valid_to.is_(None),
                    ContributionCapModel.valid_to >= payment_date,
                )
            )
            .order_by(ContributionCapModel.valid_from.desc())
        )
        cap_model = result.scalar_one_or_none()
        if cap_model is None:
            raise PayrollNotFoundError(missing_message)
        return cap_model

    async def _get_period(self, period_id: int) -> PayrollPeriodModel:
        """Handle get period."""
        period_result = await self._session.execute(
            select(PayrollPeriodModel).where(PayrollPeriodModel.id == period_id)
        )
        period = period_result.scalar_one_or_none()
        if period is None:
            raise PayrollPeriodNotFoundError(
                f"Payroll period {period_id} was not found."
            )
        return period

    async def _get_effective_employer_ended_at(
        self, employer: EmployerModel
    ) -> date | None:
        """Resolve the effective employer end date."""
        if employer.ended_at is not None:
            return employer.ended_at

        result = await self._session.execute(
            select(EmployerModel.started_at)
            .where(EmployerModel.id != employer.id)
            .where(EmployerModel.started_at > employer.started_at)
            .order_by(EmployerModel.started_at.asc())
            .limit(1)
        )
        next_started_at = result.scalar_one_or_none()
        if next_started_at is None:
            return None
        return next_started_at - timedelta(days=1)

    async def _close_overlapping_open_ended_employers(
        self, employer: EmployerModel
    ) -> None:
        """Close previous open-ended employers that overlap the new employer."""
        result = await self._session.execute(
            select(EmployerModel)
            .where(EmployerModel.id != employer.id)
            .where(EmployerModel.started_at < employer.started_at)
            .where(EmployerModel.ended_at.is_(None))
        )
        inferred_end_date = employer.started_at - timedelta(days=1)
        for overlapping_employer in result.scalars().all():
            overlapping_employer.ended_at = inferred_end_date

    async def _get_pension_plan(
        self,
        plan_id: int,
        payment_date: date,
    ) -> tuple[PensionPlanModel, PensionInstitutionModel]:
        """Handle get pension plan."""
        pension_result = await self._session.execute(
            select(PensionPlanModel, PensionInstitutionModel)
            .join(
                PensionInstitutionModel,
                PensionPlanModel.institution_id == PensionInstitutionModel.id,
            )
            .where(PensionPlanModel.id == plan_id)
        )
        pension_row = pension_result.first()
        if pension_row is None:
            raise PensionPlanNotFoundError(f"Pension plan {plan_id} was not found.")

        pension_plan_model, pension_institution_model = pension_row
        if pension_plan_model.valid_from > payment_date or (
            pension_plan_model.valid_to is not None
            and pension_plan_model.valid_to < payment_date
        ):
            raise PayrollConflictError(
                f"Pension plan {plan_id} is not valid for {payment_date.isoformat()}."
            )

        return pension_plan_model, pension_institution_model

    async def _get_health_plan(
        self,
        plan_id: int,
        payment_date: date,
        *,
        require_active: bool = False,
    ) -> tuple[HealthPlanModel, HealthInstitutionModel]:
        """Handle get health plan."""
        health_result = await self._session.execute(
            select(HealthPlanModel, HealthInstitutionModel)
            .join(
                HealthInstitutionModel,
                HealthPlanModel.institution_id == HealthInstitutionModel.id,
            )
            .where(HealthPlanModel.id == plan_id)
        )
        health_row = health_result.first()
        if health_row is None:
            raise HealthPlanNotFoundError(f"Health plan {plan_id} was not found.")

        health_plan_model, health_institution_model = health_row
        if require_active and not health_institution_model.is_active:
            raise PayrollConflictError(
                f"Health plan {plan_id} belongs to inactive health institution "
                f"{health_institution_model.code}."
            )
        if health_plan_model.valid_from > payment_date or (
            health_plan_model.valid_to is not None
            and health_plan_model.valid_to < payment_date
        ):
            raise PayrollConflictError(
                f"Health plan {plan_id} is not valid for {payment_date.isoformat()}."
            )

        return health_plan_model, health_institution_model
