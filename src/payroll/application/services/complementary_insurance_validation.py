"""Service for validating complementary insurance costs against declared amounts."""

from decimal import Decimal

from payroll.application.dto import (
    ComplementaryInsuranceValidationAuditDTO,
    ComputeComplementaryInsuranceResultDTO,
    PayrollPeriodDetailDTO,
)
from payroll.application.errors import PayrollValidationError


class ComplementaryInsuranceValidationError(PayrollValidationError):
    """Raised when complementary insurance validation fails."""


class ComplementaryInsuranceValidationService:
    """Validates calculated complementary insurance costs against declared amounts.

    This service performs comprehensive validation of complementary insurance
    costs by:
    1. Comparing calculated costs against declared employer contributions
    2. Validating the chain: gross income → deductions → taxable → insurance
    3. Generating detailed audit trails for traceability
    """

    async def validate(
        self,
        detail: PayrollPeriodDetailDTO,
        computed_costs: ComputeComplementaryInsuranceResultDTO,
    ) -> tuple[bool, list[str]]:
        """Validate computed complementary insurance costs with full audit trail.

        Performs comprehensive validation including:
        - Verification that declared contribution relates to calculated cost
        - Validation of the deduction chain (gross → taxable → insurance)
        - Tolerance-based matching (100 CLP for rounding)

        Args:
            detail: The payroll period details.
            computed_costs: The computed complementary insurance costs.

        Returns:
            Tuple of (is_valid, list_of_warnings).
        """
        warnings: list[str] = []

        # Guard: ensure summary exists
        if detail.summary is None:
            return True, warnings

        # Extract key amounts from the payroll period
        declared_amount = self._extract_declared_employer_contribution(detail)
        gross_income = detail.summary.gross_income_clp
        taxable_income = detail.summary.taxable_income_clp
        total_legal_deductions = gross_income - taxable_income

        # Build audit trail
        audit = ComplementaryInsuranceValidationAuditDTO(
            period_id=detail.id,
            gross_income_clp=gross_income,
            taxable_income_clp=taxable_income,
            total_legal_deductions_clp=total_legal_deductions,
            declared_employer_contribution_clp=declared_amount,
            calculated_total_cost_clp=computed_costs.total_cost_clp,
            individual_plan_costs=computed_costs.costs,
        )

        # Validate 1: Check if declared amount exists
        if declared_amount is None:
            warnings.append(
                "No declared complementary insurance employer contribution "
                "(health_insurance_employer_contribution) found in CSV. "
                "Validation will be performed on calculated costs only."
            )
            return True, warnings

        # Validate 2: Compare declared vs calculated (with tolerance)
        tolerance = Decimal("100")
        difference = abs(computed_costs.total_cost_clp - declared_amount)

        if difference > tolerance:
            audit_with_diff = ComplementaryInsuranceValidationAuditDTO(
                period_id=audit.period_id,
                gross_income_clp=audit.gross_income_clp,
                taxable_income_clp=audit.taxable_income_clp,
                total_legal_deductions_clp=audit.total_legal_deductions_clp,
                declared_employer_contribution_clp=audit.declared_employer_contribution_clp,
                calculated_total_cost_clp=audit.calculated_total_cost_clp,
                individual_plan_costs=audit.individual_plan_costs,
                difference_clp=difference,
                tolerance_clp=tolerance,
                has_discrepancy=True,
            )
            warnings.extend(
                self._build_cost_discrepancy_warnings(
                    audit_with_diff,
                    detail.summary.period_year,
                    detail.summary.period_month,
                )
            )

        # Validate 3: Cross-check deduction chain consistency
        deduction_warnings = self._validate_deduction_chain(detail, declared_amount)
        warnings.extend(deduction_warnings)

        return True, warnings

    def _build_cost_discrepancy_warnings(
        self, audit: ComplementaryInsuranceValidationAuditDTO, year: int, month: int
    ) -> list[str]:
        """Build detailed warnings for cost discrepancies."""
        warnings: list[str] = []

        warnings.append(
            f"[{year}-{month:02d}] Complementary insurance cost discrepancy "
            f"detected: CSV declares "
            f"{audit.declared_employer_contribution_clp} CLP but calculation "
            f"based on assigned plans yields {audit.calculated_total_cost_clp} CLP "
            f"(difference: {audit.difference_clp} CLP, "
            f"tolerance: {audit.tolerance_clp} CLP)."
        )

        # Provide plan-level details
        if audit.individual_plan_costs:
            plan_details = "; ".join(
                f"{cost.plan_name} = {cost.cost_clp} CLP"
                for cost in audit.individual_plan_costs
            )
            warnings.append(
                f"Plan breakdown: {plan_details}. "
                "This variance may indicate discrepancies in plan assignment, "
                "salary data, or economic indices (UF rates)."
            )

        # Provide guidance on root cause
        declared = audit.declared_employer_contribution_clp or Decimal("0")
        calculated = audit.calculated_total_cost_clp
        if calculated > declared:
            variance_pct = (
                ((calculated - declared) / declared * Decimal(100))
                if declared > 0
                else Decimal(0)
            )
            warnings.append(
                f"Calculated cost is {variance_pct:.1f}% higher than declared. "
                "Verify that salary base, UF rates, and plan rates are current."
            )
        else:
            variance_pct = (
                ((declared - calculated) / declared * Decimal(100))
                if declared > 0
                else Decimal(0)
            )
            warnings.append(
                f"Declared contribution is {variance_pct:.1f}% higher than "
                f"calculated. Verify that all complementary insurance plans have "
                "been assigned or check for data entry discrepancies."
            )

        return warnings

    def _validate_deduction_chain(
        self, detail: PayrollPeriodDetailDTO, declared_amount: Decimal
    ) -> list[str]:
        """Validate that deduction chain is consistent and logically sound."""
        warnings: list[str] = []

        # Guard: ensure summary exists
        if detail.summary is None:
            return warnings

        gross = detail.summary.gross_income_clp
        taxable = detail.summary.taxable_income_clp
        total_discounts = detail.summary.total_discounts_clp

        # Check 1: Gross - Taxable = Total Deductions (consistency)
        calculated_deductions = gross - taxable
        if abs(calculated_deductions - total_discounts) > Decimal("1"):
            warnings.append(
                f"Payroll deduction chain inconsistency: "
                f"(Gross {gross} - Taxable {taxable} = {calculated_deductions}) "
                f"but total_discounts = {total_discounts}. "
                "This may affect insurance cost calculations."
            )

        # Check 2: Verify that declared contribution is reasonable vs gross
        if declared_amount and gross > 0:
            contribution_ratio = declared_amount / gross * Decimal(100)
            if contribution_ratio > Decimal("15"):
                warnings.append(
                    f"Unusually high complementary insurance contribution "
                    f"ratio: {contribution_ratio:.2f}% of gross income. "
                    "Verify that health_insurance_employer_contribution "
                    "is correctly reported."
                )

        return warnings

    def _extract_declared_employer_contribution(
        self, detail: PayrollPeriodDetailDTO
    ) -> Decimal | None:
        """Extract declared employer contribution from payroll items.

        Searches for items with concept_code matching complementary insurance
        or health insurance employer contribution patterns.
        """
        # Search for declared health insurance employer contribution items
        amount = sum(
            (
                item.amount_clp
                for item in detail.items
                if item.concept_code == "HEALTH_INSURANCE_EMPLOYER_CONTRIBUTION"
            ),
            Decimal("0"),
        )
        return amount if amount > 0 else None
