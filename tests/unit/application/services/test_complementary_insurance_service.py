"""Tests for ComplementaryInsuranceService."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from payroll.application.dto import PayrollPeriodDetailDTO, PayrollSummaryDTO
from payroll.application.services.complementary_insurance_service import (
    ComplementaryInsuranceService,
)
from payroll.domain.contributions import ComplementaryInsuranceCostType
from helpers.complementary_insurance_helpers import (
    build_complementary_insurance_plan,
)


@pytest.fixture
def service(
    mock_payroll_repository: AsyncMock,
    mock_complementary_insurance_repository: AsyncMock,
) -> ComplementaryInsuranceService:
    """Create service instance."""
    return ComplementaryInsuranceService(
        mock_payroll_repository, mock_complementary_insurance_repository
    )


@pytest.mark.asyncio
async def test_assign_plans_for_period_with_vigent_plans(
    service: ComplementaryInsuranceService,
    mock_payroll_repository: AsyncMock,
    mock_complementary_insurance_repository: AsyncMock,
    payroll_summary_dto: PayrollSummaryDTO,
    payroll_period_detail_dto: PayrollPeriodDetailDTO,
) -> None:
    """Test assigning plans when vigent plans exist."""
    period_id = 123
    payment_date = date(2025, 5, 30)
    reference_date = date(2025, 6, 1)  # First day of following month
    summary = replace(
        payroll_summary_dto,
        period_id=period_id,
        payment_date=payment_date,
        total_discounts_clp=Decimal("180000"),
        net_pay_clp=Decimal("1070000"),
    )
    detail = replace(
        payroll_period_detail_dto,
        id=period_id,
        payment_date=payment_date,
        summary=summary,
    )

    plan1 = build_complementary_insurance_plan(
        plan_id=10,
        name="Plan A",
        cost_type=ComplementaryInsuranceCostType.FIXED_CLP,
        cost_value=Decimal("50000"),
    )
    plan2 = build_complementary_insurance_plan(
        plan_id=20,
        name="Plan B",
        cost_type=ComplementaryInsuranceCostType.VARIABLE_PERCENTAGE,
        cost_value=Decimal("2.5"),
    )

    mock_payroll_repository.get_period_detail.return_value = detail
    mock_complementary_insurance_repository.get_vigent_plans.return_value = [
        plan1,
        plan2,
    ]

    await service.assign_plans_for_period(period_id)

    mock_payroll_repository.get_period_detail.assert_called_once_with(period_id)
    mock_complementary_insurance_repository.get_vigent_plans.assert_called_once_with(
        reference_date
    )
    mock_complementary_insurance_repository.assign_plans_to_period.assert_called_once_with(
        period_id, [10, 20]
    )


@pytest.mark.asyncio
async def test_assign_plans_for_period_with_no_vigent_plans(
    service: ComplementaryInsuranceService,
    mock_payroll_repository: AsyncMock,
    mock_complementary_insurance_repository: AsyncMock,
    payroll_summary_dto: PayrollSummaryDTO,
    payroll_period_detail_dto: PayrollPeriodDetailDTO,
) -> None:
    """Test when no vigent plans exist."""
    period_id = 123
    payment_date = date(2025, 5, 30)
    summary = replace(
        payroll_summary_dto,
        period_id=period_id,
        payment_date=payment_date,
        total_discounts_clp=Decimal("180000"),
        net_pay_clp=Decimal("1070000"),
    )
    detail = replace(
        payroll_period_detail_dto,
        id=period_id,
        payment_date=payment_date,
        summary=summary,
    )

    mock_payroll_repository.get_period_detail.return_value = detail
    mock_complementary_insurance_repository.get_vigent_plans.return_value = []

    await service.assign_plans_for_period(period_id)

    mock_complementary_insurance_repository.assign_plans_to_period.assert_not_called()


@pytest.mark.asyncio
async def test_assign_plans_for_period_with_missing_detail(
    service: ComplementaryInsuranceService,
    mock_payroll_repository: AsyncMock,
    mock_complementary_insurance_repository: AsyncMock,
) -> None:
    """Test when period detail is missing."""
    period_id = 999

    mock_payroll_repository.get_period_detail.return_value = None

    await service.assign_plans_for_period(period_id)

    mock_complementary_insurance_repository.get_vigent_plans.assert_not_called()
    mock_complementary_insurance_repository.assign_plans_to_period.assert_not_called()


@pytest.mark.asyncio
async def test_assign_plans_for_period_with_missing_summary(
    service: ComplementaryInsuranceService,
    mock_payroll_repository: AsyncMock,
    mock_complementary_insurance_repository: AsyncMock,
    payroll_period_detail_dto: PayrollPeriodDetailDTO,
) -> None:
    """Test when summary is missing."""
    period_id = 123
    detail = replace(payroll_period_detail_dto, id=period_id, summary=None)

    mock_payroll_repository.get_period_detail.return_value = detail

    await service.assign_plans_for_period(period_id)

    mock_complementary_insurance_repository.get_vigent_plans.assert_not_called()
    mock_complementary_insurance_repository.assign_plans_to_period.assert_not_called()
