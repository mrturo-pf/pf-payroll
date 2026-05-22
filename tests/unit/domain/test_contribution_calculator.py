from datetime import date
from decimal import Decimal

from payroll.domain.contribution_calculator import ContributionCalculator
from payroll.domain.contributions import ContributionCap


def test_pension_base_respects_cap() -> None:
    calculator = ContributionCalculator()
    cap = ContributionCap("pension_health", date(2026, 1, 1), None, Decimal("90.0000"))

    assert calculator.pension_base(Decimal("1000000"), cap, Decimal("10000")) == Decimal("900000")
