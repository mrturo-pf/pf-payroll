"""Tests for ComputeComplementaryInsurance use case."""

from dataclasses import replace
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
def use_case(
    mock_payroll_repository: AsyncMock,
    mock_complementary_insurance_repository: AsyncMock,
    mock_market_data_repository: AsyncMock,
) -> ComputeComplementaryInsurance:
    """Create use case instance."""
    return ComputeComplementaryInsurance(
        mock_payroll_repository,
        mock_complementary_insurance_repository,
        mock_market_data_repository,
    )


@pytest.mark.asyncio
async def test_execute(
    use_case: ComputeComplementaryInsurance,
    mock_payroll_repository: AsyncMock,
    mock_complementary_insurance_repository: AsyncMock,
    payroll_summary_dto: PayrollSummaryDTO,
    payroll_period_detail_dto: PayrollPeriodDetailDTO,
) -> None:
    """Test execute method."""
    period_id = 123
    summary = replace(
        payroll_summary_dto,
        period_id=period_id,
        total_discounts_clp=Decimal("180000"),
        net_pay_clp=Decimal("1070000"),
    )
    detail = replace(
        payroll_period_detail_dto,
        id=period_id,
        summary=summary,
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
