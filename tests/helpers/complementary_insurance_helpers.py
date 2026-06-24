"""Shared factory helpers for complementary insurance tests."""

from datetime import date
from decimal import Decimal

from payroll.domain.contributions import (
    ComplementaryInsuranceCostType,
    ComplementaryInsurancePlan,
)


def build_complementary_insurance_plan(
    *,
    plan_id: int,
    name: str,
    cost_type: ComplementaryInsuranceCostType,
    cost_value: Decimal,
    cost_currency: str = "CLP",
) -> ComplementaryInsurancePlan:
    """Build a ComplementaryInsurancePlan for tests.

    All structural fields (provider_id, valid_from, valid_to) are fixed to
    reasonable defaults; callers only specify the fields that vary per test.
    """
    return ComplementaryInsurancePlan(
        id=plan_id,
        provider_id=1,
        name=name,
        cost_type=cost_type,
        cost_value=cost_value,
        cost_currency=cost_currency,
        valid_from=date(2024, 1, 1),
        valid_to=None,
    )
