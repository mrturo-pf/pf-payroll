"""Helpers for mapping application errors to HTTP responses."""

from fastapi import HTTPException

from payroll.application.errors import PayrollError


def to_http_exception(exc: PayrollError, *, default_status: int = 400) -> HTTPException:
    """Converts application errors into HTTP exceptions."""

    status_code = exc.status_code if isinstance(exc, PayrollError) else default_status
    return HTTPException(status_code=status_code, detail=str(exc))
