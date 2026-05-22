from decimal import Decimal

from payroll.domain.deflation import DeflationCalculator


def test_deflation_calculator_converts_nominal_into_real_clp() -> None:
    result = DeflationCalculator().deflate_amount(
        nominal_clp=Decimal("830000"),
        source_index=Decimal("100.000000"),
        target_index=Decimal("112.340000"),
    )

    assert result == Decimal("932422")
