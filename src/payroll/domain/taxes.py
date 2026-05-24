"""Tax-related domain models."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class IncomeTaxBracket:
    """Represent Income Tax Bracket."""

    valid_from: date
    valid_to: date | None
    lower_bound_utm: Decimal
    upper_bound_utm: Decimal | None
    marginal_rate: Decimal
    rebate_utm: Decimal


@dataclass(frozen=True, slots=True)
class IncomeTaxComputation:
    """Represent Income Tax Computation."""

    taxable_income_clp: Decimal
    deductible_amount_clp: Decimal
    taxable_base_clp: Decimal
    utm_value_clp: Decimal
    taxable_base_utm: Decimal
    bracket_lower_bound_utm: Decimal
    bracket_upper_bound_utm: Decimal | None
    marginal_rate: Decimal
    rebate_utm: Decimal
    tax_utm: Decimal
    tax_clp: Decimal
