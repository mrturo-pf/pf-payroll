"""Import-oriented payroll repository operations."""

from collections import defaultdict
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from payroll.application.dto import (
    ImportPayrollResultDTO,
    ImportPayrollRowDTO,
    ImportedPayrollPeriodDTO,
)
from payroll.application.errors import PayrollValidationError
from payroll.infrastructure.db.models import (
    EmployerModel,
    PayrollConceptModel,
)
from payroll.infrastructure.db.models.payroll import (
    EmployerFixedDayRoll,
    EmployerPaymentDateRule,
    PayrollItemModel,
    PayrollPeriodHealthPlanModel,
    PayrollPeriodModel,
    PayrollStatus,
)
from payroll.infrastructure.db.repositories.reference_data_repository import (
    SqlAlchemyReferenceDataRepository,
)
from payroll.infrastructure.db.repositories.payroll_repository_shared import (
    SqlAlchemyPayrollRepositoryBase,
    build_net_pay_warning,
)


class SqlAlchemyPayrollImportRepository(SqlAlchemyPayrollRepositoryBase):
    """Persistence operations related to payroll imports."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the instance."""
        super().__init__(session)
        self._reference_data_repository = SqlAlchemyReferenceDataRepository(session)

    async def _deduce_pension_plan_for_date(self, reference_date: date) -> int:
        """Deduce the valid pension plan ID for a given reference date.

        Raises PayrollValidationError if no valid plan is found.
        """
        plan = await self._reference_data_repository.get_valid_pension_plan_for_date(
            reference_date
        )
        if plan is None:
            raise PayrollValidationError(
                f"No valid pension plan found for reference date {reference_date}."
            )
        return plan.id

    async def _deduce_health_plan_ids_for_date(
        self, reference_date: date
    ) -> tuple[int, ...]:
        """Deduce valid health plan IDs for a given reference date.

        Raises PayrollValidationError if no valid plans are found.
        """
        plans = await self._reference_data_repository.get_valid_health_plans_for_date(
            reference_date
        )
        if not plans:
            raise PayrollValidationError(
                f"No valid health plans found for reference date {reference_date}."
            )
        return tuple(plan.id for plan in plans)

    def _resolve_period_plan_id(
        self,
        period_rows: list[ImportPayrollRowDTO],
        *,
        attribute_name: str,
        column_name: str,
    ) -> int | None:
        """Resolve a consistent plan id for a grouped import period."""
        values = {
            getattr(row, attribute_name, None)
            for row in period_rows
            if getattr(row, attribute_name, None) is not None
        }
        if len(values) > 1:
            raise PayrollValidationError(
                f"Inconsistent {column_name} values for the same imported period."
            )
        if not values:
            return None
        return next(iter(values))

    def _resolve_period_health_plan_ids(
        self, period_rows: list[ImportPayrollRowDTO]
    ) -> tuple[int, ...] | None:
        """Resolve consistent health plan ids for an imported period."""
        values: set[tuple[int, ...]] = set()
        for row in period_rows:
            plan_ids = getattr(row, "health_plan_ids", None)
            if plan_ids is None:
                single_plan_id = getattr(row, "health_plan_id", None)
                plan_ids = None if single_plan_id is None else (single_plan_id,)
            if plan_ids is not None:
                values.add(tuple(plan_ids))
        if len(values) > 1:
            raise PayrollValidationError(
                "Inconsistent health_plan_id values for the same imported period."
            )
        if not values:
            return None
        return next(iter(values))

    async def _sync_period_health_plans(
        self, period: PayrollPeriodModel, health_plan_ids: tuple[int, ...]
    ) -> None:
        """Replace period health plan snapshot rows."""
        await self._session.execute(
            delete(PayrollPeriodHealthPlanModel).where(
                PayrollPeriodHealthPlanModel.period_id == period.id
            )
        )
        self._session.add_all(
            [
                PayrollPeriodHealthPlanModel(
                    period_id=period.id,
                    health_plan_id=plan_id,
                )
                for plan_id in health_plan_ids
            ]
        )

    async def import_rows(
        self, rows: list[ImportPayrollRowDTO]
    ) -> ImportPayrollResultDTO:
        """Import rows."""
        if not rows:
            return ImportPayrollResultDTO(
                imported_periods=0, imported_items=0, periods=[]
            )

        concept_result = await self._session.execute(
            select(PayrollConceptModel).where(
                PayrollConceptModel.code.in_({row.concept_code for row in rows})
            )
        )
        concepts = {concept.code: concept for concept in concept_result.scalars().all()}
        missing_codes = sorted({row.concept_code for row in rows} - set(concepts))
        if missing_codes:
            raise PayrollValidationError(
                f"Unknown payroll concepts in import: {', '.join(missing_codes)}"
            )

        grouped_rows: dict[tuple[str, int, int], list[ImportPayrollRowDTO]] = (
            defaultdict(list)
        )
        for row in rows:
            grouped_rows[(row.employer, row.period_year, row.period_month)].append(row)

        imported_periods: list[ImportedPayrollPeriodDTO] = []
        imported_items = 0

        for (employer_name, year, month), period_rows in sorted(grouped_rows.items()):
            first_row = period_rows[0]
            worked_days = getattr(first_row, "worked_days", 30)

            # Try to resolve explicit plan IDs from the rows first
            pension_plan_id = self._resolve_period_plan_id(
                period_rows,
                attribute_name="pension_plan_id",
                column_name="pension_plan_id",
            )
            health_plan_ids = self._resolve_period_health_plan_ids(period_rows)

            # Check if both or neither are provided
            if (pension_plan_id is None) != (health_plan_ids is None):
                raise PayrollValidationError(
                    "Both pension_plan_id and health_plan_id must be provided together."
                )

            # If not explicitly provided, deduce from reference date
            if pension_plan_id is None:
                reference_date = date(year, month, 1)
                pension_plan_id = await self._deduce_pension_plan_for_date(
                    reference_date
                )
                health_plan_ids = await self._deduce_health_plan_ids_for_date(
                    reference_date
                )

            # Validate the plans exist
            await self._get_pension_plan(pension_plan_id, first_row.payment_date)
            if health_plan_ids is not None:
                for plan_id in health_plan_ids:
                    await self._get_health_plan(
                        plan_id,
                        first_row.payment_date,
                        require_active=True,
                    )

            employer_result = await self._session.execute(
                select(EmployerModel).where(EmployerModel.name == employer_name)
            )
            employer = employer_result.scalar_one_or_none()
            if employer is None:
                employer = EmployerModel(
                    name=employer_name,
                    started_at=first_row.payment_date,
                    payment_date_rule=(
                        EmployerPaymentDateRule.LAST_BUSINESS_DAY_OF_MONTH
                    ),
                    payment_month_offset=0,
                    payment_day_of_month=None,
                    payment_business_day_offset=0,
                    payment_calendar_day_offset=0,
                    payment_effective_on_processing_next_day=False,
                    payment_fixed_day_roll=(EmployerFixedDayRoll.PREVIOUS_BUSINESS_DAY),
                )
                self._session.add(employer)
                await self._session.flush()
                await self._close_overlapping_open_ended_employers(employer)
            elif first_row.payment_date < employer.started_at:
                employer.started_at = first_row.payment_date
                await self._close_overlapping_open_ended_employers(employer)

            period_result = await self._session.execute(
                select(PayrollPeriodModel).where(
                    PayrollPeriodModel.employer_id == employer.id,
                    PayrollPeriodModel.period_year == year,
                    PayrollPeriodModel.period_month == month,
                )
            )
            period = period_result.scalar_one_or_none()
            if period is None:
                period = PayrollPeriodModel(
                    employer_id=employer.id,
                    period_year=year,
                    period_month=month,
                    payment_date=first_row.payment_date,
                    worked_days=worked_days,
                    status=PayrollStatus(first_row.status),
                    employment_contract_kind=first_row.employment_contract_kind,
                    declared_net_pay_clp=first_row.declared_net_pay_clp,
                    expected_net_pay_clp=None,
                    net_pay_difference_clp=None,
                    pension_plan_id=pension_plan_id,
                )
                self._session.add(period)
                await self._session.flush()
                if health_plan_ids is not None:
                    await self._sync_period_health_plans(period, health_plan_ids)
            else:
                period.payment_date = first_row.payment_date
                period.worked_days = worked_days
                period.status = PayrollStatus(first_row.status)
                period.employment_contract_kind = first_row.employment_contract_kind
                period.declared_net_pay_clp = first_row.declared_net_pay_clp
                period.expected_net_pay_clp = None
                period.net_pay_difference_clp = None
                if pension_plan_id is not None and health_plan_ids is not None:
                    period.pension_plan_id = pension_plan_id
                    await self._sync_period_health_plans(period, health_plan_ids)
                await self._session.execute(
                    delete(PayrollItemModel).where(
                        PayrollItemModel.period_id == period.id
                    )
                )

            items = [
                PayrollItemModel(
                    period_id=period.id,
                    concept_id=concepts[row.concept_code].id,
                    amount_clp=row.amount_clp,
                )
                for row in period_rows
            ]
            self._session.add_all(items)
            imported_items += len(items)

            imported_periods.append(
                ImportedPayrollPeriodDTO(
                    id=period.id,
                    employer=employer.name,
                    period_year=year,
                    period_month=month,
                    payment_date=period.payment_date,
                    status=period.status.value,
                    employment_contract_kind=period.employment_contract_kind,
                    item_count=len(items),
                    worked_days=period.worked_days,
                    declared_net_pay_clp=period.declared_net_pay_clp,
                    expected_net_pay_clp=period.expected_net_pay_clp,
                    net_pay_difference_clp=period.net_pay_difference_clp,
                    net_pay_warning=build_net_pay_warning(
                        period.declared_net_pay_clp,
                        period.expected_net_pay_clp,
                        period.net_pay_difference_clp,
                    ),
                )
            )

        await self._refresh_summary_view()

        return ImportPayrollResultDTO(
            imported_periods=len(imported_periods),
            imported_items=imported_items,
            periods=imported_periods,
        )
