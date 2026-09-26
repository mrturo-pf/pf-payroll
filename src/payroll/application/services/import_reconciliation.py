"""Shared reconciliation-conflict detection for imported payroll periods.

Lives in `application/` (not `interfaces/api/routes/`) specifically so both
the HTTP routes (`/payroll/import`, `/payroll/import/rows`) and the
`payroll import` CLI command can gate a commit on the same rule without the
CLI depending on the HTTP interface layer.
"""

from payroll.application.dto import ImportedPayrollPeriodDTO
from payroll.shared.constants import COMPLEMENTARY_INSURANCE_VALIDATION_PENDING_PREFIX


def period_has_reconciliation_conflict(period: ImportedPayrollPeriodDTO) -> bool:
    """Return True if this period has a genuine declared-vs-computed mismatch.

    Deliberately excludes "pending" states: a projected period awaiting plan
    assignment, or a temporary market-data/economic-index lookup gap, also
    produce a non-null warning, but there's nothing wrong there -- there's
    simply nothing to compare yet. Blocking a commit for those would break
    the legitimate "import a future/projected period" workflow.
    Distinguished via the typed `expected_*` fields the reconciliation
    pipeline already computes (populated only once a real comparison ran),
    not by matching against warning prose -- a wording tweak elsewhere must
    never silently change this gate's behavior.
    """
    if period.net_pay_warning is not None and period.expected_net_pay_clp is not None:
        return True
    if (
        period.contribution_validation is not None
        and period.contribution_validation.warning is not None
        and period.contribution_validation.expected_pension_base_clp is not None
    ):
        return True
    if period.complementary_insurance_validation is not None:
        return any(
            not warning.startswith(COMPLEMENTARY_INSURANCE_VALIDATION_PENDING_PREFIX)
            for warning in period.complementary_insurance_validation.warnings
        )
    return False


def is_import_fully_validated(periods: list[ImportedPayrollPeriodDTO]) -> bool:
    """Return True when every period reconciled without a genuine conflict.

    "Pending" states (nothing to compare yet) do not count -- see
    period_has_reconciliation_conflict(). Callers that also need to gate on
    unresolved concept_code rows (POST /payroll/import/rows only -- CSV/XLSX
    import has no such partial-resolution concept) must combine that check
    separately; it isn't part of this function on purpose, since it isn't a
    property of the periods themselves.
    """
    return not any(period_has_reconciliation_conflict(period) for period in periods)
