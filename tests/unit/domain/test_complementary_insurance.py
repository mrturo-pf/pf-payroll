"""Tests for complementary insurance calculation logic."""

from datetime import date
from decimal import Decimal

import pytest

from payroll.domain.complementary_insurance import (
    calculate_complementary_insurance_cost,
)
from payroll.domain.contributions import (
    ComplementaryInsuranceCostType,
    ComplementaryInsurancePlan,
)


def test_calculate_complementary_insurance_cost_fixed_clp() -> None:
    """Test fixed CLP cost calculation."""
    plan = ComplementaryInsurancePlan(
        id=1,
        provider_id=1,
        name="Plan A",
        cost_type=ComplementaryInsuranceCostType.FIXED_CLP,
        cost_value=Decimal("50000"),
        cost_currency="CLP",
        valid_from=date(2025, 1, 1),
        valid_to=None,
    )
    salary_base = Decimal("3000000")

    cost = calculate_complementary_insurance_cost(plan, salary_base)

    assert cost == Decimal("50000")


def test_calculate_complementary_insurance_cost_variable_percentage() -> None:
    """Test variable percentage cost calculation."""
    plan = ComplementaryInsurancePlan(
        id=2,
        provider_id=1,
        name="Plan B",
        cost_type=ComplementaryInsuranceCostType.VARIABLE_PERCENTAGE,
        cost_value=Decimal("2.5"),
        cost_currency="CLP",
        valid_from=date(2025, 1, 1),
        valid_to=None,
    )
    salary_base = Decimal("3000000")

    cost = calculate_complementary_insurance_cost(plan, salary_base)

    assert cost == Decimal("75000.00")


def test_calculate_complementary_insurance_cost_variable_percentage_rounding() -> None:
    """Test variable percentage cost calculation with rounding."""
    plan = ComplementaryInsurancePlan(
        id=3,
        provider_id=1,
        name="Plan C",
        cost_type=ComplementaryInsuranceCostType.VARIABLE_PERCENTAGE,
        cost_value=Decimal("1.33"),
        cost_currency="CLP",
        valid_from=date(2025, 1, 1),
        valid_to=None,
    )
    salary_base = Decimal("2500000")

    cost = calculate_complementary_insurance_cost(plan, salary_base)

    assert cost == Decimal("33250.00")


def test_calculate_complementary_insurance_cost_fixed_uf_not_implemented() -> None:
    """Test that fixed UF cost calculation raises NotImplementedError."""
    plan = ComplementaryInsurancePlan(
        id=4,
        provider_id=1,
        name="Plan D",
        cost_type=ComplementaryInsuranceCostType.FIXED_UF,
        cost_value=Decimal("2.5"),
        cost_currency="UF",
        valid_from=date(2025, 1, 1),
        valid_to=None,
    )
    salary_base = Decimal("3000000")

    with pytest.raises(NotImplementedError):
        calculate_complementary_insurance_cost(plan, salary_base)


def test_calculate_complementary_insurance_cost_unknown_type() -> None:
    """Test that unknown cost type raises ValueError."""
    plan = ComplementaryInsurancePlan(
        id=5,
        provider_id=1,
        name="Plan E",
        cost_type="invalid_type",  # type: ignore
        cost_value=Decimal("2.5"),
        cost_currency="CLP",
        valid_from=date(2025, 1, 1),
        valid_to=None,
    )
    salary_base = Decimal("3000000")

    with pytest.raises(ValueError, match="Unknown cost type"):
        calculate_complementary_insurance_cost(plan, salary_base)
