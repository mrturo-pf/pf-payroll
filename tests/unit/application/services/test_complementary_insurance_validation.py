"""Tests for ComplementaryInsuranceValidationService."""

from dataclasses import replace
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
        total_discounts_clp=Decimal("250000"),
        net_pay_clp=Decimal("1000000"),
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

    detail = replace(
        base_detail,
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
    """Test validation when cost discrepancy exceeds tolerance (generates warnings)."""
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

    detail = replace(
        base_detail,
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
    assert len(warnings) > 0
    assert any("discrepancy" in w.lower() for w in warnings)


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

    detail = replace(base_detail, items=[])

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
    declared_cost = Decimal("50050")

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

    detail = replace(
        base_detail,
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


@pytest.mark.asyncio
async def test_validate_no_summary(
    service: ComplementaryInsuranceValidationService,
    base_detail: PayrollPeriodDetailDTO,
) -> None:
    """Test validation when period detail has no summary."""
    computed_costs = ComputeComplementaryInsuranceResultDTO(
        period_id=1,
        costs=[],
        total_cost_clp=Decimal("0"),
    )

    detail = replace(base_detail, summary=None)

    is_valid, warnings = await service.validate(detail, computed_costs)

    assert is_valid is True
    assert len(warnings) == 0


@pytest.mark.asyncio
async def test_validate_high_contribution_ratio(
    service: ComplementaryInsuranceValidationService,
    base_detail: PayrollPeriodDetailDTO,
) -> None:
    """Test validation when contribution ratio is unusually high."""
    high_cost = Decimal("200000")
    computed_costs = ComputeComplementaryInsuranceResultDTO(
        period_id=1,
        costs=[
            ComplementaryInsuranceCostDTO(
                plan_id=1,
                plan_name="Premium Plan",
                cost_clp=high_cost,
            )
        ],
        total_cost_clp=high_cost,
    )

    detail = replace(
        base_detail,
        items=[
            PayrollItemDetailDTO(
                concept_code="HEALTH_INSURANCE_EMPLOYER_CONTRIBUTION",
                concept_name="Health Insurance Employer Contribution",
                kind=PayrollConceptKind.DISCOUNT,
                is_taxable=True,
                amount_clp=high_cost,
                notes=None,
            )
        ],
    )

    is_valid, warnings = await service.validate(detail, computed_costs)

    assert is_valid is True
    assert any("unusually high" in w.lower() for w in warnings)


@pytest.mark.asyncio
async def test_validate_deduction_chain_inconsistency(
    service: ComplementaryInsuranceValidationService,
    base_detail: PayrollPeriodDetailDTO,
) -> None:
    """Test validation when deduction chain is inconsistent."""
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

    bad_summary = PayrollSummaryDTO(
        period_id=1,
        employer_id=1,
        employer_name="Test Corp",
        period_year=2025,
        period_month=5,
        payment_date=date(2025, 5, 30),
        taxable_income_clp=Decimal("1000000"),
        gross_income_clp=Decimal("1250000"),
        total_discounts_clp=Decimal("100000"),
        net_pay_clp=Decimal("1150000"),
    )

    detail = replace(
        base_detail,
        summary=bad_summary,
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
    assert any("inconsistency" in w.lower() for w in warnings)


@pytest.mark.asyncio
async def test_validate_calculated_greater_than_declared(
    service: ComplementaryInsuranceValidationService,
    base_detail: PayrollPeriodDetailDTO,
) -> None:
    """Test validation when calculated cost is higher than declared."""
    computed_cost = Decimal("60000")
    declared_cost = Decimal("50000")

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

    detail = replace(
        base_detail,
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
    assert any("higher than declared" in w.lower() for w in warnings)


@pytest.mark.asyncio
async def test_validate_deduction_chain_with_none_summary(
    service: ComplementaryInsuranceValidationService,
) -> None:
    """Test deduction chain validation when summary is None."""
    summary = PayrollSummaryDTO(
        period_id=1,
        employer_id=1,
        employer_name="Test Corp",
        period_year=2025,
        period_month=5,
        payment_date=date(2025, 5, 30),
        taxable_income_clp=Decimal("1000000"),
        gross_income_clp=Decimal("1250000"),
        total_discounts_clp=Decimal("250000"),
        net_pay_clp=Decimal("1000000"),
    )
    detail = PayrollPeriodDetailDTO(
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
        summary=None,
        items=[],
        pension_plan_id=1,
        health_plan_id=2,
    )

    warnings = service._validate_deduction_chain(
        detail, Decimal("50000")
    )

    assert warnings == []


