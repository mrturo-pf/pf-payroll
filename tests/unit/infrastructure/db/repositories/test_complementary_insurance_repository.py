"""Unit tests for complementary insurance repository."""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from payroll.domain.complementary_insurance import ComplementaryInsurancePlan
from payroll.domain.contributions import ComplementaryInsuranceCostType
from payroll.infrastructure.db.models.reference_data import (
    ComplementaryInsurancePlanModel,
)
from payroll.infrastructure.db.repositories.complementary_insurance_repository import (
    SqlAlchemyComplementaryInsuranceRepository,
    _map_plan_model_to_domain,
)


def test_map_plan_model_to_domain() -> None:
    """Test mapping a plan model to domain entity."""
    model = ComplementaryInsurancePlanModel(
        id=1,
        provider_id=1,
        name="Test Plan",
        cost_type=ComplementaryInsuranceCostType.FIXED_CLP,
        cost_value=Decimal("50000"),
        cost_currency="CLP",
        valid_from=date(2025, 1, 1),
        valid_to=None,
    )

    plan = _map_plan_model_to_domain(model)

    assert isinstance(plan, ComplementaryInsurancePlan)
    assert plan.id == 1
    assert plan.provider_id == 1
    assert plan.name == "Test Plan"
    assert plan.cost_type == ComplementaryInsuranceCostType.FIXED_CLP
    assert plan.cost_value == Decimal("50000")
    assert plan.cost_currency == "CLP"
    assert plan.valid_from == date(2025, 1, 1)
    assert plan.valid_to is None


@pytest.mark.asyncio
async def test_get_vigent_plans_returns_mapped_plans() -> None:
    """Test that get_vigent_plans returns properly mapped plans."""
    mock_session = AsyncMock()
    model = ComplementaryInsurancePlanModel(
        id=1,
        provider_id=1,
        name="Plan A",
        cost_type=ComplementaryInsuranceCostType.FIXED_CLP,
        cost_value=Decimal("50000"),
        cost_currency="CLP",
        valid_from=date(2025, 1, 1),
        valid_to=None,
    )
    mock_result = MagicMock()
    mock_result.scalars().all.return_value = [model]
    mock_session.execute.return_value = mock_result

    repo = SqlAlchemyComplementaryInsuranceRepository(mock_session)
    plans = await repo.get_vigent_plans(date(2025, 6, 15))

    assert len(plans) == 1
    assert plans[0].id == 1
    assert plans[0].name == "Plan A"


@pytest.mark.asyncio
async def test_get_plan_by_id_found() -> None:
    """Test getting an existing plan by ID."""
    mock_session = AsyncMock()
    model = ComplementaryInsurancePlanModel(
        id=1,
        provider_id=1,
        name="Plan A",
        cost_type=ComplementaryInsuranceCostType.VARIABLE_PERCENTAGE,
        cost_value=Decimal("2.5"),
        cost_currency="CLP",
        valid_from=date(2025, 1, 1),
        valid_to=None,
    )
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = model
    mock_session.execute.return_value = mock_result

    repo = SqlAlchemyComplementaryInsuranceRepository(mock_session)
    plan = await repo.get_plan_by_id(1)

    assert plan is not None
    assert plan.id == 1
    assert plan.name == "Plan A"


@pytest.mark.asyncio
async def test_get_plan_by_id_not_found() -> None:
    """Test getting a non-existent plan by ID."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    repo = SqlAlchemyComplementaryInsuranceRepository(mock_session)
    plan = await repo.get_plan_by_id(999)

    assert plan is None


@pytest.mark.asyncio
async def test_assign_plans_to_period_empty_list() -> None:
    """Test assigning empty plan list to period."""
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    repo = SqlAlchemyComplementaryInsuranceRepository(mock_session)

    await repo.assign_plans_to_period(1, [])

    mock_session.add.assert_not_called()


@pytest.mark.asyncio
async def test_assign_plans_to_period_adds_new_plans() -> None:
    """Test assigning new plans to period."""
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    repo = SqlAlchemyComplementaryInsuranceRepository(mock_session)
    await repo.assign_plans_to_period(1, [1, 2])

    assert mock_session.add.call_count == 2


@pytest.mark.asyncio
async def test_get_period_plans() -> None:
    """Test getting plans assigned to a period."""
    mock_session = AsyncMock()
    model = ComplementaryInsurancePlanModel(
        id=1,
        provider_id=1,
        name="Plan A",
        cost_type=ComplementaryInsuranceCostType.FIXED_CLP,
        cost_value=Decimal("50000"),
        cost_currency="CLP",
        valid_from=date(2025, 1, 1),
        valid_to=None,
    )
    mock_result = MagicMock()
    mock_result.scalars().all.return_value = [model]
    mock_session.execute.return_value = mock_result

    repo = SqlAlchemyComplementaryInsuranceRepository(mock_session)
    plans = await repo.get_period_plans(1)

    assert len(plans) == 1
    assert plans[0].name == "Plan A"
