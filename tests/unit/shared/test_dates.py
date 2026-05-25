"""Tests for shared date helpers."""

from datetime import date

from payroll.shared.dates import (
    last_business_day_of_month,
    last_day_of_month,
    resolve_effective_payment_date,
    resolve_payment_date,
)


def test_last_day_of_month_handles_regular_and_december_months() -> None:
    """Test last-day helper for regular and December months."""
    assert last_day_of_month(date(2026, 4, 29)) == date(2026, 4, 30)
    assert last_day_of_month(date(2026, 12, 2)) == date(2026, 12, 31)


def test_last_business_day_of_month_skips_weekends() -> None:
    """Test last business day skips weekends for Chilean calendars."""
    assert last_business_day_of_month(date(2026, 5, 1)) == date(2026, 5, 29)


def test_resolve_payment_date_supports_supported_rules() -> None:
    """Test payment-date resolver handles all supported rule kinds."""
    assert resolve_payment_date(2026, 5) == date(2026, 5, 29)
    assert resolve_payment_date(
        2026,
        5,
        payment_business_day_offset=1,
    ) == date(2026, 5, 28)
    assert resolve_payment_date(
        2026,
        6,
        payment_date_rule="fixed_day_of_month",
        payment_day_of_month=28,
        payment_fixed_day_roll="previous_business_day",
    ) == date(2026, 6, 26)
    assert resolve_payment_date(
        2026,
        12,
        payment_date_rule="calendar_days_before_end_of_month",
        payment_calendar_day_offset=7,
    ) == date(2026, 12, 24)


def test_resolve_effective_payment_date_can_shift_monday_settlement_to_saturday() -> (
    None
):
    """Test effective payment dates can settle on Saturday after Friday processing."""
    assert resolve_effective_payment_date(date(2026, 3, 30)) == date(2026, 3, 30)
    assert resolve_effective_payment_date(
        date(2026, 3, 30),
        payment_effective_on_processing_next_day=True,
    ) == date(2026, 3, 28)


def test_resolve_payment_date_can_roll_fixed_day_forward() -> None:
    """Test fixed-day payment rules can roll forward to the next business day."""
    assert resolve_payment_date(
        2026,
        6,
        payment_date_rule="fixed_day_of_month",
        payment_day_of_month=28,
        payment_fixed_day_roll="next_business_day",
    ) == date(2026, 6, 30)


def test_resolve_payment_date_can_apply_processing_next_day_rule() -> None:
    """Test payment-date resolver applies effective settlement rules when enabled."""
    assert resolve_payment_date(
        2026,
        3,
        payment_business_day_offset=1,
        payment_effective_on_processing_next_day=True,
    ) == date(2026, 3, 28)
