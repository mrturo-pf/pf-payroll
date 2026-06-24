"""Tests for contribution computation helpers."""

from datetime import date
from decimal import Decimal

from payroll.application.dto import (
    ComputeContributionsResultDTO,
    PayrollItemDetailDTO,
    PayrollPeriodDetailDTO,
    PayrollSummaryDTO,
)
from payroll.application.services.contribution_computation import (
    build_imported_contribution_validation,
)
from payroll.domain.contributions import (
    EmploymentContractKind,
    HealthContribution,
    HealthInstitutionKind,
    PensionContribution,
    UnemploymentContribution,
)


def _period_detail(
    items: list[PayrollItemDetailDTO],
    *,
    health_plan_ids: tuple[int, ...] | None = None,
) -> PayrollPeriodDetailDTO:
    """Build a period detail for validation tests."""
    return PayrollPeriodDetailDTO(
        id=1,
        employer_id=1,
        employer_name="ACME",
        employer_tax_id="76000000-1",
        employer_country_code="CL",
        employer_started_at=date(2020, 1, 1),
        employer_ended_at=None,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        worked_days=30,
        status="actual",
        employment_contract_kind=EmploymentContractKind.INDEFINITE,
        pension_plan_id=1,
        health_plan_id=2,
        health_plan_ids=health_plan_ids,
        health_institution_is_active=None,
        items=items,
        summary=PayrollSummaryDTO(
            period_id=1,
            employer_id=1,
            employer_name="ACME",
            period_year=2026,
            period_month=1,
            payment_date=date(2026, 1, 31),
            taxable_income_clp=Decimal("1000000"),
            gross_income_clp=Decimal("1000000"),
            total_discounts_clp=Decimal("0"),
            net_pay_clp=Decimal("1000000"),
        ),
    )


def _computed_contributions(
    *,
    pension_base: Decimal,
    pension_additional: Decimal,
    health_base: Decimal,
    health_additional: Decimal,
) -> ComputeContributionsResultDTO:
    """Build a computed contribution result for validation tests."""
    return ComputeContributionsResultDTO(
        period_id=1,
        pension_plan_id=1,
        health_plan_id=2,
        taxable_income_clp=Decimal("1000000"),
        pension=PensionContribution(
            institution_code="AFP_UNO",
            taxable_clp=Decimal("1000000"),
            cap_clp=Decimal("0"),
            capped_base_clp=Decimal("0"),
            base_amount_clp=pension_base,
            additional_amount_clp=pension_additional,
        ),
        health=HealthContribution(
            institution_code="BANMEDICA",
            institution_kind=HealthInstitutionKind.ISAPRE,
            taxable_clp=Decimal("1000000"),
            cap_clp=Decimal("0"),
            capped_base_clp=Decimal("0"),
            base_amount_clp=health_base,
            contracted_uf=Decimal("0"),
            contracted_clp=Decimal("0"),
            additional_amount_clp=health_additional,
        ),
        unemployment=UnemploymentContribution(
            contract_kind=EmploymentContractKind.INDEFINITE,
            taxable_clp=Decimal("1000000"),
            cap_clp=Decimal("0"),
            capped_base_clp=Decimal("0"),
            employee_rate=Decimal("0"),
            employee_amount_clp=Decimal("0"),
            employer_rate=Decimal("0"),
            employer_amount_clp=Decimal("0"),
        ),
        total_discount_clp=Decimal("0"),
    )


def _standard_validation(
    detail: PayrollPeriodDetailDTO,
) -> object:
    computed = _computed_contributions(
        pension_base=Decimal("100000"),
        pension_additional=Decimal("20000"),
        health_base=Decimal("30000"),
        health_additional=Decimal("40000"),
    )
    return build_imported_contribution_validation(detail, computed)


def test_build_imported_contribution_validation_returns_none() -> None:
    """Test validation returns none when imported contribution rows are absent."""
    detail = _period_detail(
        [
            PayrollItemDetailDTO(
                concept_code="SALARY_BASE",
                concept_name="Salary Base",
                kind="income",
                is_taxable=True,
                amount_clp=Decimal("1000000"),
                notes=None,
            )
        ]
    )

    assert build_imported_contribution_validation(detail, None) is None


def test_build_imported_contribution_validation_marks_pending() -> None:
    """Test validation marks contribution reconciliation as pending."""
    detail = _period_detail(
        [
            PayrollItemDetailDTO(
                concept_code="PENSION_BASE",
                concept_name="Pension Base",
                kind="discount",
                is_taxable=False,
                amount_clp=Decimal("100000"),
                notes=None,
            )
        ]
    )

    validation = build_imported_contribution_validation(detail, None)

    assert validation is not None
    assert validation.expected_pension_base_clp is None
    assert validation.warning == (
        "Contribution values will be reconciled after pension and health plans are "
        "assigned."
    )


def test_build_imported_contribution_validation_reports_mismatches() -> None:
    """Test validation reports declared vs expected mismatches."""
    detail = _period_detail(
        [
            PayrollItemDetailDTO(
                concept_code="PENSION_BASE",
                concept_name="Pension Base",
                kind="discount",
                is_taxable=False,
                amount_clp=Decimal("110000"),
                notes=None,
            ),
            PayrollItemDetailDTO(
                concept_code="PENSION_ADDITIONAL",
                concept_name="Pension Additional",
                kind="discount",
                is_taxable=False,
                amount_clp=Decimal("21000"),
                notes=None,
            ),
            PayrollItemDetailDTO(
                concept_code="HEALTH_BASE",
                concept_name="Health Base",
                kind="discount",
                is_taxable=False,
                amount_clp=Decimal("31000"),
                notes=None,
            ),
            PayrollItemDetailDTO(
                concept_code="HEALTH_ADDITIONAL_UF",
                concept_name="Health Additional Uf",
                kind="discount",
                is_taxable=False,
                amount_clp=Decimal("41000"),
                notes=None,
            ),
        ]
    )
    validation = _standard_validation(detail)

    assert validation is not None
    assert validation.pension_base_difference_clp == Decimal("10000")
    assert validation.pension_additional_difference_clp == Decimal("1000")
    assert validation.health_base_difference_clp == Decimal("1000")
    assert validation.health_plan_additional_difference_clp == Decimal("1000")
    assert "PENSION_BASE declared 110000 CLP" in validation.warning
    assert "PENSION_ADDITIONAL declared 21000 CLP" in validation.warning
    assert "HEALTH_BASE declared 31000 CLP" in validation.warning
    assert "HEALTH_ADDITIONAL_UF declared 41000 CLP" in validation.warning


def test_build_imported_contribution_validation_skips_multi_plan_additional_check() -> (
    None
):
    """Test validation skips additional health mismatch for multi-plan snapshots."""
    detail = _period_detail(
        [
            PayrollItemDetailDTO(
                concept_code="PENSION_BASE",
                concept_name="Pension Base",
                kind="discount",
                is_taxable=False,
                amount_clp=Decimal("100000"),
                notes=None,
            ),
            PayrollItemDetailDTO(
                concept_code="PENSION_ADDITIONAL",
                concept_name="Pension Additional",
                kind="discount",
                is_taxable=False,
                amount_clp=Decimal("20000"),
                notes=None,
            ),
            PayrollItemDetailDTO(
                concept_code="HEALTH_BASE",
                concept_name="Health Base",
                kind="discount",
                is_taxable=False,
                amount_clp=Decimal("30000"),
                notes=None,
            ),
            PayrollItemDetailDTO(
                concept_code="HEALTH_ADDITIONAL_UF",
                concept_name="Health Additional Uf",
                kind="discount",
                is_taxable=False,
                amount_clp=Decimal("41000"),
                notes=None,
            ),
        ],
        health_plan_ids=(1, 2, 3),
    )
    validation = _standard_validation(detail)

    assert validation is not None
    assert validation.expected_health_plan_additional_clp is None
    assert validation.health_plan_additional_difference_clp is None
    assert validation.warning is None
