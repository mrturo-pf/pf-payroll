"""Tests for test api errors."""

from payroll.application.errors import PayrollConflictError, PayrollValidationError
from payroll.interfaces.api.errors import to_http_exception


def test_to_http_exception_defaults_to_supplied_status() -> None:
    """Test to http exception defaults to supplied status."""
    exc = to_http_exception(ValueError("boom"), default_status=422)

    assert exc.status_code == 422
    assert exc.detail == "boom"


def test_to_http_exception_uses_domain_status_code() -> None:
    """Test to http exception uses domain status code."""
    exc = to_http_exception(PayrollConflictError("conflict"), default_status=400)

    assert exc.status_code == 409
    assert exc.detail == "conflict"


def test_to_http_exception_uses_structured_detail_when_present() -> None:
    """A PayrollError with explicit detail surfaces that, not the plain message.

    Covers the reconciliation-conflict case (build_reconciliation_conflict_detail())
    where the message alone can't say which period/field failed.
    """
    structured = {"message": "boom", "conflicting_periods": [{"period_year": 2026}]}
    exc = to_http_exception(
        PayrollValidationError("boom", detail=structured), default_status=400
    )

    assert exc.status_code == 400
    assert exc.detail == structured
