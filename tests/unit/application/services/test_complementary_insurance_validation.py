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


def _create_computed_costs(
    cost_clp: Decimal, plan_name: str = "Plan A"
) -> ComputeComplementaryInsuranceResultDTO:
    """Create a ComputeComplementaryInsuranceResultDTO for testing."""
    return ComputeComplementaryInsuranceResultDTO(
        period_id=1,
        costs=[
            ComplementaryInsuranceCostDTO(
                plan_id=1,
                plan_name=plan_name,
                cost_clp=cost_clp,
            )
        ]
        if cost_clp > 0
        else [],
        total_cost_clp=cost_clp,
    )


def _create_detail_with_employer_contribution(
    base_detail: PayrollPeriodDetailDTO, amount_clp: Decimal
) -> PayrollPeriodDetailDTO:
    """Create a detail with employer health insurance contribution."""
    return replace(
        base_detail,
        items=[
            PayrollItemDetailDTO(
                concept_code="HEALTH_INSURANCE_EMPLOYER_CONTRIBUTION",
                concept_name="Health Insurance Employer Contribution",
                kind=PayrollConceptKind.DISCOUNT,
                is_taxable=True,
                amount_clp=amount_clp,
                notes=None,
            )
        ],
    )


def _create_detail_with_items(
    base_detail: PayrollPeriodDetailDTO,
    employer_clp: Decimal | None = None,
    employee_clp: Decimal | None = None,
) -> PayrollPeriodDetailDTO:
    """Create a detail with specified employer and/or employee contributions."""
    items: list[PayrollItemDetailDTO] = []

    if employer_clp is not None:
        items.append(
            PayrollItemDetailDTO(
                concept_code="HEALTH_INSURANCE_EMPLOYER_CONTRIBUTION",
                concept_name="Health Insurance Employer Contribution",
                kind=PayrollConceptKind.DISCOUNT,
                is_taxable=True,
                amount_clp=employer_clp,
                notes=None,
            )
        )

    if employee_clp is not None:
        items.append(
            PayrollItemDetailDTO(
                concept_code="HEALTH_INSURANCE",
                concept_name="Health Insurance",
                kind=PayrollConceptKind.DISCOUNT,
                is_taxable=False,
                amount_clp=employee_clp,
                notes=None,
            )
        )

    return replace(base_detail, items=items)


@pytest.mark.asyncio
async def test_validate_matching_costs(
    service: ComplementaryInsuranceValidationService,
    payroll_period_detail_dto: PayrollPeriodDetailDTO,
) -> None:
    """Test validation when calculated cost matches declared amount."""
    cost = Decimal("50000")
    computed_costs = _create_computed_costs(cost)
    detail = _create_detail_with_employer_contribution(payroll_period_detail_dto, cost)

    is_valid, warnings = await service.validate(detail, computed_costs)

    assert is_valid is True
    assert len(warnings) == 0


@pytest.mark.asyncio
async def test_validate_discrepancy_exceeds_tolerance(
    service: ComplementaryInsuranceValidationService,
    payroll_period_detail_dto: PayrollPeriodDetailDTO,
) -> None:
    """Test validation when cost discrepancy exceeds tolerance."""
    computed_cost = Decimal("50000")
    declared_cost = Decimal("50500")

    computed_costs = _create_computed_costs(computed_cost)
    detail = _create_detail_with_employer_contribution(
        payroll_period_detail_dto, declared_cost
    )

    is_valid, warnings = await service.validate(detail, computed_costs)

    assert is_valid is True
    assert len(warnings) > 0
    assert any("discrepancy" in w.lower() for w in warnings)


@pytest.mark.asyncio
async def test_validate_no_declared_amount(
    service: ComplementaryInsuranceValidationService,
    payroll_period_detail_dto: PayrollPeriodDetailDTO,
) -> None:
    """Test validation when no declared contribution is found."""
    computed_costs = _create_computed_costs(Decimal("0"))
    detail = replace(payroll_period_detail_dto, items=[])

    is_valid, warnings = await service.validate(detail, computed_costs)

    assert is_valid is True
    assert len(warnings) > 0
    assert "no declared" in warnings[0].lower()


@pytest.mark.asyncio
async def test_validate_within_tolerance(
    service: ComplementaryInsuranceValidationService,
    payroll_period_detail_dto: PayrollPeriodDetailDTO,
) -> None:
    """Test validation when discrepancy is within tolerance."""
    computed_cost = Decimal("50000")
    declared_cost = Decimal("50050")

    computed_costs = _create_computed_costs(computed_cost)
    detail = _create_detail_with_employer_contribution(
        payroll_period_detail_dto, declared_cost
    )

    is_valid, warnings = await service.validate(detail, computed_costs)

    assert is_valid is True
    assert len(warnings) == 0


@pytest.mark.asyncio
async def test_validate_no_summary(
    service: ComplementaryInsuranceValidationService,
    payroll_period_detail_dto: PayrollPeriodDetailDTO,
) -> None:
    """Test validation when period detail has no summary."""
    computed_costs = _create_computed_costs(Decimal("0"))
    detail = replace(payroll_period_detail_dto, summary=None)

    is_valid, warnings = await service.validate(detail, computed_costs)

    assert is_valid is True
    assert len(warnings) == 0


@pytest.mark.asyncio
async def test_validate_high_contribution_ratio(
    service: ComplementaryInsuranceValidationService,
    payroll_period_detail_dto: PayrollPeriodDetailDTO,
) -> None:
    """Test validation when contribution ratio is unusually high."""
    high_cost = Decimal("200000")
    computed_costs = _create_computed_costs(high_cost, "Premium Plan")
    detail = _create_detail_with_employer_contribution(
        payroll_period_detail_dto, high_cost
    )

    is_valid, warnings = await service.validate(detail, computed_costs)

    assert is_valid is True
    assert any("unusually high" in w.lower() for w in warnings)


@pytest.mark.asyncio
async def test_validate_deduction_chain_inconsistency(
    service: ComplementaryInsuranceValidationService,
    payroll_period_detail_dto: PayrollPeriodDetailDTO,
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

    # Create summary where Gross - Total Discounts != Net Pay
    # This represents an inconsistent payroll balance
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
        net_pay_clp=Decimal("1000000"),  # Should be 1150000 (1250000 - 100000)
    )

    detail = replace(
        payroll_period_detail_dto,
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
    payroll_period_detail_dto: PayrollPeriodDetailDTO,
) -> None:
    """Test validation when calculated cost is higher than declared."""
    computed_cost = Decimal("60000")
    declared_cost = Decimal("50000")

    computed_costs = _create_computed_costs(computed_cost)
    detail = _create_detail_with_employer_contribution(
        payroll_period_detail_dto, declared_cost
    )

    is_valid, warnings = await service.validate(detail, computed_costs)

    assert is_valid is True
    assert any("higher than declared" in w.lower() for w in warnings)


def test_validate_deduction_chain_with_none_summary(
    service: ComplementaryInsuranceValidationService,
    payroll_period_detail_dto: PayrollPeriodDetailDTO,
) -> None:
    """Test deduction chain validation when summary is None."""
    detail = replace(payroll_period_detail_dto, summary=None)

    warnings = service._validate_deduction_chain(detail, Decimal("50000"))

    assert warnings == []


@pytest.mark.asyncio
async def test_validate_declared_zero_with_calculated_costs(
    service: ComplementaryInsuranceValidationService,
    payroll_summary_dto: PayrollSummaryDTO,
    payroll_period_detail_dto: PayrollPeriodDetailDTO,
) -> None:
    """Test validation when declared contribution is 0 but costs were calculated.

    This is a critical case: if the CSV declares 0 CLP for complementary
    insurance employer contribution, but the system calculates costs based on
    assigned plans, this should trigger an alert.
    """
    detail = replace(
        _create_detail_with_employer_contribution(
            payroll_period_detail_dto, Decimal("0")
        ),
        period_year=2026,
        period_month=6,
    )
    computed_costs = _create_computed_costs(Decimal("50000"), "Health Plan A")

    is_valid, warnings = await service.validate(detail, computed_costs)

    # Must be valid (no import failure) but should have warnings
    assert is_valid is True
    # Should detect the discrepancy between declared (0) and calculated (50000)
    assert any("discrepancy" in w.lower() for w in warnings)
    assert any("50000" in w for w in warnings)


@pytest.mark.asyncio
async def test_validate_employee_deduction_only(
    service: ComplementaryInsuranceValidationService,
    payroll_period_detail_dto: PayrollPeriodDetailDTO,
) -> None:
    """Test validation when only employee health insurance deduction is declared.

    The total should be the employee deduction (since employer contribution
    is absent) and should be compared against calculated costs.
    """
    employee_deduction = Decimal("25000")

    computed_costs = _create_computed_costs(employee_deduction)
    detail = _create_detail_with_items(
        payroll_period_detail_dto, employer_clp=None, employee_clp=employee_deduction
    )

    is_valid, warnings = await service.validate(detail, computed_costs)

    assert is_valid is True
    assert len(warnings) == 0


@pytest.mark.asyncio
async def test_validate_employer_and_employee_combined(
    service: ComplementaryInsuranceValidationService,
    payroll_period_detail_dto: PayrollPeriodDetailDTO,
) -> None:
    """Test validation when both employer and employee contributions are declared.

    The total should be employer + employee, and should match calculated costs.
    """
    employer_contribution = Decimal("30000")
    employee_deduction = Decimal("20000")
    total_declared = employer_contribution + employee_deduction

    computed_costs = _create_computed_costs(total_declared)
    detail = _create_detail_with_items(
        payroll_period_detail_dto,
        employer_clp=employer_contribution,
        employee_clp=employee_deduction,
    )

    is_valid, warnings = await service.validate(detail, computed_costs)

    assert is_valid is True
    assert len(warnings) == 0


@pytest.mark.asyncio
async def test_validate_employer_and_employee_discrepancy(
    service: ComplementaryInsuranceValidationService,
    payroll_period_detail_dto: PayrollPeriodDetailDTO,
) -> None:
    """Test validation when combined employer+employee total doesn't match calculated.

    This is the critical scenario: if employee deduction is 0 but employer
    contribution is some amount, the total might not match calculated costs,
    triggering a discrepancy alert.
    """
    employer_contribution = Decimal("30000")
    employee_deduction = Decimal("0")
    calculated_cost = Decimal("50000")

    computed_costs = _create_computed_costs(calculated_cost)
    detail = replace(
        _create_detail_with_items(
            payroll_period_detail_dto,
            employer_clp=employer_contribution,
            employee_clp=employee_deduction,
        ),
        period_year=2026,
        period_month=5,
    )

    is_valid, warnings = await service.validate(detail, computed_costs)

    assert is_valid is True
    # Should detect discrepancy: declared total (30000) vs calculated (50000)
    assert any("discrepancy" in w.lower() for w in warnings)
    assert any("30000" in w or "employer contribution 30000" in w for w in warnings)


@pytest.mark.asyncio
async def test_validate_zero_declared_with_calculated_costs(
    service: ComplementaryInsuranceValidationService,
    payroll_period_detail_dto: PayrollPeriodDetailDTO,
) -> None:
    """Test validation when nothing is declared but costs were calculated.

    Edge case: both employer and employee declare 0, but system calculates
    costs based on assigned plans. Should trigger variance alert.
    """
    computed_costs = _create_computed_costs(Decimal("50000"))
    detail = replace(
        _create_detail_with_items(
            payroll_period_detail_dto,
            employer_clp=Decimal("0"),
            employee_clp=Decimal("0"),
        ),
        period_year=2026,
        period_month=7,
    )

    is_valid, warnings = await service.validate(detail, computed_costs)

    assert is_valid is True
    # Should detect discrepancy: declared total (0) vs calculated (50000)
    assert any("discrepancy" in w.lower() for w in warnings)
    assert any("50000" in w for w in warnings)


@pytest.mark.asyncio
async def test_validate_zero_both_declared_and_calculated(
    service: ComplementaryInsuranceValidationService,
    payroll_period_detail_dto: PayrollPeriodDetailDTO,
) -> None:
    """Test validation when both declared and calculated are zero.

    Edge case: no complementary insurance declared or calculated.
    This should not trigger warnings.
    """
    computed_costs = _create_computed_costs(Decimal("0"))
    detail = replace(
        _create_detail_with_items(
            payroll_period_detail_dto,
            employer_clp=Decimal("0"),
            employee_clp=Decimal("0"),
        ),
        period_year=2026,
        period_month=8,
    )

    is_valid, warnings = await service.validate(detail, computed_costs)

    assert is_valid is True
    # No discrepancy when both are zero
    assert len(warnings) == 0
