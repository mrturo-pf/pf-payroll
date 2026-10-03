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
from payroll.application.errors import PayrollConflictError, PayrollValidationError
from payroll.application.ports.repositories import MarketDataRepository
from payroll.infrastructure.db.models import (
    EmployerModel,
    PayrollConceptModel,
)
from payroll.infrastructure.db.models.payroll import (
    PayrollItemModel,
    PayrollPeriodHealthPlanModel,
    PayrollPeriodModel,
)
from payroll.infrastructure.db.repositories.reference_data_repository import (
    SqlAlchemyReferenceDataRepository,
)
from payroll.infrastructure.db.repositories.payroll_repository_shared import (
    SqlAlchemyPayrollRepositoryBase,
    build_net_pay_warning,
)
from payroll.shared.dates import add_months


class SqlAlchemyPayrollImportRepository(SqlAlchemyPayrollRepositoryBase):
    """Persistence operations related to payroll imports."""

    def __init__(
        self,
        session: AsyncSession,
        market_data_repository: MarketDataRepository | None = None,
    ) -> None:
        """Initialize the instance.

        Overrides the shared base's __init__ only to also build the nested
        SqlAlchemyReferenceDataRepository -- forwards market_data_repository
        unchanged (see SqlAlchemyPayrollRepositoryBase.__init__'s docstring
        for why it's optional/defaulted). This mixin is first in
        SqlAlchemyPayrollRepository's MRO, so without this explicit forward
        the facade would silently drop the parameter for every caller.
        """
        super().__init__(session, market_data_repository)
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

    async def _deduce_health_plan_ids_for_month(
        self, period_year: int, period_month: int
    ) -> tuple[int, ...]:
        """Deduce every active health plan ID overlapping the given month.

        Raises PayrollValidationError if none overlap at all. Deliberately
        broader than a single reference-day check
        (get_health_plans_overlapping_month()'s docstring) so a plan that
        only takes effect mid-month still gets assigned to the period --
        ContributionCalculator.health() prorates it day by day instead of
        applying it for the full month or not at all (see
        domain/health_plan_proration.py).
        """
        plans = (
            await self._reference_data_repository.get_health_plans_overlapping_month(
                period_year, period_month
            )
        )
        if not plans:
            raise PayrollValidationError(
                "No valid health plans found for period "
                f"{period_year}-{period_month:02d}."
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

    def _validate_payment_month_matches_period(
        self,
        *,
        employer: EmployerModel,
        period_year: int,
        period_month: int,
        payment_date: date,
    ) -> None:
        """Reject an import whose payment_date does not match the period.

        The convention is that period_year/period_month represents the worked
        month, shifted by the employer's payment_month_offset (0 means "period
        == payment month", the default for every employer created today). A
        mismatch here is exactly the old, now-abandoned convention where a
        payment landing at month-end was filed under the following month.
        """
        offset = employer.payment_month_offset or 0
        expected_month = add_months(date(period_year, period_month, 1), offset)
        if (payment_date.year, payment_date.month) != (
            expected_month.year,
            expected_month.month,
        ):
            raise PayrollValidationError(
                f"payment_date {payment_date.isoformat()} does not match period "
                f"{period_year}-{period_month:02d} for employer '{employer.name}' "
                f"(payment_month_offset={offset}). Expected a payment_date in "
                f"{expected_month.year}-{expected_month.month:02d}."
            )

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
        periods_pending_reconciliation: list[tuple[PayrollPeriodModel, str, int]] = []

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
            plans_were_deduced = pension_plan_id is None
            if pension_plan_id is None:
                reference_date = date(year, month, 1)
                pension_plan_id = await self._deduce_pension_plan_for_date(
                    reference_date
                )
                health_plan_ids = await self._deduce_health_plan_ids_for_month(
                    year, month
                )

            # Validate the plans exist
            await self._get_pension_plan(pension_plan_id, first_row.payment_date)
            if health_plan_ids is not None:
                for plan_id in health_plan_ids:
                    if plans_were_deduced:
                        # get_health_plans_overlapping_month() already proved
                        # this plan overlaps the period's month and belongs to
                        # an active institution -- re-checking it against the
                        # single payment_date here would wrongly reject a
                        # plan that only covers *part* of the month (exactly
                        # the case day-level proration exists to support; see
                        # domain/health_plan_proration.py). Only explicitly
                        # caller-provided plan IDs (the else branch, not
                        # deduced) still get the strict single-day check.
                        continue
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
                raise PayrollConflictError(
                    "An employment contract must exist before importing payroll "
                    f"for employer {employer_name!r}."
                )

            self._validate_payment_month_matches_period(
                employer=employer,
                period_year=year,
                period_month=month,
                payment_date=first_row.payment_date,
            )

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

            periods_pending_reconciliation.append((period, employer.name, len(items)))

        await self._refresh_summary_view()

        for period, employer_display_name, item_count in periods_pending_reconciliation:
            await self._reconcile_period_net_pay(period, refresh_summary_view=False)
            imported_periods.append(
                ImportedPayrollPeriodDTO(
                    id=period.id,
                    employer=employer_display_name,
                    period_year=period.period_year,
                    period_month=period.period_month,
                    payment_date=period.payment_date,
                    item_count=item_count,
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

        return ImportPayrollResultDTO(
            imported_periods=len(imported_periods),
            imported_items=imported_items,
            periods=imported_periods,
        )
