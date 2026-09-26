"""Helpers for mapping application errors to HTTP responses."""

from fastapi import HTTPException

from payroll.application.errors import PayrollError


def to_http_exception(exc: PayrollError, *, default_status: int = 400) -> HTTPException:
    """Convert application errors into HTTP exceptions.

    Uses `exc.detail` (falls back to the plain message for any PayrollError
    that never opted into a structured payload -- see PayrollError) rather
    than always re-stringifying, so a raiser that attached richer detail
    (e.g. which periods/fields conflicted) isn't flattened back into an
    opaque sentence here.
    """
    status_code = exc.status_code if isinstance(exc, PayrollError) else default_status
    detail = exc.detail if isinstance(exc, PayrollError) else str(exc)
    return HTTPException(status_code=status_code, detail=detail)
