"""Complementary insurance calculation logic."""

from decimal import Decimal

from .contributions import ComplementaryInsuranceCostType, ComplementaryInsurancePlan


def calculate_complementary_insurance_cost(
    plan: ComplementaryInsurancePlan,
    salary_base_clp: Decimal,
    uf_rate_clp: Decimal | None = None,
) -> Decimal:
    """Calculate the cost of a complementary insurance plan.

    Args:
        plan: The complementary insurance plan.
        salary_base_clp: The taxable salary in CLP.
        uf_rate_clp: The UF-to-CLP exchange rate for the period. Required when
            the plan uses a FIXED_UF cost type.

    Returns:
        The calculated cost in CLP.

    Raises:
        ValueError: If plan cost_type is unsupported or uf_rate_clp is missing
            for a FIXED_UF plan.
    """
    if plan.cost_type == ComplementaryInsuranceCostType.FIXED_CLP:
        return plan.cost_value

    if plan.cost_type == ComplementaryInsuranceCostType.FIXED_UF:
        if uf_rate_clp is None:
            raise ValueError(
                f"UF rate is required for FIXED_UF plan '{plan.name}' (id={plan.id})."
            )
        return (plan.cost_value * uf_rate_clp).quantize(Decimal("1"))

    if plan.cost_type == ComplementaryInsuranceCostType.VARIABLE_PERCENTAGE:
        return (salary_base_clp * plan.cost_value / Decimal(100)).quantize(
            Decimal("0.01")
        )

    raise ValueError(f"Unknown cost type: {plan.cost_type}")
