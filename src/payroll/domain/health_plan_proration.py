"""Day-level proration for health plans that only partially cover a period.

Extracted from ContributionCalculator.health() as its own pure function
(single responsibility, easy to unit-test every boundary in isolation)
rather than inlined there. Domain-only stdlib deps (date/calendar), no I/O.
"""

import calendar
from datetime import date, timedelta
from decimal import Decimal

from payroll.domain.contributions import HealthPlan


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    """Return the first and last calendar day of the given year/month."""
    days_in_month = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, days_in_month)


def prorated_contracted_uf(
    plans: list[HealthPlan], period_year: int, period_month: int
) -> Decimal:
    """Sum every plan's contracted_uf, prorated by its overlap with the period.

    A plan valid for the entire calendar month contributes its full
    contracted_uf, exactly like before this function existed. A plan whose
    `valid_from`/`valid_to` falls inside the month -- a real-world mid-month
    Isapre plan change (new enrollment, upgrade, downgrade) -- contributes
    only the fraction of the month it was actually in force, day for day.

    This replaces the previous all-or-nothing behavior, where a plan not
    yet valid on the 1st of the month was excluded entirely even if it took
    effect on day 2, and a plan that expired mid-month either contributed
    its full UF value or none depending on which single day was checked.
    Neither extreme can reproduce a genuine partial-month declared amount
    (see docs/investigations/health-additional-uf-mismatch.md) -- this can.

    A plan with zero day-overlap with the period contributes nothing (not
    an error): callers are expected to have already filtered the assigned
    plan list to plans that overlap the period at all, but this function
    stays defensive so a stray non-overlapping plan degrades to "ignored"
    instead of raising.
    """
    period_start, period_end = _month_bounds(period_year, period_month)
    days_in_period = (period_end - period_start).days + 1

    total = Decimal("0")
    for plan in plans:
        overlap_start = max(period_start, plan.valid_from)
        overlap_end = min(period_end, plan.valid_to or period_end)
        if overlap_start > overlap_end:
            continue
        overlap_days = (overlap_end - overlap_start).days + 1
        total += plan.contracted_uf * Decimal(overlap_days) / Decimal(days_in_period)
    return total


def _plan_covers_day(plan: HealthPlan, day: date) -> bool:
    """Return whether a plan is in force on a given calendar day."""
    return plan.valid_from <= day and (plan.valid_to is None or day <= plan.valid_to)


def _sub_period_boundaries(
    plans: list[HealthPlan], period_start: date, period_end: date
) -> list[date]:
    """Split [period_start, period_end] into runs of constant plan composition.

    Every date where some plan's coverage starts, or the day right after
    some plan's coverage ends (both clipped to the period), is a boundary.
    Between two consecutive boundaries the set of plans in force never
    changes, so each resulting sub-period can be priced with one fixed
    combination of plans -- see prorated_additional_amount_clp().
    """
    boundaries = {period_start, period_end + timedelta(days=1)}
    for plan in plans:
        start = max(period_start, plan.valid_from)
        end = min(period_end, plan.valid_to or period_end)
        if start > end:
            continue
        boundaries.add(start)
        day_after_end = end + timedelta(days=1)
        if day_after_end <= period_end:
            boundaries.add(day_after_end)
    return sorted(boundaries)


def prorated_additional_amount_clp(
    plans: list[HealthPlan],
    period_year: int,
    period_month: int,
    base_amount_clp: Decimal,
    plan_uf_value_clp: Decimal,
) -> Decimal:
    """Compute the Isapre top-up ("adicional"), handling mid-month changes.

    Prorating the *combined* contracted_uf for the whole month and then
    subtracting one fixed `base_amount_clp` once (see prorated_contracted_uf())
    works fine as long as the total contracted cost stays on the same side
    of `base_amount_clp` for the entire month. It breaks down the moment a
    plan change happens mid-month and the combined cost crosses that
    threshold partway through: since the threshold itself is never
    prorated, a single day's difference in `valid_from` can swing the
    result by a full day's worth of UF instead of scaling smoothly with the
    days actually enrolled (see "Session 11" in
    docs/investigations/health-additional-uf-mismatch.md for the concrete
    case that exposed this).

    This prices the top-up separately for every distinct sub-period of
    constant plan composition within the month, prorating *both* the plan
    cost and the mandatory-minimum threshold by that sub-period's own day
    count, and only then applies `max(0, ...)` -- once per sub-period,
    never once for the whole month. When there is no mid-month change (the
    common case, a single sub-period spanning the whole month), this
    reduces to exactly the same result as the naive whole-month approach.
    """
    period_start, period_end = _month_bounds(period_year, period_month)
    days_in_period = (period_end - period_start).days + 1
    boundaries = _sub_period_boundaries(plans, period_start, period_end)

    total_excess = Decimal("0")
    for start, next_start in zip(boundaries, boundaries[1:]):
        sub_days = (next_start - start).days
        active_uf = sum(
            (plan.contracted_uf for plan in plans if _plan_covers_day(plan, start)),
            Decimal("0"),
        )
        share = Decimal(sub_days) / Decimal(days_in_period)
        sub_contracted_clp = active_uf * share * plan_uf_value_clp
        sub_base_clp = base_amount_clp * share
        total_excess += max(Decimal("0"), sub_contracted_clp - sub_base_clp)
    return total_excess
