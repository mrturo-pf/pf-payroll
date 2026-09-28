"""Shared constants."""

from decimal import Decimal

DEFAULT_CURRENCY = "CLP"
# Shared by ContributionComputationService (PENSION_BASE, PENSION_ADDITIONAL,
# HEALTH_BASE, HEALTH_ADDITIONAL_UF) and ComplementaryInsuranceValidationService
# (total declared vs. calculated employer contribution) for an analogous
# declared-vs-calculated CLP comparison: absorbs rounding noise that
# naturally accumulates across a chain of quantize_clp() calls (rate *
# capped_base, contracted_uf * uf_value, subtraction, ...). Raised from 100
# to 150 on 2026-09-27 to also cover 2025-02's HEALTH_ADDITIONAL_UF residual
# (declared $2,860 vs. computed $2,727 -- a $133 gap from not knowing the
# exact mid-month health plan enrollment date/time-of-day, not from
# rounding) -- see docs/investigations/health-additional-uf-mismatch.md,
# Session 11, for the full analysis and the explicit trade-off this widening
# accepts: every reconciliation check sharing this constant is now less
# sensitive to genuine $100-150 CLP discrepancies, ecosystem-wide, going
# forward, not just for that one period.
RECONCILIATION_TOLERANCE_CLP = Decimal("150")
MONTHLY_EXCHANGE_RATE_CODES = frozenset({"UTM"})
UNEMPLOYMENT_INSURANCE_CONCEPT_CODE = "UNEMPLOYMENT_INSURANCE"
HEALTH_ADDITIONAL_CONCEPT_CODE = "HEALTH_ADDITIONAL_UF"

# The 3 concept codes every real Chilean payslip declares directly: mandatory
# legal pension + health base contributions, always present (and always > 0
# for anyone earning above the legal minimum). Deliberately excludes
# HEALTH_ADDITIONAL_UF (the Isapre plan "top-up") -- unlike these 3, it is
# genuinely optional: an employee whose Isapre plan costs exactly the legal
# base pays nothing extra, so no such line item ever appears on their
# payslip, and it must never be *required* to exist. Confirmed against 22
# real payslips: 3 real periods (no additional Isapre plan until a later
# month) were permanently stuck in a "pending" net-pay reconciliation
# because HEALTH_ADDITIONAL_UF used to be treated as mandatory here even
# though PENSION_BASE/PENSION_ADDITIONAL/HEALTH_BASE/UNEMPLOYMENT_INSURANCE/
# INCOME_TAX were all already present and sufficient to reconcile net pay.
MANDATORY_DECLARED_CONTRIBUTION_CONCEPT_CODES = frozenset(
    {"PENSION_BASE", "PENSION_ADDITIONAL", "HEALTH_BASE"}
)
# Concept codes save_computed_contributions() persists (or replaces) whenever
# a human explicitly runs compute-contributions -- includes
# HEALTH_ADDITIONAL_UF on purpose, since that call must still be able to
# persist a genuine $0 additional-plan amount when a plan happens to cost
# exactly the legal base.
COMPUTED_CONTRIBUTION_CONCEPT_CODES = (
    MANDATORY_DECLARED_CONTRIBUTION_CONCEPT_CODES
    | frozenset({HEALTH_ADDITIONAL_CONCEPT_CODE, UNEMPLOYMENT_INSURANCE_CONCEPT_CODE})
)
INCOME_TAX_DEDUCTIBLE_CONCEPT_CODES = (
    MANDATORY_DECLARED_CONTRIBUTION_CONCEPT_CODES
    | frozenset({UNEMPLOYMENT_INSURANCE_CONCEPT_CODE})
)
INCOME_TAX_CONCEPT_CODE = "INCOME_TAX"
# The set of concept codes that must all be present as persisted items
# before a period's net-pay/review readiness gates (see
# payroll_repository_shared._reconcile_period_net_pay() and
# payroll_repository_commands.review_period()) will proceed.
# HEALTH_ADDITIONAL_UF is intentionally NOT part of this set -- see
# MANDATORY_DECLARED_CONTRIBUTION_CONCEPT_CODES's docstring above for why.
REVIEW_REQUIRED_CONCEPT_CODES = INCOME_TAX_DEDUCTIBLE_CONCEPT_CODES | frozenset(
    {INCOME_TAX_CONCEPT_CODE}
)
COMPLEMENTARY_INSURANCE_VALIDATION_PENDING_PREFIX = (
    "Complementary insurance validation pending: "
)
