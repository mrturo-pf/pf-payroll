"""Tests for shared date helpers."""

from datetime import date

from payroll.shared.dates import last_day_of_month


def test_last_day_of_month_handles_regular_and_december_months() -> None:
    """Test last-day helper for regular and December months."""
    assert last_day_of_month(date(2026, 4, 29)) == date(2026, 4, 30)
    assert last_day_of_month(date(2026, 12, 2)) == date(2026, 12, 31)
