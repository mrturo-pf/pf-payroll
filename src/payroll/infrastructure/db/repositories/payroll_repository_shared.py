"""Shared helpers for SQLAlchemy payroll repositories."""

from calendar import monthrange
from dataclasses import dataclass
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
from payroll.application.ports.repositories import MarketDataRepository
from payroll.domain.quantizers import quantize_percent
from payroll.infrastructure.db.models import (
    ContributionCapModel,
    EmployerModel,
    HealthInstitutionModel,
    HealthPlanModel,
    PayrollConceptModel,
    PensionInstitutionModel,
    PensionPlanModel,
    PayrollSummaryModel,
)
from payroll.infrastructure.db.models.payroll import (
    PayrollItemModel,
    PayrollPeriodModel,
)
from payroll.infrastructure.db.models.reference_data import ContributionCapType
from payroll.shared.constants import (
    RECONCILIATION_TOLERANCE_CLP,
    REVIEW_REQUIRED_CONCEPT_CODES,
)
from payroll.shared.dates import (
    add_months,
    is_increase_period,
    resolve_last_increase_period,
)

_NET_PAY_QUANT = Decimal("0.01")
_IPC_CODE = "IPC_CL"
_NO_INCREASE_PCT = Decimal("0.00")


@dataclass(frozen=True, slots=True)
class ProjectedFutureMonth:
    """One future month's projected net_pay_clp and salary_base increase_pct.

    `increase_pct` is the percentage variation of (normalized) salary_base
    this month represents vs. the preceding one -- 0.00 whenever this month
    is not a scheduled increase month, or whenever it is one but the
    percentage could not be computed/was floored (see _apply_ipc_step).
    `net_pay_clp` is independently nullable: it degrades to None whenever
    there is no first-month UF prediction to replicate/step from (e.g. a
    pf-rates outage), even on months where increase_pct is still a real,
    computable number from IPC data alone.
    """

    net_pay_clp: Decimal | None
    increase_pct: Decimal


def build_net_pay_warning(
    declared_net_pay_clp: Decimal | None,
    expected_net_pay_clp: Decimal | None,
    net_pay_difference_clp: Decimal | None,
) -> str | None:
    """Build net pay warning.

    Uses the same shared reconciliation tolerance as PENSION_BASE/
    PENSION_ADDITIONAL/HEALTH_BASE/HEALTH_ADDITIONAL_UF -- this is the final
    accounting check summing every income/discount concept on the payslip,
    so any residual already absorbed upstream by that tolerance would
    otherwise resurface here as a leftover difference of the same size and
    block validation anyway. See docs/investigations/health-additional-uf-
    mismatch.md, Session 12, for the case that surfaced this.
    """
    if declared_net_pay_clp is None:
        return None
    if expected_net_pay_clp is None or net_pay_difference_clp is None:
        return (
            "Declared net_pay will be reconciled after computed contributions "
            "and income tax are generated."
        )
    if abs(net_pay_difference_clp) <= RECONCILIATION_TOLERANCE_CLP:
        return None
    return (
        "Declared net_pay does not match the fully computed payroll totals. "
        f"Difference: {net_pay_difference_clp} CLP."
    )


def get_last_day_of_month(target_date: date) -> date:
    """Get the last day of the month for the given date."""
    last_day = monthrange(target_date.year, target_date.month)[1]
    return date(target_date.year, target_date.month, last_day)


async def predict_next_period_net_pay(
    session: AsyncSession,
    current_period: PayrollPeriodModel,
    current_period_end_month: date,
    market_data_repository: MarketDataRepository | None = None,
) -> Decimal | None:
    """Predict net_pay_clp for the next period based on current period data.

    Uses income items (SALARY_BASE, LEGAL_GRATUITY, TELEWORK_REFUND) from
    the current period and applies the same discount ratios for non-UF
    discounts.

    Recalculates UF-based discount concepts (HEALTH_ADDITIONAL_UF) and
    HEALTH_INSURANCE_EMPLOYER_CONTRIBUTION using the selected UF tied to
    the current period's end_date month-end.

    Adjusts income to 30-day accounting month if current period had fewer days.

    UF resolution is delegated entirely to pf-rates (MarketDataRepository):
    its own GetExchangeRateValue use case already implements a strict
    superset of what this function used to hand-roll against a local
    ExchangeRateModel table (DB hit -> provider fetch -> nearest-prior-date
    fallback -- see pf-rates' get_exchange_rate_value.py). `None` here means
    either the feature is not wired in (no market_data_repository was
    passed -- the same as this function's pre-pf-rates stub state) or
    pf-rates genuinely has no UF value to offer for that date.

    Args:
        session: Database session, for reading the current period's items.
        current_period: The current payroll period.
        current_period_end_month: Date in month to extract UF from (e.g., end_date).
        market_data_repository: Read port for pf-rates-backed UF values. `None`
            disables the prediction entirely (returns None immediately).

    Returns:
        Predicted net_pay_clp for the next period, or None if calculation fails.
    """
    if market_data_repository is None:
        return None

    last_day_current_month = get_last_day_of_month(current_period_end_month)
    uf_current = await market_data_repository.get_exchange_rate_value(
        "UF", last_day_current_month
    )
    if uf_current is None or uf_current <= 0:
        return None

    # Get current period's income and discount items
    items_result = await session.execute(
        select(PayrollItemModel.amount_clp, PayrollConceptModel.code)
        .join(
            PayrollConceptModel,
            PayrollItemModel.concept_id == PayrollConceptModel.id,
        )
        .where(PayrollItemModel.period_id == current_period.id)
    )
    items = items_result.all()

    # Extract specific income components
    income_codes = {"SALARY_BASE", "LEGAL_GRATUITY", "TELEWORK_REFUND"}
    non_uf_discount_codes = {
        "PENSION_BASE",
        "PENSION_ADDITIONAL",
        "HEALTH_BASE",
        "HEALTH_INSURANCE",
        "UNEMPLOYMENT_INSURANCE",
        "INCOME_TAX",
    }
    uf_discount_codes = {"HEALTH_ADDITIONAL_UF"}

    future_gross = Decimal("0")
    current_gross = Decimal("0")
    current_non_uf_discounts = Decimal("0")
    current_uf_discounts = Decimal("0")
    employer_health_insurance_clp = Decimal("0")

    for amount, code in items:
        if code in income_codes:
            future_gross += amount
            current_gross += amount
        elif code in non_uf_discount_codes:
            current_non_uf_discounts += amount
        elif code in uf_discount_codes:
            current_uf_discounts += amount
        elif code == "HEALTH_INSURANCE_EMPLOYER_CONTRIBUTION":
            employer_health_insurance_clp += amount

    # If no income or gross income is zero, cannot predict
    if future_gross <= 0 or current_gross <= 0:
        return None

    reference_uf_for_current: Decimal | None = uf_current
    if current_uf_discounts > 0 or employer_health_insurance_clp > 0:
        reference_uf_for_current = await market_data_repository.get_exchange_rate_value(
            "UF", current_period.payment_date
        )

    if reference_uf_for_current is None or reference_uf_for_current <= 0:
        return None

    # Recalculate employer contribution using selected UF (from current end_date month)
    # Convert from CLP (current UF) -> UF quantity -> CLP (selected UF)
    if employer_health_insurance_clp > 0:
        employer_uf_quantity = employer_health_insurance_clp / reference_uf_for_current
        employer_contribution_future = employer_uf_quantity * uf_current
        future_gross += employer_contribution_future
        current_gross += employer_health_insurance_clp

    future_uf_discounts = Decimal("0")
    if current_uf_discounts > 0:
        health_uf_quantity = current_uf_discounts / reference_uf_for_current
        future_uf_discounts = health_uf_quantity * uf_current

    # Adjust income and discounts to 30-day accounting month if needed
    worked_days = current_period.worked_days or 30
    non_uf_discount_ratio = Decimal("0")

    if current_gross > 0:
        # Calculate non-UF discount ratio from current period
        non_uf_discount_ratio = current_non_uf_discounts / current_gross

    if worked_days > 0 and worked_days < 30:
        # Project income to 30 days
        current_gross = current_gross * Decimal(30) / Decimal(worked_days)
        future_gross = future_gross * Decimal(30) / Decimal(worked_days)

    # Apply same ratio to future gross for non-UF discounts and
    # add UF-derived discounts converted with selected UF.
    future_non_uf_discounts = future_gross * non_uf_discount_ratio
    future_discounts = future_non_uf_discounts + future_uf_discounts

    # Calculate predicted net pay
    predicted_net_pay = future_gross - future_discounts

    if predicted_net_pay <= 0:
        return None

    # Quantize to 2 decimal places (CLP cents)
    return predicted_net_pay.quantize(_NET_PAY_QUANT)


async def _apply_ipc_step(
    current_value: Decimal | None,
    *,
    last_increase_period: date | None,
    latest_index: tuple[date, Decimal] | None,
    increase_month: date,
    market_data_repository: MarketDataRepository,
) -> tuple[Decimal | None, date | None, Decimal]:
    """Step `current_value` up by cumulative IPC since the last real increase.

    There is no reliable IPC figure yet for `increase_month` itself (it is
    always in the future), so this uses the IPC published for
    `last_increase_period` (the last real/already-applied raise) as the
    baseline and the most recently published IPC figure overall as the
    best available stand-in for "today's" price level -- only when that
    latest figure's own period is not itself later than `increase_month`
    (it never should be, since IPC is only ever published for the past,
    but this is the explicit guard requested in
    docs/proposals/net-pay-prediction-reimplementation-design-plan.md).

    Any missing ingredient (no prior real increase to anchor on, no IPC
    data at all, or a zero/negative baseline) degrades gracefully: the
    value is left unchanged, the anchor does not advance (so a later
    increase month in the same window still compares against the true
    last real increase instead of silently treating this skipped one as
    a 0% raise), and the reported increase_pct is 0.00 (no quantifiable
    change -- see ProjectedFutureMonth).

    Deflation floor: if the latest published IPC is *lower* than the IPC
    at the last real increase, the previous net pay is kept as-is instead
    of being reduced -- salaries do not get cut due to deflation in
    practice, only held flat (explicit user requirement, see
    docs/proposals/net-pay-prediction-reimplementation-design-plan.md).
    Same non-advancing-anchor/0.00-percentage treatment as the other
    degradation paths above: a later increase month still compares against
    the true last real increase, so any eventual rebound in prices is not
    lost.

    `current_value` may be None (no first-month UF prediction to step --
    e.g. a pf-rates outage at that unrelated step): the increase_pct is
    still computed from IPC data alone when possible, it is only the
    stepped net_pay that stays None, passed through unchanged.

    Returns:
        Tuple of (possibly stepped value or None, anchor to use for the
        *next* increase month encountered, increase_pct this step
        represents -- 0.00 when no real step was applied for any of the
        reasons above).
    """
    if last_increase_period is None or latest_index is None:
        return current_value, last_increase_period, _NO_INCREASE_PCT
    latest_period, latest_value = latest_index
    if latest_period > increase_month:
        return current_value, last_increase_period, _NO_INCREASE_PCT
    last_increase_index = await market_data_repository.get_economic_index_value(
        _IPC_CODE, last_increase_period.year, last_increase_period.month
    )
    if last_increase_index is None or last_increase_index <= 0:
        return current_value, last_increase_period, _NO_INCREASE_PCT
    if latest_value < last_increase_index:
        return current_value, last_increase_period, _NO_INCREASE_PCT
    increase_pct = quantize_percent((latest_value / last_increase_index - 1) * 100)
    stepped = (
        (current_value * latest_value / last_increase_index).quantize(_NET_PAY_QUANT)
        if current_value is not None
        else None
    )
    return stepped, increase_month, increase_pct


def degraded_future_months(
    first_future_net_pay_clp: Decimal | None,
) -> dict[int, ProjectedFutureMonth]:
    """Build the fallback series used whenever IPC data cannot be checked at all.

    Shared by project_future_months() itself (no market_data_repository
    wired in) and list_period_ranges() (market_data_repository wired in,
    but a PayrollDependencyError means pf-rates itself is unreachable right
    now) -- both degrade identically from the caller's perspective: there
    is no way to check IPC, so increase_pct is 0.00 everywhere, and
    net_pay_clp only survives for month_offset 1 (it never depended on
    this lookup in the first place).
    """
    return {
        month_offset: ProjectedFutureMonth(
            net_pay_clp=first_future_net_pay_clp if month_offset == 1 else None,
            increase_pct=_NO_INCREASE_PCT,
        )
        for month_offset in range(1, 13)
    }


async def project_future_months(
    first_future_net_pay_clp: Decimal | None,
    *,
    current_year: int,
    current_month: int,
    first_increase_period: date,
    increase_frequency: int,
    market_data_repository: MarketDataRepository | None,
) -> dict[int, ProjectedFutureMonth]:
    """Project net_pay_clp and increase_pct for future months 1 through 12.

    net_pay_clp for month_offset 1 always mirrors the UF-based prediction
    (predict_next_period_net_pay) unchanged -- projecting its own
    ratio-based assumptions any further loses accuracy fast. Months 2-12
    replicate that first prediction unchanged until an employer-configured
    increase month is reached, at which point the value is stepped up by
    cumulative IPC since the last real increase (see _apply_ipc_step) and
    the stepped value becomes the new baseline replicated forward from
    there. In the rare case month_offset 1 itself lands on a scheduled
    increase month, its own displayed net_pay_clp still stays the raw UF
    prediction (by design, the two are different, equally valid estimation
    methods for the same event), but the internal running value used to
    seed month_offset 2 onward does pick up that step -- so the configured
    raise is never silently lost even in that edge case.

    increase_pct is independent of net_pay_clp and covers all 12 months
    uniformly: it reflects the IPC-based percentage of this scheduled
    salary_base change, 0.00 on non-increase months or whenever IPC data
    is insufficient to compute a real figure (see _apply_ipc_step).

    Like predict_next_period_net_pay(), this function lets
    PayrollDependencyError propagate on a pf-rates outage rather than
    catching it -- list_period_ranges() is the single try/except boundary
    responsible for degrading gracefully (same convention, see its own
    docstring and degraded_future_months() above).

    Returns:
        Mapping of month_offset (1..12) to its ProjectedFutureMonth.
    """
    if market_data_repository is None:
        return degraded_future_months(first_future_net_pay_clp)

    current_value = first_future_net_pay_clp
    last_increase_period = resolve_last_increase_period(
        first_increase_period=first_increase_period,
        increase_frequency=increase_frequency,
        as_of=date(current_year, current_month, 1),
    )
    latest_index = await market_data_repository.get_latest_economic_index(_IPC_CODE)

    series: dict[int, ProjectedFutureMonth] = {}
    for month_offset in range(1, 13):
        period_month = add_months(date(current_year, current_month, 1), month_offset)
        increase_pct = _NO_INCREASE_PCT
        if is_increase_period(
            period_year=period_month.year,
            period_month=period_month.month,
            first_increase_period=first_increase_period,
            increase_frequency=increase_frequency,
        ):
            current_value, last_increase_period, increase_pct = await _apply_ipc_step(
                current_value,
                last_increase_period=last_increase_period,
                latest_index=latest_index,
                increase_month=period_month,
                market_data_repository=market_data_repository,
            )
        displayed_net_pay = (
            first_future_net_pay_clp if month_offset == 1 else current_value
        )
        series[month_offset] = ProjectedFutureMonth(
            net_pay_clp=displayed_net_pay, increase_pct=increase_pct
        )
    return series


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
        net_pay_warning=build_net_pay_warning(
            period.declared_net_pay_clp,
            period.expected_net_pay_clp,
            period.net_pay_difference_clp,
        ),
    )


class SqlAlchemyPayrollRepositoryBase:
    """Common helpers shared across payroll repository concerns."""

    def __init__(
        self,
        session: AsyncSession,
        market_data_repository: MarketDataRepository | None = None,
    ) -> None:
        """Initialize the instance.

        `market_data_repository` is optional and defaults to `None` so every
        existing caller/test constructing a repository with just `session`
        keeps working unchanged -- `predict_next_period_net_pay()` already
        treats `None` as "feature not wired in" and returns `None` (the same
        behavior this whole subsystem had before this parameter existed).
        Only Queries actually reads it (list_period_ranges()'s first-future-
        period prediction) -- Commands/Imports inherit it unused, a small ISP
        compromise preferred over relying on multiple-inheritance method
        resolution order to route this to only one mixin (see
        docs/proposals/net-pay-prediction-reimplementation-design-
        recommendation.md, Section 5).
        """
        self._session = session
        self._market_data_repository = market_data_repository

    async def _refresh_summary_view(self) -> None:
        """Handle refresh summary view."""
        await self._session.commit()
        await self._session.execute(text('REFRESH MATERIALIZED VIEW "PAY_MV_SUMARY"'))
        await self._session.commit()

    async def _reconcile_period_net_pay(
        self,
        period: PayrollPeriodModel,
        *,
        refresh_summary_view: bool,
    ) -> None:
        """Reconcile declared net pay once all computed concepts exist."""
        if refresh_summary_view:
            await self._refresh_summary_view()

        if period.declared_net_pay_clp is None:
            period.expected_net_pay_clp = None
            period.net_pay_difference_clp = None
            await self._session.commit()
            return

        concept_result = await self._session.execute(
            select(PayrollConceptModel.code)
            .join(
                PayrollItemModel,
                PayrollItemModel.concept_id == PayrollConceptModel.id,
            )
            .where(PayrollItemModel.period_id == period.id)
            .where(PayrollConceptModel.code.in_(REVIEW_REQUIRED_CONCEPT_CODES))
        )
        available_codes = set(concept_result.scalars().all())
        if available_codes != REVIEW_REQUIRED_CONCEPT_CODES:
            period.expected_net_pay_clp = None
            period.net_pay_difference_clp = None
            await self._session.commit()
            return

        summary_result = await self._session.execute(
            select(PayrollSummaryModel.net_pay_clp).where(
                PayrollSummaryModel.period_id == period.id
            )
        )
        expected_net_pay_clp = summary_result.scalar_one_or_none()
        if expected_net_pay_clp is None:
            period.expected_net_pay_clp = None
            period.net_pay_difference_clp = None
            await self._session.commit()
            return

        period.expected_net_pay_clp = Decimal(expected_net_pay_clp)
        period.net_pay_difference_clp = (
            period.declared_net_pay_clp - period.expected_net_pay_clp
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
            .limit(1)
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

    async def _fetch_health_plan_row(
        self, plan_id: int
    ) -> tuple[HealthPlanModel, HealthInstitutionModel]:
        """Fetch a health plan and its institution by ID, or raise if missing.

        Shared by `_get_health_plan()` (single-day validity check, for
        explicit/new plan assignments) and `_get_assigned_health_plan()`
        (existence-only, for plans already snapshotted on a period) -- both
        need the exact same join/lookup, just different validation on top.
        """
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
        return health_row[0], health_row[1]

    async def _get_health_plan(
        self,
        plan_id: int,
        payment_date: date,
        *,
        require_active: bool = False,
    ) -> tuple[HealthPlanModel, HealthInstitutionModel]:
        """Handle get health plan."""
        health_plan_model, health_institution_model = await self._fetch_health_plan_row(
            plan_id
        )
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

    async def _get_assigned_health_plan(
        self, plan_id: int
    ) -> tuple[HealthPlanModel, HealthInstitutionModel]:
        """Fetch a health plan already snapshotted onto a period, by ID only.

        Deliberately skips the single-day valid_from/valid_to check
        `_get_health_plan()` does -- a plan already recorded in
        PayrollPeriodHealthPlanModel was already proven to overlap its
        period's month (and belong to an active institution) at import time
        by get_health_plans_overlapping_month(). Re-applying a single-day
        check here at contribution-compute time would wrongly reject a plan
        that only covers *part* of the month -- exactly the case day-level
        proration exists to support (see domain/health_plan_proration.py).
        Only existence is re-verified, not validity.
        """
        return await self._fetch_health_plan_row(plan_id)
