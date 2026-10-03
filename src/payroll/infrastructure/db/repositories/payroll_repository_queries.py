"""Query-oriented payroll repository operations."""

import asyncio
from datetime import date, timedelta
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.engine import Row

from payroll.application.dto import (
    EmploymentContractDTO,
    ExportPayrollFiltersDTO,
    PayrollItemDetailDTO,
    PayrollPeriodDetailDTO,
    PayrollPeriodRangeContextDTO,
    PayrollPeriodRangeDTO,
    PayrollSummaryDTO,
)
from payroll.application.errors import PayrollConflictError, PayrollDependencyError
from payroll.infrastructure.db.models import (
    EmployerModel,
    HealthInstitutionModel,
    HealthPlanModel,
    PayrollConceptModel,
    PayrollSummaryModel,
)
from payroll.infrastructure.db.models.payroll import (
    EmploymentContractModel,
    EmployerFixedDayRoll,
    EmployerPaymentDateRule,
    PayrollItemModel,
    PayrollPeriodHealthPlanModel,
    PayrollPeriodModel,
)
from payroll.infrastructure.db.repositories.payroll_repository_shared import (
    PredictedNetPayBaseline,
    ProjectedFutureMonth,
    SqlAlchemyPayrollRepositoryBase,
    build_payroll_summary_dto,
    degraded_future_months,
    predict_next_period_net_pay,
    project_future_months,
    resolve_currency_equivalents,
)
from payroll.shared.constants import (
    FOREIGN_CURRENCY_CODES,
    HEALTH_ADDITIONAL_CONCEPT_CODE,
)
from payroll.shared.dates import add_months, resolve_payment_date


class _SummaryAmounts(NamedTuple):
    """PAY_MV_SUMARY's three derived CLP totals for a single period."""

    gross_income_clp: Decimal
    taxable_income_clp: Decimal
    total_discounts_clp: Decimal


class SqlAlchemyPayrollQueryRepository(SqlAlchemyPayrollRepositoryBase):
    """Read-only payroll queries."""

    async def _get_employer_contract_start(
        self, employer_id: int, fallback: date
    ) -> date:
        """Return the earliest contract start for an employer."""
        result = await self._session.execute(
            select(func.min(EmploymentContractModel.started_at)).where(
                EmploymentContractModel.employer_id == employer_id
            )
        )
        return result.scalar_one_or_none() or fallback

    async def get_effective_employment_contract(
        self, employer_id: int, payment_date: date
    ) -> EmploymentContractDTO:
        """Return the single contract effective on a payroll payment date."""
        result = await self._session.execute(
            select(EmploymentContractModel)
            .where(
                EmploymentContractModel.employer_id == employer_id,
                EmploymentContractModel.started_at <= payment_date,
                (EmploymentContractModel.ended_at.is_(None))
                | (EmploymentContractModel.ended_at >= payment_date),
            )
            .order_by(
                EmploymentContractModel.started_at.desc(),
                EmploymentContractModel.id.desc(),
            )
        )
        contracts = result.scalars().all()
        contract = contracts[0] if contracts else None
        if contract is None:
            raise PayrollConflictError(
                "No employment contract is effective for employer "
                f"{employer_id} on {payment_date.isoformat()}."
            )
        return EmploymentContractDTO(
            id=contract.id,
            employer_id=contract.employer_id,
            started_at=contract.started_at,
            ended_at=contract.ended_at,
            is_indefinite=contract.is_indefinite,
            position=contract.position,
        )

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

    async def _project_future_months_or_degrade(
        self,
        first_future_baseline: PredictedNetPayBaseline | None,
        *,
        current_year: int,
        current_month: int,
        first_increase_period: date,
        increase_frequency: int,
    ) -> dict[int, ProjectedFutureMonth]:
        """Project future months, degrading gracefully on a pf-rates outage.

        Same philosophy as the first_future_net_pay_clp try/except just
        above -- a PayrollDependencyError from project_future_months()'s own
        IPC lookup must not take down this otherwise fully DB-only,
        always-available endpoint either. See degraded_future_months() for
        exactly what "degrade" means here.
        """
        try:
            return await project_future_months(
                first_future_baseline,
                current_year=current_year,
                current_month=current_month,
                first_increase_period=first_increase_period,
                increase_frequency=increase_frequency,
                market_data_repository=self._market_data_repository,
            )
        except PayrollDependencyError:
            return degraded_future_months(
                first_future_baseline.net_pay_clp
                if first_future_baseline is not None
                else None
            )

    @staticmethod
    def _resolve_increase_frequency(*, configured_frequency: int | None) -> int:
        """Resolve the increase frequency in months using a safe default."""
        return configured_frequency if configured_frequency is not None else 12

    @staticmethod
    def _resolve_first_increase_period(
        *,
        employer_started_at: date,
        first_increase_period_year: int | None,
        first_increase_period_month: int | None,
        increase_frequency: int,
    ) -> date:
        """Resolve the first period where an increase should be applied."""
        if (
            first_increase_period_year is not None
            and first_increase_period_month is not None
        ):
            return date(first_increase_period_year, first_increase_period_month, 1)
        return add_months(
            date(employer_started_at.year, employer_started_at.month, 1),
            increase_frequency,
        )

    async def _resolve_current_period_row(
        self, reference_date: date
    ) -> Row[tuple[PayrollPeriodModel, EmployerModel]] | None:
        """Return the most recent declared period on or before reference_date.

        Shared by list_period_ranges() (to anchor its window) and
        get_period_range() (to resolve position/net_pay_clp_today context
        for an arbitrary period_id) -- identical "current period" semantics
        in both places, kept in one place to avoid the two ever silently
        drifting apart.
        """
        result = await self._session.execute(
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
        return result.first()

    async def _fetch_summary_amounts_map(
        self, period_ids: list[int]
    ) -> dict[int, _SummaryAmounts]:
        """Batch-fetch PAY_MV_SUMARY gross/taxable/discount totals by period_id.

        One round trip regardless of how many period_ids are requested --
        same "batch it, never N+1" discipline as the SALARY_BASE/
        HEALTH_ADDITIONAL_UF aggregate query just below. A period genuinely
        absent from the map (no row yet in the materialized view) is the
        caller's cue to leave gross_income_clp/taxable_income_clp/
        total_discounts_clp as None -- never fabricated.
        """
        if not period_ids:
            return {}
        result = await self._session.execute(
            select(
                PayrollSummaryModel.period_id,
                PayrollSummaryModel.gross_income_clp,
                PayrollSummaryModel.taxable_income_clp,
                PayrollSummaryModel.total_discounts_clp,
            ).where(PayrollSummaryModel.period_id.in_(period_ids))
        )
        return {
            row.period_id: _SummaryAmounts(
                gross_income_clp=row.gross_income_clp,
                taxable_income_clp=row.taxable_income_clp,
                total_discounts_clp=row.total_discounts_clp,
            )
            for row in result.all()
        }

    async def _fetch_employer_names_map(
        self, employer_ids: list[int]
    ) -> dict[int, str]:
        """Batch-fetch employer names for the given ids, one round trip.

        Same discipline as _fetch_summary_amounts_map(): previous/lookback
        periods in list_period_ranges() may belong to employer_ids other
        than the already-loaded `current_employer`, so their names are
        resolved here in bulk rather than one query per period.
        """
        if not employer_ids:
            return {}
        result = await self._session.execute(
            select(EmployerModel.id, EmployerModel.name).where(
                EmployerModel.id.in_(employer_ids)
            )
        )
        return {row[0]: row[1] for row in result.all()}

    async def list_period_ranges(
        self,
        *,
        today: date | None = None,
        previous_months: int | None = None,
        future_months: int | None = None,
    ) -> list[PayrollPeriodRangeDTO]:
        """List the current period plus previous and future date ranges.

        previous_months/future_months default to 12/12 (today's
        established behavior) when omitted (None) -- in that
        implicit-default case only, previous periods missing from the DB
        are padded with inferred (net_pay_clp=None) placeholders so the
        window always has exactly 12 entries, same as before this became
        configurable. The moment a caller passes previous_months
        explicitly (any value, including 12), padding is skipped
        entirely: the response only ever contains previous periods that
        are genuinely in the DB, up to that count -- fewer is a valid
        answer, inferred filler is not. future_months has no such
        distinction (future periods are always a projection, never "in
        the DB" to begin with) and is capped at 12 by the route's own
        Query validation.
        """
        previous_count = previous_months if previous_months is not None else 12
        future_count = future_months if future_months is not None else 12
        pad_previous = previous_months is None
        reference_date = today or date.today()
        current_row = await self._resolve_current_period_row(reference_date)
        if current_row is None:
            current_year = reference_date.year
            current_month = reference_date.month
            current_start = resolve_payment_date(current_year, current_month)
            current_net_pay_clp = None
            current_employer_started_at = date(current_year, current_month, 1)
            current_first_increase_period_year = None
            current_first_increase_period_month = None
            current_increase_frequency = None
            current_country_code = "CL"
            current_rule = EmployerPaymentDateRule.LAST_BUSINESS_DAY_OF_MONTH.value
            current_month_offset = 0
            current_day_of_month = None
            current_business_day_offset = 0
            current_calendar_day_offset = 0
            current_effective_on_processing_next_day = False
            current_fixed_day_roll = EmployerFixedDayRoll.PREVIOUS_BUSINESS_DAY.value
            current_inferred = True
            current_employer_id = None
        else:
            current_period, current_employer = current_row
            current_year = current_period.period_year
            current_month = current_period.period_month
            current_start = current_period.payment_date
            current_employer_started_at = await self._get_employer_contract_start(
                current_employer.id,
                date(current_year, current_month, 1),
            )
            current_first_increase_period_year = (
                current_employer.first_increase_period_year
            )
            current_first_increase_period_month = (
                current_employer.first_increase_period_month
            )
            current_increase_frequency = current_employer.increase_frequency
            current_country_code = current_employer.country_code
            current_rule = current_employer.payment_date_rule.value
            current_month_offset = current_employer.payment_month_offset
            current_day_of_month = current_employer.payment_day_of_month
            current_business_day_offset = current_employer.payment_business_day_offset
            current_calendar_day_offset = current_employer.payment_calendar_day_offset
            current_net_pay_clp = current_period.declared_net_pay_clp
            current_employer_id = current_employer.id
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

        effective_increase_frequency = self._resolve_increase_frequency(
            configured_frequency=current_increase_frequency
        )
        first_increase_period = self._resolve_first_increase_period(
            employer_started_at=current_employer_started_at,
            first_increase_period_year=current_first_increase_period_year,
            first_increase_period_month=current_first_increase_period_month,
            increase_frequency=effective_increase_frequency,
        )

        # Fetch one extra period beyond previous_count to serve as
        # lookback for the oldest window entry.
        previous_result = await self._session.execute(
            select(PayrollPeriodModel)
            .where(PayrollPeriodModel.payment_date < current_start)
            .order_by(
                PayrollPeriodModel.payment_date.desc(),
                PayrollPeriodModel.id.desc(),
            )
            .limit(previous_count + 1)
        )
        all_previous_fetched = list(previous_result.scalars().all())
        # all_previous_fetched is ordered most-recent-first (DESC);
        # the last item is oldest.
        # If previous_count + 1 were returned, the oldest is the lookback
        # and is excluded from the window.
        if len(all_previous_fetched) > previous_count:
            lookback_period_model: PayrollPeriodModel | None = all_previous_fetched[-1]
            previous_periods = all_previous_fetched[:-1]
        else:
            lookback_period_model = None
            previous_periods = all_previous_fetched

        # Fetch salary_base (sum of SALARY_BASE items) and fixed_uf_clp (sum
        # of HEALTH_ADDITIONAL_UF items -- the only UF/CLP-exchange-rate-
        # driven discount, same concept _compute_net_pay_clp_today() uses to
        # split a previous period's net_pay into its salary_base-driven vs.
        # UF-driven components, mirroring PredictedNetPayBaseline's split
        # for future months) for previous, lookback and current -- one
        # round trip, two conditional aggregates, not two separate queries.
        salary_base_map: dict[int, Decimal] = {}
        fixed_uf_clp_map: dict[int, Decimal] = {}
        period_ids = [period.id for period in previous_periods]
        if lookback_period_model is not None:
            period_ids.append(lookback_period_model.id)
        current_period_id: int | None = (
            current_period.id if current_row is not None else None
        )
        if current_period_id is not None:
            period_ids.append(current_period_id)
        if period_ids:
            aggregates_result = await self._session.execute(
                select(
                    PayrollItemModel.period_id,
                    func.sum(PayrollItemModel.amount_clp)
                    .filter(PayrollConceptModel.code == "SALARY_BASE")
                    .label("salary_base"),
                    func.sum(PayrollItemModel.amount_clp)
                    .filter(PayrollConceptModel.code == HEALTH_ADDITIONAL_CONCEPT_CODE)
                    .label("fixed_uf_clp"),
                )
                .join(
                    PayrollConceptModel,
                    PayrollItemModel.concept_id == PayrollConceptModel.id,
                )
                .where(PayrollItemModel.period_id.in_(period_ids))
                .where(
                    PayrollConceptModel.code.in_(
                        ("SALARY_BASE", HEALTH_ADDITIONAL_CONCEPT_CODE)
                    )
                )
                .group_by(PayrollItemModel.period_id)
            )
            all_aggregate_rows = aggregates_result.all()
            salary_base_map = {row[0]: row[1] for row in all_aggregate_rows}
            fixed_uf_clp_map = {
                row[0]: (row[2] if row[2] is not None else Decimal("0"))
                for row in all_aggregate_rows
            }

        # Fetch PAY_MV_SUMARY's gross/taxable/discount totals for the same
        # real periods -- unavailable (missing from the map) for any period
        # the materialized view hasn't computed yet, which simply means
        # None on the resulting DTO, same degrade-honestly philosophy as
        # every other derived field here.
        summary_amounts_map = await self._fetch_summary_amounts_map(period_ids)

        # Resolve employer names for every real period touched above.
        # `current_employer` is already a fully loaded EmployerModel (no
        # extra query needed for it), but previous/lookback periods are
        # fetched with no employer filter and may belong to a different
        # employer, so their names are batch-resolved for whichever ids
        # aren't already covered by `current_employer`.
        employer_names_map: dict[int, str] = (
            {current_employer_id: current_employer.name}
            if current_row is not None and current_employer_id is not None
            else {}
        )
        other_employer_ids = {
            period.employer_id
            for period in previous_periods
            if period.employer_id not in employer_names_map
        }
        if lookback_period_model is not None:
            other_employer_ids.add(lookback_period_model.employer_id)
            other_employer_ids.discard(current_employer_id)
        if other_employer_ids:
            employer_names_map.update(
                await self._fetch_employer_names_map(list(other_employer_ids))
            )

        # Collect all currency pairs first so the dependency is called once
        # for the complete period window instead of once per period/currency.
        previous_periods_ordered = list(reversed(previous_periods))
        currency_pairs = [
            (code, period.payment_date)
            for period in previous_periods_ordered
            if period.declared_net_pay_clp is not None
            for code in FOREIGN_CURRENCY_CODES
        ]
        if current_net_pay_clp is not None:
            currency_pairs.extend(
                (code, current_start) for code in FOREIGN_CURRENCY_CODES
            )
        exchange_rate_values: dict[tuple[str, date], Decimal | None] = {}
        batch_lookup = (
            getattr(self._market_data_repository, "get_exchange_rate_values", None)
            if self._market_data_repository is not None
            else None
        )
        if currency_pairs and batch_lookup is not None:
            try:
                exchange_rate_values = await batch_lookup(currency_pairs)
            except PayrollDependencyError:
                exchange_rate_values = {}

        previous_currencies = [
            await resolve_currency_equivalents(
                period.declared_net_pay_clp,
                rate_date=period.payment_date,
                market_data_repository=self._market_data_repository,
                exchange_rate_values=(
                    exchange_rate_values if batch_lookup is not None else None
                ),
            )
            for period in previous_periods_ordered
        ]
        current_currency = await resolve_currency_equivalents(
            current_net_pay_clp,
            rate_date=current_start,
            market_data_repository=self._market_data_repository,
            exchange_rate_values=(
                exchange_rate_values if batch_lookup is not None else None
            ),
        )

        previous_ranges = [
            PayrollPeriodRangeDTO(
                period_year=period.period_year,
                period_month=period.period_month,
                start_date=period.payment_date,
                end_date=period.payment_date,
                net_pay_clp=period.declared_net_pay_clp,
                is_current=False,
                inferred=False,
                increase=None,
                salary_base=salary_base_map.get(period.id),
                worked_days=period.worked_days,
                net_pay_usd=currency.usd,
                net_pay_eur=currency.eur,
                net_pay_uf=currency.uf,
                fixed_uf_clp=fixed_uf_clp_map.get(period.id, Decimal("0")),
                period_id=period.id,
                employer_id=period.employer_id,
                employer_name=employer_names_map.get(period.employer_id),
                gross_income_clp=(
                    amounts.gross_income_clp if amounts is not None else None
                ),
                taxable_income_clp=(
                    amounts.taxable_income_clp if amounts is not None else None
                ),
                total_discounts_clp=(
                    amounts.total_discounts_clp if amounts is not None else None
                ),
            )
            for period, currency, amounts in (
                (period, currency, summary_amounts_map.get(period.id))
                for period, currency in zip(
                    previous_periods_ordered, previous_currencies, strict=True
                )
            )
        ]

        if pad_previous and len(previous_ranges) < previous_count:
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
            for extra_offset in range(previous_count - len(previous_ranges)):
                inferred_month = add_months(
                    seed_month,
                    -(previous_count - len(previous_ranges) - 1) + extra_offset,
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
                        increase=None,
                    )
                )
            previous_ranges = inferred_previous + previous_ranges

        # Prepend lookback ghost: not part of the response window but provides
        # salary context so the oldest previous period can have its increase computed.
        if lookback_period_model is not None:
            lookback_dto = PayrollPeriodRangeDTO(
                period_year=lookback_period_model.period_year,
                period_month=lookback_period_model.period_month,
                start_date=lookback_period_model.payment_date,
                end_date=lookback_period_model.payment_date,
                net_pay_clp=lookback_period_model.declared_net_pay_clp,
                is_current=False,
                inferred=False,
                increase=None,
                salary_base=salary_base_map.get(lookback_period_model.id),
                worked_days=lookback_period_model.worked_days,
                is_lookback=True,
            )
            previous_ranges = [lookback_dto] + previous_ranges

        current_summary_amounts = (
            summary_amounts_map.get(current_period_id)
            if current_period_id is not None
            else None
        )
        current_range = PayrollPeriodRangeDTO(
            period_year=current_year,
            period_month=current_month,
            start_date=current_start,
            end_date=current_start,
            net_pay_clp=current_net_pay_clp,
            is_current=True,
            inferred=current_inferred,
            increase=None,
            salary_base=(
                salary_base_map.get(current_period_id)
                if current_period_id is not None
                else None
            ),
            worked_days=(
                current_period.worked_days if current_row is not None else None
            ),
            net_pay_usd=current_currency.usd,
            net_pay_eur=current_currency.eur,
            net_pay_uf=current_currency.uf,
            period_id=current_period_id,
            employer_id=current_employer_id,
            employer_name=(current_employer.name if current_row is not None else None),
            gross_income_clp=(
                current_summary_amounts.gross_income_clp
                if current_summary_amounts is not None
                else None
            ),
            taxable_income_clp=(
                current_summary_amounts.taxable_income_clp
                if current_summary_amounts is not None
                else None
            ),
            total_discounts_clp=(
                current_summary_amounts.total_discounts_clp
                if current_summary_amounts is not None
                else None
            ),
        )

        # Calculate predicted net_pay for the first future period. A
        # PayrollDependencyError (pf-rates unreachable/erroring) must not take
        # down this otherwise fully DB-only, always-available endpoint over
        # one optional field on one of its 24 rows -- degrade to None instead.
        first_future_baseline: PredictedNetPayBaseline | None = None
        projected_future_months: dict[int, ProjectedFutureMonth] = {}
        if future_count > 0:
            if current_row is not None and not current_inferred:
                try:
                    first_future_baseline = await predict_next_period_net_pay(
                        self._session,
                        current_period,
                        date(current_year, current_month, 1),
                        self._market_data_repository,
                    )
                except PayrollDependencyError:
                    first_future_baseline = None

            projected_future_months = await self._project_future_months_or_degrade(
                first_future_baseline,
                current_year=current_year,
                current_month=current_month,
                first_increase_period=first_increase_period,
                increase_frequency=effective_increase_frequency,
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
                net_pay_clp=projected_future_months[month_offset].net_pay_clp,
                is_current=False,
                inferred=True,
                increase=projected_future_months[month_offset].increase_pct,
            )
            for month_offset, period_month in (
                (
                    month_offset,
                    add_months(date(current_year, current_month, 1), month_offset),
                )
                for month_offset in range(1, future_count + 1)
            )
        ]
        trailing_start = resolve_payment_date(
            add_months(date(current_year, current_month, 1), future_count + 1).year,
            add_months(date(current_year, current_month, 1), future_count + 1).month,
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
                    increase=period_range.increase,
                    salary_base=period_range.salary_base,
                    worked_days=period_range.worked_days,
                    is_lookback=period_range.is_lookback,
                    net_pay_usd=period_range.net_pay_usd,
                    net_pay_eur=period_range.net_pay_eur,
                    net_pay_uf=period_range.net_pay_uf,
                    period_id=period_range.period_id,
                    employer_id=period_range.employer_id,
                    employer_name=period_range.employer_name,
                    gross_income_clp=period_range.gross_income_clp,
                    taxable_income_clp=period_range.taxable_income_clp,
                    total_discounts_clp=period_range.total_discounts_clp,
                )
            )
        return completed_ranges

    async def get_period_range(
        self, period_id: int
    ) -> PayrollPeriodRangeContextDTO | None:
        """Get a single real period in the unified period-range shape.

        Unlike list_period_ranges() (anchored to a fixed window around
        "today"), this works for any period_id regardless of age -- it
        fetches exactly the one requested period, its immediate real
        predecessor (for `increase`), and today's resolved "current"
        period (for `net_pay_clp_today`, only ever computed when the
        target turns out to be `previous`). Every period this method
        touches is a real DB row -- there is no "projection" concept here,
        unlike list_period_ranges()'s synthetic future entries -- so
        `increase` is always derived the same way regardless of the
        target's eventual position.
        """
        target_result = await self._session.execute(
            select(PayrollPeriodModel, EmployerModel)
            .join(EmployerModel, PayrollPeriodModel.employer_id == EmployerModel.id)
            .where(PayrollPeriodModel.id == period_id)
        )
        target_row = target_result.first()
        if target_row is None:
            return None
        target_period, target_employer = target_row

        predecessor_result = await self._session.execute(
            select(PayrollPeriodModel)
            .where(PayrollPeriodModel.payment_date < target_period.payment_date)
            .order_by(
                PayrollPeriodModel.payment_date.desc(),
                PayrollPeriodModel.id.desc(),
            )
            .limit(1)
        )
        predecessor_period = predecessor_result.scalar_one_or_none()

        current_row = await self._resolve_current_period_row(date.today())
        current_period = current_row[0] if current_row is not None else None
        is_previous = (
            current_period is not None
            and target_period.payment_date < current_period.payment_date
        )

        period_ids = [target_period.id]
        if predecessor_period is not None:
            period_ids.append(predecessor_period.id)
        if current_period is not None:
            period_ids.append(current_period.id)

        salary_base_map: dict[int, Decimal] = {}
        fixed_uf_clp_map: dict[int, Decimal] = {}
        aggregates_result = await self._session.execute(
            select(
                PayrollItemModel.period_id,
                func.sum(PayrollItemModel.amount_clp)
                .filter(PayrollConceptModel.code == "SALARY_BASE")
                .label("salary_base"),
                func.sum(PayrollItemModel.amount_clp)
                .filter(PayrollConceptModel.code == HEALTH_ADDITIONAL_CONCEPT_CODE)
                .label("fixed_uf_clp"),
            )
            .join(
                PayrollConceptModel,
                PayrollItemModel.concept_id == PayrollConceptModel.id,
            )
            .where(PayrollItemModel.period_id.in_(period_ids))
            .where(
                PayrollConceptModel.code.in_(
                    ("SALARY_BASE", HEALTH_ADDITIONAL_CONCEPT_CODE)
                )
            )
            .group_by(PayrollItemModel.period_id)
        )
        aggregate_rows = aggregates_result.all()
        salary_base_map = {row[0]: row[1] for row in aggregate_rows}
        fixed_uf_clp_map = {
            row[0]: (row[2] if row[2] is not None else Decimal("0"))
            for row in aggregate_rows
        }
        summary_amounts_map = await self._fetch_summary_amounts_map([target_period.id])

        target_currency_task = resolve_currency_equivalents(
            target_period.declared_net_pay_clp,
            rate_date=target_period.payment_date,
            market_data_repository=self._market_data_repository,
        )
        if is_previous and current_period is not None:
            current_currency_task = resolve_currency_equivalents(
                current_period.declared_net_pay_clp,
                rate_date=current_period.payment_date,
                market_data_repository=self._market_data_repository,
            )
            target_currency, current_currency = await asyncio.gather(
                target_currency_task, current_currency_task
            )
        else:
            target_currency = await target_currency_task
            current_currency = None

        target_amounts = summary_amounts_map.get(target_period.id)
        target_dto = PayrollPeriodRangeDTO(
            period_year=target_period.period_year,
            period_month=target_period.period_month,
            start_date=target_period.payment_date,
            end_date=target_period.payment_date,
            net_pay_clp=target_period.declared_net_pay_clp,
            is_current=(
                current_period is not None and target_period.id == current_period.id
            ),
            inferred=False,
            salary_base=salary_base_map.get(target_period.id),
            worked_days=target_period.worked_days,
            net_pay_usd=target_currency.usd,
            net_pay_eur=target_currency.eur,
            net_pay_uf=target_currency.uf,
            fixed_uf_clp=fixed_uf_clp_map.get(target_period.id, Decimal("0")),
            period_id=target_period.id,
            employer_id=target_period.employer_id,
            employer_name=target_employer.name,
            gross_income_clp=(
                target_amounts.gross_income_clp if target_amounts is not None else None
            ),
            taxable_income_clp=(
                target_amounts.taxable_income_clp
                if target_amounts is not None
                else None
            ),
            total_discounts_clp=(
                target_amounts.total_discounts_clp
                if target_amounts is not None
                else None
            ),
        )
        predecessor_dto = (
            PayrollPeriodRangeDTO(
                period_year=predecessor_period.period_year,
                period_month=predecessor_period.period_month,
                start_date=predecessor_period.payment_date,
                end_date=predecessor_period.payment_date,
                net_pay_clp=predecessor_period.declared_net_pay_clp,
                is_current=False,
                inferred=False,
                salary_base=salary_base_map.get(predecessor_period.id),
                worked_days=predecessor_period.worked_days,
            )
            if predecessor_period is not None
            else None
        )
        current_dto = (
            PayrollPeriodRangeDTO(
                period_year=current_period.period_year,
                period_month=current_period.period_month,
                start_date=current_period.payment_date,
                end_date=current_period.payment_date,
                net_pay_clp=current_period.declared_net_pay_clp,
                is_current=True,
                inferred=False,
                period_id=current_period.id,
                salary_base=salary_base_map.get(current_period.id),
                worked_days=current_period.worked_days,
                net_pay_uf=(
                    current_currency.uf if current_currency is not None else None
                ),
            )
            if current_period is not None
            else None
        )
        return PayrollPeriodRangeContextDTO(
            target=target_dto, predecessor=predecessor_dto, current=current_dto
        )

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

        health_plan_ids_result = await self._session.execute(
            select(PayrollPeriodHealthPlanModel.health_plan_id)
            .where(PayrollPeriodHealthPlanModel.period_id == period.id)
            .order_by(PayrollPeriodHealthPlanModel.health_plan_id.asc())
        )
        health_plan_ids = tuple(
            int(plan_id) for plan_id in health_plan_ids_result.scalars().all()
        )
        primary_health_plan_id = int(health_plan_ids[0]) if health_plan_ids else None

        health_institution_is_active = None
        if primary_health_plan_id is not None:
            health_institution_result = await self._session.execute(
                select(HealthInstitutionModel.is_active)
                .join(
                    HealthPlanModel,
                    HealthPlanModel.institution_id == HealthInstitutionModel.id,
                )
                .where(HealthPlanModel.id == primary_health_plan_id)
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
            period_year=period.period_year,
            period_month=period.period_month,
            payment_date=period.payment_date,
            worked_days=period.worked_days,
            pension_plan_id=period.pension_plan_id,
            health_plan_id=primary_health_plan_id,
            items=items,
            summary=summary,
            health_plan_ids=health_plan_ids or None,
            health_institution_is_active=health_institution_is_active,
        )

    async def list_period_details(
        self, filters: ExportPayrollFiltersDTO
    ) -> list[PayrollPeriodDetailDTO]:
        """List full period detail (items included) for periods matching filters.

        Reuses get_period_detail() per matching period id rather than a
        bespoke bulk query with its own joins: at today's volume (dozens of
        periods for a single employer, per the design brief's own "Expected
        volume" note) the extra round trips per period are not a real cost,
        and this keeps the bulk and single-period code paths from ever
        describing a period's shape differently (DRY). Revisit only if
        export volume genuinely grows past that (YAGNI).
        """
        query = select(PayrollPeriodModel.id).join(
            EmployerModel, PayrollPeriodModel.employer_id == EmployerModel.id
        )
        if filters.employer is not None:
            query = query.where(EmployerModel.name == filters.employer)
        if filters.period_year is not None:
            query = query.where(PayrollPeriodModel.period_year == filters.period_year)
        if filters.period_month is not None:
            query = query.where(PayrollPeriodModel.period_month == filters.period_month)
        query = query.order_by(
            PayrollPeriodModel.period_year.asc(),
            PayrollPeriodModel.period_month.asc(),
            EmployerModel.name.asc(),
        )
        result = await self._session.execute(query)
        period_ids = list(result.scalars().all())

        details: list[PayrollPeriodDetailDTO] = []
        for period_id in period_ids:
            detail = await self.get_period_detail(period_id)
            if detail is not None:
                details.append(detail)
        return details

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
