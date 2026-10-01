"""Shared date helpers."""

from datetime import date, timedelta

import holidays


def is_increase_period(
    *,
    period_year: int,
    period_month: int,
    first_increase_period: date,
    increase_frequency: int,
) -> bool:
    """Return whether the provided period matches the increase cadence.

    Moved here (from a repository staticmethod) so both the period-range
    listing and the future net_pay projection can share the exact same
    cadence check without duplicating it -- see
    docs/proposals/net-pay-prediction-reimplementation-design-plan.md.
    """
    period_month_index = (period_year * 12) + period_month
    first_increase_index = (
        first_increase_period.year * 12
    ) + first_increase_period.month
    delta_months = period_month_index - first_increase_index
    return delta_months >= 0 and delta_months % increase_frequency == 0


def resolve_last_increase_period(
    *,
    first_increase_period: date,
    increase_frequency: int,
    as_of: date,
) -> date | None:
    """Return the most recent increase-cadence month at or before `as_of`.

    This is the backward-looking counterpart to `is_increase_period()`: it
    answers "what was the last real salary increase, as of this period",
    used as the IPC baseline when later stepping a replicated future
    prediction up at the next increase-cadence month. Returns None when
    `as_of` predates `first_increase_period` entirely -- no increase has
    happened yet under this employer's configured cadence, so there is no
    baseline to compare against.
    """
    delta_months = (as_of.year - first_increase_period.year) * 12 + (
        as_of.month - first_increase_period.month
    )
    if delta_months < 0:
        return None
    steps = delta_months // increase_frequency
    return add_months(first_increase_period, steps * increase_frequency)


def last_day_of_month(value: date) -> date:
    """Return the last day of the month for the provided date."""
    if value.month == 12:
        return date(value.year, 12, 31)
    return date(value.year, value.month + 1, 1) - timedelta(days=1)


def add_months(value: date, months: int) -> date:
    """Return the first day of the month shifted by the requested months."""
    total_months = (value.year * 12) + (value.month - 1) + months
    year = total_months // 12
    month = (total_months % 12) + 1
    return date(year, month, 1)


def is_business_day(value: date, *, country_code: str = "CL") -> bool:
    """Return whether the date is a business day for the given country."""
    holiday_calendar = holidays.country_holidays(country_code, years=[value.year])
    return value.weekday() < 5 and value not in holiday_calendar


def previous_business_day(value: date, *, country_code: str = "CL") -> date:
    """Return the closest business day on or before the provided date."""
    current = value
    while not is_business_day(current, country_code=country_code):
        current -= timedelta(days=1)
    return current


def next_business_day(value: date, *, country_code: str = "CL") -> date:
    """Return the closest business day on or after the provided date."""
    current = value
    while not is_business_day(current, country_code=country_code):
        current += timedelta(days=1)
    return current


def last_business_day_of_month(value: date, *, country_code: str = "CL") -> date:
    """Return the last business day of the month for the provided date."""
    return previous_business_day(last_day_of_month(value), country_code=country_code)


def resolve_effective_payment_date(
    scheduled_payment_date: date,
    *,
    country_code: str = "CL",
    payment_effective_on_processing_next_day: bool = False,
) -> date:
    """Return the effective payment date after optional prior-day processing."""
    if not payment_effective_on_processing_next_day:
        return scheduled_payment_date
    processing_date = previous_business_day(
        scheduled_payment_date - timedelta(days=1),
        country_code=country_code,
    )
    return processing_date + timedelta(days=1)


def resolve_payment_date(
    period_year: int,
    period_month: int,
    *,
    country_code: str = "CL",
    payment_date_rule: str = "last_business_day_of_month",
    payment_month_offset: int = 0,
    payment_day_of_month: int | None = None,
    payment_business_day_offset: int = 0,
    payment_calendar_day_offset: int = 0,
    payment_effective_on_processing_next_day: bool = False,
    payment_fixed_day_roll: str = "previous_business_day",
) -> date:
    """Resolve the employer payment date for a remuneration month."""
    target_month = add_months(date(period_year, period_month, 1), payment_month_offset)
    if payment_date_rule == "fixed_day_of_month":
        day = payment_day_of_month or 1
        candidate = date(
            target_month.year,
            target_month.month,
            min(day, last_day_of_month(target_month).day),
        )
        scheduled_payment_date = (
            next_business_day(candidate, country_code=country_code)
            if payment_fixed_day_roll == "next_business_day"
            else previous_business_day(candidate, country_code=country_code)
        )
        return resolve_effective_payment_date(
            scheduled_payment_date,
            country_code=country_code,
            payment_effective_on_processing_next_day=(
                payment_effective_on_processing_next_day
            ),
        )

    if payment_date_rule == "calendar_days_before_end_of_month":
        scheduled_payment_date = last_day_of_month(target_month) - timedelta(
            days=payment_calendar_day_offset
        )
        return resolve_effective_payment_date(
            scheduled_payment_date,
            country_code=country_code,
            payment_effective_on_processing_next_day=(
                payment_effective_on_processing_next_day
            ),
        )

    scheduled_payment_date = last_business_day_of_month(
        target_month,
        country_code=country_code,
    )
    for _ in range(payment_business_day_offset):
        scheduled_payment_date = previous_business_day(
            scheduled_payment_date - timedelta(days=1),
            country_code=country_code,
        )
    return resolve_effective_payment_date(
        scheduled_payment_date,
        country_code=country_code,
        payment_effective_on_processing_next_day=(
            payment_effective_on_processing_next_day
        ),
    )
