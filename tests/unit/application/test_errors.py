"""Tests for the application-layer error hierarchy's structured detail support."""

from payroll.application.errors import PayrollValidationError


def test_detail_defaults_to_the_message() -> None:
    """A plain single-string raise (the vast majority of call sites) needs no change.

    `detail` must equal the message itself when no explicit detail is
    given, so every existing `PayrollValidationError("...")` call across
    the codebase keeps behaving exactly as before.
    """
    exc = PayrollValidationError("boom")

    assert str(exc) == "boom"
    assert exc.detail == "boom"


def test_detail_can_be_a_structured_payload() -> None:
    """An explicit detail overrides the message-based default."""
    structured = {"message": "boom", "conflicting_periods": []}
    exc = PayrollValidationError("boom", detail=structured)

    assert str(exc) == "boom"
    assert exc.detail == structured
