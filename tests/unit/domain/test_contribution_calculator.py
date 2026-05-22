from datetime import date
from decimal import Decimal

from payroll.domain.contribution_calculator import ContributionCalculator
from payroll.domain.contributions import (
    ContributionCap,
    HealthInstitution,
    HealthInstitutionKind,
    HealthPlan,
    PensionInstitution,
    PensionPlan,
)


def test_pension_base_respects_cap() -> None:
    calculator = ContributionCalculator()
    cap = ContributionCap("pension_health", date(2026, 1, 1), None, Decimal("90.0000"))

    assert calculator.pension_base(Decimal("1000000"), cap, Decimal("10000")) == Decimal("900000")


def test_pension_contribution_uses_mandatory_and_additional_rates() -> None:
    calculator = ContributionCalculator()
    contribution = calculator.pension(
        taxable_clp=Decimal("1000000"),
        plan=PensionPlan(
            id=1,
            institution=PensionInstitution(
                code="AFP_UNO",
                name="AFP Uno",
                mandatory_rate=Decimal("0.10"),
            ),
            valid_from=date(2026, 1, 1),
            valid_to=None,
            additional_rate=Decimal("0.0127"),
        ),
        cap=ContributionCap("pension_health", date(2026, 1, 1), None, Decimal("90.0000")),
        uf_value_clp=Decimal("10000"),
    )

    assert contribution.cap_clp == Decimal("900000")
    assert contribution.capped_base_clp == Decimal("900000")
    assert contribution.base_amount_clp == Decimal("90000")
    assert contribution.additional_amount_clp == Decimal("11430")


def test_health_contribution_uses_contract_amount_for_isapre_only() -> None:
    calculator = ContributionCalculator()
    isapre_contribution = calculator.health(
        taxable_clp=Decimal("1000000"),
        plan=HealthPlan(
            id=2,
            institution=HealthInstitution(
                code="BANMEDICA",
                name="Banmedica",
                kind=HealthInstitutionKind.ISAPRE,
                mandatory_rate=Decimal("0.07"),
            ),
            valid_from=date(2026, 1, 1),
            valid_to=None,
            plan_name="Plan Oro",
            contracted_uf=Decimal("4.5000"),
        ),
        cap=ContributionCap("pension_health", date(2026, 1, 1), None, Decimal("90.0000")),
        uf_value_clp=Decimal("10000"),
    )
    fonasa_contribution = calculator.health(
        taxable_clp=Decimal("1000000"),
        plan=HealthPlan(
            id=3,
            institution=HealthInstitution(
                code="FONASA",
                name="Fonasa",
                kind=HealthInstitutionKind.FONASA,
                mandatory_rate=Decimal("0.07"),
            ),
            valid_from=date(2026, 1, 1),
            valid_to=None,
            plan_name="Base",
            contracted_uf=Decimal("0"),
        ),
        cap=ContributionCap("pension_health", date(2026, 1, 1), None, Decimal("90.0000")),
        uf_value_clp=Decimal("10000"),
    )

    assert isapre_contribution.base_amount_clp == Decimal("63000")
    assert isapre_contribution.contracted_clp == Decimal("45000")
    assert isapre_contribution.additional_amount_clp == Decimal("0")
    assert fonasa_contribution.contracted_clp == Decimal("0")
    assert fonasa_contribution.additional_amount_clp == Decimal("0")
