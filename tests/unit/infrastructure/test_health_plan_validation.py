"""Tests for active health-plan validation."""

from datetime import date

import pytest

from payroll.application.errors import PayrollConflictError
from payroll.infrastructure.db.repositories.payroll_repository import (
    SqlAlchemyPayrollRepository,
)

from test_payroll_repository import FakeResult, FakeSession, build_health_pair


@pytest.mark.asyncio
async def test_health_plan_validation_rejects_inactive_institution() -> None:
    """An inactive health institution cannot be used for automatic import deduction."""
    repository = SqlAlchemyPayrollRepository(
        FakeSession(
            [
                FakeResult(
                    first_row=build_health_pair(
                        code="LEGACY", name="Legacy", active=False
                    )
                )
            ]
        )
    )  # type: ignore[arg-type]

    with pytest.raises(
        PayrollConflictError,
        match="Health plan 22 belongs to inactive health institution LEGACY",
    ):
        await repository._get_health_plan(  # noqa: SLF001
            22, date(2026, 1, 31), require_active=True
        )
