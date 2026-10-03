"""Tests for import contract prerequisites."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from payroll.application.errors import PayrollConflictError
from payroll.infrastructure.db.repositories.payroll_repository import (
    SqlAlchemyPayrollRepository,
)

from test_payroll_repository import (
    FakeResult,
    FakeSession,
    build_health_pair,
    build_import_row,
    build_pension_pair,
    build_plan_deduction_and_validation_results,
)


@pytest.mark.asyncio
async def test_import_rejects_employer_without_existing_contract() -> None:
    """Import must not recreate employment history implicitly."""
    pension_plan, pension_institution = build_pension_pair(
        plan_id=1, code="AFP_TEST", name="AFP Test", additional_rate=Decimal("0")
    )
    health_plan, health_institution = build_health_pair(plan_id=1, institution_id=1)
    session = FakeSession(
        [
            FakeResult(scalar_rows=[SimpleNamespace(id=1, code="SALARY_BASE")]),
            *build_plan_deduction_and_validation_results(
                pension_plan, pension_institution, health_plan, health_institution
            ),
            FakeResult(scalar_one=None),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    with pytest.raises(PayrollConflictError, match="employment contract must exist"):
        await repository.import_rows([build_import_row(employer="UNKNOWN")])
