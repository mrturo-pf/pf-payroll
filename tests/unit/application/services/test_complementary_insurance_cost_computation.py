"""Tests for ComplementaryInsuranceCostComputationService."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from payroll.application.dto import PayrollPeriodDetailDTO, PayrollSummaryDTO
from payroll.application.errors import EconomicIndexNotFoundError
from payroll.application.services.complementary_insurance_cost_computation import (
    ComplementaryInsuranceCostComputationService,
)
from payroll.domain.contributions import ComplementaryInsuranceCostType
from tests.helpers.complementary_insurance_helpers import (
    build_complementary_insurance_plan,
)


def _make_uf_plan_detail_and_plan(
    period_id: int,
    payroll_summary_dto: PayrollSummaryDTO,
    payroll_period_detail_dto: PayrollPeriodDetailDTO,
) -> tuple[PayrollPeriodDetailDTO, object]:
    payment_date = date(2025, 3, 31)
    summary = replace(
        payroll_summary_dto,
        period_id=period_id,
        period_month=3,
        payment_date=payment_date,
        taxable_income_clp=Decimal("3000000"),
        gross_income_clp=Decimal("3500000"),
        total_discounts_clp=Decimal("400000"),
        net_pay_clp=Decimal("3100000"),
    )
    detail = replace(
        payroll_period_detail_dto,
        id=period_id,
        period_month=3,
        payment_date=payment_date,
        summary=summary,
    )
    plan = build_complementary_insurance_plan(
        plan_id=30,
        name="Plan UF",
        cost_type=ComplementaryInsuranceCostType.FIXED_UF,
        cost_value=Decimal("2"),
        cost_currency="UF",
    )
    return detail, plan


@pytest.fixture
def service(
    mock_payroll_repository: AsyncMock,
    mock_complementary_insurance_repository: AsyncMock,
    mock_market_data_repository: AsyncMock,
) -> ComplementaryInsuranceCostComputationService:
    """Create service instance."""
    return ComplementaryInsuranceCostComputationService(
        mock_payroll_repository,
        mock_complementary_insurance_repository,
        mock_market_data_repository,
    )


@pytest.mark.asyncio
async def test_compute_with_fixed_and_variable_plans(
    service: ComplementaryInsuranceCostComputationService,
    mock_payroll_repository: AsyncMock,
    mock_complementary_insurance_repository: AsyncMock,
    payroll_summary_dto: PayrollSummaryDTO,
    payroll_period_detail_dto: PayrollPeriodDetailDTO,
) -> None:
    """Test computing costs with both fixed and variable plans."""
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

    plan1 = build_complementary_insurance_plan(
        plan_id=10,
        name="Plan A (Fixed)",
        cost_type=ComplementaryInsuranceCostType.FIXED_CLP,
        cost_value=Decimal("50000"),
    )
    plan2 = build_complementary_insurance_plan(
        plan_id=20,
        name="Plan B (2%)",
        cost_type=ComplementaryInsuranceCostType.VARIABLE_PERCENTAGE,
        cost_value=Decimal("2.5"),
    )

    mock_payroll_repository.get_period_detail.return_value = detail
    mock_complementary_insurance_repository.get_period_plans.return_value = [
        plan1,
        plan2,
    ]

    result = await service.compute(period_id)

    assert result.period_id == period_id
    assert len(result.costs) == 2
    assert result.costs[0].plan_id == 10
    assert result.costs[0].cost_clp == Decimal("50000")
    assert result.costs[1].plan_id == 20
    # 2.5% of 1,000,000 = 25,000
    assert result.costs[1].cost_clp == Decimal("25000.00")
    assert result.total_cost_clp == Decimal("75000.00")


@pytest.mark.asyncio
async def test_compute_with_no_plans(
    service: ComplementaryInsuranceCostComputationService,
    mock_payroll_repository: AsyncMock,
    mock_complementary_insurance_repository: AsyncMock,
    payroll_summary_dto: PayrollSummaryDTO,
    payroll_period_detail_dto: PayrollPeriodDetailDTO,
) -> None:
    """Test computing when no plans are assigned."""
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

    mock_payroll_repository.get_period_detail.return_value = detail
    mock_complementary_insurance_repository.get_period_plans.return_value = []

    result = await service.compute(period_id)

    assert result.period_id == period_id
    assert result.costs == []
    assert result.total_cost_clp == Decimal("0")


@pytest.mark.asyncio
async def test_compute_with_missing_detail(
    service: ComplementaryInsuranceCostComputationService,
    mock_payroll_repository: AsyncMock,
    mock_complementary_insurance_repository: AsyncMock,
) -> None:
    """Test computing when period detail is missing."""
    period_id = 999

    mock_payroll_repository.get_period_detail.return_value = None

    result = await service.compute(period_id)

    assert result.period_id == period_id
    assert result.costs == []
    assert result.total_cost_clp == Decimal("0")
    mock_complementary_insurance_repository.get_period_plans.assert_not_called()


@pytest.mark.asyncio
async def test_compute_with_missing_summary(
    service: ComplementaryInsuranceCostComputationService,
    mock_payroll_repository: AsyncMock,
    mock_complementary_insurance_repository: AsyncMock,
    payroll_period_detail_dto: PayrollPeriodDetailDTO,
) -> None:
    """Test computing when summary is missing."""
    period_id = 123
    detail = replace(payroll_period_detail_dto, id=period_id, summary=None)

    mock_payroll_repository.get_period_detail.return_value = detail

    result = await service.compute(period_id)

    assert result.period_id == period_id
    assert result.costs == []
    assert result.total_cost_clp == Decimal("0")
    mock_complementary_insurance_repository.get_period_plans.assert_not_called()


@pytest.mark.asyncio
async def test_compute_with_fixed_uf_plan(
    mock_payroll_repository: AsyncMock,
    mock_complementary_insurance_repository: AsyncMock,
    mock_market_data_repository: AsyncMock,
    payroll_summary_dto: PayrollSummaryDTO,
    payroll_period_detail_dto: PayrollPeriodDetailDTO,
) -> None:
    """Test computing costs with a FIXED_UF plan fetches and converts UF rate."""
    period_id = 10
    reference_date = date(2025, 4, 1)  # First day of following month
    detail, plan = _make_uf_plan_detail_and_plan(
        period_id, payroll_summary_dto, payroll_period_detail_dto
    )

    mock_payroll_repository.get_period_detail.return_value = detail
    mock_complementary_insurance_repository.get_period_plans.return_value = [plan]
    mock_market_data_repository.get_exchange_rate_value.return_value = Decimal("38500")

    service = ComplementaryInsuranceCostComputationService(
        mock_payroll_repository,
        mock_complementary_insurance_repository,
        mock_market_data_repository,
    )
    result = await service.compute(period_id)

    mock_market_data_repository.get_exchange_rate_value.assert_called_once_with(
        "UF", reference_date
    )
    assert len(result.costs) == 1
    # 2 UF * 38500 CLP/UF = 77000
    assert result.costs[0].cost_clp == Decimal("77000")
    assert result.total_cost_clp == Decimal("77000")


@pytest.mark.asyncio
async def test_compute_with_fixed_uf_plan_missing_rate_raises(
    mock_payroll_repository: AsyncMock,
    mock_complementary_insurance_repository: AsyncMock,
    mock_market_data_repository: AsyncMock,
    payroll_summary_dto: PayrollSummaryDTO,
    payroll_period_detail_dto: PayrollPeriodDetailDTO,
) -> None:
    """Test that a FIXED_UF plan with missing UF rate raises EconomicIndexNotFoundError.

    When the market data repository returns None for the UF rate, the service
    must surface an EconomicIndexNotFoundError.
    """
    period_id = 11
    detail, plan = _make_uf_plan_detail_and_plan(
        period_id, payroll_summary_dto, payroll_period_detail_dto
    )

    mock_payroll_repository.get_period_detail.return_value = detail
    mock_complementary_insurance_repository.get_period_plans.return_value = [plan]
    mock_market_data_repository.get_exchange_rate_value.return_value = None

    service = ComplementaryInsuranceCostComputationService(
        mock_payroll_repository,
        mock_complementary_insurance_repository,
        mock_market_data_repository,
    )

    with pytest.raises(EconomicIndexNotFoundError, match="UF rate not found"):
        await service.compute(period_id)
