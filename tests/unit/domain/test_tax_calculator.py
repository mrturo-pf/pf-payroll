from datetime import date
from decimal import Decimal

from payroll.domain.tax_calculator import ChileanTaxCalculator, quantize_utm
from payroll.domain.taxes import IncomeTaxBracket


def test_income_tax_calculator_applies_progressive_bracket() -> None:
    calculator = ChileanTaxCalculator()
    result = calculator.income_tax(
        taxable_income_clp=Decimal("1000000"),
        deductible_amount_clp=Decimal("170000"),
        bracket=IncomeTaxBracket(
            valid_from=date(2026, 1, 1),
            valid_to=None,
            lower_bound_utm=Decimal("13.5000"),
            upper_bound_utm=Decimal("30.0000"),
            marginal_rate=Decimal("0.040000"),
            rebate_utm=Decimal("0.5400"),
        ),
        utm_value_clp=Decimal("67000"),
    )

    assert quantize_utm(Decimal("12.3456789")) == Decimal("12.345679")
    assert result.taxable_base_clp == Decimal("830000")
    assert result.taxable_base_utm == Decimal("12.388060")
    assert result.tax_utm == Decimal("0")
    assert result.tax_clp == Decimal("0")


def test_income_tax_calculator_generates_positive_tax_when_base_exceeds_threshold() -> None:
    calculator = ChileanTaxCalculator()
    result = calculator.income_tax(
        taxable_income_clp=Decimal("2500000"),
        deductible_amount_clp=Decimal("200000"),
        bracket=IncomeTaxBracket(
            valid_from=date(2026, 1, 1),
            valid_to=None,
            lower_bound_utm=Decimal("30.0000"),
            upper_bound_utm=Decimal("50.0000"),
            marginal_rate=Decimal("0.080000"),
            rebate_utm=Decimal("1.7400"),
        ),
        utm_value_clp=Decimal("67000"),
    )

    assert result.taxable_base_utm == Decimal("34.328358")
    assert result.tax_utm == Decimal("1.006269")
    assert result.tax_clp == Decimal("67420")
