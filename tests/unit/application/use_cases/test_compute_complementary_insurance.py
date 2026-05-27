"""Tests for ComputeComplementaryInsurance use case."""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from payroll.application.dto import PayrollPeriodDetailDTO, PayrollSummaryDTO
from payroll.application.use_cases.compute_complementary_insurance import (
    ComputeComplementaryInsurance,
)
from payroll.domain.contributions import (
    ComplementaryInsuranceCostType,
    ComplementaryInsurancePlan,
)


@pytest.fixture
def mock_payroll_repository() -> AsyncMock:
    """Create mock payroll repository."""
    return AsyncMock()


@pytest.fixture
def mock_complementary_insurance_repository() -> AsyncMock:
    """Create mock complementary insurance repository."""
    return AsyncMock()


@pytest.fixture
def use_case(
    mock_payroll_repository: AsyncMock,
    mock_complementary_insurance_repository: AsyncMock,
) -> ComputeComplementaryInsurance:
    """Create use case instance."""
    return ComputeComplementaryInsurance(
        mock_payroll_repository, mock_complementary_insurance_repository
    )


@pytest.mark.asyncio
async def test_execute(
    use_case: ComputeComplementaryInsurance,
    mock_payroll_repository: AsyncMock,
    mock_complementary_insurance_repository: AsyncMock,
) -> None:
    """Test execute method."""
    period_id = 123
    summary = PayrollSummaryDTO(
        period_id=period_id,
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
    detail = PayrollPeriodDetailDTO(
        id=period_id,
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

    plan = ComplementaryInsurancePlan(
        id=10,
        provider_id=1,
        name="Plan A",
        cost_type=ComplementaryInsuranceCostType.FIXED_CLP,
        cost_value=Decimal("50000"),
        cost_currency="CLP",
        valid_from=date(2024, 1, 1),
        valid_to=None,
    )

    mock_payroll_repository.get_period_detail.return_value = detail
    mock_complementary_insurance_repository.get_period_plans.return_value = [plan]

    result = await use_case.execute(period_id)

    assert result.period_id == period_id
    assert len(result.costs) == 1
    assert result.total_cost_clp == Decimal("50000")
    mock_payroll_repository.get_period_detail.assert_called_once_with(period_id)
    mock_complementary_insurance_repository.get_period_plans.assert_called_once_with(
        period_id
    )
