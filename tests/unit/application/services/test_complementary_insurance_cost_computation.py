"""Tests for ComplementaryInsuranceCostComputationService."""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from payroll.application.dto import PayrollPeriodDetailDTO, PayrollSummaryDTO
from payroll.application.errors import EconomicIndexNotFoundError
from payroll.application.services.complementary_insurance_cost_computation import (
    ComplementaryInsuranceCostComputationService,
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
def mock_market_data_repository() -> AsyncMock:
    """Create mock market data repository."""
    return AsyncMock()


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
) -> None:
    """Test computing costs with both fixed and variable plans."""
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

    plan1 = ComplementaryInsurancePlan(
        id=10,
        provider_id=1,
        name="Plan A (Fixed)",
        cost_type=ComplementaryInsuranceCostType.FIXED_CLP,
        cost_value=Decimal("50000"),
        cost_currency="CLP",
        valid_from=date(2024, 1, 1),
        valid_to=None,
    )
    plan2 = ComplementaryInsurancePlan(
        id=20,
        provider_id=1,
        name="Plan B (2%)",
        cost_type=ComplementaryInsuranceCostType.VARIABLE_PERCENTAGE,
        cost_value=Decimal("2.5"),
        cost_currency="CLP",
        valid_from=date(2024, 1, 1),
        valid_to=None,
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
) -> None:
    """Test computing when no plans are assigned."""
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
) -> None:
    """Test computing when summary is missing."""
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
) -> None:
    """Test computing costs with a FIXED_UF plan fetches and converts UF rate."""
    period_id = 10
    payment_date = date(2025, 3, 31)
    reference_date = date(2025, 4, 1)  # First day of following month
    summary = PayrollSummaryDTO(
        period_id=period_id,
        employer_id=1,
        employer_name="Test Corp",
        period_year=2025,
        period_month=3,
        payment_date=payment_date,
        taxable_income_clp=Decimal("3000000"),
        gross_income_clp=Decimal("3500000"),
        total_discounts_clp=Decimal("400000"),
        net_pay_clp=Decimal("3100000"),
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
        period_month=3,
        payment_date=payment_date,
        status="actual",
        employment_contract_kind="indefinite",
        worked_days=30,
        summary=summary,
        items=[],
        pension_plan_id=1,
        health_plan_id=2,
    )
    plan = ComplementaryInsurancePlan(
        id=30,
        provider_id=1,
        name="Plan UF",
        cost_type=ComplementaryInsuranceCostType.FIXED_UF,
        cost_value=Decimal("2"),
        cost_currency="UF",
        valid_from=date(2024, 1, 1),
        valid_to=None,
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
) -> None:
    """Test that a FIXED_UF plan with missing UF rate raises EconomicIndexNotFoundError.

    When the market data repository returns None for the UF rate, the service
    must surface an EconomicIndexNotFoundError.
    """
    period_id = 11
    summary = PayrollSummaryDTO(
        period_id=period_id,
        employer_id=1,
        employer_name="Test Corp",
        period_year=2025,
        period_month=3,
        payment_date=date(2025, 3, 31),
        taxable_income_clp=Decimal("3000000"),
        gross_income_clp=Decimal("3500000"),
        total_discounts_clp=Decimal("400000"),
        net_pay_clp=Decimal("3100000"),
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
        period_month=3,
        payment_date=date(2025, 3, 31),
        status="actual",
        employment_contract_kind="indefinite",
        worked_days=30,
        summary=summary,
        items=[],
        pension_plan_id=1,
        health_plan_id=2,
    )
    plan = ComplementaryInsurancePlan(
        id=30,
        provider_id=1,
        name="Plan UF",
        cost_type=ComplementaryInsuranceCostType.FIXED_UF,
        cost_value=Decimal("2"),
        cost_currency="UF",
        valid_from=date(2024, 1, 1),
        valid_to=None,
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
