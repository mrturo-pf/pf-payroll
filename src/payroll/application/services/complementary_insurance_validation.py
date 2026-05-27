"""Service for validating complementary insurance costs against declared amounts."""

from decimal import Decimal

from payroll.application.dto import (
    ComputeComplementaryInsuranceResultDTO,
    PayrollPeriodDetailDTO,
)
from payroll.application.errors import PayrollValidationError


class ComplementaryInsuranceValidationError(PayrollValidationError):
    """Raised when complementary insurance validation fails."""


class ComplementaryInsuranceValidationService:
    """Validates calculated complementary insurance costs against declared amounts."""

    async def validate(
        self,
        detail: PayrollPeriodDetailDTO,
        computed_costs: ComputeComplementaryInsuranceResultDTO,
    ) -> tuple[bool, list[str]]:
        """Validate computed complementary insurance costs.

        Compares the total calculated cost (after deductions) against the declared
        health_insurance_employer_contribution in the CSV. The employer contribution
        is taxable and subject to legal deductions (AFP, ISAPRE, income tax).

        Args:
            detail: The payroll period details.
            computed_costs: The computed complementary insurance costs.

        Returns:
            Tuple of (is_valid, list_of_warnings).
        """
        warnings: list[str] = []

        # Extract declared employer contribution from imported items
        declared_amount = self._extract_declared_employer_contribution(detail)
        if declared_amount is None:
            warnings.append(
                "No declared complementary insurance employer contribution "
                "(health_insurance_employer_contribution) found in CSV."
            )
            return True, warnings

        total_calculated = computed_costs.total_cost_clp

        # Check if totals match (with reasonable tolerance for rounding)
        tolerance = Decimal("100")  # Allow 100 CLP difference due to rounding
        if abs(total_calculated - declared_amount) > tolerance:
            difference = abs(total_calculated - declared_amount)
            warnings.append(
                f"Complementary insurance cost discrepancy: calculated "
                f"{total_calculated} but CSV declared {declared_amount} "
                f"(difference: {difference})"
            )

        return len(warnings) == 0 or all(
            "discrepancy" not in w.lower() for w in warnings
        ), warnings

    def _extract_declared_employer_contribution(
        self, detail: PayrollPeriodDetailDTO
    ) -> Decimal | None:
        """Extract declared employer contribution from payroll items."""
        # The health_insurance_employer_contribution is stored as a payroll item
        # with concept_code = "HEALTH_INSURANCE_EMPLOYER_CONTRIBUTION" or similar
        # For now, we sum items with this code
        amount = sum(
            (
                item.amount_clp
                for item in detail.items
                if item.concept_code == "HEALTH_INSURANCE_EMPLOYER_CONTRIBUTION"
            ),
            Decimal("0"),
        )
        return amount if amount > 0 else None
