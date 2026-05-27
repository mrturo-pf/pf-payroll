"""Tests for ComplementaryInsuranceService."""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from payroll.application.dto import PayrollPeriodDetailDTO, PayrollSummaryDTO
from payroll.application.services.complementary_insurance_service import (
    ComplementaryInsuranceService,
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
) -> None:
    """Test assigning plans when vigent plans exist."""
    period_id = 123
    payment_date = date(2025, 5, 30)
    summary = PayrollSummaryDTO(
        period_id=period_id,
        employer_id=1,
        employer_name="Test Corp",
        period_year=2025,
        period_month=5,
        payment_date=payment_date,
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
        payment_date=payment_date,
        status="actual",
        employment_contract_kind="indefinite",
        worked_days=30,
        summary=summary,
        items=[],
        pension_plan_id=1,
        health_plan_id=2,
    )

    plan1 = ComplementaryInsurancePlan(
        id=10,
        provider_id=1,
        name="Plan A",
        cost_type=ComplementaryInsuranceCostType.FIXED_CLP,
        cost_value=Decimal("50000"),
        cost_currency="CLP",
        valid_from=date(2024, 1, 1),
        valid_to=None,
    )
    plan2 = ComplementaryInsurancePlan(
        id=20,
        provider_id=1,
        name="Plan B",
        cost_type=ComplementaryInsuranceCostType.VARIABLE_PERCENTAGE,
        cost_value=Decimal("2.5"),
        cost_currency="CLP",
        valid_from=date(2024, 1, 1),
        valid_to=None,
    )

    mock_payroll_repository.get_period_detail.return_value = detail
    mock_complementary_insurance_repository.get_vigent_plans.return_value = [
        plan1,
        plan2,
    ]

    await service.assign_plans_for_period(period_id)

    mock_payroll_repository.get_period_detail.assert_called_once_with(period_id)
    mock_complementary_insurance_repository.get_vigent_plans.assert_called_once_with(
        payment_date
    )
    mock_complementary_insurance_repository.assign_plans_to_period.assert_called_once_with(
        period_id, [10, 20]
    )


@pytest.mark.asyncio
async def test_assign_plans_for_period_with_no_vigent_plans(
    service: ComplementaryInsuranceService,
    mock_payroll_repository: AsyncMock,
    mock_complementary_insurance_repository: AsyncMock,
) -> None:
    """Test when no vigent plans exist."""
    period_id = 123
    payment_date = date(2025, 5, 30)
    summary = PayrollSummaryDTO(
        period_id=period_id,
        employer_id=1,
        employer_name="Test Corp",
        period_year=2025,
        period_month=5,
        payment_date=payment_date,
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
        payment_date=payment_date,
        status="actual",
        employment_contract_kind="indefinite",
        worked_days=30,
        summary=summary,
        items=[],
        pension_plan_id=1,
        health_plan_id=2,
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
) -> None:
    """Test when summary is missing."""
    period_id = 123
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
        summary=None,
        items=[],
        pension_plan_id=1,
        health_plan_id=2,
    )

    mock_payroll_repository.get_period_detail.return_value = detail

    await service.assign_plans_for_period(period_id)

    mock_complementary_insurance_repository.get_vigent_plans.assert_not_called()
    mock_complementary_insurance_repository.assign_plans_to_period.assert_not_called()
