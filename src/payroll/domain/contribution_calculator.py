"""Domain service for contribution calculations."""

from dataclasses import dataclass
from decimal import Decimal

from payroll.domain.contributions import ContributionCap

CLP_QUANT = Decimal("1")


def quantize_clp(value: Decimal) -> Decimal:
    return value.quantize(CLP_QUANT)


@dataclass(frozen=True, slots=True)
class ContributionCalculator:
    def pension_base(self, taxable_clp: Decimal, cap: ContributionCap, uf_value_clp: Decimal) -> Decimal:
        cap_clp = quantize_clp(cap.value_uf * uf_value_clp)
        return min(taxable_clp, cap_clp)
