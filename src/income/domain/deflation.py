"""Domain service for nominal-to-real amount deflation."""

from dataclasses import dataclass
from decimal import Decimal

from income.domain.quantizers import quantize_clp


@dataclass(frozen=True, slots=True)
class DeflationCalculator:
    """Calculate real amounts from nominal amounts and index values."""

    def deflate_amount(
        self, nominal_clp: Decimal, source_index: Decimal, target_index: Decimal
    ) -> Decimal:
        """Deflate a CLP amount and quantize it to whole pesos."""
        return quantize_clp(nominal_clp * target_index / source_index)
