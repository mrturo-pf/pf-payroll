"""Tests for shared income-domain capabilities."""

from decimal import Decimal

from income.domain.deflation import DeflationCalculator
from income.domain.quantizers import quantize_clp, quantize_percent


def test_quantizers_are_available_from_income_package() -> None:
    """Quantizers expose shared financial precision rules."""
    assert quantize_clp(Decimal("123.6")) == Decimal("124")
    assert quantize_percent(Decimal("12.346")) == Decimal("12.35")


def test_deflation_is_independent_of_payroll_persistence() -> None:
    """Deflation uses only amounts and indexes, not payroll infrastructure."""
    result = DeflationCalculator().deflate_amount(
        nominal_clp=Decimal("830000"),
        source_index=Decimal("100.000000"),
        target_index=Decimal("112.340000"),
    )

    assert result == Decimal("932422")
