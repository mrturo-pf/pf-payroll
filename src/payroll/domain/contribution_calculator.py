"""Domain service for contribution calculations."""

from dataclasses import dataclass
from decimal import Decimal

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
from payroll.domain.errors import (
    DomainValidationError,
    UnsupportedEmploymentContractKindError,
)
from payroll.domain.health_plan_proration import prorated_contracted_uf
from payroll.domain.quantizers import quantize_clp


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
        plans: list[HealthPlan],
        period_year: int,
        period_month: int,
        cap: ContributionCap,
        cap_uf_value_clp: Decimal,
        plan_uf_value_clp: Decimal,
    ) -> HealthContribution:
        """Handle health.

        `plans` is every health plan assigned to this period, not a single
        pre-aggregated one -- the caller (get_contribution_context()) hands
        over each plan's own valid_from/valid_to so this function can
        prorate a mid-month plan change day by day instead of an
        all-or-nothing sum (see prorated_contracted_uf()). All plans in the
        list are assumed to share the same institution -- the caller
        validates that invariant before this is ever called.
        """
        if not plans:
            raise DomainValidationError(
                "ContributionCalculator.health() requires at least one "
                "assigned health plan."
            )
        institution = plans[0].institution
        cap_clp, capped_base = self._capped_base(taxable_clp, cap, cap_uf_value_clp)

        base_amount = quantize_clp(capped_base * institution.mandatory_rate)
        contracted_uf = prorated_contracted_uf(plans, period_year, period_month)

        if institution.kind is HealthInstitutionKind.ISAPRE and contracted_uf > 0:
            contracted_clp = quantize_clp(contracted_uf * plan_uf_value_clp)
            additional_amount = max(Decimal("0"), contracted_clp - base_amount)
        else:
            contracted_clp = Decimal("0")
            additional_amount = Decimal("0")

        return HealthContribution(
            institution_code=institution.code,
            institution_kind=institution.kind,
            taxable_clp=taxable_clp,
            cap_clp=cap_clp,
            capped_base_clp=capped_base,
            base_amount_clp=base_amount,
            contracted_uf=contracted_uf,
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

    def pension_base(
        self, taxable_clp: Decimal, cap: ContributionCap, uf_value_clp: Decimal
    ) -> Decimal:
        """Handle pension base."""
        _, capped_base = self._capped_base(taxable_clp, cap, uf_value_clp)
        return capped_base
