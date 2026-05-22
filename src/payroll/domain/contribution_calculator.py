"""Domain service for contribution calculations."""

from dataclasses import dataclass
from decimal import Decimal

from payroll.domain.contributions import (
    ContributionCap,
    HealthContribution,
    HealthInstitutionKind,
    HealthPlan,
    PensionContribution,
    PensionPlan,
)

CLP_QUANT = Decimal("1")


def quantize_clp(value: Decimal) -> Decimal:
    return value.quantize(CLP_QUANT)


@dataclass(frozen=True, slots=True)
class ContributionCalculator:
    def pension(
        self,
        taxable_clp: Decimal,
        plan: PensionPlan,
        cap: ContributionCap,
        uf_value_clp: Decimal,
    ) -> PensionContribution:
        cap_clp = quantize_clp(cap.value_uf * uf_value_clp)
        capped_base = min(taxable_clp, cap_clp)

        base_amount = quantize_clp(capped_base * plan.institution.mandatory_rate)
        additional_amount = quantize_clp(capped_base * plan.additional_rate)

        return PensionContribution(
            institution_code=plan.institution.code,
            taxable_clp=taxable_clp,
            cap_clp=cap_clp,
            capped_base_clp=capped_base,
            base_amount_clp=base_amount,
            additional_amount_clp=additional_amount,
        )

    def health(
        self,
        taxable_clp: Decimal,
        plan: HealthPlan,
        cap: ContributionCap,
        uf_value_clp: Decimal,
    ) -> HealthContribution:
        cap_clp = quantize_clp(cap.value_uf * uf_value_clp)
        capped_base = min(taxable_clp, cap_clp)

        base_amount = quantize_clp(capped_base * plan.institution.mandatory_rate)

        if plan.institution.kind is HealthInstitutionKind.ISAPRE and plan.contracted_uf > 0:
            contracted_clp = quantize_clp(plan.contracted_uf * uf_value_clp)
            additional_amount = max(Decimal("0"), contracted_clp - base_amount)
        else:
            contracted_clp = Decimal("0")
            additional_amount = Decimal("0")

        return HealthContribution(
            institution_code=plan.institution.code,
            institution_kind=plan.institution.kind,
            taxable_clp=taxable_clp,
            cap_clp=cap_clp,
            capped_base_clp=capped_base,
            base_amount_clp=base_amount,
            contracted_uf=plan.contracted_uf,
            contracted_clp=contracted_clp,
            additional_amount_clp=additional_amount,
        )

    def pension_base(self, taxable_clp: Decimal, cap: ContributionCap, uf_value_clp: Decimal) -> Decimal:
        cap_clp = quantize_clp(cap.value_uf * uf_value_clp)
        return min(taxable_clp, cap_clp)
