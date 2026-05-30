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
        - Verification that declared contribution (employer + employee) relates
          to calculated cost
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
        # Total declared = employer contribution + employee deduction (health_insurance)
        employer_declared = self._extract_declared_employer_contribution(detail)
        employee_declared = self._extract_employee_health_insurance(detail)

        # If neither is present, skip validation
        if employer_declared is None and employee_declared is None:
            warnings.append(
                "No declared complementary insurance amounts found in CSV "
                "(neither health_insurance_employer_contribution nor "
                "health_insurance). Validation skipped."
            )
            return True, warnings

        # Total declared amount (both employer and employee contributions)
        total_declared = (employer_declared or Decimal("0")) + (
            employee_declared or Decimal("0")
        )

        gross_income = detail.summary.gross_income_clp
        taxable_income = detail.summary.taxable_income_clp
        total_legal_deductions = gross_income - taxable_income

        # Build audit trail
        audit = ComplementaryInsuranceValidationAuditDTO(
            period_id=detail.id,
            gross_income_clp=gross_income,
            taxable_income_clp=taxable_income,
            total_legal_deductions_clp=total_legal_deductions,
            declared_employer_contribution_clp=total_declared,
            calculated_total_cost_clp=computed_costs.total_cost_clp,
            individual_plan_costs=computed_costs.costs,
        )

        # Validate: Compare total declared vs calculated (with tolerance)
        tolerance = Decimal("100")
        difference = abs(computed_costs.total_cost_clp - total_declared)

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
                    employer_declared,
                    employee_declared,
                )
            )

        # Validate deduction chain consistency
        deduction_warnings = self._validate_deduction_chain(detail, total_declared)
        warnings.extend(deduction_warnings)

        return True, warnings

    def _build_cost_discrepancy_warnings(
        self,
        audit: ComplementaryInsuranceValidationAuditDTO,
        year: int,
        month: int,
        employer_declared: Decimal | None,
        employee_declared: Decimal | None,
    ) -> list[str]:
        """Build detailed warnings for cost discrepancies.

        Shows breakdown of employer and employee contributions and compares
        the total against calculated costs.
        """
        warnings: list[str] = []

        employer_part = employer_declared or Decimal("0")
        employee_part = employee_declared or Decimal("0")
        total_declared = employer_part + employee_part

        warnings.append(
            f"[{year}-{month:02d}] Complementary insurance cost discrepancy "
            f"detected. CSV declares: "
            f"employer contribution {employer_part} CLP + "
            f"employee deduction {employee_part} CLP = "
            f"{total_declared} CLP total, but calculation "
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
        calculated = audit.calculated_total_cost_clp
        if calculated > total_declared:
            if total_declared > 0:
                variance_pct = (
                    (calculated - total_declared) / total_declared * Decimal(100)
                )
            else:
                variance_pct = Decimal(0)
            warnings.append(
                f"Calculated cost is {variance_pct:.1f}% higher than declared total. "
                "Verify that salary base, UF rates, plan rates are current, "
                "and that all complementary insurance plans have been assigned."
            )
        else:
            # When calculated <= total_declared with a discrepancy > 100,
            # total_declared must be > 100 (so the division below is safe)
            variance_pct = (total_declared - calculated) / total_declared * Decimal(100)
            warnings.append(
                f"Declared total is {variance_pct:.1f}% higher than "
                f"calculated. Verify plan assignments, rates, and salary data "
                "for accuracy."
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
        total_discounts = detail.summary.total_discounts_clp
        net_pay = detail.summary.net_pay_clp

        # Check 1: Gross - Total Discounts = Net Pay (consistency)
        # This validates the core payroll equation
        calculated_net_pay = gross - total_discounts
        if abs(calculated_net_pay - net_pay) > Decimal("1"):
            warnings.append(
                f"Payroll deduction chain inconsistency: "
                f"(Gross {gross} - Total Discounts {total_discounts} = "
                f"{calculated_net_pay}) but Net Pay = {net_pay}. "
                "This indicates the payroll balance is incorrect."
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

        Returns the declared amount (including 0), or None if the concept
        code is not found in the CSV at all.
        """
        # Search for declared health insurance employer contribution items
        items = [
            item.amount_clp
            for item in detail.items
            if item.concept_code == "HEALTH_INSURANCE_EMPLOYER_CONTRIBUTION"
        ]

        # If items exist (even if all are 0), return the sum
        # Only return None if the concept_code was never found
        if items:
            return sum(items, Decimal("0"))

        return None

    def _extract_employee_health_insurance(
        self, detail: PayrollPeriodDetailDTO
    ) -> Decimal | None:
        """Extract declared employee health insurance deduction from payroll items.

        Searches for items with concept_code == "HEALTH_INSURANCE" (employee deduction).

        Returns the declared amount (including 0), or None if the concept
        code is not found in the CSV at all. This follows the same logic as
        _extract_declared_employer_contribution to maintain consistency:
        - Decimal('0') means the concept was in the CSV with value 0
        - None means the concept was never declared in the CSV
        """
        # Search for declared health insurance (employee deduction) items
        items = [
            item.amount_clp
            for item in detail.items
            if item.concept_code == "HEALTH_INSURANCE"
        ]

        # If items exist (even if all are 0), return the sum
        # Only return None if the concept_code was never found
        if items:
            return sum(items, Decimal("0"))

        return None
