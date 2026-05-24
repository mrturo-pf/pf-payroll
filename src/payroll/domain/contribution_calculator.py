"""Domain service for contribution calculations."""

from dataclasses import dataclass
from decimal import Decimal

from payroll.domain.errors import UnsupportedEmploymentContractKindError
from payroll.domain.contributions import (
    ContributionCap,
    EmploymentContractKind,
    HealthContribution,
    HealthInstitutionKind,
    HealthPlan,
    PensionContribution,
    PensionPlan,
    UnemploymentContribution,
)

CLP_QUANT = Decimal("1")


def quantize_clp(value: Decimal) -> Decimal:
    """Quantize clp."""
    return value.quantize(CLP_QUANT)


@dataclass(frozen=True, slots=True)
class ContributionCalculator:
    """Provide contribution calculator."""

    def _capped_base(
        self,
        taxable_clp: Decimal,
        cap: ContributionCap,
        uf_value_clp: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """Handle capped base."""
        cap_clp = quantize_clp(cap.value_uf * uf_value_clp)
        return cap_clp, min(taxable_clp, cap_clp)

    def pension(
        self,
        taxable_clp: Decimal,
        plan: PensionPlan,
        cap: ContributionCap,
        uf_value_clp: Decimal,
    ) -> PensionContribution:
        """Handle pension."""
        cap_clp, capped_base = self._capped_base(taxable_clp, cap, uf_value_clp)

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
        """Handle health."""
        cap_clp, capped_base = self._capped_base(taxable_clp, cap, uf_value_clp)

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

    def unemployment(
        self,
        taxable_clp: Decimal,
        contract_kind: EmploymentContractKind,
        cap: ContributionCap,
        uf_value_clp: Decimal,
    ) -> UnemploymentContribution:
        """Handle unemployment."""
        cap_clp, capped_base = self._capped_base(taxable_clp, cap, uf_value_clp)
        if contract_kind is EmploymentContractKind.INDEFINITE:
            employee_rate = Decimal("0.006")
            employer_rate = Decimal("0.024")
        elif contract_kind is EmploymentContractKind.FIXED_TERM:
            employee_rate = Decimal("0")
            employer_rate = Decimal("0.03")
        else:
            raise UnsupportedEmploymentContractKindError(
                f"Unsupported employment contract kind: {contract_kind.value}"
            )

        return UnemploymentContribution(
            contract_kind=contract_kind,
            taxable_clp=taxable_clp,
            cap_clp=cap_clp,
            capped_base_clp=capped_base,
            employee_rate=employee_rate,
            employee_amount_clp=quantize_clp(capped_base * employee_rate),
            employer_rate=employer_rate,
            employer_amount_clp=quantize_clp(capped_base * employer_rate),
        )

    def pension_base(self, taxable_clp: Decimal, cap: ContributionCap, uf_value_clp: Decimal) -> Decimal:
        """Handle pension base."""
        _, capped_base = self._capped_base(taxable_clp, cap, uf_value_clp)
        return capped_base
