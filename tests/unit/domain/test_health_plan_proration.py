"""Tests for domain health plan proration."""

from datetime import date
from decimal import Decimal

from payroll.domain.contributions import (
    HealthInstitution,
    HealthInstitutionKind,
    HealthPlan,
)
from payroll.domain.health_plan_proration import prorated_contracted_uf

_INSTITUTION = HealthInstitution(
    code="BANMEDICA",
    name="Banmedica",
    kind=HealthInstitutionKind.ISAPRE,
    mandatory_rate=Decimal("0.07"),
)


def _plan(
    *, plan_id: int, valid_from: date, valid_to: date | None, contracted_uf: Decimal
) -> HealthPlan:
    return HealthPlan(
        id=plan_id,
        institution=_INSTITUTION,
        valid_from=valid_from,
        valid_to=valid_to,
        plan_name="Plan",
        contracted_uf=contracted_uf,
    )


def test_prorated_contracted_uf_returns_zero_for_empty_plan_list() -> None:
    """Test prorated contracted uf returns zero for empty plan list."""
    assert prorated_contracted_uf([], 2026, 1) == Decimal("0")


def test_prorated_contracted_uf_uses_full_amount_for_full_month_plan() -> None:
    """Test a plan valid the whole month contributes its full contracted_uf."""
    plan = _plan(
        plan_id=1,
        valid_from=date(2024, 11, 1),
        valid_to=None,
        contracted_uf=Decimal("1.70"),
    )
    assert prorated_contracted_uf([plan], 2025, 2) == Decimal("1.70")


def test_prorated_contracted_uf_prorates_plan_starting_mid_month() -> None:
    """Test a plan that only starts partway through the month is prorated.

    February 2025 has 28 days. A plan effective from Feb 20th onward covers
    9 of those 28 days (20th through 28th, inclusive).
    """
    plan = _plan(
        plan_id=1,
        valid_from=date(2025, 2, 20),
        valid_to=None,
        contracted_uf=Decimal("2.80"),
    )
    expected = Decimal("2.80") * Decimal(9) / Decimal(28)
    assert prorated_contracted_uf([plan], 2025, 2) == expected


def test_prorated_contracted_uf_prorates_plan_ending_mid_month() -> None:
    """Test a plan that expires partway through the month is prorated.

    2026-01 has 31 days. A plan valid only through Jan 10th covers the
    first 10 days.
    """
    plan = _plan(
        plan_id=1,
        valid_from=date(2025, 1, 1),
        valid_to=date(2026, 1, 10),
        contracted_uf=Decimal("3.10"),
    )
    expected = Decimal("3.10") * Decimal(10) / Decimal(31)
    assert prorated_contracted_uf([plan], 2026, 1) == expected


def test_prorated_contracted_uf_ignores_plan_with_no_overlap() -> None:
    """Test a plan entirely outside the requested month contributes nothing."""
    plan = _plan(
        plan_id=1,
        valid_from=date(2025, 3, 1),
        valid_to=None,
        contracted_uf=Decimal("4.94"),
    )
    assert prorated_contracted_uf([plan], 2025, 2) == Decimal("0")


def test_prorated_contracted_uf_sums_multiple_overlapping_plans() -> None:
    """Test multiple plans each contribute their own prorated share, summed."""
    ges = _plan(
        plan_id=1,
        valid_from=date(2024, 11, 1),
        valid_to=None,
        contracted_uf=Decimal("0.91"),
    )
    additional = _plan(
        plan_id=2,
        valid_from=date(2024, 11, 1),
        valid_to=None,
        contracted_uf=Decimal("0.79"),
    )
    base_partial = _plan(
        plan_id=3,
        valid_from=date(2025, 2, 20),
        valid_to=None,
        contracted_uf=Decimal("4.94"),
    )
    total = prorated_contracted_uf([ges, additional, base_partial], 2025, 2)
    expected = (
        Decimal("0.91") + Decimal("0.79") + (Decimal("4.94") * Decimal(9) / Decimal(28))
    )
    assert total == expected
