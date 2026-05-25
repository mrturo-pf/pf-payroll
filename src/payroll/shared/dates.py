"""Shared date helpers."""

from datetime import date, timedelta

import holidays


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
