"""Domain service for nominal-to-real amount deflation."""

from dataclasses import dataclass
from decimal import Decimal

from payroll.domain.contribution_calculator import quantize_clp


@dataclass(frozen=True, slots=True)
class DeflationCalculator:
    def deflate_amount(self, nominal_clp: Decimal, source_index: Decimal, target_index: Decimal) -> Decimal:
        return quantize_clp(nominal_clp * target_index / source_index)
