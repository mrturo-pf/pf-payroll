"""Import-oriented payroll repository operations."""

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from payroll.application.dto import (
    ImportPayrollResultDTO,
    ImportPayrollRowDTO,
    ImportedPayrollPeriodDTO,
    MarketDataSyncRequestDTO,
)
from payroll.application.errors import PayrollValidationError
from payroll.infrastructure.db.models import (
    EconomicIndexModel,
    EmployerModel,
    ExchangeRateModel,
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
from payroll.shared.dates import last_day_of_month
from payroll.shared.constants import (
    DAILY_MARKET_RATE_CODES,
    MONTHLY_ECONOMIC_INDEX_CODES,
    MONTHLY_MARKET_RATE_CODES,
)


def first_day_of_month(value: date) -> date:
    """Return the first day of the date month."""
    return date(value.year, value.month, 1)


def build_daily_date_range(start_date: date, end_date: date) -> list[date]:
    """Build an inclusive day-by-day range."""
    return [
        start_date + timedelta(days=offset)
        for offset in range((end_date - start_date).days + 1)
    ]


def build_month_date_range(start_date: date, end_date: date) -> list[date]:
    """Build an inclusive month-by-month range using month starts."""
    month_date = first_day_of_month(start_date)
    end_month = first_day_of_month(end_date)
    month_dates: list[date] = []
    while month_date <= end_month:
        month_dates.append(month_date)
        if month_date.month == 12:
            month_date = date(month_date.year + 1, 1, 1)
        else:
            month_date = date(month_date.year, month_date.month + 1, 1)
    return month_dates


def build_month_period_range(
    start_year: int,
    start_month: int,
    end_year: int,
    end_month: int,
) -> list[tuple[int, int]]:
    """Build an inclusive month period range."""
    period_year = start_year
    period_month = start_month
    periods: list[tuple[int, int]] = []
    while (period_year, period_month) <= (end_year, end_month):
        periods.append((period_year, period_month))
        if period_month == 12:
            period_year += 1
            period_month = 1
        else:
            period_month += 1
    return periods


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
        persisted_periods: list[PayrollPeriodModel] = []
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
            health_plan_id = (
                None if health_plan_ids is None else int(health_plan_ids[0])
            )

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
                health_plan_id = int(health_plan_ids[0]) if health_plan_ids else None

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
                    health_plan_id=health_plan_id,
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
                    period.health_plan_id = health_plan_id
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
            persisted_periods.append(period)

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
        market_data_sync_request = await self._build_market_data_sync_request(
            persisted_periods
        )

        return ImportPayrollResultDTO(
            imported_periods=len(imported_periods),
            imported_items=imported_items,
            periods=imported_periods,
            market_data_sync_request=market_data_sync_request,
        )

    async def _build_market_data_sync_request(
        self, periods: list[PayrollPeriodModel]
    ) -> MarketDataSyncRequestDTO | None:
        """Build a deduplicated market-data sync request for imported periods."""
        if not periods:
            return None

        exchange_rate_dates: dict[str, set[date]] = {}
        economic_index_periods: dict[str, set[tuple[int, int]]] = {}

        for period in {item.id: item for item in periods}.values():
            previous_period = await self._get_previous_period_for_employer(
                employer_id=period.employer_id,
                period_id=period.id,
                payment_date=period.payment_date,
            )
            await self._collect_missing_exchange_rate_dates(
                period,
                previous_period,
                exchange_rate_dates,
            )
            await self._collect_missing_index_periods(
                period,
                previous_period,
                economic_index_periods,
            )

        if not exchange_rate_dates and not economic_index_periods:
            return None

        return MarketDataSyncRequestDTO(
            exchange_rate_dates={
                currency_code: sorted(rate_dates)
                for currency_code, rate_dates in exchange_rate_dates.items()
                if rate_dates
            },
            economic_index_periods={
                code: sorted(periods)
                for code, periods in economic_index_periods.items()
                if periods
            },
        )

    async def _collect_missing_exchange_rate_dates(
        self,
        period: PayrollPeriodModel,
        previous_period: PayrollPeriodModel | None,
        missing_dates_by_code: dict[str, set[date]],
    ) -> None:
        """Collect missing exchange-rate dates for a payroll period."""
        start_payment_date = (
            previous_period.payment_date
            if previous_period is not None
            else period.payment_date
        )
        requested_daily_dates = build_daily_date_range(
            start_payment_date, period.payment_date
        )
        for currency_code in DAILY_MARKET_RATE_CODES:
            requested_dates = requested_daily_dates
            if currency_code == "UF":
                requested_dates = sorted(
                    set(requested_daily_dates)
                    | {last_day_of_month(period.payment_date)}
                )
            missing_dates = await self._list_missing_exchange_rate_dates(
                currency_code=currency_code,
                requested_dates=requested_dates,
            )
            if missing_dates:
                missing_dates_by_code.setdefault(currency_code, set()).update(
                    missing_dates
                )

        start_month_date = (
            first_day_of_month(previous_period.payment_date)
            if previous_period is not None
            else first_day_of_month(period.payment_date)
        )
        requested_month_dates = build_month_date_range(
            start_month_date, first_day_of_month(period.payment_date)
        )
        for currency_code in MONTHLY_MARKET_RATE_CODES:
            missing_dates = await self._list_missing_exchange_rate_dates(
                currency_code=currency_code,
                requested_dates=requested_month_dates,
            )
            if missing_dates:
                missing_dates_by_code.setdefault(currency_code, set()).update(
                    missing_dates
                )

    async def _collect_missing_index_periods(
        self,
        period: PayrollPeriodModel,
        previous_period: PayrollPeriodModel | None,
        missing_periods_by_code: dict[str, set[tuple[int, int]]],
    ) -> None:
        """Collect missing economic-index periods for a payroll period."""
        start_year = (
            previous_period.period_year
            if previous_period is not None
            else period.period_year
        )
        start_month = (
            previous_period.period_month
            if previous_period is not None
            else period.period_month
        )
        requested_periods = build_month_period_range(
            start_year,
            start_month,
            period.period_year,
            period.period_month,
        )
        for code in MONTHLY_ECONOMIC_INDEX_CODES:
            missing_periods = await self._list_missing_index_periods(
                code=code,
                requested_periods=requested_periods,
            )
            if missing_periods:
                missing_periods_by_code.setdefault(code, set()).update(missing_periods)

    async def _get_previous_period_for_employer(
        self,
        *,
        employer_id: int,
        period_id: int,
        payment_date: date,
    ) -> PayrollPeriodModel | None:
        """Return the closest previous payroll period for the employer."""
        result = await self._session.execute(
            select(PayrollPeriodModel)
            .where(PayrollPeriodModel.employer_id == employer_id)
            .where(PayrollPeriodModel.id != period_id)
            .where(PayrollPeriodModel.payment_date < payment_date)
            .order_by(PayrollPeriodModel.payment_date.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _list_missing_exchange_rate_dates(
        self,
        *,
        currency_code: str,
        requested_dates: list[date],
    ) -> list[date]:
        """List missing exchange-rate dates for the requested range."""
        if not requested_dates:
            return []
        result = await self._session.execute(
            select(ExchangeRateModel.rate_date).where(
                ExchangeRateModel.currency_code == currency_code,
                ExchangeRateModel.rate_date >= requested_dates[0],
                ExchangeRateModel.rate_date <= requested_dates[-1],
            )
        )
        existing_dates = set(result.scalars().all())
        return [
            requested_date
            for requested_date in requested_dates
            if requested_date not in existing_dates
        ]

    async def _list_missing_index_periods(
        self,
        *,
        code: str,
        requested_periods: list[tuple[int, int]],
    ) -> list[tuple[int, int]]:
        """List missing economic-index periods for the requested range."""
        if not requested_periods:
            return []
        start_year, start_month = requested_periods[0]
        end_year, end_month = requested_periods[-1]
        start_key = start_year * 100 + start_month
        end_key = end_year * 100 + end_month
        period_key = (
            EconomicIndexModel.period_year * 100 + EconomicIndexModel.period_month
        )
        result = await self._session.execute(
            select(
                EconomicIndexModel.period_year,
                EconomicIndexModel.period_month,
            ).where(
                EconomicIndexModel.code == code,
                period_key >= start_key,
                period_key <= end_key,
            )
        )
        existing_periods = {tuple(row) for row in result.all()}
        return [
            requested_period
            for requested_period in requested_periods
            if requested_period not in existing_periods
        ]
