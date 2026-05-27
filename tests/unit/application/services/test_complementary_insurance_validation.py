"""Tests for ComplementaryInsuranceValidationService."""

from datetime import date
from decimal import Decimal

import pytest

from payroll.application.dto import (
    ComplementaryInsuranceCostDTO,
    ComputeComplementaryInsuranceResultDTO,
    PayrollItemDetailDTO,
    PayrollPeriodDetailDTO,
    PayrollSummaryDTO,
)
from payroll.application.services.complementary_insurance_validation import (
    ComplementaryInsuranceValidationService,
)
from payroll.infrastructure.db.models.reference_data import PayrollConceptKind


@pytest.fixture
def service() -> ComplementaryInsuranceValidationService:
    """Create validation service instance."""
    return ComplementaryInsuranceValidationService()


@pytest.fixture
def base_detail() -> PayrollPeriodDetailDTO:
    """Create base period detail for testing."""
    summary = PayrollSummaryDTO(
        period_id=1,
        employer_id=1,
        employer_name="Test Corp",
        period_year=2025,
        period_month=5,
        payment_date=date(2025, 5, 30),
        taxable_income_clp=Decimal("1000000"),
        gross_income_clp=Decimal("1250000"),
        total_discounts_clp=Decimal("180000"),
        net_pay_clp=Decimal("1070000"),
    )
    return PayrollPeriodDetailDTO(
        id=1,
        employer_id=1,
        employer_name="Test Corp",
        employer_tax_id="123456789",
        employer_country_code="CL",
        employer_started_at=date(2020, 1, 1),
        employer_ended_at=None,
        period_year=2025,
        period_month=5,
        payment_date=date(2025, 5, 30),
        status="actual",
        employment_contract_kind="indefinite",
        worked_days=30,
        summary=summary,
        items=[],
        pension_plan_id=1,
        health_plan_id=2,
    )


@pytest.mark.asyncio
async def test_validate_matching_costs(
    service: ComplementaryInsuranceValidationService,
    base_detail: PayrollPeriodDetailDTO,
) -> None:
    """Test validation when calculated cost matches declared amount."""
    cost = Decimal("50000")
    computed_costs = ComputeComplementaryInsuranceResultDTO(
        period_id=1,
        costs=[
            ComplementaryInsuranceCostDTO(
                plan_id=1,
                plan_name="Plan A",
                cost_clp=cost,
            )
        ],
        total_cost_clp=cost,
    )

    detail = PayrollPeriodDetailDTO(
        id=base_detail.id,
        employer_id=base_detail.employer_id,
        employer_name=base_detail.employer_name,
        employer_tax_id=base_detail.employer_tax_id,
        employer_country_code=base_detail.employer_country_code,
        employer_started_at=base_detail.employer_started_at,
        employer_ended_at=base_detail.employer_ended_at,
        period_year=base_detail.period_year,
        period_month=base_detail.period_month,
        payment_date=base_detail.payment_date,
        status=base_detail.status,
        employment_contract_kind=base_detail.employment_contract_kind,
        worked_days=base_detail.worked_days,
        summary=base_detail.summary,
        pension_plan_id=base_detail.pension_plan_id,
        health_plan_id=base_detail.health_plan_id,
        items=[
            PayrollItemDetailDTO(
                concept_code="HEALTH_INSURANCE_EMPLOYER_CONTRIBUTION",
                concept_name="Health Insurance Employer Contribution",
                kind=PayrollConceptKind.DISCOUNT,
                is_taxable=True,
                amount_clp=cost,
                notes=None,
            )
        ],
    )

    is_valid, warnings = await service.validate(detail, computed_costs)

    assert is_valid is True
    assert len(warnings) == 0


@pytest.mark.asyncio
async def test_validate_discrepancy_exceeds_tolerance(
    service: ComplementaryInsuranceValidationService,
    base_detail: PayrollPeriodDetailDTO,
) -> None:
    """Test validation when cost discrepancy exceeds tolerance."""
    computed_cost = Decimal("50000")
    declared_cost = Decimal("50500")

    computed_costs = ComputeComplementaryInsuranceResultDTO(
        period_id=1,
        costs=[
            ComplementaryInsuranceCostDTO(
                plan_id=1,
                plan_name="Plan A",
                cost_clp=computed_cost,
            )
        ],
        total_cost_clp=computed_cost,
    )

    detail = PayrollPeriodDetailDTO(
        id=base_detail.id,
        employer_id=base_detail.employer_id,
        employer_name=base_detail.employer_name,
        employer_tax_id=base_detail.employer_tax_id,
        employer_country_code=base_detail.employer_country_code,
        employer_started_at=base_detail.employer_started_at,
        employer_ended_at=base_detail.employer_ended_at,
        period_year=base_detail.period_year,
        period_month=base_detail.period_month,
        payment_date=base_detail.payment_date,
        status=base_detail.status,
        employment_contract_kind=base_detail.employment_contract_kind,
        worked_days=base_detail.worked_days,
        summary=base_detail.summary,
        pension_plan_id=base_detail.pension_plan_id,
        health_plan_id=base_detail.health_plan_id,
        items=[
            PayrollItemDetailDTO(
                concept_code="HEALTH_INSURANCE_EMPLOYER_CONTRIBUTION",
                concept_name="Health Insurance Employer Contribution",
                kind=PayrollConceptKind.DISCOUNT,
                is_taxable=True,
                amount_clp=declared_cost,
                notes=None,
            )
        ],
    )

    is_valid, warnings = await service.validate(detail, computed_costs)

    assert is_valid is False
    assert len(warnings) > 0
    assert "discrepancy" in warnings[0].lower()


@pytest.mark.asyncio
async def test_validate_no_declared_amount(
    service: ComplementaryInsuranceValidationService,
    base_detail: PayrollPeriodDetailDTO,
) -> None:
    """Test validation when no declared contribution is found."""
    computed_costs = ComputeComplementaryInsuranceResultDTO(
        period_id=1,
        costs=[],
        total_cost_clp=Decimal("0"),
    )

    detail = PayrollPeriodDetailDTO(
        id=base_detail.id,
        employer_id=base_detail.employer_id,
        employer_name=base_detail.employer_name,
        employer_tax_id=base_detail.employer_tax_id,
        employer_country_code=base_detail.employer_country_code,
        employer_started_at=base_detail.employer_started_at,
        employer_ended_at=base_detail.employer_ended_at,
        period_year=base_detail.period_year,
        period_month=base_detail.period_month,
        payment_date=base_detail.payment_date,
        status=base_detail.status,
        employment_contract_kind=base_detail.employment_contract_kind,
        worked_days=base_detail.worked_days,
        summary=base_detail.summary,
        pension_plan_id=base_detail.pension_plan_id,
        health_plan_id=base_detail.health_plan_id,
        items=[],
    )

    is_valid, warnings = await service.validate(detail, computed_costs)

    assert is_valid is True
    assert len(warnings) > 0
    assert "no declared" in warnings[0].lower()


@pytest.mark.asyncio
async def test_validate_within_tolerance(
    service: ComplementaryInsuranceValidationService,
    base_detail: PayrollPeriodDetailDTO,
) -> None:
    """Test validation when discrepancy is within tolerance."""
    computed_cost = Decimal("50000")
    declared_cost = Decimal("50050")  # 50 CLP difference, within 100 CLP tolerance

    computed_costs = ComputeComplementaryInsuranceResultDTO(
        period_id=1,
        costs=[
            ComplementaryInsuranceCostDTO(
                plan_id=1,
                plan_name="Plan A",
                cost_clp=computed_cost,
            )
        ],
        total_cost_clp=computed_cost,
    )

    detail = PayrollPeriodDetailDTO(
        id=base_detail.id,
        employer_id=base_detail.employer_id,
        employer_name=base_detail.employer_name,
        employer_tax_id=base_detail.employer_tax_id,
        employer_country_code=base_detail.employer_country_code,
        employer_started_at=base_detail.employer_started_at,
        employer_ended_at=base_detail.employer_ended_at,
        period_year=base_detail.period_year,
        period_month=base_detail.period_month,
        payment_date=base_detail.payment_date,
        status=base_detail.status,
        employment_contract_kind=base_detail.employment_contract_kind,
        worked_days=base_detail.worked_days,
        summary=base_detail.summary,
        pension_plan_id=base_detail.pension_plan_id,
        health_plan_id=base_detail.health_plan_id,
        items=[
            PayrollItemDetailDTO(
                concept_code="HEALTH_INSURANCE_EMPLOYER_CONTRIBUTION",
                concept_name="Health Insurance Employer Contribution",
                kind=PayrollConceptKind.DISCOUNT,
                is_taxable=True,
                amount_clp=declared_cost,
                notes=None,
            )
        ],
    )

    is_valid, warnings = await service.validate(detail, computed_costs)

    assert is_valid is True
    assert len(warnings) == 0
