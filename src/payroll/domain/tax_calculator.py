"""Domain service for Chilean monthly income tax calculations."""

from dataclasses import dataclass
from decimal import Decimal

from payroll.domain.contribution_calculator import quantize_clp
from payroll.domain.taxes import IncomeTaxBracket, IncomeTaxComputation

UTM_QUANT = Decimal("0.000001")


def quantize_utm(value: Decimal) -> Decimal:
    """Quantize utm."""
    return value.quantize(UTM_QUANT)


@dataclass(frozen=True, slots=True)
class ChileanTaxCalculator:
    """Provide chilean tax calculator."""

    def income_tax(
        self,
        taxable_income_clp: Decimal,
        deductible_amount_clp: Decimal,
        bracket: IncomeTaxBracket,
        utm_value_clp: Decimal,
    ) -> IncomeTaxComputation:
        """Handle income tax."""
        taxable_base_clp = max(Decimal("0"), taxable_income_clp - deductible_amount_clp)
        taxable_base_utm = (
            quantize_utm(taxable_base_clp / utm_value_clp)
            if utm_value_clp > 0
            else Decimal("0")
        )
        tax_utm = max(
            Decimal("0"),
            quantize_utm(
                (taxable_base_utm * bracket.marginal_rate) - bracket.rebate_utm
            ),
        )
        tax_clp = quantize_clp(tax_utm * utm_value_clp)

        return IncomeTaxComputation(
            taxable_income_clp=taxable_income_clp,
            deductible_amount_clp=deductible_amount_clp,
            taxable_base_clp=taxable_base_clp,
            utm_value_clp=utm_value_clp,
            taxable_base_utm=taxable_base_utm,
            bracket_lower_bound_utm=bracket.lower_bound_utm,
            bracket_upper_bound_utm=bracket.upper_bound_utm,
            marginal_rate=bracket.marginal_rate,
            rebate_utm=bracket.rebate_utm,
            tax_utm=tax_utm,
            tax_clp=tax_clp,
        )
