"""Day-level proration for health plans that only partially cover a period.

Extracted from ContributionCalculator.health() as its own pure function
(single responsibility, easy to unit-test every boundary in isolation)
rather than inlined there. Domain-only stdlib deps (date/calendar), no I/O.
"""

import calendar
from datetime import date
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
