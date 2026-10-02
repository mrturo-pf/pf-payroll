"""Shared helpers for SQLAlchemy payroll repositories."""

import asyncio
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
from payroll.domain.quantizers import quantize_currency_amount, quantize_percent
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


@dataclass(frozen=True, slots=True)
class PredictedNetPayBaseline:
    """First-future-month net_pay prediction, split by what a later raise should scale.

    `net_pay_clp` is the full prediction -- `scalable_clp - fixed_uf_clp` --
    unchanged from what this function has always returned; used as-is for
    month_offset=1's displayed value and for every month until an increase
    is first stepped (see project_future_months()).

    `scalable_clp` is the part of `net_pay_clp` that trails the employer's
    own salary_base: `future_gross` (SALARY_BASE/LEGAL_GRATUITY/
    TELEWORK_REFUND, already 30-day-prorated) net of the current period's
    proportional non-UF discount ratio (pension/health/unemployment/tax).
    This is the only piece later increase steps multiply by the IPC-derived
    cycle ratio (see _apply_ipc_step()/_extrapolate_cycle_ratio()) -- a
    salary_base raise is exactly what that ratio represents, so it is what
    should scale, not the final net figure.

    `fixed_uf_clp` is the part of `net_pay_clp` driven by the UF/CLP
    exchange rate (HEALTH_ADDITIONAL_UF, already netted against the
    employer's UF-converted health contribution) -- it moves with currency
    markets, not with a salary_base raise, so it is carried forward
    unchanged at every future increase step rather than being scaled by
    the same ratio as `scalable_clp`. There is no future UF forecast to
    recompute it against, so "unchanged" is the only available, and
    correct, choice -- exactly how this amount was already being carried
    forward (just bundled invisibly inside net_pay_clp) before this split.
    """

    net_pay_clp: Decimal
    scalable_clp: Decimal
    fixed_uf_clp: Decimal


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
) -> PredictedNetPayBaseline | None:
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

    Returns a `PredictedNetPayBaseline` rather than a bare Decimal so that
    project_future_months() can later scale only the salary_base-driven
    portion of this prediction when a configured increase lands on a later
    future month, instead of scaling the whole net figure (which would
    incorrectly also inflate/deflate the UF-driven portion by the same
    salary raise ratio -- see PredictedNetPayBaseline's own docstring).

    Args:
        session: Database session, for reading the current period's items.
        current_period: The current payroll period.
        current_period_end_month: Date in month to extract UF from (e.g., end_date).
        market_data_repository: Read port for pf-rates-backed UF values. `None`
            disables the prediction entirely (returns None immediately).

    Returns:
        The predicted baseline for the next period, split into its
        scalable/fixed-UF components, or None if calculation fails.
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

    # Calculate predicted net pay, split into the salary_base-driven portion
    # (scalable_clp, what a later raise should actually scale) and the
    # UF-driven portion (fixed_uf_clp, what a later raise must leave alone
    # -- see PredictedNetPayBaseline's docstring). Quantize both to cents
    # *before* subtracting so net_pay_clp == scalable_clp - fixed_uf_clp
    # holds exactly, with no independent-rounding drift between the two.
    scalable_clp = (future_gross - future_non_uf_discounts).quantize(_NET_PAY_QUANT)
    fixed_uf_clp = future_uf_discounts.quantize(_NET_PAY_QUANT)
    predicted_net_pay = scalable_clp - fixed_uf_clp

    if predicted_net_pay <= 0:
        return None

    return PredictedNetPayBaseline(
        net_pay_clp=predicted_net_pay,
        scalable_clp=scalable_clp,
        fixed_uf_clp=fixed_uf_clp,
    )


async def _extrapolate_cycle_ratio(
    *,
    last_increase_period: date,
    last_increase_index: Decimal,
    latest_period: date,
    latest_value: Decimal,
    increase_frequency: int,
    market_data_repository: MarketDataRepository,
) -> Decimal:
    """Return the best-estimate ratio for the employer's full N-month cycle.

    `real_ratio` (`latest_value / last_increase_index`) only ever reflects
    the M months of IPC actually published since the last real increase --
    if the employer's configured cadence (`increase_frequency`, N months)
    is wider than that, M alone understates the full cycle's expected
    adjustment (see docs/proposals/future-increase-ipc-extrapolation-
    design-recommendation.md for the worked example this fixes: M=4, N=12
    reported as if M were the whole cycle).

    This extrapolates the remaining `N - M` months using the geometric mean
    monthly rate observed over the most recent N published months (a
    trailing window ending at `latest_period`, independent of
    `last_increase_period` -- the most representative proxy for "what
    inflation looks like right now"), then recompounds it on top of
    `real_ratio` for the full cycle: `real_ratio * (monthly_ratio **
    missing_months)`.

    Falls back to `real_ratio` alone -- i.e. today's pre-extrapolation
    behavior -- whenever there is nothing left to extrapolate (`M >= N`:
    the real data already covers, or exceeds, a full cycle, nothing to
    project and no new IPC fetch is even attempted) or the trailing-window
    IPC figure needed to estimate a monthly rate isn't available/valid.
    Mirrors every other degrade-to-best-available guard in
    `_apply_ipc_step()`: never raises on missing *data*, only propagates a
    genuine `PayrollDependencyError` from `get_economic_index_value()` on a
    real pf-rates outage, same as every other market-data call in this
    module.
    """
    real_ratio = latest_value / last_increase_index
    months_elapsed = (latest_period.year * 12 + latest_period.month) - (
        last_increase_period.year * 12 + last_increase_period.month
    )
    missing_months = increase_frequency - months_elapsed
    if missing_months <= 0:
        return real_ratio
    period_back = add_months(latest_period, -increase_frequency)
    trailing_anchor_value = await market_data_repository.get_economic_index_value(
        _IPC_CODE, period_back.year, period_back.month
    )
    if trailing_anchor_value is None or trailing_anchor_value <= 0:
        return real_ratio
    trailing_ratio = latest_value / trailing_anchor_value
    monthly_ratio = trailing_ratio ** (Decimal(1) / Decimal(increase_frequency))
    return real_ratio * (monthly_ratio**missing_months)


async def _apply_ipc_step(
    current_scalable: Decimal | None,
    *,
    last_increase_period: date | None,
    latest_index: tuple[date, Decimal] | None,
    increase_month: date,
    increase_frequency: int,
    market_data_repository: MarketDataRepository,
) -> tuple[Decimal | None, date | None, Decimal]:
    """Step `current_scalable` up by the extrapolated IPC ratio for a full cycle.

    `current_scalable` is the salary_base-driven portion of the running net
    pay baseline only (see PredictedNetPayBaseline.scalable_clp) -- the
    UF-driven portion is tracked and netted back in separately by the
    caller (project_future_months()), never scaled here. This keeps a
    salary raise from also inflating/deflating a UF-indexed discount that
    has nothing to do with it.

    There is no reliable IPC figure yet for `increase_month` itself (it is
    always in the future), so this uses the IPC published for
    `last_increase_period` (the last real/already-applied raise) as the
    baseline and the most recently published IPC figure overall as the
    best available stand-in for "today's" price level -- only when that
    latest figure's own period is not itself later than `increase_month`
    (it never should be, since IPC is only ever published for the past,
    but this is the explicit guard requested in
    docs/proposals/net-pay-prediction-reimplementation-design-plan.md).
    The ratio between those two points (M months of real data) is then
    extrapolated up to the employer's full configured cycle (N months) by
    `_extrapolate_cycle_ratio()` -- see its own docstring and
    docs/proposals/future-increase-ipc-extrapolation-design-recommendation.md
    for why M alone previously understated the real adjustment.

    Any missing ingredient (no prior real increase to anchor on, no IPC
    data at all, or a zero/negative baseline) degrades gracefully: the
    value is left unchanged, the anchor does not advance (so a later
    increase month in the same window still compares against the true
    last real increase instead of silently treating this skipped one as
    a 0% raise), and the reported increase_pct is 0.00 (no quantifiable
    change -- see ProjectedFutureMonth).

    Deflation floor: if the extrapolated cycle ratio is still *below* 1
    (a net decrease) after extrapolation, the previous net pay is kept
    as-is instead of being reduced -- salaries do not get cut due to
    deflation in practice, only held flat (explicit user requirement, see
    docs/proposals/net-pay-prediction-reimplementation-design-plan.md).
    Gated on the final, consolidated ratio rather than the raw M-month-only
    ratio (docs/proposals/future-increase-ipc-extrapolation-design-
    recommendation.md, Section 5) since that consolidated ratio is what
    actually drives both `net_pay_clp` and `increase_pct` going forward.
    Same non-advancing-anchor/0.00-percentage treatment as the other
    degradation paths above: a later increase month still compares against
    the true last real increase, so any eventual rebound in prices is not
    lost.

    `current_scalable` may be None (no first-month UF prediction to step --
    e.g. a pf-rates outage at that unrelated step): the increase_pct is
    still computed from IPC data alone when possible, it is only the
    stepped value that stays None, passed through unchanged.

    Returns:
        Tuple of (possibly stepped value or None, anchor to use for the
        *next* increase month encountered, increase_pct this step
        represents -- 0.00 when no real step was applied for any of the
        reasons above).
    """
    if last_increase_period is None or latest_index is None:
        return current_scalable, last_increase_period, _NO_INCREASE_PCT
    latest_period, latest_value = latest_index
    if latest_period > increase_month:
        return current_scalable, last_increase_period, _NO_INCREASE_PCT
    last_increase_index = await market_data_repository.get_economic_index_value(
        _IPC_CODE, last_increase_period.year, last_increase_period.month
    )
    if last_increase_index is None or last_increase_index <= 0:
        return current_scalable, last_increase_period, _NO_INCREASE_PCT
    total_ratio = await _extrapolate_cycle_ratio(
        last_increase_period=last_increase_period,
        last_increase_index=last_increase_index,
        latest_period=latest_period,
        latest_value=latest_value,
        increase_frequency=increase_frequency,
        market_data_repository=market_data_repository,
    )
    if total_ratio < 1:
        return current_scalable, last_increase_period, _NO_INCREASE_PCT
    increase_pct = quantize_percent((total_ratio - 1) * 100)
    stepped = (
        (current_scalable * total_ratio).quantize(_NET_PAY_QUANT)
        if current_scalable is not None
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
    first_future_baseline: PredictedNetPayBaseline | None,
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
    increase month is reached, at which point only the salary_base-driven
    portion (first_future_baseline.scalable_clp) is stepped up by
    cumulative IPC since the last real increase (see _apply_ipc_step) --
    the UF-driven portion (first_future_baseline.fixed_uf_clp) is netted
    back in unchanged every month, including stepped ones, since a salary
    raise has no bearing on a UF-indexed discount (see
    PredictedNetPayBaseline's own docstring for why). The stepped
    net_pay_clp becomes the new baseline replicated forward from there. In
    the rare case month_offset 1 itself lands on a scheduled increase
    month, its own displayed net_pay_clp still stays the raw UF prediction
    (by design, the two are different, equally valid estimation methods for
    the same event), but the internal running value used to seed
    month_offset 2 onward does pick up that step -- so the configured raise
    is never silently lost even in that edge case.

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
    first_future_net_pay_clp = (
        first_future_baseline.net_pay_clp if first_future_baseline is not None else None
    )
    if market_data_repository is None:
        return degraded_future_months(first_future_net_pay_clp)

    current_scalable = (
        first_future_baseline.scalable_clp
        if first_future_baseline is not None
        else None
    )
    fixed_uf_clp = (
        first_future_baseline.fixed_uf_clp
        if first_future_baseline is not None
        else Decimal("0")
    )
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
            (
                current_scalable,
                last_increase_period,
                increase_pct,
            ) = await _apply_ipc_step(
                current_scalable,
                last_increase_period=last_increase_period,
                latest_index=latest_index,
                increase_month=period_month,
                increase_frequency=increase_frequency,
                market_data_repository=market_data_repository,
            )
        current_net_pay = (
            (current_scalable - fixed_uf_clp) if current_scalable is not None else None
        )
        displayed_net_pay = (
            first_future_net_pay_clp if month_offset == 1 else current_net_pay
        )
        series[month_offset] = ProjectedFutureMonth(
            net_pay_clp=displayed_net_pay, increase_pct=increase_pct
        )
    return series


_FOREIGN_CURRENCY_CODES = ("USD", "EUR", "UF")


@dataclass(frozen=True, slots=True)
class CurrencyEquivalents:
    """A CLP amount expressed in USD, EUR, and UF for a given date.

    Each currency degrades independently to None -- see
    resolve_currency_equivalents() for exactly when and why.
    """

    usd: Decimal | None
    eur: Decimal | None
    uf: Decimal | None


_NO_CURRENCY_EQUIVALENTS = CurrencyEquivalents(usd=None, eur=None, uf=None)


def _convert_to_currency(
    amount_clp: Decimal, rate_or_error: Decimal | None | BaseException
) -> Decimal | None:
    """Convert amount_clp using rate_or_error, degrading any failure to None.

    `rate_or_error` comes straight out of an `asyncio.gather(...,
    return_exceptions=True)` slot: it is either the resolved Decimal rate,
    None (pf-rates has no rate for that currency/date), or a raised
    exception object (e.g. PayrollDependencyError on a pf-rates outage).
    `isinstance(..., Decimal)` filters out both non-Decimal cases in one
    guard; a non-positive rate is also rejected defensively even though
    pf-rates should never publish one.
    """
    if not isinstance(rate_or_error, Decimal) or rate_or_error <= 0:
        return None
    return quantize_currency_amount(amount_clp / rate_or_error)


async def resolve_currency_equivalents(
    net_pay_clp: Decimal | None,
    *,
    rate_date: date,
    market_data_repository: MarketDataRepository | None,
) -> CurrencyEquivalents:
    """Convert net_pay_clp into USD/EUR/UF equivalents for rate_date.

    Each of the three currencies degrades independently to None: no
    market_data_repository wired in at all, nothing to convert (net_pay_clp
    itself is None -- e.g. an inferred period with no declared pay), that
    specific currency's rate for rate_date is simply not published by
    pf-rates, or pf-rates raised (PayrollDependencyError or otherwise).

    Unlike predict_next_period_net_pay()/project_future_months(), this
    helper never raises -- it is a 3-way best-effort enrichment where each
    currency is independent of the other two, not a single pipeline that
    benefits from letting list_period_ranges() be the one try/except
    boundary. The three lookups run concurrently (`asyncio.gather`) since
    list_period_ranges() calls this once per previous/current period in
    the window (up to 13 times), and sequential currency-by-currency,
    period-by-period calls would otherwise serialize dozens of independent
    HTTP round trips to pf-rates.
    """
    if net_pay_clp is None or market_data_repository is None:
        return _NO_CURRENCY_EQUIVALENTS

    usd_rate, eur_rate, uf_rate = await asyncio.gather(
        *(
            market_data_repository.get_exchange_rate_value(code, rate_date)
            for code in _FOREIGN_CURRENCY_CODES
        ),
        return_exceptions=True,
    )
    return CurrencyEquivalents(
        usd=_convert_to_currency(net_pay_clp, usd_rate),
        eur=_convert_to_currency(net_pay_clp, eur_rate),
        uf=_convert_to_currency(net_pay_clp, uf_rate),
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
