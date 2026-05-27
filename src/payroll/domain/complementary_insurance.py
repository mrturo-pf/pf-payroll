"""Complementary insurance calculation logic."""

from decimal import Decimal

from .contributions import ComplementaryInsuranceCostType, ComplementaryInsurancePlan


def calculate_complementary_insurance_cost(
    plan: ComplementaryInsurancePlan,
    salary_base_clp: Decimal,
) -> Decimal:
    """Calculate the cost of a complementary insurance plan.

    Args:
        plan: The complementary insurance plan.
        salary_base_clp: The taxable salary in CLP.

    Returns:
        The calculated cost in CLP.

    Raises:
        ValueError: If plan cost_type is unsupported.
    """
    if plan.cost_type == ComplementaryInsuranceCostType.FIXED_CLP:
        return plan.cost_value

    if plan.cost_type == ComplementaryInsuranceCostType.FIXED_UF:
        raise NotImplementedError(
            "Fixed UF complementary insurance cost calculation requires UF rate."
        )

    if plan.cost_type == ComplementaryInsuranceCostType.VARIABLE_PERCENTAGE:
        return (salary_base_clp * plan.cost_value / Decimal(100)).quantize(
            Decimal("0.01")
        )

    raise ValueError(f"Unknown cost type: {plan.cost_type}")
