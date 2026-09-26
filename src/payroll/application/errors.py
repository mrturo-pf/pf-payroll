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
