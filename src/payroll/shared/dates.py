"""Shared date helpers."""

from datetime import date, timedelta


def last_day_of_month(value: date) -> date:
    """Return the last day of the month for the provided date."""
    if value.month == 12:
        return date(value.year, 12, 31)
    return date(value.year, value.month + 1, 1) - timedelta(days=1)
