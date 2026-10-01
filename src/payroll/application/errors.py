"""Domain-oriented application errors with transport-friendly status codes."""


class PayrollError(ValueError):
    """Base application error.

    `detail` lets a raiser attach a structured (JSON-serializable) payload
    richer than the plain message -- e.g. which specific periods/fields
    conflicted, not just "something didn't reconcile". Defaults to the
    message itself, so every existing single-string raise across the
    codebase (`PayrollValidationError("...")`, etc.) keeps working exactly
    as before with zero call-site changes; only a raiser that explicitly
    wants richer output needs to pass `detail=`.
    """

    status_code = 400

    def __init__(self, message: str, *, detail: object | None = None) -> None:
        """Initialize the instance, defaulting detail to the message."""
        super().__init__(message)
        self.detail: object = message if detail is None else detail


class PayrollValidationError(PayrollError):
    """Raised for invalid input or malformed commands."""


class PayrollNotFoundError(PayrollError):
    """Raised when required domain data does not exist."""

    status_code = 404


class PayrollConflictError(PayrollError):
    """Raised when a business precondition blocks the operation."""

    status_code = 409


class PayrollDependencyError(PayrollError):
    """Raised when configured external providers fail to return required data."""

    status_code = 502


class PayrollDependencyConfigurationError(PayrollError):
    """Raised when an external dependency is required but not configured."""

    status_code = 503


class PayrollImportNotValidatedError(PayrollError):
    """Raised by the import endpoints when the result is not fully validated.

    Scoped to POST /payroll/import/spreadsheet and POST /payroll/import/json
    only, and raised identically regardless of `mode`: mode="commit" needs
    every row resolved and every period reconciled before it ever writes
    anything, and mode="validate" needs the exact same two conditions before
    it can tell the caller "this would be safe to commit" -- so in both
    cases, "not validated" is the same underlying fact about the payroll
    data itself, not about what the endpoint was asked to do with it. Using
    one error class/status for both keeps that fact reported consistently
    instead of splitting it across two different codes depending on mode.

    422 (Unprocessable Entity), not the plain 400 most other
    PayrollValidationError call sites use: the request is syntactically
    well-formed (valid JSON/CSV, right shape, required fields present --
    any failure of *that* kind is still a plain 400 from elsewhere in these
    routes), it just fails a business-level reconciliation rule once
    genuinely evaluated. That distinction is exactly what RFC 4918 carved
    422 out for. Deliberately still a 4xx, not a 5xx: this is a fact about
    the caller's payroll data, not a server-side failure -- it is fully
    deterministic (the same input reliably produces the same outcome), so a
    5xx would misleadingly invite automatic retries/alerting for a
    condition retrying can never fix.

    The structured detail (which periods/rows are the problem) travels in
    `detail`, same as any other PayrollError -- see
    build_reconciliation_conflict_detail() and its `unresolved_rows`
    extension in the /payroll/import/json route.

    A direct consequence: `ImportPayrollResponse.saved` can never be
    `False`. `saved=False` would describe "we attempted mode="commit" and
    the write did not persist" -- but that is exactly this error's trigger
    condition, so it is always raised (422) before any response is built
    instead. `saved` only ever ends up `True` (mode="commit", validated) or
    `None` (mode="validate", nothing attempted); there is no 5xx anywhere
    in this pair for a "saved" failure to report, because that state does
    not exist.
    """

    status_code = 422


class PayrollPeriodNotFoundError(PayrollNotFoundError):
    """Raised when a payroll period cannot be found."""


class PayrollSummaryNotFoundError(PayrollNotFoundError):
    """Raised when a payroll summary cannot be found."""


class PensionPlanNotFoundError(PayrollNotFoundError):
    """Raised when a pension plan cannot be found."""


class HealthPlanNotFoundError(PayrollNotFoundError):
    """Raised when a health plan cannot be found."""


class ExchangeRateNotFoundError(PayrollNotFoundError):
    """Raised when a required exchange rate cannot be found."""


class EconomicIndexNotFoundError(PayrollNotFoundError):
    """Raised when a required economic index cannot be found."""


class IncomeTaxBracketNotFoundError(PayrollNotFoundError):
    """Raised when no income tax bracket matches the requested period/base."""


class PdfTemplateNotFoundError(PayrollNotFoundError):
    """Raised when a PDF template cannot be found by its template_id."""
