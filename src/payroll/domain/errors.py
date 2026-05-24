"""Domain-specific errors."""


class DomainValidationError(ValueError):
    """Raised when a domain invariant or supported input set is violated."""


class UnsupportedEmploymentContractKindError(DomainValidationError):
    """Raised when unemployment rules receive an unsupported contract kind."""
