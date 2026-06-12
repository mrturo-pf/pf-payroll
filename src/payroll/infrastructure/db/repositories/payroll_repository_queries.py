"""Query-oriented payroll repository operations."""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from payroll.application.dto import (
    PayrollItemDetailDTO,
    PayrollPeriodDetailDTO,
    PayrollPeriodRangeDTO,
    PayrollSummaryDTO,
)
from payroll.infrastructure.db.models import (
    EmployerModel,
    HealthInstitutionModel,
    HealthPlanModel,
    PayrollConceptModel,
    PayrollSummaryModel,
)
from payroll.infrastructure.db.models.payroll import (
    EmployerFixedDayRoll,
    EmployerPaymentDateRule,
    PayrollItemModel,
    PayrollPeriodHealthPlanModel,
    PayrollPeriodModel,
)
from payroll.infrastructure.db.repositories.payroll_repository_shared import (
    SqlAlchemyPayrollRepositoryBase,
    build_payroll_summary_dto,
    predict_next_period_net_pay,
)
from payroll.shared.dates import add_months, resolve_payment_date


class SqlAlchemyPayrollQueryRepository(SqlAlchemyPayrollRepositoryBase):
    """Read-only payroll queries."""

    def _resolve_effective_month_offset(
        self,
        *,
        period_year: int,
        period_month: int,
        payment_date: date,
        country_code: str,
        payment_date_rule: str,
        payment_month_offset: int,
        payment_day_of_month: int | None,
        payment_business_day_offset: int,
        payment_calendar_day_offset: int,
        payment_effective_on_processing_next_day: bool,
        payment_fixed_day_roll: str,
    ) -> int:
        """Resolve the offset that best matches the observed payment date."""
        configured_start = resolve_payment_date(
            period_year,
            period_month,
            country_code=country_code,
            payment_date_rule=payment_date_rule,
            payment_month_offset=payment_month_offset,
            payment_day_of_month=payment_day_of_month,
            payment_business_day_offset=payment_business_day_offset,
            payment_calendar_day_offset=payment_calendar_day_offset,
            payment_effective_on_processing_next_day=(
                payment_effective_on_processing_next_day
            ),
            payment_fixed_day_roll=payment_fixed_day_roll,
        )
        if configured_start == payment_date:
            return payment_month_offset
        for candidate_offset in range(-12, 13):
            if candidate_offset == payment_month_offset:
                continue
            candidate_start = resolve_payment_date(
                period_year,
                period_month,
                country_code=country_code,
                payment_date_rule=payment_date_rule,
                payment_month_offset=candidate_offset,
                payment_day_of_month=payment_day_of_month,
                payment_business_day_offset=payment_business_day_offset,
                payment_calendar_day_offset=payment_calendar_day_offset,
                payment_effective_on_processing_next_day=(
                    payment_effective_on_processing_next_day
                ),
                payment_fixed_day_roll=payment_fixed_day_roll,
            )
            if candidate_start == payment_date:
                return candidate_offset
        return payment_month_offset

    async def list_period_ranges(
        self, *, today: date | None = None
    ) -> list[PayrollPeriodRangeDTO]:
        """List the current period plus 12 previous and 12 next date ranges."""
        reference_date = today or date.today()
        current_result = await self._session.execute(
            select(PayrollPeriodModel, EmployerModel)
            .join(EmployerModel, PayrollPeriodModel.employer_id == EmployerModel.id)
            .where(PayrollPeriodModel.declared_net_pay_clp.is_not(None))
            .where(PayrollPeriodModel.payment_date <= reference_date)
            .order_by(
                PayrollPeriodModel.payment_date.desc(),
                PayrollPeriodModel.id.desc(),
            )
            .limit(1)
        )
        current_row = current_result.first()
        if current_row is None:
            current_year = reference_date.year
            current_month = reference_date.month
            current_start = resolve_payment_date(current_year, current_month)
            current_net_pay_clp = None
            current_country_code = "CL"
            current_rule = EmployerPaymentDateRule.LAST_BUSINESS_DAY_OF_MONTH.value
            current_month_offset = 0
            current_day_of_month = None
            current_business_day_offset = 0
            current_calendar_day_offset = 0
            current_effective_on_processing_next_day = False
            current_fixed_day_roll = EmployerFixedDayRoll.PREVIOUS_BUSINESS_DAY.value
            current_inferred = True
        else:
            current_period, current_employer = current_row
            current_year = current_period.period_year
            current_month = current_period.period_month
            current_start = current_period.payment_date
            current_country_code = current_employer.country_code
            current_rule = current_employer.payment_date_rule.value
            current_month_offset = current_employer.payment_month_offset
            current_day_of_month = current_employer.payment_day_of_month
            current_business_day_offset = current_employer.payment_business_day_offset
            current_calendar_day_offset = current_employer.payment_calendar_day_offset
            current_net_pay_clp = current_period.declared_net_pay_clp
            current_effective_on_processing_next_day = (
                current_employer.payment_effective_on_processing_next_day
            )
            current_fixed_day_roll = current_employer.payment_fixed_day_roll.value
            current_month_offset = self._resolve_effective_month_offset(
                period_year=current_year,
                period_month=current_month,
                payment_date=current_start,
                country_code=current_country_code,
                payment_date_rule=current_rule,
                payment_month_offset=current_month_offset,
                payment_day_of_month=current_day_of_month,
                payment_business_day_offset=current_business_day_offset,
                payment_calendar_day_offset=current_calendar_day_offset,
                payment_effective_on_processing_next_day=(
                    current_effective_on_processing_next_day
                ),
                payment_fixed_day_roll=current_fixed_day_roll,
            )
            current_inferred = False

        previous_result = await self._session.execute(
            select(PayrollPeriodModel)
            .where(PayrollPeriodModel.payment_date < current_start)
            .order_by(
                PayrollPeriodModel.payment_date.desc(),
                PayrollPeriodModel.id.desc(),
            )
            .limit(12)
        )
        previous_periods = list(previous_result.scalars().all())
        previous_ranges = [
            PayrollPeriodRangeDTO(
                period_year=period.period_year,
                period_month=period.period_month,
                start_date=period.payment_date,
                end_date=period.payment_date,
                net_pay_clp=period.declared_net_pay_clp,
                is_current=False,
                inferred=False,
            )
            for period in reversed(previous_periods)
        ]

        if len(previous_ranges) < 12:
            if previous_ranges:
                seed_month = add_months(
                    date(
                        previous_ranges[0].period_year,
                        previous_ranges[0].period_month,
                        1,
                    ),
                    -1,
                )
            else:
                seed_month = add_months(date(current_year, current_month, 1), -1)
            inferred_previous: list[PayrollPeriodRangeDTO] = []
            for extra_offset in range(12 - len(previous_ranges)):
                inferred_month = add_months(
                    seed_month, -(12 - len(previous_ranges) - 1) + extra_offset
                )
                inferred_previous.append(
                    PayrollPeriodRangeDTO(
                        period_year=inferred_month.year,
                        period_month=inferred_month.month,
                        start_date=resolve_payment_date(
                            inferred_month.year,
                            inferred_month.month,
                        ),
                        end_date=resolve_payment_date(
                            inferred_month.year,
                            inferred_month.month,
                        ),
                        net_pay_clp=None,
                        is_current=False,
                        inferred=True,
                    )
                )
            previous_ranges = inferred_previous + previous_ranges

        current_range = PayrollPeriodRangeDTO(
            period_year=current_year,
            period_month=current_month,
            start_date=current_start,
            end_date=current_start,
            net_pay_clp=current_net_pay_clp,
            is_current=True,
            inferred=current_inferred,
        )

        # Calculate predicted net_pay for the first future period
        first_future_net_pay_clp: Decimal | None = None
        if current_row is not None and not current_inferred:
            first_future_net_pay_clp = await predict_next_period_net_pay(
                self._session,
                current_period,
                date(current_year, current_month, 1),
            )

        future_ranges = [
            PayrollPeriodRangeDTO(
                period_year=period_month.year,
                period_month=period_month.month,
                start_date=resolve_payment_date(
                    period_month.year,
                    period_month.month,
                    country_code=current_country_code,
                    payment_date_rule=current_rule,
                    payment_month_offset=current_month_offset,
                    payment_day_of_month=current_day_of_month,
                    payment_business_day_offset=current_business_day_offset,
                    payment_calendar_day_offset=current_calendar_day_offset,
                    payment_effective_on_processing_next_day=(
                        current_effective_on_processing_next_day
                    ),
                    payment_fixed_day_roll=current_fixed_day_roll,
                ),
                end_date=date(period_month.year, period_month.month, 1),
                net_pay_clp=(first_future_net_pay_clp if month_offset == 1 else None),
                is_current=False,
                inferred=True,
            )
            for month_offset, period_month in (
                (
                    month_offset,
                    add_months(date(current_year, current_month, 1), month_offset),
                )
                for month_offset in range(1, 13)
            )
        ]
        trailing_start = resolve_payment_date(
            add_months(date(current_year, current_month, 1), 13).year,
            add_months(date(current_year, current_month, 1), 13).month,
            country_code=current_country_code,
            payment_date_rule=current_rule,
            payment_month_offset=current_month_offset,
            payment_day_of_month=current_day_of_month,
            payment_business_day_offset=current_business_day_offset,
            payment_calendar_day_offset=current_calendar_day_offset,
            payment_effective_on_processing_next_day=(
                current_effective_on_processing_next_day
            ),
            payment_fixed_day_roll=current_fixed_day_roll,
        )
        all_ranges = previous_ranges + [current_range] + future_ranges
        completed_ranges: list[PayrollPeriodRangeDTO] = []
        for index, period_range in enumerate(all_ranges):
            next_start = (
                all_ranges[index + 1].start_date
                if index + 1 < len(all_ranges)
                else trailing_start
            )
            completed_ranges.append(
                PayrollPeriodRangeDTO(
                    period_year=period_range.period_year,
                    period_month=period_range.period_month,
                    start_date=period_range.start_date,
                    end_date=next_start - timedelta(days=1),
                    net_pay_clp=period_range.net_pay_clp,
                    is_current=period_range.is_current,
                    inferred=period_range.inferred,
                )
            )
        return completed_ranges

    async def get_period_detail(self, period_id: int) -> PayrollPeriodDetailDTO | None:
        """Get period detail."""
        period_result = await self._session.execute(
            select(PayrollPeriodModel, EmployerModel)
            .join(EmployerModel, PayrollPeriodModel.employer_id == EmployerModel.id)
            .where(PayrollPeriodModel.id == period_id)
        )
        period_row = period_result.first()
        if period_row is None:
            return None
        period, employer = period_row
        employer_ended_at = await self._get_effective_employer_ended_at(employer)
        health_institution_is_active = None
        if period.health_plan_id is not None:
            health_institution_result = await self._session.execute(
                select(HealthInstitutionModel.is_active)
                .join(
                    HealthPlanModel,
                    HealthPlanModel.institution_id == HealthInstitutionModel.id,
                )
                .where(HealthPlanModel.id == period.health_plan_id)
            )
            health_institution_is_active = (
                health_institution_result.scalar_one_or_none()
            )

        items_result = await self._session.execute(
            select(PayrollItemModel, PayrollConceptModel)
            .join(
                PayrollConceptModel,
                PayrollItemModel.concept_id == PayrollConceptModel.id,
            )
            .where(PayrollItemModel.period_id == period.id)
            .order_by(PayrollConceptModel.kind, PayrollConceptModel.code)
        )
        items = [
            PayrollItemDetailDTO(
                concept_code=concept.code,
                concept_name=concept.name,
                kind=concept.kind.value,
                is_taxable=concept.is_taxable,
                amount_clp=item.amount_clp,
                notes=item.notes,
            )
            for item, concept in items_result.all()
        ]

        health_plan_ids_result = await self._session.execute(
            select(PayrollPeriodHealthPlanModel.health_plan_id)
            .where(PayrollPeriodHealthPlanModel.period_id == period.id)
            .order_by(PayrollPeriodHealthPlanModel.health_plan_id.asc())
        )
        health_plan_ids = tuple(
            int(plan_id) for plan_id in health_plan_ids_result.scalars().all()
        )

        summary_result = await self._session.execute(
            select(PayrollSummaryModel, EmployerModel)
            .join(EmployerModel, PayrollSummaryModel.employer_id == EmployerModel.id)
            .where(PayrollSummaryModel.period_id == period.id)
        )
        summary_row = summary_result.first()
        summary = None
        if summary_row is not None:
            summary_model, summary_employer = summary_row
            summary = build_payroll_summary_dto(
                summary_model,
                employer_name=summary_employer.name,
                period=period,
            )

        return PayrollPeriodDetailDTO(
            id=period.id,
            employer_id=employer.id,
            employer_name=employer.name,
            employer_tax_id=employer.tax_id,
            employer_country_code=employer.country_code,
            employer_started_at=employer.started_at,
            employer_ended_at=employer_ended_at,
            period_year=period.period_year,
            period_month=period.period_month,
            payment_date=period.payment_date,
            worked_days=period.worked_days,
            status=period.status.value,
            employment_contract_kind=period.employment_contract_kind,
            pension_plan_id=period.pension_plan_id,
            health_plan_id=period.health_plan_id,
            items=items,
            summary=summary,
            health_plan_ids=health_plan_ids or None,
            health_institution_is_active=health_institution_is_active,
        )

    async def list_period_summaries(self) -> list[PayrollSummaryDTO]:
        """List period summaries."""
        result = await self._session.execute(
            select(PayrollSummaryModel, EmployerModel, PayrollPeriodModel)
            .join(EmployerModel, PayrollSummaryModel.employer_id == EmployerModel.id)
            .join(
                PayrollPeriodModel,
                PayrollSummaryModel.period_id == PayrollPeriodModel.id,
            )
            .order_by(
                PayrollSummaryModel.period_year.desc(),
                PayrollSummaryModel.period_month.desc(),
                EmployerModel.name,
            )
        )
        return [
            build_payroll_summary_dto(
                summary,
                employer_name=employer.name,
                period=period,
            )
            for summary, employer, period in result.all()
        ]
